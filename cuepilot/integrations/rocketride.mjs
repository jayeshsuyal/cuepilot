/** Bounded, staging-only RocketRide runner. No account mutations or tunnel creation. */
import { readFile } from 'node:fs/promises';
import { randomUUID } from 'node:crypto';
import { isIP } from 'node:net';
import { resolve } from 'node:path';
import { pathToFileURL } from 'node:url';
import { parseEnv } from 'node:util';
import { Question, RocketRideClient } from 'rocketride';

const PIPE = new URL('../pipelines/cuepilot.pipe', import.meta.url);
const MODEL_SCHEMA = new URL('../pipelines/schema/llm_openai.json', import.meta.url);
const ROUTES = Object.freeze({
  prepare: ['ingest-memory', 'recall-recipe', 'validate-show', 'plan'],
  execute: ['validate-show', 'execute', 'verify'],
});
const EXPECTED_PROVIDERS = ['webhook', 'agent_rocketride', 'response_answers', 'llm_openai', 'memory_internal', 'tool_http_request'];
export const ROCKETRIDE_LIMITS = Object.freeze({
  prepare: Object.freeze({ operationMs: 480_000, sendMs: 360_000, httpSeconds: 180 }),
  execute: Object.freeze({ operationMs: 360_000, sendMs: 240_000, httpSeconds: 120 }),
  controlMs: 20_000, bridgeMs: 12_000, terminateMs: 15_000, disconnectMs: 8_000,
});
const SCENES = ['intro', 'presentation', 'holding'];

class BridgeError extends Error {
  constructor(code) { super(code); this.name = 'BridgeError'; this.code = code; }
}
const fail = code => { throw new BridgeError(code); };
const escapeRegex = value => value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
const validRunId = value => typeof value === 'string' && /^[A-Za-z0-9_-]{1,128}$/.test(value);

async function bounded(operation, milliseconds, code, signal) {
  let timer;
  let abort;
  try {
    signal?.throwIfAborted();
    return await Promise.race([Promise.resolve().then(operation), new Promise((_, reject) => {
      timer = setTimeout(() => reject(new BridgeError(code)), milliseconds);
      if (signal) {
        abort = () => reject(signal.reason);
        signal.addEventListener('abort', abort, { once: true });
      }
    })]);
  } finally {
    clearTimeout(timer);
    if (abort) signal.removeEventListener('abort', abort);
  }
}

function publicBase(value) {
  let url;
  try { url = new URL(value); } catch { fail('PUBLIC_HTTPS_BRIDGE_REQUIRED'); }
  if (url.protocol !== 'https:' || url.username || url.password || url.search || url.hash
      || url.pathname !== '/' || (url.port && url.port !== '443')
      || isIP(url.hostname) || url.hostname === 'localhost'
      || /\.(?:localhost|local|invalid|test|internal)$/.test(url.hostname)
      || !url.hostname.includes('.')) fail('PUBLIC_HTTPS_BRIDGE_REQUIRED');
  return url.origin;
}

function settings(env) {
  const errors = [];
  let base;
  try { base = publicBase(env.CUEPILOT_PUBLIC_BASE_URL); } catch (e) { errors.push(e.code); }
  let uri;
  try {
    const u = new URL(env.ROCKETRIDE_URI);
    if (u.protocol !== 'https:' || u.hostname !== 'staging.rocketride.ai'
        || u.username || u.password || u.search || u.hash || u.pathname !== '/'
        || (u.port && u.port !== '443')) fail('STAGING_URI_REQUIRED');
    uri = u.origin;
  } catch { errors.push('STAGING_URI_REQUIRED'); }
  const apiKey = env.ROCKETRIDE_APIKEY;
  if (!apiKey) errors.push('ROCKETRIDE_APIKEY_REQUIRED');
  const bridgeToken = env.CUEPILOT_BRIDGE_TOKEN;
  if (!bridgeToken || bridgeToken.length < 24 || /\s/.test(bridgeToken)) errors.push('BRIDGE_TOKEN_REQUIRED_MIN_24_CHARS');
  // Do not silently use Cognee's key, another provider's key, or assume platform billing.
  const modelKey = env.CUEPILOT_OPENAI_API_KEY || env.ROCKETRIDE_OPENAI_KEY;
  if (!modelKey || /\s/.test(modelKey)) errors.push('OPENAI_MODEL_KEY_REQUIRED');
  const profile = env.CUEPILOT_ROCKETRIDE_MODEL_PROFILE || 'openai-4o';
  return { uri, apiKey, base, bridgeToken, modelKey, profile, errors };
}

/** Returns a safe template when the public bridge is not yet configured. */
export async function buildRocketRidePipeline({ phase = 'prepare', env = process.env } = {}) {
  if (!ROUTES[phase]) fail('INVALID_PHASE');
  const pipeline = JSON.parse(await readFile(PIPE, 'utf8'));
  if (pipeline.components?.length !== EXPECTED_PROVIDERS.length
      || pipeline.components.some((node, index) => node.provider !== EXPECTED_PROVIDERS[index])) fail('PIPELINE_SHAPE_CHANGED');
  const cfg = settings(env);
  const modelSchema = JSON.parse(await readFile(MODEL_SCHEMA, 'utf8'));
  const known = modelSchema.Pipe?.schema?.properties?.profile?.enum;
  if (!Array.isArray(known) || !known.includes(cfg.profile) || cfg.profile === 'custom') fail('MODEL_PROFILE_NOT_VERIFIED');
  const model = pipeline.components.find(node => node.provider === 'llm_openai');
  const agent = pipeline.components.find(node => node.provider === 'agent_rocketride');
  if (agent.config.instructions.filter(instruction => instruction.includes('timeout ${ROCKETRIDE_CUEPILOT_HTTP_TIMEOUT_SECONDS}')).length !== 1) fail('PIPELINE_TIMEOUT_BINDING_INVALID');
  agent.config.instructions = agent.config.instructions.map(instruction => instruction.replace(
    'timeout ${ROCKETRIDE_CUEPILOT_HTTP_TIMEOUT_SECONDS}', `timeout ${ROCKETRIDE_LIMITS[phase].httpSeconds}`));
  // The checked-in template owns this server-side reference. Never substitute a
  // runtime credential into the pipeline sent for validation or stored remotely.
  const credentialReference = model.config[model.config.profile]?.apikey;
  if (typeof credentialReference !== 'string'
      || !/^\$\{ROCKETRIDE_CUEPILOT_OPENAI_KEY\}$/.test(credentialReference)) fail('MODEL_CREDENTIAL_REFERENCE_INVALID');
  model.config = { profile: cfg.profile, [cfg.profile]: { apikey: credentialReference }, parameters: {} };
  const http = pipeline.components.find(node => node.provider === 'tool_http_request');
  const base = cfg.base || 'https://cuepilot-unconfigured.invalid';
  const pattern = `^${escapeRegex(base)}/api/v1/tools/(?:${ROUTES[phase].join('|')})$`;
  http.config.urlWhitelist = [{ whitelistPattern: pattern }];
  // Compile and test locally: invalid regexes are skipped by the remote provider.
  const whitelist = new RegExp(pattern);
  if (!whitelist.test(`${base}/api/v1/tools/${ROUTES[phase][0]}`)
      || whitelist.test(`${base}/api/v1/tools/${ROUTES[phase][0]}?redirect=x`)
      || whitelist.test(`${base}/api/v1/runs/approve`)
      || (phase === 'prepare' && whitelist.test(`${base}/api/v1/tools/execute`))) fail('UNSAFE_HTTP_WHITELIST');
  for (const method of ['GET', 'PUT', 'PATCH', 'DELETE', 'HEAD', 'OPTIONS']) http.config[`allow${method}`] = false;
  http.config.allowPOST = true;
  http.config.maxConcurrentRequests = 1;
  return pipeline;
}

async function connect(cfg, signal, phase = 'prepare') {
  if (!cfg.uri || !cfg.apiKey) fail('STAGING_AUTH_CONFIGURATION_REQUIRED');
  for (let attempt = 0; attempt < 3; attempt++) {
    signal?.throwIfAborted();
    // Retry only connection setup before any task exists. Never retry use/send
    // or a stage-changing call after an uncertain response.
    // send() awaits DataPipe.close() using this same long request timeout.
    const client = new RocketRideClient({ uri: cfg.uri, auth: cfg.apiKey, persist: false, env: {}, requestTimeout: ROCKETRIDE_LIMITS[phase].sendMs + 5_000 });
    try {
      await bounded(() => client.connect(cfg.apiKey, { timeout: 10_000 }), 15_000, 'STAGING_CONNECT_TIMEOUT', signal);
      return client;
    } catch (error) {
      await bounded(() => client.disconnect(), 5_000, 'DISCONNECT_TIMEOUT').catch(() => {});
      signal?.throwIfAborted();
      if (error?.name === 'AuthenticationException') fail('STAGING_AUTH_REJECTED');
      if (attempt === 2) throw error;
      await new Promise(resolve => setTimeout(resolve, 250 * (attempt + 1)));
    }
  }
}

async function validateAndBalance(client, pipeline, profile, signal) {
  // Verify against the current server as well as the checked-in schema snapshot.
  const definition = await bounded(() => client.getService('llm_openai'), ROCKETRIDE_LIMITS.controlMs, 'MODEL_SCHEMA_TIMEOUT', signal);
  const profiles = definition?.Pipe?.schema?.properties?.profile?.enum;
  if (!Array.isArray(profiles) || !profiles.includes(profile)) fail('MODEL_PROFILE_NOT_ON_STAGING');
  const validation = await bounded(() => client.validate({ pipeline }), ROCKETRIDE_LIMITS.controlMs, 'VALIDATION_TIMEOUT', signal);
  if (!Array.isArray(validation?.errors) || !Array.isArray(validation?.warnings)) fail('VALIDATION_RESPONSE_INVALID');
  const orgId = client.getOrgId();
  if (!orgId) fail('CREDIT_BALANCE_ORGANIZATION_UNAVAILABLE');
  const credit = await bounded(() => client.billing.getCreditBalance(orgId), ROCKETRIDE_LIMITS.controlMs, 'CREDIT_BALANCE_TIMEOUT', signal);
  const balances = credit?.balances;
  if (!balances || typeof balances !== 'object' || Array.isArray(balances)
      || Object.values(balances).some(value => typeof value !== 'number' || !Number.isFinite(value))) fail('CREDIT_BALANCE_RESPONSE_INVALID');
  return {
    validation: { passed: validation.errors.length === 0, errors: validation.errors.length, warnings: validation.warnings.length },
    // The sponsor reports tokens as the execution-credit unit. Unrelated wallets do not prove compute availability.
    credits: { positiveComputeCredit: typeof balances.tokens === 'number' && balances.tokens > 0 },
    modelProfileVerified: true,
  };
}

async function bridgeRead(cfg, path, signal) {
  const readSignal = AbortSignal.any([AbortSignal.timeout(ROCKETRIDE_LIMITS.bridgeMs), ...(signal ? [signal] : [])]);
  return bounded(async () => {
    const response = await fetch(`${cfg.base}${path}`, {
      method: 'GET', headers: { Authorization: `Bearer ${cfg.bridgeToken}`, Accept: 'application/json' },
      redirect: 'error', signal: readSignal,
    });
    if (!response.ok) fail('BRIDGE_READ_FAILED');
    const reader = response.body?.getReader();
    if (!reader) fail('BRIDGE_RESPONSE_INVALID');
    const chunks = [];
    let size = 0;
    try {
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        size += value.byteLength;
        if (size > 1_000_000) fail('BRIDGE_RESPONSE_TOO_LARGE');
        chunks.push(value);
      }
    } finally { await reader.cancel().catch(() => {}); }
    let value;
    try { value = JSON.parse(Buffer.concat(chunks).toString('utf8')); } catch { fail('BRIDGE_RESPONSE_INVALID'); }
    if (!value || typeof value !== 'object' || Array.isArray(value)) fail('BRIDGE_RESPONSE_INVALID');
    return value;
  }, ROCKETRIDE_LIMITS.bridgeMs, 'BRIDGE_READ_TIMEOUT', signal);
}

async function canonicalRun(cfg, runId, signal) {
  const run = await bridgeRead(cfg, `/api/v1/runs/${encodeURIComponent(runId)}`, signal);
  if (run.id !== runId || !['queued', 'needs_approval', 'approved', 'running', 'completed', 'blocked', 'failed'].includes(run.status)) fail('CANONICAL_RUN_INVALID');
  return run;
}

function verifiedPlan(run) {
  const plan = run.plan;
  // The dedicated public bridge deliberately projects only the approved hash.
  // Full recipe, provenance, show revision and cue guards remain local to API.
  if (run.executionMode !== 'live' || !plan || typeof plan.hash !== 'string'
      || !/^[a-f0-9]{64}$/.test(plan.hash)) fail('CANONICAL_PLAN_INVALID');
  return plan;
}

function freshRun(run) {
  if (!Array.isArray(run.receipts) || run.receipts.length !== 0
      || (run.nextStep !== undefined && run.nextStep !== 0)) fail('FRESH_RUN_REQUIRED');
}

function executionEvidence(run) {
  verifiedPlan(run);
  const receipts = run.receipts;
  if (run.status !== 'completed' || (run.nextStep !== undefined && run.nextStep !== 3)
      || !Array.isArray(receipts) || receipts.length !== 3
      || receipts.some((r, index) => r.ok !== true || r.runId !== run.id || r.stepIndex !== index
        || r.scene !== SCENES[index] || typeof r.id !== 'string' || !r.id
        || !Number.isInteger(r.stageRevision) || r.stageRevision < 1
        || (index > 0 && r.stageRevision <= receipts[index - 1].stageRevision)
        || typeof r.committedAt !== 'string' || !Number.isFinite(Date.parse(r.committedAt)))
      || new Set(receipts.map(r => r.id)).size !== 3) fail('CANONICAL_RECEIPTS_INVALID');
  // The API computes this from verified Rote execution AND successful outcome
  // write-back. Stage completion alone precedes both and cannot prove success.
  if (run.verifiedCompletion !== true) fail('VERIFIED_COMPLETION_REQUIRED');
  return { receiptIds: receipts.map(receipt => receipt.id), receiptCount: receipts.length, verifiedCompletion: true };
}

function knownError(error, stage) {
  return error instanceof BridgeError ? error.code : `${stage.toUpperCase().replace(/[^A-Z0-9]+/g, '_')}_FAILED`;
}

/** Validation never starts a task or sends the model key / bridge token. */
export async function checkRocketRide({ env = process.env, offline = false, signal } = {}) {
  const cfg = settings(env);
  const report = { ok: false, taskStarted: false, blockers: [...cfg.errors], offline };
  let client;
  let stage = 'local_pipeline';
  try {
    const pipeline = await buildRocketRidePipeline({ env });
    report.localPipelineValid = true;
    if (!offline) {
      stage = 'staging_auth';
      client = await connect(cfg, signal);
      stage = 'staging_validation';
      Object.assign(report, await validateAndBalance(client, pipeline, cfg.profile, signal));
      if (!report.validation.passed) report.blockers.push('PIPELINE_VALIDATION_FAILED');
      if (!report.credits.positiveComputeCredit) report.blockers.push('POSITIVE_COMPUTE_CREDIT_REQUIRED');
      if (cfg.base && cfg.bridgeToken) {
        stage = 'bridge_health';
        await bridgeRead(cfg, '/api/v1/health', signal);
        report.bridgeReachableFromClient = true;
      }
    }
    report.ok = report.blockers.length === 0 && !offline;
  } catch (error) { report.blockers.push(knownError(error, stage)); }
  finally {
    if (client) await bounded(() => client.disconnect(), ROCKETRIDE_LIMITS.disconnectMs, 'DISCONNECT_TIMEOUT').catch(() => report.blockers.push('CONNECTION_CLEANUP_UNCONFIRMED'));
  }
  report.blockers = [...new Set(report.blockers)];
  report.ok = report.ok && report.blockers.length === 0;
  return report;
}

/** Canonical backend status, not generated answers, decides whether a phase succeeded. */
export async function runRocketRide({ runId, phase = 'prepare', env = process.env, signal } = {}) {
  const cfg = settings(env);
  const report = { ok: false, runId, taskStarted: false, taskStartAttempted: false, freshExecution: false,
    phase, blockers: [...cfg.errors], elapsedMs: null };
  let client;
  let taskToken;
  let requestedToken;
  let before;
  let startAttempted = false;
  let stage = 'configuration';
  const began = Date.now();
  const limits = ROCKETRIDE_LIMITS[phase] || ROCKETRIDE_LIMITS.prepare;
  report.operationDeadlineMs = limits.operationMs;
  report.maxElapsedMs = limits.operationMs + ROCKETRIDE_LIMITS.terminateMs + ROCKETRIDE_LIMITS.disconnectMs + ROCKETRIDE_LIMITS.bridgeMs;
  const deadline = new AbortController();
  const timer = setTimeout(() => deadline.abort(new BridgeError('OPERATION_DEADLINE_EXCEEDED')), limits.operationMs);
  const operationSignal = AbortSignal.any([deadline.signal, ...(signal ? [signal] : [])]);
  try {
    operationSignal.throwIfAborted();
    if (!validRunId(runId)) fail('INVALID_RUN_ID');
    if (!ROUTES[phase]) fail('INVALID_PHASE');
    if (cfg.errors.length) return report;
    const pipeline = await buildRocketRidePipeline({ phase, env });
    report.pipelineId = pipeline.project_id;
    stage = 'bridge_preflight';
    await bridgeRead(cfg, '/api/v1/health', operationSignal);
    before = await canonicalRun(cfg, runId, operationSignal);
    if (before.executionMode !== 'live') fail('LIVE_RUN_REQUIRED');
    if ((phase === 'prepare' && before.status === 'needs_approval') || (phase === 'execute' && before.status === 'completed')) {
      verifiedPlan(before);
      if (phase === 'execute') Object.assign(report, executionEvidence(before));
      else freshRun(before);
      report.ok = true;
      report.status = before.status;
      report.planHash = before.plan.hash;
      report.canonicalVerified = true;
      report.alreadySatisfied = true;
      return report;
    }
    if (phase === 'prepare' && before.status !== 'queued') fail('PREPARATION_REQUIRES_QUEUED_RUN');
    if (phase === 'execute') {
      if (before.status !== 'approved') fail('EXECUTION_REQUIRES_APPROVED_PLAN');
      verifiedPlan(before);
      report.planHash = before.plan.hash;
    }
    freshRun(before);
    stage = 'staging_auth';
    client = await connect(cfg, operationSignal, phase);
    stage = 'staging_validation';
    Object.assign(report, await validateAndBalance(client, pipeline, cfg.profile, operationSignal));
    if (!report.validation.passed) fail('PIPELINE_VALIDATION_FAILED');
    if (!report.credits.positiveComputeCredit) fail('POSITIVE_COMPUTE_CREDIT_REQUIRED');
    // Validation/authentication can take a minute. Never launch against stale
    // preflight state; the API must still atomically claim execution itself.
    stage = 'bridge_recheck';
    const current = await canonicalRun(cfg, runId, operationSignal);
    if (current.executionMode !== 'live' || current.status !== before.status
        || current.speakerId !== before.speakerId || JSON.stringify(current.plan) !== JSON.stringify(before.plan)) fail('RUN_CHANGED_BEFORE_START');
    freshRun(current);
    stage = 'pipeline_start';
    taskToken = requestedToken = randomUUID();
    startAttempted = true;
    report.taskStartAttempted = true;
    const started = await bounded(() => client.use({
      pipeline, token: taskToken, source: 'webhook_1', ttl: 120, threads: 1, useExisting: false,
      name: `CuePilot ${phase}`, pipelineTraceLevel: 'metadata',
      env: {
        ROCKETRIDE_CUEPILOT_PUBLIC_BASE_URL: cfg.base,
        ROCKETRIDE_CUEPILOT_BRIDGE_TOKEN: cfg.bridgeToken,
        ROCKETRIDE_CUEPILOT_OPENAI_KEY: cfg.modelKey,
        ROCKETRIDE_CUEPILOT_PHASE: phase,
      },
    }), 45_000, 'PIPELINE_START_TIMEOUT', operationSignal);
    if (!started || typeof started.token !== 'string' || !started.token) fail('PIPELINE_START_RESPONSE_INVALID');
    taskToken = started.token;
    report.taskStarted = true;
    if (taskToken !== requestedToken) fail('PIPELINE_TASK_IDENTITY_CHANGED');
    stage = 'pipeline_send';
    const question = new Question({ expectJson: true });
    question.addQuestion(JSON.stringify({ runId, phase }));
    const planningSteps = new Set();
    const completedWaves = new Set();
    const observeProgress = async (type, data) => {
      // Accept only fixed host progress messages. Never retain thoughts, tool
      // arguments, answers, arbitrary event payloads or credential-bearing text.
      if (type !== 'thinking' || typeof data?.message !== 'string') return;
      const planning = /^Planning step ([1-9]|1[0-2])\.\.\.$/.exec(data.message);
      const completed = /^Step ([1-9]|1[0-2]) complete$/.exec(data.message);
      if (planning) { planningSteps.add(Number(planning[1])); report.planningStepsObserved = planningSteps.size; }
      if (completed) { completedWaves.add(Number(completed[1])); report.toolWavesObserved = completedWaves.size; }
      if (data.message === 'Generating final answer...') report.plannerFinalization = 'done';
      if (data.message === 'Synthesizing final answer...') report.plannerFinalization = 'synthesis';
    };
    const result = await bounded(() => client.send(taskToken, JSON.stringify(question.toDict()), {}, 'application/rocketride-question', observeProgress), limits.sendMs, 'PIPELINE_RESPONSE_UNCERTAIN', operationSignal);
    report.answerReceived = Boolean(result?.result_types && Object.values(result.result_types).includes('answers'));
    // Raw answers and traces can contain credentials echoed by a provider: neither is returned or logged.
  } catch (error) {
    report.blockers.push(operationSignal.aborted
      ? (deadline.signal.aborted ? 'OPERATION_DEADLINE_EXCEEDED' : 'OPERATION_CANCELLED') : knownError(error, stage));
  } finally {
    clearTimeout(timer);
    if (client && taskToken && startAttempted) {
      try {
        // The SDK accepts a caller-supplied token. An ambiguous use() response
        // still requires terminating that token; idle TTL is not cancellation.
        await bounded(() => Promise.all([...new Set([requestedToken, taskToken])].map(token => client.terminate(token))),
          ROCKETRIDE_LIMITS.terminateMs, 'TERMINATE_TIMEOUT');
        report.taskTerminated = true;
      } catch { report.blockers.push('TASK_CLEANUP_UNCONFIRMED_120_SECOND_IDLE_TTL'); }
    }
    if (client) await bounded(() => client.disconnect(), ROCKETRIDE_LIMITS.disconnectMs, 'DISCONNECT_TIMEOUT').catch(() => report.blockers.push('CONNECTION_CLEANUP_UNCONFIRMED'));
    // Reconcile even after a lost response or cancellation, after task cleanup.
    // This GET is an observation, never proof that an in-flight bridge job stopped.
    if (startAttempted) {
      try {
        const after = await canonicalRun(cfg, runId);
        report.status = after.status;
        report.receiptCount = Array.isArray(after.receipts) ? after.receipts.length : 0;
        if (after.executionMode !== 'live' || after.speakerId !== before.speakerId) fail('CANONICAL_RUN_CHANGED');
        if (phase === 'execute' && JSON.stringify(after.plan) !== JSON.stringify(before.plan)) fail('APPROVED_PLAN_CHANGED');
        if (after.status !== (phase === 'prepare' ? 'needs_approval' : 'completed')) fail(after.status === 'blocked' ? 'RUN_BLOCKED_BY_BACKEND' : 'EXPECTED_PHASE_STATUS_NOT_REACHED');
        verifiedPlan(after);
        if (phase === 'execute') Object.assign(report, executionEvidence(after));
        else freshRun(after);
        report.planHash = after.plan.hash;
        report.canonicalVerified = true;
        report.ok = report.blockers.length === 0;
        report.freshExecution = report.ok && phase === 'execute';
      } catch (error) { report.blockers.push(knownError(error, 'canonical_verification')); }
      // Even apparently successful writes remain uncertain if task startup or
      // cleanup failed. No automatic retry may infer zero effects from failure.
      if (report.blockers.length) report.reconciliationRequired = true;
    }
    report.elapsedMs = Date.now() - began;
    report.blockers = [...new Set(report.blockers)];
    report.ok = report.ok && report.blockers.length === 0;
  }
  return report;
}

async function loadCliEnvironment() {
  let loaded = {};
  for (const file of [new URL('../../.env', import.meta.url), new URL('../.env', import.meta.url)]) {
    try { loaded = { ...loaded, ...parseEnv(await readFile(file, 'utf8')) }; }
    catch (error) { if (error.code !== 'ENOENT') fail('ENV_FILE_UNREADABLE'); }
  }
  return { ...loaded, ...process.env };
}

if (process.argv[1] && pathToFileURL(resolve(process.argv[1])).href === import.meta.url) {
  const cancellation = new AbortController();
  const cancel = () => cancellation.abort(new BridgeError('OPERATION_CANCELLED'));
  process.on('SIGINT', cancel);
  process.on('SIGTERM', cancel);
  try {
    const args = process.argv.slice(2);
    const env = await loadCliEnvironment();
    let report;
    if (args.length === 0 || (args.length === 1 && args[0] === '--validate-only')) report = await checkRocketRide({ env, signal: cancellation.signal });
    else if (args.length === 1 && args[0] === '--offline') report = await checkRocketRide({ env, offline: true, signal: cancellation.signal });
    else if (args.length === 4 && args[0] === '--run' && args[2] === '--phase' && ROUTES[args[3]]) report = await runRocketRide({ runId: args[1], phase: args[3], env, signal: cancellation.signal });
    else fail('USAGE_EXPECTED_VALIDATE_ONLY_OR_RUN_ID_PHASE_PREPARE_EXECUTE');
    console.log(JSON.stringify(report, null, 2));
    process.exitCode = report.ok ? 0 : 2;
  } catch (error) {
    console.log(JSON.stringify({ ok: false, taskStarted: false, blockers: [knownError(error, 'runner')] }));
    process.exitCode = 2;
  } finally {
    process.removeListener('SIGINT', cancel);
    process.removeListener('SIGTERM', cancel);
  }
}

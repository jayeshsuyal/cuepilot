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

class BridgeError extends Error {
  constructor(code) { super(code); this.name = 'BridgeError'; this.code = code; }
}
const fail = code => { throw new BridgeError(code); };
const escapeRegex = value => value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
const validRunId = value => typeof value === 'string' && /^[A-Za-z0-9_-]{1,128}$/.test(value);

async function bounded(promise, milliseconds, code) {
  let timer;
  try {
    return await Promise.race([promise, new Promise((_, reject) => {
      timer = setTimeout(() => reject(new BridgeError(code)), milliseconds);
    })]);
  } finally { clearTimeout(timer); }
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
        || u.username || u.password || u.search || u.hash) fail('STAGING_URI_REQUIRED');
    uri = u.origin;
  } catch { errors.push('STAGING_URI_REQUIRED'); }
  const apiKey = env.ROCKETRIDE_APIKEY;
  if (!apiKey) errors.push('ROCKETRIDE_APIKEY_REQUIRED');
  const bridgeToken = env.CUEPILOT_BRIDGE_TOKEN;
  if (!bridgeToken || bridgeToken.length < 24 || /\s/.test(bridgeToken)) errors.push('BRIDGE_TOKEN_REQUIRED_MIN_24_CHARS');
  // Do not silently use Cognee's key, another provider's key, or assume platform billing.
  const modelKey = env.CUEPILOT_OPENAI_API_KEY || env.ROCKETRIDE_OPENAI_KEY;
  if (!modelKey || /\s/.test(modelKey)) errors.push('OPENAI_MODEL_KEY_REQUIRED');
  const profile = env.CUEPILOT_ROCKETRIDE_MODEL_PROFILE || 'openai-4o-mini';
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

async function connect(cfg) {
  if (!cfg.uri || !cfg.apiKey) fail('STAGING_AUTH_CONFIGURATION_REQUIRED');
  const client = new RocketRideClient({ uri: cfg.uri, auth: cfg.apiKey, persist: false, env: {}, requestTimeout: 20_000 });
  try {
    await bounded(client.connect(cfg.apiKey, { timeout: 10_000 }), 15_000, 'STAGING_CONNECT_TIMEOUT');
    return client;
  } catch (error) {
    await bounded(client.disconnect(), 5_000, 'DISCONNECT_TIMEOUT').catch(() => {});
    throw error;
  }
}

async function validateAndBalance(client, pipeline, profile) {
  // Verify against the current server as well as the checked-in schema snapshot.
  const definition = await client.getService('llm_openai');
  const profiles = definition?.Pipe?.schema?.properties?.profile?.enum;
  if (!Array.isArray(profiles) || !profiles.includes(profile)) fail('MODEL_PROFILE_NOT_ON_STAGING');
  const validation = await client.validate({ pipeline });
  if (!Array.isArray(validation?.errors) || !Array.isArray(validation?.warnings)) fail('VALIDATION_RESPONSE_INVALID');
  const orgId = client.getOrgId();
  if (!orgId) fail('CREDIT_BALANCE_ORGANIZATION_UNAVAILABLE');
  const credit = await client.billing.getCreditBalance(orgId);
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

async function bridgeRead(cfg, path) {
  const response = await fetch(`${cfg.base}${path}`, {
    method: 'GET', headers: { Authorization: `Bearer ${cfg.bridgeToken}`, Accept: 'application/json' },
    redirect: 'error', signal: AbortSignal.timeout(12_000),
  });
  if (!response.ok) fail('BRIDGE_READ_FAILED');
  const contentLength = Number(response.headers.get('content-length') || 0);
  if (contentLength > 1_000_000) fail('BRIDGE_RESPONSE_TOO_LARGE');
  const value = await response.json();
  if (!value || typeof value !== 'object' || Array.isArray(value)) fail('BRIDGE_RESPONSE_INVALID');
  return value;
}

async function canonicalRun(cfg, runId) {
  const run = await bridgeRead(cfg, `/api/v1/runs/${encodeURIComponent(runId)}`);
  if (run.id !== runId || !['queued', 'needs_approval', 'approved', 'running', 'completed', 'blocked', 'failed'].includes(run.status)) fail('CANONICAL_RUN_INVALID');
  return run;
}

function knownError(error, stage) {
  return error instanceof BridgeError ? error.code : `${stage.toUpperCase().replace(/[^A-Z0-9]+/g, '_')}_FAILED`;
}

/** Validation never starts a task or sends the model key / bridge token. */
export async function checkRocketRide({ env = process.env, offline = false } = {}) {
  const cfg = settings(env);
  const report = { ok: false, taskStarted: false, blockers: [...cfg.errors], offline };
  let client;
  let stage = 'local_pipeline';
  try {
    const pipeline = await buildRocketRidePipeline({ env });
    report.localPipelineValid = true;
    if (!offline) {
      stage = 'staging_auth';
      client = await connect(cfg);
      stage = 'staging_validation';
      Object.assign(report, await validateAndBalance(client, pipeline, cfg.profile));
      if (!report.validation.passed) report.blockers.push('PIPELINE_VALIDATION_FAILED');
      if (!report.credits.positiveComputeCredit) report.blockers.push('POSITIVE_COMPUTE_CREDIT_REQUIRED');
      if (cfg.base && cfg.bridgeToken) {
        stage = 'bridge_health';
        await bridgeRead(cfg, '/api/v1/health');
        report.bridgeReachableFromClient = true;
      }
    }
    report.ok = report.blockers.length === 0 && !offline;
  } catch (error) { report.blockers.push(knownError(error, stage)); }
  finally {
    if (client) await bounded(client.disconnect(), 8_000, 'DISCONNECT_TIMEOUT').catch(() => report.blockers.push('CONNECTION_CLEANUP_UNCONFIRMED'));
  }
  report.blockers = [...new Set(report.blockers)];
  report.ok = report.ok && report.blockers.length === 0;
  return report;
}

/** Canonical backend status, not generated answers, decides whether a phase succeeded. */
export async function runRocketRide({ runId, phase = 'prepare', env = process.env } = {}) {
  const cfg = settings(env);
  const report = { ok: false, taskStarted: false, phase, blockers: [...cfg.errors], elapsedMs: null };
  let client;
  let taskToken;
  let startAttempted = false;
  let stage = 'configuration';
  const began = Date.now();
  try {
    if (!validRunId(runId)) fail('INVALID_RUN_ID');
    if (!ROUTES[phase]) fail('INVALID_PHASE');
    if (cfg.errors.length) return report;
    const pipeline = await buildRocketRidePipeline({ phase, env });
    report.pipelineId = pipeline.project_id;
    stage = 'bridge_preflight';
    await bridgeRead(cfg, '/api/v1/health');
    const before = await canonicalRun(cfg, runId);
    if (before.executionMode !== 'live') fail('LIVE_RUN_REQUIRED');
    if ((phase === 'prepare' && before.status === 'needs_approval') || (phase === 'execute' && before.status === 'completed')) {
      report.ok = true;
      report.status = before.status;
      report.alreadySatisfied = true;
      return report;
    }
    if (phase === 'prepare' && before.status !== 'queued') fail('PREPARATION_REQUIRES_QUEUED_RUN');
    if (phase === 'execute' && (before.status !== 'approved' || !before.plan?.hash)) fail('EXECUTION_REQUIRES_APPROVED_PLAN');
    stage = 'staging_auth';
    client = await connect(cfg);
    stage = 'staging_validation';
    Object.assign(report, await validateAndBalance(client, pipeline, cfg.profile));
    if (!report.validation.passed) fail('PIPELINE_VALIDATION_FAILED');
    if (!report.credits.positiveComputeCredit) fail('POSITIVE_COMPUTE_CREDIT_REQUIRED');
    stage = 'pipeline_start';
    taskToken = randomUUID();
    startAttempted = true;
    const started = await bounded(client.use({
      pipeline, token: taskToken, source: 'webhook_1', ttl: 120, threads: 1, useExisting: false,
      name: `CuePilot ${phase}`, pipelineTraceLevel: 'metadata',
      env: {
        ROCKETRIDE_CUEPILOT_PUBLIC_BASE_URL: cfg.base,
        ROCKETRIDE_CUEPILOT_BRIDGE_TOKEN: cfg.bridgeToken,
        ROCKETRIDE_CUEPILOT_OPENAI_KEY: cfg.modelKey,
        ROCKETRIDE_CUEPILOT_PHASE: phase,
      },
    }), 45_000, 'PIPELINE_START_TIMEOUT');
    if (!started || typeof started.token !== 'string' || !started.token) fail('PIPELINE_START_RESPONSE_INVALID');
    taskToken = started.token;
    report.taskStarted = true;
    stage = 'pipeline_send';
    const question = new Question({ expectJson: true });
    question.addQuestion(JSON.stringify({ runId, phase }));
    const result = await bounded(client.send(taskToken, JSON.stringify(question.toDict()), {}, 'application/rocketride-question'), 180_000, 'PIPELINE_RESPONSE_UNCERTAIN');
    report.answerReceived = Boolean(result?.result_types && Object.values(result.result_types).includes('answers'));
    // Raw answers and traces can contain credentials echoed by a provider: neither is returned or logged.
    stage = 'canonical_verification';
    const after = await canonicalRun(cfg, runId);
    report.status = after.status;
    report.receiptCount = Array.isArray(after.receipts) ? after.receipts.length : 0;
    report.ok = phase === 'prepare' ? after.status === 'needs_approval' : after.status === 'completed';
    if (!report.ok) report.blockers.push(after.status === 'blocked' ? 'RUN_BLOCKED_BY_BACKEND' : 'EXPECTED_PHASE_STATUS_NOT_REACHED');
  } catch (error) {
    report.blockers.push(knownError(error, stage));
    if (startAttempted) report.reconciliationRequired = true;
  } finally {
    if (client && taskToken && startAttempted) {
      try {
        await bounded(client.terminate(taskToken), 15_000, 'TERMINATE_TIMEOUT');
        report.taskTerminated = true;
      } catch { report.blockers.push('TASK_CLEANUP_UNCONFIRMED_120_SECOND_IDLE_TTL'); }
    }
    if (client) await bounded(client.disconnect(), 8_000, 'DISCONNECT_TIMEOUT').catch(() => report.blockers.push('CONNECTION_CLEANUP_UNCONFIRMED'));
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
  try {
    const args = process.argv.slice(2);
    const env = await loadCliEnvironment();
    let report;
    if (args.length === 0 || (args.length === 1 && args[0] === '--validate-only')) report = await checkRocketRide({ env });
    else if (args.length === 1 && args[0] === '--offline') report = await checkRocketRide({ env, offline: true });
    else if (args.length === 4 && args[0] === '--run' && args[2] === '--phase' && ROUTES[args[3]]) report = await runRocketRide({ runId: args[1], phase: args[3], env });
    else fail('USAGE_EXPECTED_VALIDATE_ONLY_OR_RUN_ID_PHASE_PREPARE_EXECUTE');
    console.log(JSON.stringify(report, null, 2));
    process.exitCode = report.ok ? 0 : 2;
  } catch (error) {
    console.log(JSON.stringify({ ok: false, taskStarted: false, blockers: [knownError(error, 'runner')] }));
    process.exitCode = 2;
  }
}

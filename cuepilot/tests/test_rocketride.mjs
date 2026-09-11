import assert from 'node:assert/strict';
import { randomBytes } from 'node:crypto';
import { test } from 'node:test';
import { RocketRideClient } from 'rocketride';
import { buildRocketRidePipeline, checkRocketRide, runRocketRide, ROCKETRIDE_LIMITS } from '../integrations/rocketride.mjs';

const env = {
  ROCKETRIDE_URI: 'https://staging.rocketride.ai', ROCKETRIDE_APIKEY: randomBytes(32).toString('hex'),
  CUEPILOT_PUBLIC_BASE_URL: 'https://bridge.example.com',
  CUEPILOT_BRIDGE_TOKEN: randomBytes(32).toString('hex'),
  CUEPILOT_OPENAI_API_KEY: randomBytes(32).toString('hex'), ROCKETRIDE_UNRELATED_SECRET: randomBytes(32).toString('hex'),
};
const runId = 'fresh-run';
const plan = { id: 'plan', hash: 'a'.repeat(64), recipeId: 'stage-sequence', recipeVersion: 1,
  showRevision: 1, speakerId: 'ravi', origin: 'sponsor',
  cues: ['intro', 'presentation', 'holding'].map((scene, index) => ({ index, scene })) };
const approved = () => ({ id: runId, executionMode: 'live', speakerId: 'ravi', status: 'approved',
  plan: structuredClone(plan), nextStep: 0, receipts: [], traces: [] });
const completed = () => ({ ...approved(), status: 'completed', nextStep: 3, verifiedCompletion: true,
  receipts: plan.cues.map(({ scene, index }) => ({ ok: true, id: `receipt-${index}`, runId,
    stepIndex: index, scene, stageRevision: index + 10, committedAt: '2026-09-11T10:00:00Z' })),
  traces: [{ provider: 'rote', status: 'verified', operation: 'replay', evidence: {} }] });

// Every SDK/network call is replaced. These tests cannot authenticate or invoke a model.
function harness(t, options = {}) {
  const events = [];
  const starts = [];
  const initial = options.initial ?? approved();
  const final = options.final ?? completed();
  const runs = [...(options.runs ?? [initial, initial, final])];
  let sdkTimeout;
  t.mock.method(globalThis, 'fetch', async (url, init) => {
    events.push(url.endsWith('/health') ? 'health' : 'read');
    assert.equal(init.redirect, 'error');
    assert.equal(init.headers.Authorization, `Bearer ${env.CUEPILOT_BRIDGE_TOKEN}`);
    if (options.response) return options.response(url);
    return new Response(JSON.stringify(url.endsWith('/health') ? { ok: true } : runs.shift()));
  });
  const methods = {
    connect: async function () {
      events.push('connect'); sdkTimeout = this._requestTimeout;
      assert.deepEqual(this._env, {});
      await options.connect?.();
      this._billing = { getCreditBalance: async () => ({ balances: { tokens: 1 } }) };
    },
    getService: async () => ({ Pipe: { schema: { properties: { profile: { enum: ['openai-4o'] } } } } }),
    validate: options.validate ?? (async () => ({ errors: [], warnings: [] })),
    getOrgId: () => 'fake-organization',
    use: async params => {
      events.push('use'); starts.push(params);
      return options.use ? options.use(params) : { token: params.token };
    },
    send: async (...args) => {
      events.push('send');
      assert.equal(args[3], 'application/rocketride-question');
      return options.send ? options.send(...args) : { result_types: { output: 'answers' }, answers: env.CUEPILOT_OPENAI_API_KEY };
    },
    terminate: async token => { events.push('terminate'); return options.terminate?.(token); },
    disconnect: async () => { events.push('disconnect'); return options.disconnect?.(); },
  };
  for (const [name, implementation] of Object.entries(methods)) t.mock.method(RocketRideClient.prototype, name, implementation);
  return { events, starts, sdkTimeout: () => sdkTimeout };
}

test('offline validation and phase whitelists start no task and never substitute credentials', async t => {
  const h = harness(t);
  const check = await checkRocketRide({ env, offline: true });
  assert.equal(check.localPipelineValid, true);
  assert.equal(check.taskStarted, false);
  assert.deepEqual(h.events, []);
  for (const phase of ['prepare', 'execute']) {
    const pipe = await buildRocketRidePipeline({ phase, env });
    assert.equal(pipe.components.find(node => node.provider === 'llm_openai').config.profile, 'openai-4o');
    const http = pipe.components.find(node => node.provider === 'tool_http_request');
    const whitelist = new RegExp(http.config.urlWhitelist[0].whitelistPattern);
    assert.equal(http.config.allowPOST, true);
    assert.equal(http.config.allowGET, false);
    assert.equal(http.config.maxConcurrentRequests, 1);
    assert.equal(whitelist.test(`${env.CUEPILOT_PUBLIC_BASE_URL}/api/v1/tools/execute`), phase === 'execute');
    for (const suffix of ['../runs/approve', 'stage/cue', 'validate-show?redirect=x', 'validate-show/extra']) {
      assert.equal(whitelist.test(`${env.CUEPILOT_PUBLIC_BASE_URL}/api/v1/tools/${suffix}`), false);
    }
    assert.equal(JSON.stringify(pipe).includes(env.CUEPILOT_OPENAI_API_KEY), false);
    assert.equal(JSON.stringify(pipe).includes(env.CUEPILOT_BRIDGE_TOKEN), false);
    assert.ok(pipe.components.find(node => node.provider === 'agent_rocketride').config.instructions.some(
      instruction => instruction.includes(`timeout ${ROCKETRIDE_LIMITS[phase].httpSeconds},`)));
    assert.equal(JSON.stringify(pipe).includes('ROCKETRIDE_CUEPILOT_HTTP_TIMEOUT_SECONDS'), false);
  }
});

test('fresh execution requires exact receipts and Rote outcome, with cleanup before final read', async t => {
  const h = harness(t);
  const result = await runRocketRide({ env, runId, phase: 'execute' });
  assert.equal(result.ok, true);
  assert.equal(result.freshExecution, true);
  assert.equal(result.canonicalVerified, true);
  assert.deepEqual(result.receiptIds, ['receipt-0', 'receipt-1', 'receipt-2']);
  assert.equal(result.verifiedCompletion, true);
  assert.deepEqual(h.events.slice(-3), ['terminate', 'disconnect', 'read']);
  assert.ok(h.sdkTimeout() > ROCKETRIDE_LIMITS.execute.sendMs);
  assert.equal(h.starts[0].useExisting, false);
  assert.deepEqual(Object.keys(h.starts[0].env).sort(), ['ROCKETRIDE_CUEPILOT_BRIDGE_TOKEN',
    'ROCKETRIDE_CUEPILOT_OPENAI_KEY', 'ROCKETRIDE_CUEPILOT_PHASE', 'ROCKETRIDE_CUEPILOT_PUBLIC_BASE_URL']);
  for (const secret of [env.ROCKETRIDE_APIKEY, env.CUEPILOT_BRIDGE_TOKEN, env.CUEPILOT_OPENAI_API_KEY, env.ROCKETRIDE_UNRELATED_SECRET]) {
    assert.equal(JSON.stringify(result).includes(secret), false);
  }
});

test('completed shortcut validates evidence but does not claim fresh execution or start a task', async t => {
  const h = harness(t, { initial: completed() });
  const result = await runRocketRide({ env, runId, phase: 'execute' });
  assert.equal(result.ok, true);
  assert.equal(result.alreadySatisfied, true);
  assert.equal(result.freshExecution, false);
  assert.equal(result.taskStartAttempted, false);
  assert.equal(h.starts.length, 0);
});

test('transient connection failures retry before starting exactly one task', async t => {
  let attempts = 0;
  const h = harness(t, { connect: async () => {
    if (++attempts < 3) throw new Error('temporary connection failure');
  } });
  const result = await runRocketRide({ env, runId, phase: 'execute' });
  assert.equal(result.ok, true);
  assert.equal(attempts, 3);
  assert.equal(h.starts.length, 1);
  assert.equal(h.events.filter(event => event === 'send').length, 1);
  assert.equal(h.events.filter(event => event === 'disconnect').length, 3);
});

test('credential rejection fails before task creation without authentication retries', async t => {
  const h = harness(t, { connect: async () => {
    const error = new Error(env.ROCKETRIDE_APIKEY);
    error.name = 'AuthenticationException';
    throw error;
  } });
  const result = await runRocketRide({ env, runId, phase: 'execute' });
  assert.equal(result.ok, false);
  assert.equal(result.taskStartAttempted, false);
  assert.ok(result.blockers.includes('STAGING_AUTH_REJECTED'));
  assert.equal(h.events.filter(event => event === 'connect').length, 1);
  assert.equal(h.starts.length, 0);
  assert.equal(JSON.stringify(result).includes(env.ROCKETRIDE_APIKEY), false);
});

test('progress retains bounded host counts only and cannot override canonical failure', async t => {
  harness(t, { final: approved(), send: async (_token, _data, _info, _mime, progress) => {
    await progress('thinking', { message: 'Planning step 1...', token: env.CUEPILOT_OPENAI_API_KEY });
    await progress('thinking', { message: 'Planning step 1...' });
    await progress('thinking', { message: 'Step 1 complete' });
    await progress('thinking', { message: 'Planning step 2...' });
    await progress('thinking', { message: 'Planning step 999...' });
    await progress('thinking', { message: `Planning step 3... ${env.CUEPILOT_BRIDGE_TOKEN}` });
    await progress('answer', { message: 'Step 2 complete' });
    await progress('thinking', { message: 'Generating final answer...' });
    return { result_types: { output: 'answers' }, answers: env.CUEPILOT_OPENAI_API_KEY };
  } });
  const result = await runRocketRide({ env, runId, phase: 'execute' });
  assert.equal(result.ok, false);
  assert.equal(result.planningStepsObserved, 2);
  assert.equal(result.toolWavesObserved, 1);
  assert.equal(result.plannerFinalization, 'done');
  for (const secret of Object.values(env).filter(value => value.length === 64)) {
    assert.equal(JSON.stringify(result).includes(secret), false);
  }
});

for (const [name, mutate, blocker] of [
  ['foreign run receipt', run => { run.receipts[1].runId = 'someone-else'; }, 'CANONICAL_RECEIPTS_INVALID'],
  ['wrong scene', run => { run.receipts[1].scene = 'intro'; }, 'CANONICAL_RECEIPTS_INVALID'],
  ['duplicate receipt', run => { run.receipts[1].id = run.receipts[0].id; }, 'CANONICAL_RECEIPTS_INVALID'],
  ['non-monotonic revision', run => { run.receipts[1].stageRevision = run.receipts[0].stageRevision; }, 'CANONICAL_RECEIPTS_INVALID'],
  ['missing receipt', run => { run.receipts.pop(); }, 'CANONICAL_RECEIPTS_INVALID'],
  ['failed Rote export or outcome write-back after third cue', run => { run.verifiedCompletion = false; }, 'VERIFIED_COMPLETION_REQUIRED'],
  ['missing completion verification', run => { delete run.verifiedCompletion; }, 'VERIFIED_COMPLETION_REQUIRED'],
  ['changed approved plan', run => { run.plan.hash = 'b'.repeat(64); }, 'APPROVED_PLAN_CHANGED'],
]) {
  test(`rejects ${name} even when canonical status is completed`, async t => {
    const final = completed(); mutate(final);
    harness(t, { final });
    const result = await runRocketRide({ env, runId, phase: 'execute' });
    assert.equal(result.ok, false);
    assert.equal(result.freshExecution, false);
    assert.equal(result.reconciliationRequired, true);
    assert.ok(result.blockers.includes(blocker));
  });
}

test('refuses a partial approved run before spending credits', async t => {
  const initial = approved(); initial.receipts = completed().receipts.slice(0, 1); initial.nextStep = 1;
  const h = harness(t, { initial });
  const result = await runRocketRide({ env, runId, phase: 'execute' });
  assert.equal(result.ok, false);
  assert.ok(result.blockers.includes('FRESH_RUN_REQUIRED'));
  assert.deepEqual(h.events, ['health', 'read']);
});

test('rechecks run after validation and refuses another caller taking execution', async t => {
  const current = { ...approved(), status: 'running', nextStep: 1 };
  const h = harness(t, { runs: [approved(), current] });
  const result = await runRocketRide({ env, runId, phase: 'execute' });
  assert.equal(result.ok, false);
  assert.ok(result.blockers.includes('RUN_CHANGED_BEFORE_START'));
  assert.equal(h.starts.length, 0);
});

test('prepare success requires a real pending sponsor plan and zero receipts', async t => {
  const initial = { ...approved(), status: 'queued', plan: null };
  const final = { ...approved(), status: 'needs_approval' };
  const h = harness(t, { initial, final });
  const result = await runRocketRide({ env, runId, phase: 'prepare' });
  assert.equal(result.ok, true);
  assert.equal(result.freshExecution, false);
  assert.equal(result.status, 'needs_approval');
  assert.ok(h.sdkTimeout() > ROCKETRIDE_LIMITS.prepare.sendMs);
  assert.equal(result.maxElapsedMs, 515_000);
});

test('dedicated public bridge projection is sufficient and does not need private traces', async t => {
  const project = run => ({ id: run.id, executionMode: run.executionMode, status: run.status,
    plan: { hash: run.plan.hash }, receipts: run.receipts, verifiedCompletion: run.verifiedCompletion ?? false });
  harness(t, { initial: project(approved()), final: project(completed()) });
  const result = await runRocketRide({ env, runId, phase: 'execute' });
  assert.equal(result.ok, true);
  assert.equal(result.verifiedCompletion, true);
  assert.equal(result.freshExecution, true);
  assert.equal(result.maxElapsedMs, 395_000);
});

test('ambiguous startup terminates the requested token and reconciles canonical status', async t => {
  let requested;
  let terminated;
  harness(t, { use: async params => { requested = params.token; throw new Error('fake secret'); },
    terminate: token => { terminated = token; } });
  const result = await runRocketRide({ env, runId, phase: 'execute' });
  assert.equal(result.ok, false);
  assert.equal(result.taskStarted, false);
  assert.equal(result.taskStartAttempted, true);
  assert.equal(result.reconciliationRequired, true);
  assert.equal(result.status, 'completed');
  assert.equal(terminated, requested);
  assert.equal(JSON.stringify(result).includes('fake secret'), false);
});

test('unexpected task identity never sends input and cleans up both possible tasks', async t => {
  const terminated = [];
  const h = harness(t, { use: async () => ({ token: 'unexpected' }), terminate: token => terminated.push(token) });
  const result = await runRocketRide({ env, runId, phase: 'execute' });
  assert.ok(result.blockers.includes('PIPELINE_TASK_IDENTITY_CHANGED'));
  assert.equal(result.reconciliationRequired, true);
  assert.equal(h.events.includes('send'), false);
  assert.deepEqual(new Set(terminated), new Set([h.starts[0].token, 'unexpected']));
});

test('cancellation during send terminates and reconciles without claiming zero writes', async t => {
  const controller = new AbortController();
  const h = harness(t, { send: async () => { controller.abort(); return new Promise(() => {}); } });
  const result = await runRocketRide({ env, runId, phase: 'execute', signal: controller.signal });
  assert.equal(result.ok, false);
  assert.ok(result.blockers.includes('OPERATION_CANCELLED'));
  assert.equal(result.reconciliationRequired, true);
  assert.equal(result.receiptCount, 3);
  assert.deepEqual(h.events.slice(-3), ['terminate', 'disconnect', 'read']);
});

test('send timeout cleans up the live task and reports reconciliation even with complete receipts', async t => {
  t.mock.timers.enable({ apis: ['setTimeout'] });
  const sent = Promise.withResolvers();
  harness(t, { send: async () => { sent.resolve(); return new Promise(() => {}); } });
  const pending = runRocketRide({ env, runId, phase: 'execute' });
  await sent.promise;
  t.mock.timers.tick(ROCKETRIDE_LIMITS.execute.sendMs + 1);
  const result = await pending;
  assert.equal(result.ok, false);
  assert.ok(result.blockers.includes('PIPELINE_RESPONSE_UNCERTAIN'));
  assert.equal(result.taskTerminated, true);
  assert.equal(result.reconciliationRequired, true);
});

test('unconfirmed cleanup prevents success and requires reconciliation', async t => {
  harness(t, { terminate: () => { throw new Error('remote error'); } });
  const result = await runRocketRide({ env, runId, phase: 'execute' });
  assert.equal(result.ok, false);
  assert.ok(result.blockers.includes('TASK_CLEANUP_UNCONFIRMED_120_SECOND_IDLE_TTL'));
  assert.equal(result.reconciliationRequired, true);
});

test('bridge response limit also applies to chunked responses without Content-Length', async t => {
  const h = harness(t, { response: async () => new Response(' '.repeat(1_000_001)) });
  const result = await runRocketRide({ env, runId, phase: 'execute' });
  assert.ok(result.blockers.includes('BRIDGE_RESPONSE_TOO_LARGE'));
  assert.equal(h.starts.length, 0);
});

test('custom staging ports and paths are blocked before any network request', async t => {
  const h = harness(t);
  for (const uri of ['https://staging.rocketride.ai:8443', 'https://staging.rocketride.ai/other']) {
    const result = await runRocketRide({ env: { ...env, ROCKETRIDE_URI: uri }, runId, phase: 'execute' });
    assert.ok(result.blockers.includes('STAGING_URI_REQUIRED'));
  }
  assert.deepEqual(h.events, []);
});

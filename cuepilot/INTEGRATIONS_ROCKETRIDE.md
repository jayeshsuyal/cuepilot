# CuePilot / RocketRide staging integration

This is a real six-node `.pipe` and installed-SDK runner, not proof of an executed
end-to-end show. The live staging schema was read on 2026-09-11. Authentication
passed, this six-node pipeline validated with **0 errors and 0 warnings**, and the
account returned a positive compute-credit balance. Actual
execution still requires the configured model and public bridge described below.

## Interfaces

Run from the workspace root. The CLI reads the root `.env`, then `cuepilot/.env`;
already exported environment variables take precedence. It never prints keys.

```sh
node cuepilot/integrations/rocketride.mjs --offline
node cuepilot/integrations/rocketride.mjs --validate-only
node cuepilot/integrations/rocketride.mjs --run RUN_ID --phase prepare
node cuepilot/integrations/rocketride.mjs --run RUN_ID --phase execute
```

`--offline` checks the pipeline and reports configuration blockers without network
traffic. `--validate-only` authenticates, checks the current model-profile schema,
validates the pipeline and reads existing compute credits; it never starts a
pipeline. When configured, it also GETs bridge health. Exit 2 means incomplete or
blocked; successful complete validation/execution exits 0.

Backend imports do not load `.env` files or execute a task automatically:

```js
import { checkRocketRide, runRocketRide } from './integrations/rocketride.mjs';
const check = await checkRocketRide({ env: process.env });
const result = await runRocketRide({ runId, phase: 'prepare', env: process.env });
```

The Python backend can invoke the CLI as a subprocess, parse its single JSON
result, and enforce an outer timeout greater than the runner's bounded operation
window. Do not turn a failed JSON report into success or fall back to practice
mode silently. A result includes `ok`, `blockers`, `taskStarted`, `phase`,
`elapsedMs`, and canonical status when available. It contains no raw model answer,
task token or provider trace. Its `elapsedMs` includes SDK startup and cleanup;
it is not a pure execution-duration benchmark.

## Required configuration

| Variable | Meaning |
| --- | --- |
| `ROCKETRIDE_URI` | Existing authenticated `https://staging.rocketride.ai` target |
| `ROCKETRIDE_APIKEY` | Existing staging account key |
| `CUEPILOT_PUBLIC_BASE_URL` | HTTPS origin of the operator-configured bridge, with no path/query/userinfo |
| `CUEPILOT_BRIDGE_TOKEN` | Dedicated bridge bearer token, at least 24 characters |
| `CUEPILOT_OPENAI_API_KEY` | Explicit key for the selected native OpenAI provider |
| `ROCKETRIDE_OPENAI_KEY` | Supported existing alternative to the previous key |
| `CUEPILOT_ROCKETRIDE_MODEL_PROFILE` | Optional; defaults to verified preset `openai-4o-mini` |

The runner supports only preset profiles listed by both the saved and current
staging schema. It does not infer a model key from Cognee's configuration or assume
the staging compute wallet pays for the model provider. A present key plus schema
validation proves configuration, not provider authentication; an actual model
call is required to prove that last step.

Only four explicitly constructed `ROCKETRIDE_CUEPILOT_*` substitutions are sent
on `use()`: bridge URL/token, model key, and phase. The SDK constructor receives an
empty environment, so unrelated workspace secrets are not forwarded. The checked-in
pipeline contains placeholders and an intentionally unreachable `.invalid` URL.
Do not execute that template directly from Designer without equivalent runtime
bindings and guardrails. No tunnel is created by this integration.

## Pipeline and phases

`webhook → agent_rocketride → response_answers`, with controlled `llm_openai`,
exactly one `memory_internal` scratchpad, and `tool_http_request`. HydraDB remains
durable memory behind the bridge; the internal scratchpad does not replace it.

The webhook is fed a serialized SDK `Question` with MIME
`application/rocketride-question`, which selects the questions lane. Plain JSON
or `text/plain` would select the wrong lane for this agent.

RocketRide calls separate bounded operations. It never invokes a catch-all
`run_everything` route. Every POST body is `{ "runId": "..." }`.

| Phase | Allowed tool order | Required starting state | Required ending state |
| --- | --- | --- | --- |
| prepare | ingest-memory → recall-recipe → validate-show → plan | queued, live mode | needs_approval |
| execute | validate-show → execute → verify | approved, live mode, plan hash present | completed |

An already-prepared or already-completed run returns its canonical status without
another task. `running`, `blocked`, and `failed` runs are not blindly retried.
The operator's approval happens through the normal backend API between phases.
The caller must preserve `approved` status until `/tools/execute` claims execution;
do not preemptively mark the run `running` before starting this runner.

The provider's HTTP guard permits POST only, one concurrent request, and an
anchored regex for the phase's exact `/api/v1/tools/` endpoints. The full staging
schema requires `urlWhitelist: [{ "whitelistPattern": "..." }]`; the prose
integration guide's array-of-strings example differs and must not be copied.
Prepare has no execute/verify URL in its whitelist. Approval endpoints are never
whitelisted. The runner compiles/tests its regex before submission because the
provider skips malformed patterns.

## Required backend guards

Prompts are not an authorization or stage-order boundary. The backend must:

- Authenticate every tool POST using `CUEPILOT_BRIDGE_TOKEN`.
- Enforce the current run's stage and prerequisites for every operation; tool
  endpoints must not complete missing upstream work themselves.
- Bind approval to the exact plan hash and current show revision; recheck these
  immediately before stage changes. Execute must independently require approval.
- Invoke the actual approved Rote procedure and record stage receipts. Do not
  substitute native scene writes and still label the result Rote replay.
- Preserve idempotency of scene actions and return authoritative run status.
- Stop on missing assets, unready speakers, provider failure or revision changes.

Read-only preflight uses GET `/api/v1/health` and GET `/api/v1/runs/{runId}` at the
configured public origin. Responses accept the bearer header. The run response
must follow `contracts/api.ts`, including `id`, `executionMode`, `status`, `plan`
and `receipts`. The runner rereads this canonical status after the pipeline; it
does not trust the agent's generated completion message.

## Failure and evidence

No credit purchase, account update, deployment or public-tunnel creation occurs.
No task is started without positive **token compute credits**, configuration,
current schema validation and bridge preflight. Local health success does not
prove staging can reach the bridge: only the actual staging tool call proves it.

Tasks use 12 maximum planning waves, 1 execution thread, a 180-second send bound
and a 120-second idle TTL. Cleanup explicitly terminates even an ambiguously
started task and disconnects the SDK. An idle TTL is not an active-runtime limit;
`TASK_CLEANUP_UNCONFIRMED` requires inspecting staging before another attempt.

Any failure after task startup is conservatively marked `reconciliationRequired`.
Inspect canonical receipts before retrying: a lost response does not prove zero
stage changes. Returned `answerReceived` only describes an answers lane being
present; it is not successful-action evidence. Actual success requires the
expected canonical backend status.

Trace capture is `metadata`, avoiding full request/header payloads. Raw remote
answers and traces are deliberately excluded from CLI output because a provider
could echo a credential. Preserve safe backend operation evidence for the demo;
do not claim total model/token usage from incomplete sponsor telemetry.

Saved full schema snapshots live in `pipelines/schema/`. Sources: installed
`.rocketride/docs/ROCKETRIDE_PIPELINES.md`, `ROCKETRIDE_INTEGRATIONS.md`,
`ROCKETRIDE_typescript_API.md`, installed SDK, and authenticated staging
`getService()` responses. Snyk scanning belongs in the application's build gate.

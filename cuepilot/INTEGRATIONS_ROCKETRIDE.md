# CuePilot / RocketRide staging integration

This is a six-node `.pipe` and installed-SDK runner, not proof of an executed
end-to-end show. The live staging schema was read on 2026-09-11. Authentication
passed and the earlier pipeline snapshot validated with **0 errors and 0 warnings**;
the account returned a positive compute-credit balance then. The current hardened
pipeline still needs fresh staging validation and live execution with the
configured model and public bridge. `validation-2026-09-11.json` is historical
evidence, not validation of later source changes.

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
// Optional AbortSignal cancels waiting, then attempts task cleanup and reconciliation.
const cancellable = await runRocketRide({ runId, phase: 'execute', env: process.env, signal });
```

The Python backend can invoke the CLI as a subprocess, parse its single JSON
result, and enforce an outer timeout greater than the runner's bounded operation
window (use **540 seconds** for both phases). Do not turn a failed JSON report into
success or fall back to practice mode silently. A result includes `ok`, `runId`,
`blockers`, `taskStartAttempted`, `taskStarted`, `phase`, `elapsedMs`,
`operationDeadlineMs`, `maxElapsedMs`, and canonical status when available.
Verified execution also includes `verifiedCompletion`, `canonicalVerified`,
`receiptCount`, `receiptIds`, `planHash`, and `freshExecution`.
It contains no raw model answer,
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
The HTTP timeout placeholder is bound locally to the selected phase before
validation; it is not a fifth environment variable sent to the server.
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

An already-prepared or already-completed run returns validated canonical evidence
without another task, with `alreadySatisfied: true` and `freshExecution: false`.
This does not count as a new replay. Completed runs require the same receipt and
completion-marker checks as newly executed runs. `running`, `blocked`, and
`failed` runs are not blindly retried. Execution requires an approved hash and
zero existing receipts; a supplied `nextStep` must be zero. The runner rechecks
these conditions after staging validation, immediately before task startup.
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
- Atomically claim/deduplicate each operation; concurrent dispatch must not start
  two stage executions. The runner's preflight cannot implement an atomic API claim.
- Make `/tools/verify` persist successful outcome evidence only after actual Rote
  execution and matching receipts. Compute `verifiedCompletion: true` only from
  completed receipts, verified Rote execution, and verified outcome write-back.

Read-only preflight uses GET `/api/v1/health` and GET `/api/v1/runs/{runId}` at the
configured public origin. Responses accept the bearer header. The dedicated
bridge can return its narrow projection: `id`, `executionMode`, `status`,
`plan: {hash} | null`, `receipts`, and `verifiedCompletion`. Full plans, notes,
provenance and traces remain local. The runner rereads this canonical status
after task cleanup, including after errors. It never trusts the generated answer.

Execution success requires the unchanged approved plan hash, exactly three
successful receipts for this run with unique IDs, ordered intro/presentation/
holding scenes, increasing stage revisions, valid commit timestamps, and
`verifiedCompletion === true`. Three scene writes can reach `completed` before
Rote export or memory write-back fails; the marker prevents false success then.
Bridge responses are limited to 1 MB while streaming, even without Content-Length.

## Failure and evidence

No credit purchase, account update, deployment or public-tunnel creation occurs.
No task is started without positive **token compute credits**, configuration,
current schema validation and bridge preflight. Local health success does not
prove staging can reach the bridge: only the actual staging tool call proves it.

Tasks use 12 maximum planning waves, 1 execution thread and a 120-second idle TTL.
The actual installed SDK accepts custom task tokens and does not expose an
AbortSignal on `send()`; its `DataPipe.close()` uses the client request timeout.
The SDK timeout must exceed the whole send window, not just one HTTP operation.

| Bound | prepare | execute |
| --- | ---: | ---: |
| HTTP tool request, seconds | 180 | 120 |
| Entire `send()`, seconds | 360 | 240 |
| SDK request timeout, seconds | 365 | 245 |
| Aggregate work deadline, seconds | 480 | 360 |
| Maximum including cleanup/final read, seconds | 515 | 395 |

Individual schema/validation/credit calls retain a 20-second bound. Prepare
allows Cognee ingestion up to 150 seconds, followed by recall, Hotdata and plan.
Execute allows Hotdata up to 60 seconds, Rote's 105-second aggregate operation
plus at most 5 seconds process cleanup, and outcome write-back. The official
installed HTTP tool documentation caps a request at 300 seconds; these values
remain below it. The public proxy must permit the corresponding upstream work
and the parent CLI must use the 540-second outer deadline.

SIGINT/SIGTERM or the optional AbortSignal stops local waiting, attempts remote
termination (15 seconds), disconnects (8 seconds), and rereads canonical status
(12 seconds). The parent should send SIGTERM and drain output for **40 seconds**
before using SIGKILL, including on parent cancellation. Immediate kill prevents
cleanup. Cleanup attempts both possible tokens on an unexpected returned task
identity and never sends work to that task. A lost startup response still triggers
termination with the requested token.

An idle TTL is not an active-runtime limit. A termination acknowledgement does
not prove cancellation of an already accepted bridge/Rote operation, and late
startup or network responses can remain ambiguous. `TASK_CLEANUP_UNCONFIRMED`
requires inspecting staging before another attempt.

Any failure after a task start attempt is marked `reconciliationRequired`,
including incomplete canonical results or cleanup failure.
Inspect canonical receipts before retrying: a lost response does not prove zero
stage changes. Returned `answerReceived` only describes an answers lane being
present; it is not successful-action evidence. Actual success requires the
expected canonical backend state and verified completion evidence.

Trace capture is `metadata`, avoiding full request/header payloads. Raw remote
answers and traces are deliberately excluded from CLI output because a provider
could echo a credential. Preserve safe backend operation evidence for the demo;
do not claim total model/token usage from incomplete sponsor telemetry.

Focused offline checks: `node --test cuepilot/tests/test_rocketride.mjs` exercises
the installed SDK with mocked methods and mocked fetch; it performs no account
authentication, model calls or stage mutations. These checks cover phase URL and
timeout bindings, secret isolation, fresh-run enforcement, receipt integrity,
the redacted public projection, cancellation, timeouts, ambiguous startup and
cleanup failure. Run `--validate-only` against the current pipeline before a live
attempt; offline tests are not sponsor execution evidence.

Required API integration: parse the single JSON report and require exit code 0,
`ok: true`, and independent current canonical checks before recording a verified
RocketRide trace. Keep the safe metadata above (especially `freshExecution`,
`taskStartAttempted` and `reconciliationRequired`) so retries and lost responses
are not reported as fresh work. Preserve the API's operation claims, approval
hash, current rule/readiness guards and verified outcome marker.

Saved full schema snapshots live in `pipelines/schema/`. Sources: installed
`.rocketride/docs/ROCKETRIDE_PIPELINES.md`, `ROCKETRIDE_INTEGRATIONS.md`,
`ROCKETRIDE_typescript_API.md`, installed SDK, and authenticated staging
`getService()` responses. Snyk scanning belongs in the application's build gate.

# CuePilot × Rote

Rote records and replays the approved **intro → presentation → holding** sequence.
RocketRide orchestrates the surrounding sponsor operations. The local stage API
remains the authority for approval, current rules/readiness, ownership and receipts.

## Execution evidence and current limits

Fresh proof-v2 acceptance **passed at 20:48 UTC on September 11, 2026** using
actual Rote and the local API:

- Maya `79aa2681-31f1-4c0b-91bd-ae3c40209424` learned package
  `cuepilot-20260911T204750-3f147f46` with three matched captured/API receipts.
- Ravi `9015ddcb-a8f4-4a78-bd8c-f716d23a5856` replayed that exact package and proof
  on a new input/speaker, producing three new matched receipts.
- Alex `c11f37ca-cc21-40f4-879d-b08fc98fa327` was interrupted 0.053 seconds after
  introduction by a missing presentation. Replay failed as expected, the stage
  held, exactly one receipt remained, and restoring the asset did not resume it.

These runs used fixture plans and real Rote; they do **not** establish live
Cognee/Hotdata/RocketRide execution. The private report is
`cuepilot/.runtime/acceptance/20260911T204801.614383Z_adb1828fc4ef43f0a2a5dc4e7fca52ea.json`.
The active practice proof cannot authorize a live memory recipe. Preserve/archive
its active pointer before a fresh live acceptance; retain the immutable package.

### Historical rehearsal

Rote 0.82.0 genuinely captured Maya practice run
`55e57559-8896-4ce4-9bfa-4f92a17fea71` at references `@1`, `@2`, `@3`, and exported
`plays/learned/cuepilot-20260911T193228-ff66cf07/`. That package and its evidence
remain immutable historical local state. Its active pointer was archived before
the fresh acceptance above. The authored 1.5-second display dwell is identical during
record/replay and is not an AI performance improvement.

The hardened adapter uses proof version 2. The historical Maya proof has no rule
identity or canonical receipt hashes and contains an older transport. It returns
`learned_package_upgrade_required`; it cannot establish the new verification
claims. Do not silently rewrite its proof, label it newly verified, or fall back
to practice. The coordinator must preserve/archive its existing active pointer
and then record a fresh approved rehearsal to produce a new package. `learn()`
will not overwrite an existing active pointer. Live learning needs a current
live plan and matching memory proof; an old practice package does not authorize
an unrelated live recipe.

Focused synthetic adapter/transport tests and local Python subprocess tests are
included. They prove validation and cleanup behavior, not sponsor success.
The installed Rote SDK's `ProcessExecResponseBody`, presentation outcomes and
actual Maya exporter shape were inspected. A separate synthetic `play validate`
attempt with an isolated unauthenticated runtime exited 77 (`rote requires login`);
that isolated check did not validate the changed renderer. The subsequent
authenticated acceptance above verified export/validation and fresh-input replay.
No credentials were copied into the isolated runtime.

Generated packages, transcripts and active pointers remain ignored local drafts.
Nothing is published to the Rote registry. A fresh clone must learn its own play.

## Adapter contract

`integrations/rote.py` preserves `inspect()` / `readiness()`,
`await learn(run_id, base_url)`, and `await replay(run_id, base_url)`.
Results retain `provider`, `status`, `operation`, `evidence`, and `reason`.
Verified inspection/learning/replay adds `evidence.procedure: {id, sha256}`:
`id` is the immutable package name; `sha256` hashes its proof file. The same
identity is also provided as top-level `procedure` for direct adapter consumers.
Inspection says `replayExecuted: false`; it only verifies local prerequisites.

Verified execution includes full canonical `evidence.receipts`, separately matched
`capturedReceipts`, `identity`, and `commandEvidence`. Replay also provides the
Rote run ID, `learnedFromRunId`, `newInput`, `newSpeaker`, and `replayExecuted`.
Different run IDs alone do not prove a different speaker. Failures after a cue
attempt include `executionAttempted: true` and `reconciliationRequired: true`,
even if the API has already reached `completed`.

## Identity and execution gates

Both learning and replay require `status: approved`, an empty receipt list, and
integer `nextStep: 0`. A busy adapter fails immediately with `rote_execution_busy`.
Replay additionally refuses the learning run ID. No resume/force-resume flags are
used; completed or restored steps cannot count as a fresh execution.

The reusable identity binds recipe ID/version, the exact three supported cues,
and SHA-256 of the rule notes. Speaker, show revision and approval hash are
per-run values and can change between compatible executions. Live identity also
binds `run.memoryProof` (`recipe_id`, `source_id`, `note_sha256`, `graph_sha256`),
with the note hash and actual plan recipe ID matching. Practice uses the known
`speaker-segment-v1` fixture. A changed note, graph, source, recipe version or cue
order blocks reuse. The full approved plan must remain unchanged during execution.

Every package check verifies the active proof hash, exact file inventory, all file
hashes, canonical learning receipts, current authored transport/dependencies,
and deterministic rederivation of `main.ts` from the pinned raw export. Symlinks
and unlisted files are rejected. This is local integrity checking against the
trusted active pointer, not a remote signature or protection against a user who
controls and rewrites all local evidence.

Learning invokes three real `rote proc run` captures. It queries each typed
process response and requires spawned=true, no timeout, and integer exit code 0.
A recorder exit of zero alone is insufficient. Captured receipts must match the
API's final receipts by run, step, deterministic request ID, receipt ID, scene,
revision, commit time and canonical JSON digest. The raw wire digest remains
available separately. Only then is the successful trace exported and validated.

Parameter generalization accepts exactly `run_id` and `base_url` as required
string parameters with no defaults, the three expected captured argv lists, and
no unexpected execution fields. It preserves Rote's step names, packages the
one-cue helper, and makes the recorded ordering explicit. An appended authored
presentation renderer exposes typed step outcomes and process status/stdout;
it does not execute cues. These transformations are recorded in the proof.

Replay invokes **one `rote play run`**. Rote's DAG owns deterministic cue ordering.
The renderer must report three newly completed process steps with successful
child exits. Each captured receipt must match the API's three ordered canonical
receipts, with unique IDs and consecutive stage revisions. A success summary or
the API's completed status alone is insufficient. Package identity is rechecked
after execution.

## Timeouts and cancellation

An aggregate 105-second deadline covers preflight, captures or replay, export,
validation and receipt checks. Each ordinary CLI call has a 60-second limit;
`play run` has a 90-second limit inside that aggregate deadline. Local status GETs
use an 8-second socket timeout; the cue transport uses 12 seconds. No retries or
unbounded execution queues are introduced.

Each CLI starts a new POSIX process group with stdin disabled. Timeout or
cancellation kills the whole group and drains/reaps the CLI, with a 5-second
communication cleanup bound. Process creation is shielded so cancellation during
pipe setup cannot discard the group leader's handle; creation must finish before
cleanup can recover that handle. Thus a pathological OS process-creation stall
can delay cancellation beyond the normal 110-second operation/cleanup window.
Cancellation is re-raised after cleanup and the API must record that interrupted
attempt before allowing another execution. A killed process cannot revoke an
HTTP cue already committed by the stage API: always reconcile canonical receipts.

RocketRide's execute HTTP window is 120 seconds. Its separate prepare and execute
phase deadlines are documented in `INTEGRATIONS_ROCKETRIDE.md`; the parent CLI
needs a 540-second outer deadline and 40 seconds of SIGTERM cleanup grace.

## Coordinator API integration

- Preserve approved status until the execution operation is claimed. Use atomic
  per-run operation claims; the adapter's process-local lock is not a database
  claim and cannot protect multiple API workers.
- Supply the full local run, including current `memoryProof`, from GET
  `/api/v1/runs/{id}` on loopback port 8787. The public orchestration bridge can
  keep its separate redacted projection on port 8788.
- Bind current approval/rule/readiness and operation ownership inside every stage
  cue transaction. Checks here do not replace those transaction guards.
- Persist the full safe adapter result. Treat failed/blocked results and
  cancellation as incomplete even when physical stage receipts are complete.
  Reconcile interrupted/ambiguous attempts instead of starting a blind replay.
- Before successful outcome write-back, require verified Rote execution, matching
  canonical receipts and the exact `evidence.procedure`. Root's public
  `verifiedCompletion` marker must require both verified Rote execution and
  verified outcome write-back; RocketRide checks that marker.

Use the existing authenticated `sponsor-setup/rote/rote` wrapper. Recording creates
uniquely named workspaces and leaves unrelated workspaces intact. Nested macOS
sandboxing can block Rote's own `sandbox-exec`; the coordinator runs real Rote
checks outside that nested sandbox. Relative export/play paths avoid the
installed exporter's expansion of `~` inside the repository name `Hackathon~`.
Only loopback HTTP(S) origins are accepted by the cue helper; bearer credentials
remain in the private environment, and proxies/redirects are disabled.

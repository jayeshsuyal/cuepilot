# Repeatable API acceptance

Start the API separately on `127.0.0.1:8787`, with the synthetic Maya/Ravi/Alex
show ready and stage holding. Resolve any unfinished runs before starting. The
runner uses only the API; it never wipes databases, edits existing notes, clears
Rote packages, or substitutes fixtures for live failures. Before `rote` or `live`,
an operator must explicitly archive any existing `cuepilot/plays/evidence/active.json`
pointer while retaining its package and evidence. A read-only existence check
blocks those modes before API requests when that pointer is still present:
`fresh_maya_learning_requires_explicit_active_pointer_archive`. The runner never
archives or removes it automatically. Practice mode does not require this step.

From the workspace root:

```sh
sponsor-setup/memory/.venv/bin/python -m cuepilot.acceptance practice
sponsor-setup/memory/.venv/bin/python -m cuepilot.acceptance rote
sponsor-setup/memory/.venv/bin/python -m cuepilot.acceptance live --timeout 600
```

Each command performs stage actions. **Live consumes configured sponsor
services/credits.** Implementation verification did not execute these commands.
Tokens come privately from the process environment, root `.env`, and
`cuepilot/.env`, matching API precedence. Both operator and bridge tokens are
required. No model keys are sent in acceptance requests. Requests target a fixed
loopback address, reject redirects, and stream bounded JSON responses.

Every mode approves only the returned hash of the exact intro → presentation →
holding plan for a fixed synthetic speaker. Maya and Ravi must each complete
three canonical receipts and pass `/tools/verify`. Alex is interrupted after
intro: its presentation is marked missing, its run must block with exactly one
receipt, and the stage must hold. The runner restores `slides-alex` to ready in
cleanup. A restoration failure is recorded as failed and needs operator review.

- `practice`: explicit fixture plans and manual `/advance` calls. Alex's next cue
  must return HTTP 409. No sponsor success is claimed.
- `rote`: fixture plans executed through `/tools/execute`; no manual cue calls.
  Maya must perform a fresh verified `learn` sourced from this Maya run. Ravi
  must perform `replay` sourced from that Maya run, with new input/new speaker
  flags and the same package path, procedure ID, proof hash, and recipe identity.
- `live`: sponsor plans prepared and executed through RocketRide. Successful runs
  require actual sponsor traces, verified RocketRide execute evidence, canonical
  receipts, and `/tools/verify`, plus the same Maya-learns/Ravi-replays checks.
  No fixture fallback is permitted.

Both automated modes cross-check the latest Rote trace with the canonical
execute-operation result. Returned and captured receipts must correspond to the
current run's three canonical receipts, including IDs, step order, revisions,
timestamps, deterministic request IDs and independently computed canonical
SHA-256 digests. Two successful replays of an unrelated old package cannot pass.
The report stores the verified learning/replay link in `roteLearningAcceptance`.

Automated Alex cases poll every 50 ms and require the missing-asset PATCH to
complete less than 1.5 seconds after the intro receipt timestamp. They require
exactly one canonical receipt and no observed/receipted presentation. A missed
window fails the test. The expected interrupted run remains blocked; passing
that safety test does not mean the segment completed successfully.

Per-run deadlines default to 600 seconds, configurable from 30 through 899.
Timeouts stop the acceptance client but may leave server-side work running;
inspect canonical runs before retrying. The runner refuses to start while an
unfinished run is visible. It does not reset or cancel unrelated work.

Timestamped JSON reports are saved privately under ignored
`cuepilot/.runtime/acceptance/`, including actual plans, canonical runs, traces,
stage observations, verification responses, and cleanup outcome. Known secret
values and credential fields are redacted. Exit codes: `0` passed, `1` failed,
`2` blocked. Missing credentials/runtime/provider setup are blocked, never
reported as completed. These API observations do not constitute continuous
video capture or a provider security assessment.

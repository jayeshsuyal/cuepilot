# CuePilot × Rote

Rote records and replays the approved stage sequence: **intro → presentation → holding**. This is a local integration. RocketRide orchestration and other sponsor integrations have separate verification states.

## Verified on this machine

- Rote 0.82.0 captured three successful process operations for Maya practice run `55e57559-8896-4ce4-9bfa-4f92a17fea71`, including matching API receipts at Rote references `@1`, `@2`, and `@3`.
- `rote workspace export` exported that successful trace. The package was parameterized for `run_id` and `base_url`, given explicit cue dependencies, and validated with `rote play validate`.
- Local learned package: `plays/learned/cuepilot-20260911T193228-ff66cf07/`. Source trace, receipt hashes, and transformation notes are in its `resources/` directory. Package files are read-only; `plays/evidence/active.json` identifies their hashes.
- **A new-speaker replay is not yet verified.** The Ravi test was interrupted before any replay evidence was saved. Do not describe the product as proven to replay successfully on new input yet.
- No automated Rote adapter test suite has been completed. Earlier helper capability probes do not constitute stage replay evidence.

Generated packages, CLI transcripts, and the active pointer are ignored local state. A fresh clone must learn its own play. Nothing was released or published to the Rote registry; the exported package is a local draft.

## Adapter contract

`integrations/rote.py` exposes `inspect()` / `readiness()`, `await learn(run_id, base_url)`, and `await replay(run_id, base_url)`. Results contain `provider`, `status`, `operation`, `evidence`, and `reason`. `replay` returns `blocked` with reason `learned_play_required` before the first learned package exists.

Learning invokes real `rote proc run` three times, checks the captured child exit codes and receipts, then exports. Rote's recorder itself can exit zero after capturing a failed child, so the adapter checks the recorded child status separately. Replay invokes **one `rote play run`**; its DAG owns cue ordering. Python does not reimplement the replay loop.

The authored `plays/stage-sequence/resources/cue.py` helper performs one authenticated HTTP cue and validates its receipt. It has a fixed 1.5-second display dwell after intro and presentation. This authored duration is not an AI performance improvement. The learned procedure comes from the captured successful trace; packaging and parameter generalization are explicit authored transformations.

Only loopback HTTP(S) origins are accepted. The bridge token comes privately from the process environment or ignored `cuepilot/.env`; it is never a CLI argument. The helper disables proxies and redirects. The API remains responsible for approval, live asset readiness, step order, and idempotency on every cue.

## Operational notes / remaining verification

Use the existing `sponsor-setup/rote/rote` wrapper and authenticated runtime. Recording creates uniquely named `cuepilot-*` workspaces and leaves other workspaces intact. A Codex nested macOS sandbox blocks Rote's own `sandbox-exec`; the local API must run outside that nested sandbox for real Rote execution.

The installed exporter expands `~` even inside the repository directory name `Hackathon~`. The adapter therefore uses relative export/play paths and packaged `@resource{cue.py}` references.

Next verification: approve a fresh Ravi practice run, call `replay`, and require both Rote's three completed steps and the API's three matching ordered receipts. Then verify a fresh approved run whose asset becomes missing fails before a stage cue. These checks remain outstanding. Never count an old completed run or authored helper probe as a successful new-input replay.

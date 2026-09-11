# Copy this into your teammate's Codex

We are building **CuePilot**, a live-show director that learns a rehearsed cue
sequence and safely reuses it for another speaker. You own the frontend only in
`cuepilot/web/`. Jayesh's Codex owns the API, sponsor adapters, contracts and tests.
Do not modify files outside your frontend folder without coordinating.

Build with **pnpm, React, TypeScript and Vite**. Start from
`cuepilot/contracts/api.ts`, which defines the frozen v1 API. Do not invent routes
or rename fields. Keep frontend tooling in your own `cuepilot/web/package.json`.

Build two browser routes:

1. `/`: operator desk. Speaker selector, production notes, stage preview,
   plan preview, approve, next cue in local practice, execute in live mode,
   execution trace, and a presentation-readiness toggle for the judge.
2. `/stage`: clean full-screen program output. Holding card, speaker introduction,
   and presentation scenes. It reads the real API stage state; no invented playback
   progress, sponsor success, speed-up figures or client-only scene transitions.

Design direction: a compact broadcast control desk with a dark background,
large readable program preview, warm amber cue accents and a clear LIVE/HOLDING
indicator. Use typography, layout and a small number of controls. No stock imagery
is needed. Give blocked conditions a readable explanation rather than only color.
Make the projected `/stage` view readable from the back of a room.

API base is `/api/v1`. For development, Vite proxies `/api` to
`http://127.0.0.1:8787`. Bind Vite to loopback. Inject the server-only
`CUEPILOT_OPERATOR_TOKEN` as a Bearer header from Vite's server environment;
never use a `VITE_` variable for this secret, bundle it in browser code, or commit
it. Credentials will be shared privately on the final demo machine.

Read-only GETs need no token. Poll stage, show and current run every 500ms.
Mutations must display API errors and re-fetch state. Asset/speaker changes carry
the current show revision. Approval carries the exact returned plan hash.
Generate a fresh requestId for each intended cue, but retain it when retrying the
same cue after a network error. The backend is authoritative about step order.

**Practice mode** is deliberately labelled local UI rehearsal. It supplies a
fixture plan and allows an operator to advance real local stage cues. It does not
claim Cognee, Hotdata, RocketRide or Rote executed. **Live mode** requires verified
sponsor work and can report blocked setup. Render the returned evidence honestly.

While the backend is being built, a typed fixture adapter is acceptable behind
an explicit `Fixture data` label. Keep it replaceable through one API client.
Do not let fixture data silently substitute for failed live requests.

First deliverable: three scenes render and switch from the shared state shape.
Second: create practice run -> preview -> approve -> advance three cues.
Third: mark the presentation missing before its cue, show the backend switching
to holding with the reason, then recover through a new approved plan.

Definition of done: pnpm build succeeds; keyboard-accessible controls; no secrets
in client bundle; no fake sponsor badges; live and fixture states distinguishable;
successful and rejected actions come from server responses. Run the applicable
Snyk source/dependency checks and report unresolved findings.

Backend additions preserve the original fields/routes. Add an operator Cancel
button using `POST /api/v1/runs/:id/cancel {}`; it holds an active stage, retains
committed receipts and waits for driver cleanup. Disable repeat submission while
the request is pending. A live run with omitted notes inherits the current
production notes. Explicitly changed notes become the current production rule
and invalidate old approvals, including runs prepared before that change.

`GET /api/v1/runs/:id` adds `verifiedCompletion`. `completed` means the three
physical cues happened; `verifiedCompletion: true` also requires verified Rote
execution and Hydra outcome write-back. Render the final RocketRide execution
trace separately. Do not label a live run fully verified from `status` alone.

Do not deploy, connect real streaming accounts, add OBS, microphones, scheduling,
multitenancy or a second agent. One segment type, three speakers, one stage.

## Integration checkpoints

- First 30 minutes: types, fixture scenes and API startup.
- Hour 2: operator desk talks to the local backend.
- Hour 3: one complete sponsor-backed rehearsal, subject to account gates.
- Hour 4: real Rote replay for a new speaker.
- Hour 5: missing-asset interruption and duplicate-request checks.
- Hour 6: security fixes, visual polish and three-minute rehearsal.

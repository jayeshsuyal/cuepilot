# CuePilot frontend

The operator desk (`/`) and full-screen program output (`/stage`) use the frozen
[`../contracts/api.ts`](../contracts/api.ts) types directly. All frontend tooling
and local verification live in this folder. No backend, adapter, contract, or
backend test files were changed.

## Run against the demo backend

From this folder:

```sh
pnpm install --frozen-lockfile
# Privately copy the backend's CUEPILOT_OPERATOR_TOKEN into .env.local.
# The variable must not have a VITE_ prefix.
pnpm dev
```

Run Jayesh's backend separately at `http://127.0.0.1:8787`. Open
`http://127.0.0.1:5173/` for the desk and `/stage` for projected output. Both
servers bind to loopback. Restart Vite after changing its server environment.
`pnpm preview` previews static files only; use `pnpm dev` for the authenticated
API proxy on the demo machine. No deployment has been configured or performed.

Vite reads `CUEPILOT_OPERATOR_TOKEN` from its process environment or ignored
`.env.local`, strips browser-supplied Authorization, and adds the Bearer token
only to upstream mutations. It rejects browser writes from another origin.
The client never receives or stores the credential. Read-only GETs need no token.

## Direct the show

1. Choose a speaker, edit production notes, and choose **Practice**. The speaker
   selection and notes are drafts for the next run; they do not change the stage.
2. **Create practice run**, inspect the returned three cues and plan details,
   then **Approve plan**. Approval sends the exact returned plan hash.
3. **Next cue** advances introduction → presentation → holding. The API decides
   the next step and returns a committed receipt. There are no automatic scene
   transitions or invented playback progress.
4. For the interruption demonstration, create and approve another practice run,
   advance the introduction, then turn off **Presentation**. The backend puts
   the stage on holding and supplies the reason. Restore presentation readiness,
   create a recovery plan, approve it, and advance its three cues.
5. **Live** creates a sponsor-backed preparation request. **Execute live** is
   available only for a returned approved sponsor plan. The local setup currently
   returns a RocketRide setup block; no completed sponsor rehearsal or Rote replay
   is claimed. Inspect the exact returned provider evidence in the trace.

A cue request ID is persisted in session storage before sending. A transport
failure keeps that ID, including across reloads and a changed current-run view.
**Retry same cue** reconciles the same intent; other writes remain disabled until
it resolves. Known API rejections clear the intent, display the backend error,
and refresh state. Every mutation is followed by a read. Show, stage, and the
tracked run are polled every 500 ms with overlapping polls suppressed.

On first load, the latest item returned by `GET /runs` is selected. Thereafter
the desk polls `GET /runs/:id`; creating a run selects its returned ID. The frozen
contract has no `runs/current` endpoint. Readiness controls send `expectedRevision`
for the current show. The stage uses the exact `Stage.title`, `subtitle`, `scene`,
`reason`, and `revision`; it has no fabricated slide URLs or progress fields.

## Explicit fixtures

Choose **Fixture data** in the data-source selector, or open `/?source=fixture`.
Its stage link is `/stage?source=fixture`. Both tabs share an isolated local
fixture store using the same v1 contract shapes. It is always labelled
**Fixture data · Local UI rehearsal** and cannot execute live sponsor work.
It never substitutes for a failed API request. The default `/stage` always uses
the API. A disconnected API stage retains the last state with an explicit stale
warning, and disables operator writes.

## Verification

```sh
pnpm build
pnpm verify:transport
pnpm format:check
pnpm security:node
pnpm security:code
pnpm security:scan
```

`verify:transport` checks exact request fields, errors, authentication boundaries,
and cue intent retention without calling sponsors. Security commands use an
installed Snyk CLI; `CUEPILOT_SNYK_CLI` can point to a privately installed binary.
Source scans upload eligible frontend source to Snyk and need that authorization
plus Snyk sign-in. Scan artifacts are ignored under `verification/security-reports/`
to respect frontend-only ownership. A scanner failure is an incomplete check.
See [VERIFICATION.md](VERIFICATION.md) for actual results and remaining gates.

For repeatable local API checks without changing backend files or data:

```sh
python3.12 -m venv .local-api-venv
.local-api-venv/bin/python -m pip install -r ../requirements.txt
.local-api-venv/bin/python -B verification/run-api.py
# In another terminal, run pnpm dev, then:
node verification/api-smoke.mjs
```

The helper uses the unmodified backend's `create_app` with a database in ignored
`.local-runtime/`, generates an ignored test credential only if `.env.local` is
absent, and disables Python bytecode writes outside this folder. The API smoke
check creates synthetic practice runs on that isolated backend; it verifies
real approvals, rejections, duplicate requests, three receipts, proxy origin
protection, and absence of the local token in the production bundle.

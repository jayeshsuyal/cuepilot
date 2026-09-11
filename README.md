# CuePilot

A live-show director that learns from rehearsal and safely reuses a stage-cue
sequence for another speaker: **introduction -> presentation -> holding**.

## Start here

- **Teammate frontend:** [copy-paste handoff](cuepilot/TEAMMATE_HANDOFF.md)
- **Shared types and routes:** [API contract](cuepilot/contracts/api.ts)
- **Backend:** [FastAPI service](cuepilot/api.py)
- **Architecture and flow:** [current CuePilot diagrams](docs/cuepilot-architecture.md)
- **Acceptance runs:** [Maya learn, Ravi replay, Alex interruption](cuepilot/ACCEPTANCE_RUNNER.md)
- **Public bridge:** [restricted authenticated proxy](cuepilot/BRIDGE.md)
- **RocketRide:** [validated pipeline and execution gates](cuepilot/INTEGRATIONS_ROCKETRIDE.md)
- **Cognee, HydraDB and Hotdata:** [adapter setup and verification gaps](cuepilot/INTEGRATIONS_MEMORY.md)
- **Rote:** [recording evidence and replay handoff](cuepilot/INTEGRATIONS_ROTE.md)
- **Security:** [scan baseline and open dependency findings](SECURITY.md)
- **Sponsor installation:** [setup notes](SPONSOR_SETUP.md)

Jayesh owns the backend, sponsor adapters and shared contract. **oasb16 owns
`cuepilot/web/`**, the operator desk and projected stage. Keep each workstream in
its own branch and use pull requests. Coordinate before changing the shared API.

## Run the backend

Use Python 3.12 and pnpm. From the repository root on a fresh machine:

```sh
python3.12 -m venv sponsor-setup/memory/.venv
sponsor-setup/memory/.venv/bin/python -m pip install -r cuepilot/requirements.txt
pnpm install --frozen-lockfile
pnpm dev:cuepilot
```

On the existing demo machine, the Python environment is already installed.
The API listens on **http://127.0.0.1:8787**; interactive API docs are at `/docs`.
The frontend will run separately from `cuepilot/web/`, with `/api` proxied to the
backend. Its package and screen implementation belong to the teammate.

On first startup, the API creates private local operator and bridge tokens in
`cuepilot/.env`. Preserve those values when adding entries from
`cuepilot/.env.example`. Never commit credentials. The Vite proxy reads the
operator token server-side; the browser must not receive sponsor secrets.

```sh
pnpm test:cuepilot
pnpm test:cuepilot:rocketride
pnpm check:cuepilot:pipeline
pnpm security:code
```

## Honest execution modes

**Practice** is local UI rehearsal with an explicitly labelled fixture plan. It
supports approval, three real stage changes, durable receipts, and retry checks.
It never claims that the five sponsors all ran.

**Live** requires Cognee-derived cue rules with graph provenance in HydraDB,
fresh Hotdata readiness checks, RocketRide orchestration through an authenticated
HTTPS bridge, and actual Rote recording/replay. Missing configuration produces a
blocked result. A validated pipeline alone is not a completed integration.

The stage API checks plan approval, current show revision, speaker/asset readiness,
step order and stage ownership inside the same database transaction as each cue.
A retry returns its original receipt. A missing presentation switches to holding
and blocks the outdated sequence.

Operation claims prevent duplicate drivers and recover interrupted work after
restart. Operator cancellation stops tracked work, holds an active stage and
preserves committed receipts. Updated live production notes invalidate old rules
at planning, approval, execution and cue boundaries. Physical completion and
verified sponsor completion are separate fields in the shared contract.

## Build scope

One segment type, three synthetic speakers, one browser stage. First prove a
rehearsal, replay with a new speaker, then let a judge mark an asset unavailable.
Capture real execution evidence and keep unresolved Snyk findings visible.

Earlier **Dock** proposal diagrams under `docs/architecture/` and `output/` are
historical planning material; they do not describe CuePilot's current UI.

This repository contains source and shareable setup scripts. Installed tools,
private logins, generated Rote workspaces, runtime databases and local scan reports
stay on the machine and are excluded from Git.

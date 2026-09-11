# Cognee and HydraDB setup

Verified on September 11, 2026 on this Apple Silicon Mac.

| Component | Verification |
| --- | --- |
| Python 3.12.12 | Isolated environment at `.venv`; system Python 3.9 remains unchanged |
| Cognee 1.5.4 | Official keyless demo imported 47 nodes / 86 edges and answered two lexical queries; repeat run also passed |
| Neo4j driver 6.3.0 | Connected to HydraDB and completed a graph write/read |
| HydraDB | Local container running; synthetic relationship written through Bolt and read through Bolt and HTTP |
| Dependency consistency | `pip check` passed |

## Run checks

From the workspace root:

```bash
bash sponsor-setup/memory/cognee_demo.sh
sponsor-setup/memory/.venv/bin/python sponsor-setup/memory/hydradb_smoke.py
```

The Cognee demo uses its bundled graph and needs no model credentials. It validates graph ingestion and retrieval. Building a graph from new raw text additionally needs an LLM and embedding provider. For the default provider, put `LLM_API_KEY` in the workspace `.env`, then run:

```bash
sponsor-setup/memory/.venv/bin/python sponsor-setup/memory/cognee_ingest.py
```

That command sends only `sample.txt` (synthetic setup data) to the configured model provider, and may consume provider credits. It uses dataset `hackathon_setup`; it never clears other datasets. Configure both `LLM_*` and `EMBEDDING_*` settings when switching away from OpenAI defaults. Raw-text extraction has not yet been run because no model key was configured.

## Cognee Cloud verification

The separate `cognee_cloud_ingest.py` script is ready but has not been run. It
uses the installed `cognee==1.5.4` public Cloud connection API. Add these values
to the workspace-root `.env` privately, using the actual tenant URL and key
shown on the Cognee Cloud API Keys page:

```dotenv
COGNEE_SERVICE_URL=https://your-tenant.aws.cognee.ai
COGNEE_API_KEY=
```

After account access and prepaid credits are ready, run from the workspace root:

```bash
sponsor-setup/memory/.venv/bin/python sponsor-setup/memory/cognee_cloud_ingest.py
```

This uploads only `sample.txt` into a newly named `hackathon_setup_cloud_*`
dataset, requires the server to confirm ingestion completed, and verifies all
three sample statements in graph-sourced recall text. Recall uses keyword search
(`CHUNKS_LEXICAL`) to avoid a separate answer-generation model call. Ingestion
still performs hosted processing and consumes Cloud credits. Existing datasets
are preserved; each successful run retains its verification dataset.

The script loads the root `.env`, suppresses raw SDK/account output, disables
telemetry and SDK file logs, and writes a sanitized JSON receipt under
`cloud-receipts/`. Receipts contain only test metadata, the sample hash, timings,
and verification flags; no API keys, tenant URLs, or raw responses. An error is
reported by category and never counts as a pass.

The documented Cloud workflow uses tenant credentials instead of a local
`LLM_API_KEY`. Actual server-side model availability and prepaid-credit access
must be confirmed by a successful run. In this SDK version `serve()` also
caches credentials in `~/.cognee/cloud_credentials.json` with permissions `0600`;
`disconnect()` closes the client and leaves those cached credentials intact.
See the [Cloud SDK guide](https://docs.cognee.ai/cognee-cloud/connections/cloud-sdk)
and [current credit billing documentation](https://docs.cognee.ai/cognee-cloud/functionality/account-and-billing).

## Local HydraDB

Colima 0.10.3 and Docker CLI 29.7.2 are installed. Colima profile `hackathon` uses 2 CPUs, 2 GiB memory, a 10 GiB data disk and the default 20 GiB root disk (sparse). Only this memory folder is mounted into the VM. The launcher pins the official container image by digest.

Start after a reboot:

```bash
colima start hackathon --activate=false --ssh-config=false
bash sponsor-setup/memory/hydradb_start.sh
```

Connections:

- Bolt: `bolt://127.0.0.1:7687`, username `neo4j`, graph/database `default`.
- HTTP: `http://127.0.0.1:8443/v1/graphs/default/query`, namespace `default`, cell `cell-0`.
- Readiness: `http://127.0.0.1:9090/readyz`.
- Password/Bearer token: generated locally in `.hydradb/auth-token`; never commit or print it.

All published ports bind to `127.0.0.1`. Data persists in `.hydradb/store`. Each smoke run adds two synthetic nodes and one edge. The launcher uses a separate Docker client configuration because the existing global configuration referenced an unavailable Docker Desktop credential helper.

Stop without deleting stored data:

```bash
docker --context colima-hackathon stop hackathon-hydradb
colima stop hackathon
```

Cognee currently uses its bundled local graph store. A production Cognee-to-Hydra graph adapter is application integration work and is not claimed by these independent setup checks.

## Reproduce the Python install

```bash
/opt/homebrew/bin/python3.12 -m venv sponsor-setup/memory/.venv
sponsor-setup/memory/.venv/bin/python -m pip install -r sponsor-setup/memory/requirements.lock.txt
```

`requirements.txt` pins direct dependencies; `requirements.lock.txt` records the complete verified environment. First Cognee use also downloads its Ladybug JSON database extension into `~/.lbdb`. Anonymous Cognee telemetry is disabled in the provided scripts.

## Official references

- [Cognee installation](https://docs.cognee.ai/getting-started/installation)
- [Cognee keyless demo and Python quickstart](https://docs.cognee.ai/getting-started/quickstart)
- [HydraDB server setup and query protocol](https://github.com/hydra-db/hydradb)
- [Colima setup](https://github.com/abiosoft/colima)

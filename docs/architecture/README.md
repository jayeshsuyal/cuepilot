# Dock architecture and implementation contract

Status: proposed build blueprint, September 11, 2026. These diagrams describe
the product to implement, not an already integrated application. The attached
reference photo shows RocketRide's real Designer; our equivalent will be an
editable `.pipe` pipeline once its nodes and connections are configured and
validated against staging.

## Deliverables

- [Six-page architecture pack](../../output/pdf/dock-system-architecture.pdf)
- [System architecture](../../output/architecture/01-system-architecture.svg)
- [System flow](../../output/architecture/02-system-flow.svg)
- [RocketRide Designer blueprint](../../output/architecture/03-rocketride-designer-blueprint.svg)
- [Memory model and contract](../../output/architecture/04-memory-and-contract.svg)
- [Six-hour build map](../../output/architecture/05-six-hour-build-map.svg)
- [Readiness and acceptance gates](../../output/architecture/06-readiness-and-acceptance.svg)

Each diagram also has a PNG export beside its SVG. SVGs remain editable and
resolution independent. `build_diagrams.py` regenerates the SVGs and vector PDF
using ReportLab in the bundled document runtime. No third-party font or image
downloads are required.

## Product boundary

Dock helps an operations coordinator import a recurring supplier shipment file
into a receiving system that expects individual units. First run: learn an
evidence-backed rule and approve a procedure. Subsequent compatible runs: reuse
that procedure with new inputs. Changed or ambiguous rules: stop before writing.

MVP: one supplier, one destination, CSV plus a supplier note, two operations:

1. Trim leading and trailing whitespace from a SKU. Do not change letter case,
   punctuation, leading zeroes, or invent a product mapping.
2. Convert a nonnegative integer number of cases into individual units using the
   rule for that supplier, SKU, and effective date. Unit rows remain unit rows.

Every batch declares one effective date. Reject conflicting row dates in this
MVP; mixed-date batches require a later per-row date contract.

The receiving API and its current catalog will be local implementations with
synthetic data. There is no claimed live ERP, supplier, or Shopify integration.
The diagram's example quantities are illustrative fixtures, not measured runs.

## Runtime responsibilities

RocketRide is the only planning/orchestration owner. The Dock adapter exposes
bounded operations; it does not run another autonomous planner. Rote is the local
executor for an approved reusable procedure, beneath that orchestration.

| Layer | Meaningful work | Evidence |
| --- | --- | --- |
| Cognee | Extract candidate entities and relationships from new or updated supplier notes | Dataset/source identity, extracted graph and provenance |
| HydraDB | Persist the Cognee-derived graph, approved rules, recipe bindings and outcomes | Actual Cypher writes and relationship-aware reads across runs |
| hotdata.dev | Query every new batch against an independently maintained current catalog | Actual SQL, catalog revision, validation results |
| RocketRide | Select and sequence the operations; choose learn/replay/stop; verify result | Executed pipeline ID and tool trace |
| Rote | Record a verified successful procedure and replay it using a new batch | Procedure ID/version, parameters, execution receipt |
| Snyk | Scan source and dependencies during development, after fixes and before submission | Scan artifacts and explicit unresolved findings |

Cognee need not reprocess an unchanged note on every replay. It does new work on
the initial and changed-note cases. Current batch validation and durable recall
still run every time. No layer is added merely to generate a sponsor call count.

## Cloud and local boundaries

Keep staging RocketRide as the execution runtime. One `tool_http_request` node,
named Dock Operations, reaches the other four sponsor services through the local
Dock adapter's allowlisted routes over a single authenticated HTTPS tunnel. The laptop's
HydraDB ports and local Rote process stay local; do not expose either directly.
The UI sends runs through its backend and never receives sponsor credentials.

RocketRide makes separate traced calls to ingest/store evidence, recall rules,
query the batch, preview changes, execute/replay and verify the receipt. There is
no `run_everything` endpoint: the bridge contains adapters and execution guards,
while RocketRide owns the sequence. Approval, version and retry checks are enforced
by code even if a model proposes an invalid call.

The adapter uses the same Cognee workspace for ingestion and graph export, and
the same Hotdata account for batch/catalog queries. A small explicit graph bridge
validates Cognee's extracted entities and relationships before writing them to
HydraDB. Do not replace this with an independently hand-built graph or another
model's invented relationship extraction.

Graph export is an early feasibility gate. The installed RocketRide Cognee tool
lists remember/recall/status; it does not establish a working graph-export path.
Verify a documented Cognee export/backend path before committing the integration.

Cloud staging cannot call laptop `localhost`. An SDK `client.tool()` call invokes
server-side tools; it is not a local reverse callback. The authenticated tunnel
and an actual harmless tool call must pass before UI polish. This document does
not create a public tunnel or grant additional access.

## Available RocketRide components

The installed staging catalog contains `webhook`, `agent_rocketride`,
`llm_openai`, `memory_internal`, `tool_http_request`, `tool_cognee`, `db_hotdata`,
`mcp_client`, and `response_answers`.

`agent_rocketride` requires one LLM and exactly one `memory_internal` scratchpad.
That scratchpad is session context, not a substitute for durable HydraDB memory.
Control edges are declared on attached tools/LLM/memory and point to the agent.
The agent emits the answers lane, so `response_answers` is the compatible sink.
The adapter serves the canonical machine-readable RunContract separately.

The primary blueprint uses only the HTTP bridge for sponsor operations. Native
Cognee and Hotdata nodes are possible future alternatives, not additional active
connections in this plan. Native `db_hydradb` describes a managed Hydra memory API; compatibility with the
installed OSS Hydra/Cypher server has not been verified. There is no native Rote
provider in the installed catalog. Both therefore use bounded local adapter tools.
`db_hotdata` also needs an LLM connection when used as a native node. Never invent
provider IDs or configuration fields: use the installed schemas and validate.

## Data and authority

Keep raw input immutable. A transformed row records `rowId`, `rawSku`,
`normalizedSku`, `inputQuantity`, `inputUnit`, `outputUnits`, `ruleVersionId`, and
the explanation of its changes.

Candidate rule extraction is evidence, not authority. The independent catalog
must confirm exactly one applicable rule per supplier/SKU/effective date. Reject
missing or overlapping rules, invalid or ambiguous SKUs, nonfinite or negative
quantities, unsupported fractional cases, and trim collisions that merge distinct
identities. A future rule must not rewrite the interpretation of historical rows.

Graph relationships:

```text
Supplier      -SUPPLIES->          SKU
RuleVersion   -FOR_SUPPLIER->      Supplier
RuleVersion   -APPLIES_TO->        SKU
RuleVersion   -EVIDENCED_BY->      SourceDocument
RuleVersion   -VERIFIED_AGAINST->  CatalogSnapshot
RecipeVersion -BINDS->            RuleVersion
Run           -EXECUTED->         RecipeVersion
Run           -PRODUCED->         ImportReceipt
```

Rule versions retain units per case, effective interval, document hash,
extraction reference, catalog revision and approval state. Recipe versions retain
ordered operations, schema fingerprint, relevant rule bindings, Rote procedure
identity and the first successful receipt. Preserve old versions for lineage.

## Proposed HTTP contract

These are implementation route names, not existing sponsor endpoints:

| Route | Owner | Result |
| --- | --- | --- |
| `POST /runs` | Shared backend | Allocate run, persist immutable input references |
| `GET /runs/{runId}` | Shared backend | Canonical RunContract, including status and evidence |
| `POST /runs/{runId}/approve` | Shared backend | Approval bound to batch hash, plan hash and rule versions |
| `POST /tools/ingest-memory` | You | Cognee candidate graph plus validated Hydra bridge result |
| `POST /tools/recall-recipe` | You | Applicable approved recipe and graph provenance |
| `POST /tools/query-batch` | You | Hotdata batch/catalog checks with fresh revision |
| `POST /tools/preview-batch` | You | Row-level proposed changes, plan hash and validation evidence |
| `POST /tools/execute-recipe` | Teammate | Bounded first-run execution or Rote replay |
| `POST /tools/verify-receipt` | Teammate | Receipt lookup/reconciliation using the stable idempotency key |
| `POST /imports` | Teammate | Atomic guarded import or existing receipt on retry |

RunContract minimum fields:

```json
{
  "runId": "run-example",
  "supplierId": "supplier-demo",
  "batchId": "batch-example",
  "batchSha256": "<computed-from-immutable-input>",
  "schemaFingerprint": "<computed-from-supported-schema>",
  "effectiveDate": "2026-09-11",
  "mode": "learn",
  "status": "needs_approval",
  "catalogSnapshot": {"id": "snapshot-example", "revision": "v1", "fetchedAt": null},
  "recipe": {"id": null, "version": null, "ruleBindings": []},
  "evidence": [],
  "diffs": [],
  "checks": [],
  "receipt": null,
  "idempotencyKey": "<supplier+batch-content-hash+destination>",
  "telemetry": {"elapsedMs": null, "instrumentedPlannerCalls": null, "providerUsage": null}
}
```

Suggested statuses: `queued`, `analyzing`, `needs_approval`, `executing`, `reconciling`,
`imported`, `blocked`, `failed`. The example above is a contract sketch, not a
real run. The implementation must validate types and required fields.

## Execution and replay invariants

1. Look up the receipt by the stable idempotency key first. An already committed
   batch returns that receipt without a new write, even if the catalog changed
   afterward. For every new batch, query fresh batch data and relevant catalog
   rules, including during replay.
2. Compare schema and relevant rule versions/values against recipe bindings.
3. Bind first approval to the exact input, plan and rule versions.
4. Allow only the two implemented transformations; never execute arbitrary
   model-generated code, shell commands, SQL writes or arbitrary destination URLs.
5. Validate row-level outputs, row counts and unit totals before committing.
6. In the receiving transaction, return an existing receipt first. Otherwise,
   check the expected catalog revision and import atomically. A revision change
   aborts the new write, including changes after preflight.
7. Enforce a unique key from supplier + immutable batch hash + destination in the
   same transaction. Do not include run ID or recipe version in that key: retries
   and replanning must not create duplicate receipts.
8. Publish the reusable Rote procedure only after a verified successful import.
   Bind reusable arguments; never bake first-run batch IDs or output rows into it.
9. A confirmed precommit rejection or rollback means zero new rows. A timeout
   or unknown response enters `reconciling`: look up/retry with the same key and
   retain an unknown outcome until resolved. Never report zero writes merely
   because the response was lost.

These are a small fixed set of checks in a local demo API, not a new policy
engine. SQLite is sufficient for the local catalog, run state and atomic receipt
transaction; HydraDB remains the durable relationship memory.

## Demo proof and instrumentation

- A: new note says 12 units/case; 10 cases becomes 120 units after approval.
- B: different batch, quantities and row order; 7 cases becomes 84 using replay.
- C: relevant catalog rule changes to 6; the old procedure stops with zero writes.
- Retry B with the same batch hash; the API returns the same receipt.

Measure the same wall-clock boundaries for A and B, and retain actual run traces.
Count only model calls visible to our instrumentation. Hosted Cognee, Hotdata or
RocketRide internals may be opaque; report unknown totals as unavailable. Do not
claim zero total LLM calls, a dollar saving or a speed multiplier without evidence.
An optional model-planning counter may demonstrate reuse even when total provider
usage is unavailable. No benchmark numbers are prefilled in the diagrams.

## Team boundary and timebox

First 30 minutes together: confirm account gates, graph export and one staging
call to the local adapter; freeze RunContract and route ownership.

You own Cognee-to-Hydra memory, Hotdata validation and the input/diff view.
Your teammate owns RocketRide orchestration, Rote execution, the receiving API,
and trace/receipt display. Both integrate the single screen through RunContract.
Use separate module ownership to reduce simultaneous edits to shared files.

Hours 0.5-2: implement the two sides. Hour 3: first complete learn/import path.
Hour 4: replay with a new file. Hour 5: changed-rule and retry checks. Hour 6:
security triage/rescans, final evidence, rehearsal and buffer. Do not spend that
buffer on connectors, a general workflow builder, multi-supplier inference, ERP
integration, a second agent, or a new analytics dashboard.

## Remaining setup gates

Cognee is funded but hosted ingestion/API configuration and graph export remain
unverified. Hotdata account activation and a cloud SQL test remain pending.
RocketRide needs positive compute credits and tool profile validation. Local
Hydra graph checks and the official Rote warm-up passed, but the application
integration has not. The latest recorded security baseline has six outstanding
dependency advisories; see [SECURITY.md](../../SECURITY.md).

Architecture source scan: `pnpm security:code` completed with zero findings on
September 11, 2026. Evidence is in the ignored local report directory
`sponsor-setup/snyk/reports/20260911T182734.087507Z/`. This is a source-only result;
it does not clear the six dependency findings.

## References used

- User-provided Hackathon Problem Statement and Official Builder Guide.
- User-provided RocketRide Designer photo `IMG_5694.HEIC` (visual reference).
- Installed `.rocketride/docs/ROCKETRIDE_README.md`, `ROCKETRIDE_PIPELINES.md`,
  `ROCKETRIDE_INTEGRATIONS.md`, component catalog and schemas.
- Sponsor setup results and Snyk evidence already recorded in this workspace.

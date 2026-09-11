# Memory and current-state adapters

The public async entry points remain `ingest_note(note, source_id)`,
`recall_recipe(show_id)` and `validate_show(show, speaker_id)`. Pass the actual
show ID as `source_id`. Results contain `status`, `records` of
`{provider,status,operation,evidence,reason}`, and `reason`. Memory verification
includes `supported_template`, `recipe_id`, `note_sha256`, `graph_sha256`, and
`source_id`; Hotdata verification includes `show_revision`. The API must compare
these with the run's exact note and current show revision before using them.
Failure, unavailable configuration, incomplete processing, and stale evidence
never become fixtures or sponsor success.

## Cognee extraction and repeat runs

The [official Cloud client](https://github.com/topoteretes/cognee/blob/main/cognee/api/v1/serve/cloud_client.py)
confirms multipart file field `data`, form fields `datasetName` / `custom_prompt`,
and `X-Api-Key`. The adapter explicitly requests `run_in_background=false` at the
[remember endpoint](https://docs.cognee.ai/api-reference/remember/remember),
validates the returned dataset UUID and optional dataset name, and requires
completion without an explicit error before fetching its
[dataset graph](https://docs.cognee.ai/api-reference/datasets/get-dataset-graph).
If Cloud returns `running`, it polls that exact dataset's `cognify_pipeline`
[status](https://docs.cognee.ai/api-reference/datasets/get-dataset-status) every two
seconds for at most 60 seconds. Only `completed` or
`DATASET_PROCESSING_COMPLETED` permits export; errors, unknown statuses and timeout
cannot publish a Hydra recipe. Resuming an already returned receipt requires no
second upload.
A valid completion can have ID-only `items`; missing item names or content hashes
are not themselves failures. Unconfirmed `running`, malformed completion and unresolved
exports cannot verify a recipe. It uses HTTP directly and does not import or
change Cognee's global SDK session.

The graph must actually contain directed introduction → presentation → holding
relationships and unavailable-speaker/presentation → holding fallbacks. The small
explicit vocabulary in `_prove_template` is deliberately limited to this segment.
Only `Entity` nodes can prove these rules; exported `EntityType` taxonomy nodes
remain stored but cannot supply or duplicate an entity rule. The observed exact
Cloud labels `unavailablespeaker` and `unavailablepresentation` are accepted
alongside their spaced forms; no fuzzy label matching is used.
An extraction prompt suggests vocabulary but cannot prove a plan. Missing rules,
duplicate recognized entities, contradictory ordering, and unavailable → another
scene fallbacks block even when the expected edges also exist. Canonical node and
edge ordering makes graph hashes stable across export reordering; duplicate edges
and original node IDs/properties/relationship labels remain represented.

The visualization graph API omits edge properties. Provenance is at dataset and
source-note level, not per-text-span attribution. `COGNEE_SERVICE_URL` must be an
HTTPS cognee.ai tenant URL, and `COGNEE_API_KEY` comes from the caller's environment.
No provider call or credential loading occurs on import.

An initial ingest reads Hydra first, then performs hosted Cognee extraction only
when there is no current recipe for the exact note hash. Repeating the same note
performs real Hydra traversal and a fresh Cognee dataset export, compares its hash
and rules, and returns `reexport_unchanged_graph` evidence with
`reused_extraction=true`. It makes no new remember call. A failed re-export or
changed graph blocks reuse. `recall_recipe` independently performs actual Hydra
reads and returns only a Hydra record with stored Cognee provenance inside it;
callers must not invent a fresh Cognee success from a local recall alone.

## Hydra graph and successful outcomes

Hydra uses the existing Neo4j driver against a loopback Bolt endpoint. Its
[Cypher implementation](https://github.com/hydra-db/hydradb/blob/main/src/query/opencypher.rs)
supports the `UNWIND` vertex-upsert and relationship-create forms used here; plain
node `CREATE`, ordinary `MATCH … CREATE`, and `MERGE … ON CREATE` are unsuitable.
The adapter uses auto-commit operations because Hydra does not support
[transactions spanning multiple RUN requests](https://github.com/hydra-db/hydradb/blob/main/architecture.md).
All provider values are parameters and all query templates are fixed.

Each import has a separate receipt identity. Vertices retain their original
Cognee payload plus dataset/source/note/graph provenance. Edges retain labels,
dataset and receipt identity, with explicit IDs for parallel relationships.
Before publishing a recipe, the adapter reads back every vertex and the exact
outgoing relationship multiset. Recall repeats these checks against the actual
stored graph, verifies its digest and rule proof, and rejects changed or missing
vertices/edges. An interrupted import cannot publish a complete recipe. Envelope
version 2 is required; legacy version 1 JSON-only proof is rejected and no silent
migration is attempted.

New internal entry points, with no public route or contract change in this task:

```python
await record_successful_outcome(
    source_id=show_id, note_sha256=note_hash, graph_sha256=graph_hash,
    recipe_id=recipe_id, run=completed_run,
    rote_result=verified_rote_result, procedure={"id": package_name, "sha256": proof_hash},
)
await recall_successful_outcome(
    show_id, note_sha256=note_hash, graph_sha256=graph_hash,
    recipe_id=recipe_id, procedure={"id": package_name, "sha256": proof_hash},
)
```

The coordinator must provide trusted server state, not client-authored evidence.
A write requires a completed **live** run, its exact note hash, a sponsor plan
whose `recipeId` matches, and `run.memoryProof` with matching
`source_id/note_sha256/graph_sha256/recipe_id` captured before execution. It also
requires exactly three ordered successful intro/presentation/holding receipts
with consecutive stage revisions, and verified Rote `learn` or `replay` evidence
for that run and those receipts. `rote_result.evidence.procedure` must equal the
supplied immutable `{id,sha256}` identity. Full Rote receipt fields are compared
with committed receipts; the older learning helper's raw HTTP receipt hashes are
also checked exactly when full receipts are absent. Practice or partial runs
cannot publish a live outcome. No prior outcome trace is required.

The outcome ID deterministically hashes its rule/procedure binding and run ID.
Hydra's [atomic generation guard](https://github.com/hydra-db/hydradb/blob/main/src/shard/write.rs)
uses constant generation 1 so a retry cannot replace stored proof. Exact read-back
makes conflicting retries fail. Recall requires the current recipe, exact note
and graph hashes, and exact procedure; it checks outcome and receipt digests.
Re-extracting a note into a different recipe invalidates reuse of earlier outcomes.
Outcome metadata is historical proof, never current speaker/asset readiness.
The API remains responsible for current run/show checks and serializing run
mutations while adapter work is in flight.

Both outcome operations return `status`, `records`, `reason`, `outcome_id`,
`source_id`, `note_sha256`, `graph_sha256`, `recipe_id`, `procedure`,
`supported_template`, and `execution` on success. `execution` contains `run_id`,
`execution_mode`, `show_revision`, `plan_sha256`, `speaker_id`, `operation`,
`receipts`, `receipts_sha256`, and `rote_run_id`. The Hydra record's evidence has
those binding and execution fields plus `read_back_verified=true`.

Hydra configuration is `HYDRADB_BOLT_URL` (default `bolt://127.0.0.1:7687`),
`HYDRADB_GRAPH_ID` (default `default`) and `HYDRADB_AUTH_TOKEN`, falling back to the
private setup token file. Remote Hydra URLs are rejected. Graph exports are
bounded to 2 MB, 200 nodes and 400 edges. Ingestion has a 150-second total deadline,
recall 20 seconds, and outcome operations 30 seconds. No credentials are returned.

## Hotdata snapshots and current readiness

Live API verification passed on September 11 at 20:54 UTC using the supplied API
token directly: Maya passed, Ravi reused that snapshot with a new verified query,
and Alex's changed revision returned a verified missing-asset query and correctly
blocked execution. This created two synthetic snapshot databases with one-hour
expiry requested, two load receipts and three query IDs. Private redacted report:
`cuepilot/.runtime/hotdata-checks/readiness-20260911T205439.476554Z.json`.
This verifies Hotdata independently; Cognee extraction and full RocketRide
orchestration remain separate live gates.

Hotdata needs a read-write [API token](https://www.hotdata.dev/docs/core-concepts#authentication)
for the chosen workspace, supplied as `HOTDATA_API_KEY` and `HOTDATA_WORKSPACE`.
`X-Database-Id` selects query scope; it is not a second credential.
The adapter [creates a temporary database](https://www.hotdata.dev/docs/api-reference/databases),
requests a one-hour best-effort expiry, loads one immutable CSV snapshot with
explicit column types, and verifies the synchronous load receipt's connection,
schema, table and row count. This avoids numeric-looking IDs being inferred as
numbers. An HTTP 202 background acceptance cannot establish publication.
The load idempotency key hashes the returned database ID, schema, table and
complete load payload. Reusing the same CSV after a process restart is a new
operation when its destination changes; a snapshot-only key caused a confirmed
HTTP 409 and is no longer used.

A [fresh synchronous query](https://www.hotdata.dev/docs/api-reference/query)
checks selected speaker readiness and its matching presentation. The result must
have the exact columns, one preview row, one total row, no truncation, a query-run
ID, and the exact typed snapshot values. A null persisted result ID is supported
when the full result is inline. A deprecated `row_count` alone is insufficient.
A verified query reporting unavailable speaker/asset produces overall `blocked`;
stay on holding. Only use `show_revision` when overall status is `verified`, then
compare it again with the current show revision.

A bounded process-local cache retains at most 16 proved snapshot references for
55 minutes, keyed by workspace, credential digest and complete snapshot hash.
Another speaker on the same snapshot reuses the database but makes a real query
and reports `snapshot_reused=true`. A revision or content change publishes a new
database. Errors evict that reference and remain failed/blocked; they do not
silently retry against a fixture. Concurrent cache misses may create duplicate
expiring databases. Cache state is not durable. Results retain the captured
revision even if the caller mutates its input during I/O.

Validation has a 60-second deadline. Partial failures may leave an expiring
database. The adapter uses fixed endpoints, restricted SQL and identifier
validation; it does not accept caller URLs/SQL or follow redirects.

## Verification scope

The focused offline suites exercise provider HTTP response shapes, extraction
completion, graph normalization/proof, driver-boundary graph corruption and
provenance, idempotent outcome read/write, stale rule/procedure rejection, and
Hotdata publication/query/revision mismatch. Driver-boundary tests do not execute
the Hydra parser. This task made no live sponsor calls and copied no credentials.
The coordinator must exercise the full Cognee → Hydra path, guarded outcome
write/read and real Hotdata load/query against the running services. In particular,
the installed Hydra build must accept the documented guarded-upsert dialect.
No source-only or mocked check establishes live sponsor success.

Validation for this change: `pnpm test:cuepilot` passed 59 tests, including 47 new
focused provider/persistence tests. `pnpm security:code` completed with no reported
source findings after a sandbox DNS failure was retried with network access.
Final `pnpm security:scan` completed: source 0, Node 1 high advisory, optional setup
Python 5 advisories (1 critical, 2 high, 2 medium). These are the same unresolved
dependency advisories documented in `SECURITY.md`; no dependency was changed,
ignored or removed to obtain a pass. Final scan evidence is Git-ignored under
`sponsor-setup/snyk/reports/20260911T203030.356637Z/` in this worktree.

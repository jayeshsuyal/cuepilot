# Memory and current-state adapters

`cuepilot.integrations.memory` exposes async `ingest_note(note, source_id)` and
`recall_recipe(show_id)`. Pass `source_id=show.id`. Both return an overall
`status`, `records` containing `{provider,status,operation,evidence,reason}`, and
`reason`. Verified results include `supported_template`, `recipe_id`,
`note_sha256`, `graph_sha256`, and `source_id`. The API must compare `note_sha256`
with the current production note before using the template.

Ingestion uses the [documented remember endpoint](https://docs.cognee.ai/api-reference/remember/remember)
and [dataset graph endpoint](https://docs.cognee.ai/api-reference/datasets/get-dataset-graph).
Its multipart fields and `X-Api-Key` header match installed Cognee 1.5.4's Cloud
client. It uses HTTP directly, without importing Cognee or changing its global
SDK session. Credentials are `COGNEE_SERVICE_URL` and `COGNEE_API_KEY`, supplied
by the caller's environment; the configured URL must be HTTPS under cognee.ai.
Importing the module does not load credentials or call a provider. Calling
configured ingestion consumes hosted credits; no such live processing was run
while implementing this adapter.

The graph must actually contain directed introduction → presentation → holding
relationships and unavailable-speaker/presentation → holding fallback rules.
Only the small explicit vocabulary in `_prove_template` is accepted. A custom
extraction prompt suggests that vocabulary but cannot itself verify a plan.
Empty, unsupported, conflicting, or unresolvable graph exports block execution.
The visualization graph API omits edge properties; provenance here is at dataset
and source-note level, not per-text-span evidence.

Only a verified graph is written to the existing loopback HydraDB through the
installed Neo4j driver. Original node IDs/properties and relationship labels are
preserved alongside dataset ID, source ID, note hash, and graph hash. All Cypher
values are parameters. Imported relationship traversals and the final recipe
payload must read back correctly. Interrupted imports may leave isolated graph
data; the recipe is published last. Recall rechecks the stored provenance,
digest and relationship proof. It currently emits a HydraDB record; the stored
Cognee provenance is inside that record's evidence. The caller must not invent a
fresh Cognee success record. There is no outcome/learned-procedure write-back yet.

Hydra reads `HYDRADB_BOLT_URL` (default `bolt://127.0.0.1:7687`),
`HYDRADB_GRAPH_ID` (default `default`), and `HYDRADB_AUTH_TOKEN`, falling back to
the private setup token file. Remote Hydra endpoints are rejected. Graph exports
are bounded to 2 MB, 200 nodes and 400 edges; ingestion has a 150-second total
deadline and recall 20 seconds. This application-specific persistence path has
not yet been exercised against the running Hydra service. The earlier sponsor
setup smoke verified simpler graph writes and traversal only.

`cuepilot.integrations.hotdata.validate_show(show, speaker_id)` accepts the
shared contract: show `id/revision`, speaker `ready/presentationAssetId`, and
slide asset `status`. It requires `HOTDATA_API_KEY` and `HOTDATA_WORKSPACE`.
It [creates a temporary database](https://www.hotdata.dev/docs/core-concepts)
with a one-hour expiry, [publishes one CSV snapshot and queries it](https://www.hotdata.dev/docs/push-data)
using fixed API paths and a restricted SQL template. Each validation uses a new
database to avoid mixing revisions. It verifies the actual returned revision,
speaker readiness, and matching asset against the submitted snapshot.

The Hotdata result includes `ready`, `records`, and overall `status`. A verified
query reporting an unavailable speaker/asset produces overall `blocked`; stay on
holding. Accept `show_revision` only when overall status is `verified`, then
compare it with the current revision before planning/execution. Validation has
a 60-second total deadline; partial failures leave at most the expiring database.
No Hotdata live write/query was run during implementation. Neither adapter
creates accounts, changes billing, follows redirects, accepts user URLs/SQL, or
returns provider error bodies or credentials.

Remaining validation: exercise the full Cloud export → Hydra persistence flow
after credentials and live processing are authorized, then test a real Hotdata
snapshot/query. Mocked/offline checks cannot establish sponsor-backed execution.

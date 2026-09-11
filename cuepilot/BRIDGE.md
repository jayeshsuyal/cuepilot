# Narrow RocketRide ingress

The local app remains on `127.0.0.1:8787`. This separate bridge listens only on
`127.0.0.1:8788` and forwards a fixed set of authenticated routes to that app.
It does not create a tunnel, bind to a public interface, or accept an upstream URL.

```sh
sponsor-setup/memory/.venv/bin/python -m cuepilot.bridge
```

`CUEPILOT_BRIDGE_TOKEN` is loaded from the private environment/root `.env` and
`cuepilot/.env`, consistently with the API. It must match the API's dedicated
bridge token. Each exposed request requires `Authorization: Bearer ...`; an
unconfigured bridge fails closed. Do not put the operator token in this service.
Any separately authorized tunnel should target **8788**, not the full API on 8787.

Allowed routes are exactly:

- `GET /api/v1/health`
- `GET /api/v1/runs/{canonical-lowercase-UUID}`
- `POST /api/v1/tools/ingest-memory`
- `POST /api/v1/tools/recall-recipe`
- `POST /api/v1/tools/validate-show`
- `POST /api/v1/tools/plan`
- `POST /api/v1/tools/execute`
- `POST /api/v1/tools/verify`

POST requires `application/json` and exactly `{ "runId": "<UUID>" }`. Extra
fields, duplicate JSON keys, query parameters, encoded path variants, extra
slashes and other methods/routes are rejected. Request bodies must finish
arriving within 5 seconds. Operator routes, run listings,
show/stage reads, direct `stage/cue`, documentation and OpenAPI are not exposed.
Rote still applies individual cues against the private local API directly.

The bridge forwards only its own authorization/JSON headers and a reconstructed
runId body. It does not forward caller cookies, arbitrary headers or query data,
and it does not follow upstream redirects or use environment HTTP proxies.
Upstream request bodies are limited to 4 KiB; upstream JSON responses to 64 KiB,
including streamed bodies without declared lengths. Responses are projected to
small typed summaries. Canonical run reads retain only the runner-required ID,
mode, status, plan hash, optional `verifiedCompletion` boolean and bounded cue
receipts. Missing legacy completion flags default to false; nonboolean flags
are omitted. Each receipt must explicitly
report `ok: true`; receipts must form an ordered cue prefix with matching scenes
and strictly increasing stage revisions. Notes, trace/evidence payloads,
raw provider messages and arbitrary upstream response headers are withheld.

After a successful tool result, the bridge reads that same run from the fixed
local API with an additional five-second bound. `runStatus`, `completedOperation`
and, when required, `nextOperation` describe actual workflow progress using fixed
operation names. A later blocked or failed run suppresses completion and next-step
hints, even if the tool returned a cached success. Preparation advances through
ingest, recall, validation and plan; approved validation advances to execution;
verified Rote execution with three canonical receipts advances to verification.
Successful plan responses with canonical `needs_approval`, and successful verify
responses with canonical `completed`, explicitly return `nextOperation: null`.
That terminal value clears earlier hints: the orchestrator must use only the
latest response's progress metadata. Cached success on a blocked or failed run
still omits both `completedOperation` and `nextOperation`.
These fields expose no dynamic URLs or private evidence and do not authorize
skipping any API prerequisite. Malformed state or a timed-out progress read fails
closed and requires reconciliation.

Upstream total deadlines are 190 seconds for ingestion, 130 seconds for execution
and Hotdata validation, and 30 seconds for other calls, with a 3-second connect
bound. The caller must set a compatible timeout; the bridge cannot extend an
earlier RocketRide HTTP timeout. Timeout or response loss after a POST is marked
`reconciliationRequired`; inspect the canonical local run before retrying.

The local API remains responsible for approval, stage order, fresh show checks,
Rote execution and idempotency. This bridge restricts public reachability; it
does not grant additional action authority or synthesize successful results.

Focused tests use an injected in-memory HTTP transport and make no live calls:

```sh
sponsor-setup/memory/.venv/bin/python -m unittest cuepilot.tests.test_bridge -v
```

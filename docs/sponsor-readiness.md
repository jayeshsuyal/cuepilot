# Sponsor connection verification

Verified in this workspace on September 11, 2026. Credentials remain in ignored
local configuration with owner-only permissions; this document contains no keys.

| Service | Verified behavior |
| --- | --- |
| Cognee | Hosted ingestion completed; the exported graph has 11 nodes and 16 edges. |
| HydraDB | The entire Cognee graph was persisted and read back through local Hydra. Recall and unchanged-note reuse passed. The supplied cloud key also passed a read-only database-list request; the app continues using local Bolt. |
| hotdata.dev | Actual synthetic data loading and fresh readiness queries passed, including a missing-presentation case. Load idempotency now binds the destination and full payload so API restarts can publish the same snapshot into a fresh database. |
| RocketRide | Actual live preparation, approved Rote execution and verification passed through the authenticated bridge. The existing funded staging login is selected; the newly supplied key authenticated but had no available compute credit. |
| Modiqo / Rote | Actual live Maya learning, Ravi replay of the same package with new receipts, and Alex interruption passed. Existing CLI login works. |
| Snyk | Existing CLI login completes source scans; the latest source scan passed with zero findings at 23:40 UTC. The supplied token returned HTTP 401, so it is not used for scanning. Six previously reported dependency advisories remain open. |
| Model provider | The supplied model key works. The default supported GPT-4o profile completed the live flows. |

`COGNEE_URL` is normalized privately to the app's `COGNEE_SERVICE_URL`.
`ROCKETRIDE_APIKEY` selects the working staging credential. The opaque supplied
`HYDRADB_CONNECTION_STRING` is a hosted API key, not a Bolt URL; it does not replace
the local database connection. Rote uses its existing CLI authentication and does
not require an additional `ROTE_API_KEY` here.

The authorized temporary Cloudflare tunnel exposes only the authenticated bridge
on port 8788. Anonymous health requests return 401; private operator and direct cue
routes return 404. The full local API on port 8787 is not tunneled.

Full live learn → replay → interruption acceptance passed at 22:35 UTC. Its
private report is
`cuepilot/.runtime/acceptance/20260911T223543.936639Z_fb0d972f96f14eb996f96ffb7669e690.json`.
Maya and Ravi each have three matched canonical receipts; Alex retained one intro
receipt, blocked the presentation and held the stage. This verifies procedure
reuse and current-state guards; no latency or cost reduction is claimed.

Other private evidence is under `cuepilot/.runtime/` and
`sponsor-setup/snyk/reports/`. The temporary tunnel and local services must remain
running for the configured demo. Generated plays and credentials are not part of
a fresh checkout; a new machine must configure services and learn its own play.

Follow-up bug sweep: read-only RocketRide staging/bridge validation, Hydra recipe
traversal, Cognee existing-graph proof, and Hotdata workspace access passed at
23:30–23:31 UTC. At 23:40 UTC, a prepared Maya plan executed through the patched
desk/API with three receipts and verified traces for all five sponsors. See
[the bug-sweep report](bug-sweep-2026-09-11.md) for the earlier reproduced approval
race, its fix, and closing verification. A fresh Ravi browser run also passed
at 23:42 UTC, with execution starting after verified preparation and three new
receipts.

# Backend acceptance and release gates

## Integration result — September 11, 20:48 UTC

The coordinator integrated the fixes below and the memory/Rote/RocketRide work.
**166 Python tests and 22 Node tests pass.** The combined suite includes local
HTTP transport and real subprocess lifecycle checks; mock sponsor tests remain
explicitly separate from live evidence. Local process/listener tests require
permission to run outside a nested sandbox on the demo machine.

The final bounded review found and then verified fixes for cancellation of
in-flight tool requests and obsolete queued-rule approval. Cancellation gathers
both driver and provider tasks even after physical cues complete. Current rules
are checked at plan, approval, execution claim and cue commit. Acceptance now
requires fresh Maya learning and Ravi replay of the same package, identity and
proof, with each captured receipt matching its new canonical run.

Real local acceptance passed: practice Maya/Ravi/blocked Alex at 20:32 UTC;
fresh Rote Maya learn → Ravi replay → Alex interruption at 20:48 UTC. See
[Rote evidence](../cuepilot/INTEGRATIONS_ROTE.md). Actual Hydra parser/persistence
compatibility passed 11 checks with clearly labelled synthetic provenance; this
does not count as hosted Cognee extraction. Updated RocketRide staging validation
passed with zero errors/warnings and no task execution.

Full five-sponsor live acceptance remains open pending Cognee/Hotdata/model
credentials and a working public HTTPS bridge. Cloudflare allocation timed out
after explicit exposure authorization; the local restricted bridge works. The
frontend is a separate teammate deliverable and has not been reviewed here.
No final application/security score or full live signoff is claimed.

The following sections preserve the independent baseline review and its original
reproductions. Their source locations and failing counts describe `528323d`,
not the integrated backend. Current additions to the contract document cancel
and production-note invalidation; baseline comments about absent routes are
historical.

## Original independent review

Review baseline: `528323d`, September 11, 2026. This review changes only independent
tests and this checklist. Application fixes, integration, the public bridge, live
sponsor calls, and signoff belong to the coordinating task. No public v1 contract
or frontend files changed.

## What the offline suite establishes

Run from the repository root with the existing isolated Python environment:

```sh
pnpm test:cuepilot
```

On an isolated worktree without installed tools, the equivalent interpreter is
`/Users/jayeshsuyal/Documents/ChatGPT/Hackathon~/sponsor-setup/memory/.venv/bin/python`.
Use it to run `-m unittest discover -s cuepilot/tests -v` from this worktree, not
from the main checkout. Tests use temporary SQLite databases, ephemeral tokens,
mock provider transports and fake subprocesses. They start no network listener,
call no real sponsor, and read no demo `.env` through `create_app`.

The baseline's existing 12 tests pass. The 18 added acceptance tests have **8
passes and 10 failures** on that baseline. Failures are ordinary failing tests:
none are skipped, marked expected-failure, or converted to success. They are
release gates for the coordinator's fixes. Stronger application proof schemas
may require updating fixture construction; preserve the behavioral assertions.

Passing checks establish role separation on all existing mutation routes,
cross-run approval rejection, real local Maya/Ravi stage receipts, immediate
missing-asset holding and fresh approval for recovery, retry persistence across
restart/invalidation, no completion from provider prose alone, provider error
body/credential redaction, stale Hotdata rejection, and Rote timeout cleanup.
The Maya/Ravi test is **practice**, not learned or replayed sponsor execution.

## Reproduced blockers

Locations below refer to the reviewed baseline. Run a single gate with
`python -m unittest cuepilot.tests.test_security_acceptance.SecurityAcceptanceTests.TEST_NAME -v`.
The concurrent gate uses `ConcurrentExecutionAcceptanceTests`; provider gates
use `cuepilot.tests.test_security_providers.ProviderSecurityAcceptance`.

| Gate / test suffix | Reproduction and observed result | Source and required behavior |
| --- | --- | --- |
| `recalled_rule_for_other_note_invalidates_pending_live_plan` | Ingest a supported current note; recall a verified Hydra result for a different note; validate current show; plan still succeeds. | `cuepilot/api.py:154–184`: sticky `supportedTemplate` survives inconsistent recall. Bind note, graph, recipe and source proof to the current plan; invalidate inconsistent proof. |
| `bridge_cannot_recall_before_ingestion` | Authenticated bridge recalls a queued run before ingestion. Adapter is invoked. | `cuepilot/api.py:471–477`: enforce prepare prerequisites before provider work. |
| `bridge_cannot_ingest_over_an_approved_rule` | Authenticated bridge ingests after approval. Adapter is invoked. | `cuepilot/api.py:463–469`: freeze approved rule provenance and reject wrong-phase tools. |
| `stale_show_blocks_before_starting_rote` | Approve Maya, change another asset/show revision, call bridge execute. Rote starts. | `cuepilot/api.py:487–499`: reject stale approval and require current postapproval validation before starting Rote. Per-cue checks do prevent later stage writes, but do not prevent this launch. |
| `stage_owner_conflict_blocks_before_starting_rote` | Maya owns intro; approved Ravi bridge execute still calls Rote. | `cuepilot/api.py:487–499`: claim the stage transactionally before external execution. Existing cue-level ownership prevents interleaved writes. |
| `bridge_cannot_report_live_completion_using_only_direct_stage_cues` | Approve a stub-prepared live run, call all three stage/cue endpoints directly, then verify: `ok=true`, no Rote/RocketRide execution. | `cuepilot/api.py:236–285,455–457,502–506`: live cue authorization must require an active execution claim; completion evidence must establish actual execution. Exact operator approval itself is still required. |
| `reused_request_id_alias_cannot_accidentally_advance_operator` | Commit step 0 with ID A, retry step 0 with B (original receipt returned), then `/advance` with B commits step 1. | `cuepilot/api.py:243–251`: an acknowledged alias must remain associated with the original cue, or be rejected. A subsequent retry must not advance. |
| `overlapping_bridge_execute_claims_rote_once` | Two overlapping `/tools/execute` calls for one approved run enter the Rote adapter twice. | `cuepilot/api.py:487–499`: one atomic execution claim; reject/join/reconcile competing requests without a second launch. |
| `conflicting_unavailable_rule_cannot_publish_a_verified_recipe` | Valid sequence and both unavailable→holding edges, plus unavailable-presentation→presentation `fallback_to`: `ingest_note` verifies and persists it. | `cuepilot/integrations/memory.py:112–144`: reject contradictory unavailable fallback targets before persistence; known safe edges do not cancel unsafe ones. |
| `cancelled_rote_operation_stops_and_reaps_its_process` | Cancel `_cli` after process creation. Fake process remains running and unreaped. | `cuepilot/integrations/rote.py:125–138`: clean up on cancellation, re-raise cancellation, and keep execution ownership until cleanup/reconciliation. Confirm descendant process termination in the real runtime. |

Each suffix above is prefixed with `test_` in the suite. Several blockers share a
root cause; the count is failing scenarios, not ten independently scored CVEs.
Do not treat a static scan as evidence that these behavior failures are resolved.

## Additional review gaps

- Public reads: the initial API deliberately exposes GET show, stage, runs and
  run detail without authentication (`cuepilot/api.py:404–422`), including notes
  and trace evidence. Publishing the complete port 8787 app also exposes docs
  and every route. The separate bridge must allow only intended authenticated
  tool endpoints and minimal health/redacted-run reads. Deny operator mutations,
  run enumeration, direct stage cues, docs and arbitrary proxy paths at the
  public boundary. This is a separate deployment boundary, not an unannounced
  change to the frozen local frontend contract. Bridge tests are owned elsewhere.
- Verify public responses and provider-success evidence contain no operator,
  bridge or sponsor keys, authorization headers, raw provider bodies, private
  notes, environment dumps or local paths. The error-redaction test covers two
  failure paths, not every public response or the teammate's browser bundle.
- `rocketride.mjs:133–142` checks only declared `Content-Length` before
  `response.json()`. A missing/incorrect length or chunked response bypasses its
  intended 1 MB bound. Count streamed bytes and cancel at the limit.
- `rote.py:167–173` checks receipt count, indices, `ok` and run ID but not scene
  order, unique IDs or stage revision progression. During learn, independently
  compare each captured receipt hash with the canonical API receipt; do not
  accept CLI exit zero or three generic success objects as replay evidence.
- The initial API has no operator cancel/hold route or explicit cancellation
  status in v1. Do not invent a frontend route. Coordinate an internal shutdown
  and interruption path that stops/reconciles external work, holds the stage,
  retains committed receipts, and invalidates resumability. Asset invalidation
  is the existing authorized interruption control.
- In-process locks do not establish cross-process ownership or recovery after a
  crash. Recheck persisted claims and receipts on restart. An HTTP timeout or
  lost response is not proof of zero stage changes; retry/reconcile by identity.
- No live Rote descendants, live RocketRide cleanup, public HTTPS bridge,
  browser bundle, provider quotas or actual sponsor account behavior were tested
  by this review. The coordinator owns those checks.

## Live Maya → Ravi → invalidation acceptance

Keep a private evidence directory with UTC timestamps, final commit ID, run IDs,
execution mode, plan hash, show revision and redacted sponsor operation receipts.
Do not paste tokens, provider request headers or raw model answers into reports.
Run on the coordinator's local service and approved bridge, one stage owner at a
time. Each phase below must use newly created runs, not an old completed run.

1. **Learn Maya.** Start a fresh Maya live run. Require actual Cognee dataset and
   graph export, note/source/graph digests, Hydra write-and-read provenance and
   the derived supported rule. Require actual Hotdata database/load/query
   evidence for the same show revision, speaker and asset. A configured key,
   valid schema or successful setup smoke alone does not satisfy this gate.
2. **Approve and execute.** Capture the displayed plan and exact operator
   approval. Revalidate after approval. Require authenticated RocketRide tool
   operations through the public bridge and canonical state after execution.
   Require actual Rote recording: successful child exits for the three cues,
   export, validation and an active learned-package digest. Record authored
   parameterization/dependency transformations as such.
3. **Match receipts.** Maya must have exactly ordered intro, presentation,
   holding receipts with matching run ID, distinct receipt IDs, increasing stage
   revisions and commit timestamps. Cross-check Rote captured hashes against
   canonical API receipts and current holding stage. Preserve original receipts
   even if later provider cleanup fails; report cleanup uncertainty separately.
4. **Replay Ravi.** Create and approve a fresh Ravi live plan with current
   memory/Hotdata evidence. Require one actual `rote play run` of Maya's learned
   package on Ravi's new run ID. Match all three Rote outcomes to Ravi's new API
   receipts and final holding state. Do not count helper calls, re-learning Ravi,
   an old run, practice results or adapter mocks as new-speaker replay.
5. **Invalidate presentation.** In a fresh approved Ravi run, mark slides
   missing before the presentation cue, using the current show revision. Stage
   must immediately become holding, subsequent presentation must be rejected,
   run must be blocked, and receipts must contain only already committed cues.
   Repeat with an unready speaker. Restoring readiness alone must not resume an
   old plan; prepare and approve a new run.
6. **Invalidate rule.** Change the production note or replace/invalidate its
   recalled graph/recipe provenance through the coordinator's authorized
   backend path. A previously approved/learned sequence must not run under that
   different rule. Require blocked status, holding and no new unsafe receipt.
   The frozen v1 API has no rule-edit endpoint; no successful rule-invalidation
   demo can be claimed until this backend path and proof binding are verified.
7. **Lose responses and compete.** Drop a response after one cue commits; retry
   with its original request ID, including after restart. It must return the
   exact same receipt without advancing stage revision. Reuse/conflict IDs and
   overlap execute requests; require a single driver. Attempt another speaker
   while the first owns the stage; reject before starting external execution.
8. **Interrupt and reconcile.** Interrupt Rote/RocketRide during execution and
   verify children/tasks are stopped or explicitly marked unconfirmed. Holding,
   durable partial receipts and a blocked/reconciliation result are required.
   A provider's generated "completed" message cannot override canonical state.

No speedup claim is supported without separately measured comparable runs. The
authored 1.5-second dwell is not an AI optimization. A completed practice run, a
validated pipeline or a configured sponsor is not evidence all sponsors ran.

## Dependency review and scan evidence

All six advisories in `SECURITY.md` remain open. The September 11 baseline JSON
lists `fixedIn: []` for every advisory. This review found no verified compatible
upgrade; it changed no manifest, lockfile, runtime package or ignore policy.

| Installed dependency / advisory | Verified review and remaining limitation |
| --- | --- |
| Cognee 1.5.4 / critical `17675444` | Installed version matches [PyPI](https://pypi.org/project/cognee/). Baseline has no fix/upgrade path; current advisory fetch was unavailable. Cloud HTTP use does not resolve the optional SDK finding. |
| DiskCache 5.6.3 / high `15268422` | [Snyk reports no fixed version](https://security.snyk.io/vuln/SNYK-PYTHON-DISKCACHE-15268422); [PyPI](https://pypi.org/project/diskcache/) matches installed. Keep caches private; this limits exposure, not the finding. |
| LiteLLM 1.96.2 / high `17391451` | Baseline has no fix/upgrade path; current advisory fetch was unavailable. |
| LiteLLM 1.96.2 / medium `17393717` | [Snyk reports no fixed version](https://security.snyk.io/vuln/SNYK-PYTHON-LITELLM-17393717). Avoid starting optional proxy/SSO/debug services. |
| LiteLLM 1.96.2 / medium `17393719` | [Snyk reports no fixed version](https://security.snyk.io/vuln/SNYK-PYTHON-LITELLM-17393719). Installed expiration checks alone do not clear the advisory. |
| adm-zip 0.6.1 / high `19276676` | [Snyk still flags it](https://security.snyk.io/vuln/SNYK-JS-ADMZIP-19276676). The [official 0.6.1 release](https://github.com/cthackers/adm-zip/releases/tag/v0.6.1) adds symlink extraction guards, which exist locally. Keep open pending reconciliation; not a confirmed false positive. |

Cognee declares `litellm>=1.83.7,<1.97.0`; installed 1.96.2 is the latest listed
below that cap in the [release history](https://pypi.org/project/litellm/).
Upgrading beyond the cap is not a verified compatible fix. Snyk's suggested
public RocketRide 1.3.0 differs from the required vendored staging client despite
their identical version strings. Do not replace it without staging compatibility
evidence and coordinator approval. Shared `python -m pip check` passed; that
checks dependency consistency, not vulnerability remediation.

Scan evidence is private under `sponsor-setup/snyk/reports/`. Initial source
attempts recorded `prerequisite_missing` (no worktree CLI) and `scan_failed`
(sandbox DNS failure). Reusing the authorized shared CLI and retrying with
network access completed Snyk Code with zero reported findings in
`20260911T200133.386764Z/`. This scan does not clear the acceptance failures or
dependency advisories.

Final `pnpm security:scan` completed at 20:28 UTC against the final test-source
state, with overall exit **1** (findings). Reports and exact commands are in
`20260911T202740.797912Z/`:

| Scope | Actual result |
| --- | --- |
| Snyk Code | Completed, exit 0, zero reported source findings. |
| Root Node dependencies | Completed, exit 1, 40 dependencies; the one high adm-zip advisory remains. |
| Optional memory Python dependencies | Completed, exit 1, 125 dependencies; the same five advisories remain (one critical, two high, two medium). |

`acceptance-20260911/tests.log` and `tests-summary.json` retain the final
`pnpm test:cuepilot` result and test-source hashes: **30 tests, 20 passed,
10 failed**. This is the unchanged application baseline plus the independent
acceptance suite, not a final integrated release result. Worktree-local tool
references reused the existing CLI and Python environment; no credentials or
runtime packages were copied or changed.

Before signoff, run `pnpm security:scan` against integrated final source and
inspect every mode's status and raw evidence. It scans the optional memory
manifest, so also scan `cuepilot/requirements.txt` separately for the application
runtime. A failed/unsupported/unauthenticated scan is incomplete, never clean.
Resolve applicable findings, rescan affected scope, and disclose remaining
advisories and live gaps. No numeric security score is supported.

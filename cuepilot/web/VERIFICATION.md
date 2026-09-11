# Frontend verification — 2026-09-11

## Current integrated status — 22:35 UTC

Full live API acceptance subsequently passed: Maya learned, Ravi replayed the
same package with three new receipts, and Alex stopped after one introduction
receipt. See [sponsor readiness](../../docs/sponsor-readiness.md) for the retained
evidence and working connections. The backend now passes 187 Python and 25
RocketRide Node tests. The latest full Snyk scan at 22:36 UTC reported zero source
findings and six open dependency advisories; see [SECURITY.md](../../SECURITY.md).
The browser checks below used practice plans; live API acceptance does not
retroactively make those browser checks live execution. Earlier credential and
tunnel blockers below are historical.

## Earlier demo machine integration — 21:05 UTC

Omkar's `d9c6ee0` frontend was integrated with the reviewed backend. Production
build and formatting pass. Nine transport tests and four operator tests pass;
the backend has 167 passing Python tests plus 22 RocketRide Node tests.

The coordinator ran the 10 API/proxy smoke checks against the actual local demo
service, then exercised the browser desk and separate stage: approve → introduce
→ cancel held with one receipt; missing presentation held another introduced
speaker with one receipt; restoring readiness did not resume that plan; a newly
approved recovery completed all three cues. Canonical evidence is private at
`cuepilot/.runtime/frontend-browser-acceptance.json`. These were practice plans,
not a claim of a completed five-sponsor pipeline.

Integration fixes bind each pending cue to its displayed step, retain uncertain
5xx responses for identical retries, omit unedited live notes, provide operator
cancel, and distinguish physical completion from verified sponsor completion.
Legacy pending intents without a step fail closed instead of guessing a new cue.

Snyk source completed with **zero reported findings** at 21:03 UTC after
clarifying a public fixture storage identifier and removing CLI-derived report
filenames. The frontend's **100 dependencies passed Snyk with no vulnerable
paths**. Reports: root `sponsor-setup/snyk/reports/20260911T210305.104186Z/` and
frontend `verification/security-reports/2026-09-11T20-59-12.915Z/`. The six
existing root/optional dependency findings remain open. No ignore policy was used.

Hotdata independently passed real API load/query/reuse/invalidation checks.
Full live acceptance still needs Cognee/model credentials and a working public
HTTPS bridge. Cloudflare tunnel allocation timed out despite authorization.

The following is Omkar's original isolated verification, preserved for context;
its scanner/authentication blockers were subsequently resolved on this machine.

## Original frontend verification

Repository baseline: `528323df847d3ce45ded1d8f1c2d78ce10d3756e`.
Frontend branch: `codex/operator-desk`. Scope: `cuepilot/web/` only.
The backend, shared contract, sponsor adapters, and backend tests were not edited.

## Completed

| Check | Observed result |
| --- | --- |
| `pnpm build` | TypeScript and Vite production build pass. |
| `pnpm verify:transport` | 5 checks pass: frozen request paths/fields, exact plan hash, no client Authorization header, backend error preservation, no fixture fallback, uncertain-request retention across reload/current-run change, and fresh IDs for subsequent cues. |
| `node verification/api-smoke.mjs` | 10 checks pass against the unchanged backend through Vite, using isolated local data. Raw receipts are in ignored `verification/api-checks.json`. |
| Real API rehearsal in browser | Maya: create practice run → preview → approve → intro → presentation → holding. Three actual receipts; both browser routes read shared API state. |
| Missing asset in browser | Ravi: approve → intro → turn presentation off. PATCH returned revision 2, run became blocked, stage changed immediately to holding at revision 5 with the backend's unavailable-presentation reason. |
| Rejected action in browser | Creating a recovery plan with the presentation still missing returned the backend rejection and displayed its message. |
| Recovery in browser | Restored asset (show revision 3), created a new plan, approved its new hash, then completed all three cues. Stage revisions 6–8 and three backend receipts. |
| Duplicate request | Same requestId returned an identical receipt and left stage state and receipt count unchanged. A fresh requestId advanced the next cue. |
| Authorization | Direct API mutation without a token returned 401. Same-origin proxy mutations succeeded without browser credentials. Cross-origin proxy writes returned 403. |
| Revision and approval rejection | Stale expectedRevision and incorrect planHash returned 409; unapproved cue also returned 409. |
| Token isolation | Compared the actual ignored local token against every built asset. No token value or `CUEPILOT_OPERATOR_TOKEN` identifier in the browser bundle. |
| Disconnected API in browser | Stopped the isolated backend. Local API remained selected, operator writes disabled, and stage retained its last state with “Output is not confirmed current.” No fixture substitution. |
| Explicit fixture flow in browser | Selected Fixture data, created and approved a plan, completed all three cues. `/stage?source=fixture` shared the fixture state across tabs and always displayed its fixture label. |
| Keyboard and visual review | Used keyboard activation for approval, next cue, speaker radios, and run mode. All rendered buttons have text or an accessible name; native controls have labels and visible focus styling. No horizontal overflow at 1280px. Inspected operator, intro, presentation, holding, and blocked-stage output. |
| Supplementary `pnpm audit` | Completed with 0 reported vulnerabilities in the current frontend dependency graph. This is **not** a Snyk result. Raw report: ignored `verification/pnpm-audit.json`. |

The API smoke verification creates synthetic practice runs; it is not sponsor
execution. The unit checks simulate network outcomes and do not claim backend
receipts. Browser and API checks above use real local backend responses.

## Still blocked / unverified

- **Sponsor-backed rehearsal and Rote replay:** a real live preparation request
  returned `queued`, then `blocked`, with a RocketRide `prepare` trace and a setup
  failure reason. The UI displays exactly that evidence. No verified sponsor
  rehearsal, real Rote replay, streaming integration, or deployment is claimed.
- **Snyk Code:** automatic approval review rejected the source scan because it
  uploads eligible private frontend source to Snyk. The scan did not execute.
  Explicit authorization for that source upload is still needed.
- **Snyk dependencies:** the initial attempt needed Snyk's workspace flag. The
  corrected dependency-only attempt reached Snyk and returned **401 Unauthorized**
  (`SNYK-0005`). Snyk authentication is required. This is an incomplete scan,
  not a clean result. The exact command and output are retained in ignored
  `verification/security-reports/2026-09-11T20-06-11.130Z/`.
- **Authenticated final-demo-machine verification:** the generated operator token
  and SQLite database belong only to the isolated local check. The final demo
  machine needs its privately shared operator token in Vite's server environment
  and verified sponsor setup before the live checkpoints can pass.

## Upstream findings remain open

The pulled repository's [`../../SECURITY.md`](../../SECURITY.md) reports these six
unresolved advisories in the root/optional sponsor dependency environments. They
were not introduced by this frontend package, were not rescanned successfully on
this machine, and were not changed outside frontend ownership:

| Dependency | Severity | Upstream reported advisory |
| --- | --- | --- |
| cognee 1.5.4 | Critical | SNYK-PYTHON-COGNEE-17675444 |
| diskcache 5.6.3 | High | SNYK-PYTHON-DISKCACHE-15268422 |
| litellm 1.96.2 | High | SNYK-PYTHON-LITELLM-17391451 |
| litellm 1.96.2 | Medium | SNYK-PYTHON-LITELLM-17393717 |
| litellm 1.96.2 | Medium | SNYK-PYTHON-LITELLM-17393719 |
| adm-zip 0.6.1 | High | SNYK-JS-ADMZIP-19276676 |

The upstream note about potential adm-zip advisory metadata lag is not a
confirmed false positive; its finding remains open. No vulnerability ignores
or severity filters were added. Snyk source and dependency checks must be rerun
on the final state after authorization/sign-in; use `pnpm security:scan` here.

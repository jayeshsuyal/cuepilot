# Hackathon security status

Latest application checks: September 11, 2026, Snyk CLI 1.1307.2,
organization `jksuyal`.

- Integrated CuePilot source: completed with 0 reported findings at 20:48 UTC.
  The full scan covered the integrated API, adapters, bridge and acceptance runner
  as supported by Snyk. Earlier credential-placeholder and report-filename
  findings were fixed without suppressions.
- CuePilot Python runtime (`cuepilot/requirements.txt`): 19 dependencies scanned,
  0 reported vulnerabilities. The application uses Cognee Cloud over HTTP, so this
  manifest does not require the optional local Cognee SDK.
- Node and optional sponsor-setup dependencies: the final full scan at 20:48 UTC
  still reports the six advisories below. Its overall exit is 1 because findings
  remain; it is not a clean full security result.

The earlier sponsor-setup baseline at 17:53–17:54 UTC was:

| Scan | Result | Scope |
| --- | --- | --- |
| Snyk Code | Completed, 0 reported issues | 8 Python files and 2 JavaScript modules; supported source selected using existing Git exclusions |
| Node dependencies | Completed, 1 unique high issue | Root pnpm project, 40 dependencies, including development dependencies |
| Python dependencies | Completed, 5 unique issues | Installed memory environment, 125 dependencies: 1 critical, 2 high, 2 medium |

The dependency findings remain open. Authentication errors, unsupported files and
scan failures must never be described as clean. A successful static scan is not
a guarantee that the application contains no vulnerabilities.

## Outstanding dependency advisories

| Dependency | Severity | Snyk advisory |
| --- | --- | --- |
| cognee 1.5.4 | Critical | [Arbitrary Code Injection](https://security.snyk.io/vuln/SNYK-PYTHON-COGNEE-17675444) |
| diskcache 5.6.3, through Cognee | High | [Deserialization of Untrusted Data](https://security.snyk.io/vuln/SNYK-PYTHON-DISKCACHE-15268422) |
| litellm 1.96.2, through Cognee | High | [Insufficient Session Expiration](https://security.snyk.io/vuln/SNYK-PYTHON-LITELLM-17391451) |
| litellm 1.96.2, through Cognee | Medium | [Missing Authentication for Critical Function](https://security.snyk.io/vuln/SNYK-PYTHON-LITELLM-17393717) |
| litellm 1.96.2, through Cognee | Medium | [Insufficient Session Expiration](https://security.snyk.io/vuln/SNYK-PYTHON-LITELLM-17393719) |
| adm-zip 0.6.1, through RocketRide | High | [Symlink Attack](https://security.snyk.io/vuln/SNYK-JS-ADMZIP-19276676) |

The captured Snyk reports list no fixed versions for these six advisories.
Review supported upgrades or changes that actually remove an affected feature
or dependency; do not exclude installed dependencies to manufacture a pass.

The adm-zip finding needs sponsor verification: its
[0.6.1 release](https://github.com/cthackers/adm-zip/releases/tag/v0.6.1), released
on September 11, explicitly adds symlink protection to archive extraction, and
those guards are present locally. Snyk still flags the installed version. This
may be advisory metadata lag; it is not a confirmed false positive. Keep the
finding open until reconciled. Do not replace the mandated staging RocketRide
SDK with an incompatible public client just to change a scanner result.

The Cognee advisory describes unsandboxed notebook-cell execution through an
HTTP API. The prepared demo and Cloud client do not start that server or call
that endpoint. The LiteLLM findings concern its proxy/SSO routes; those services
are not started by either prepared workflow. This is usage-scope evidence, not
proof that the flagged packages or hosted service are secure. DiskCache's issue
involves loading an attacker-modified cache; its finding also remains open.

No compatible, verified upgrade was found for the five Python advisories.
CuePilot's Cloud adapter now uses HTTP calls and its minimal runtime manifest was
scanned separately. The installed optional setup environment still includes the
local SDK and its findings remain disclosed here.

## Evidence and repeatability

The baseline's raw logs, JSON, SARIF and exact commands are in the private,
Git-ignored directory `sponsor-setup/snyk/reports/20260911T175330.727446Z/`.
The latest integrated source and dependency evidence is in
`sponsor-setup/snyk/reports/20260911T204805.537064Z/`; the application Python
dependency report is in `sponsor-setup/snyk/reports/cuepilot-app-dependencies/`.
The separate `mcp-readiness.json` receipt verifies the local server handshake
and discovery of code and dependency tools; it does not claim an MCP scan ran.

Use `pnpm security:scan` before submission, and the individual source/dependency
commands after relevant changes. Retain results from before and after fixes.
The code scan uploads eligible source to Snyk; dependency scans send resolved
dependency metadata. Existing exclusions protect credentials and local runtime
data. No vulnerability ignore rules were added.

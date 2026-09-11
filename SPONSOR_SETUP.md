# Compound Agents hackathon — sponsor setup

This workspace contains the five sponsor tools and repeatable checks for the
September 11, 2026 hackathon. Setup status below describes tested behavior, not an
integrated application. All accounts are intended for `jksuyal@gmail.com`.

| Sponsor | Verified here | Remaining |
| --- | --- | --- |
| Cognee 1.5.4 | Local bundled graph import and recall passed twice (47 nodes, 86 edges); Cloud code redeemed for $35 | Save the existing Cloud URL/key and run the synthetic Cloud ingestion test; hosted ingestion remains unverified |
| HydraDB | Local server running; graph write, relationship traversal, Bolt and HTTP reads passed | Ready for application integration |
| hotdata.dev 0.33.0 | Official CLI installed; browser sign-in completed; synthetic CSV and verified SQL-result checker prepared | Account activation requests a credit card; CLI authorization and cloud test remain blocked |
| RocketRide staging | VS Code extension 1.2.0; matching staging SDK 1.3.0; authenticated connection; 151 component schemas and catalog; echo pipeline validation passed | No positive compute credit balance; event promo code needed before execution |
| Modiqo/Rote 0.82.0 | Google sign-in; official Play 0.4.98 installed for Codex; Hello warm-up passed all 9 steps | Connect the application API chosen for the actual project |
| Snyk 1.1307.2 | Official ARM64 CLI installed and authenticated; Snyk Code enabled; project MCP configuration and server handshake verified; source and dependency scans completed | Six dependency findings remain; restart the Codex MCP connection to load its tools |

Rote's normal-environment preflight also passed all seven checks. Restart Codex
after finishing any pending browser sign-in to load the newly installed Play
plugin and skills. CLI checks already work in this workspace.

## Check the workspace

```sh
pnpm sponsors:check       # offline inventory; never prints credentials
pnpm sponsors:check:live  # read-only health/auth checks
pnpm check:rocketride    # authenticated staging connection
pnpm check:rocketride:pipeline # validates echo pipeline; runs only with existing credits
pnpm check:cognee        # bundled keyless graph demo
pnpm check:cognee:cloud  # fresh synthetic Cloud ingestion/recall; requires Cloud credentials and credits
pnpm check:hydradb       # writes two synthetic graph nodes and reads their edge
pnpm check:rote          # reruns the official pinned Hello Play
```

The doctor distinguishes installation, credential presence, connection checks,
and completed demos. A successful offline inventory does not certify a working
five-tool application.

## Security baseline

Snyk scanned the setup on September 11, 2026. Static analysis covered 10 supported
source files (8 Python, 2 JavaScript modules) and reported zero issues. The Node
dependency scan found 1 high issue; Python found 1 critical, 2 high, and 2 medium
issues. These are six unique dependency advisories, not a clean submission result.

```sh
pnpm security:code    # first-party source
pnpm security:node    # pnpm dependencies, including development dependencies
pnpm security:python  # installed Cognee/HydraDB Python dependencies
pnpm security:scan    # all three; saves timestamped evidence
```

[Security baseline and outstanding findings](SECURITY.md) documents the results.
[Snyk setup](sponsor-setup/snyk/README.md) explains authentication, scan scope and
reports. Project instructions in `AGENTS.md` require scans during development,
after fixes and against the final submission. No finding was suppressed.

## Finish account/model prerequisites

### Hotdata

Browser sign-in has completed. Hotdata currently redirects CLI authorization to
**Activate your account**, which requires adding a credit card and says usage
beyond free credits will be billed. No card was added and no paid account was
activated by this setup. Ask the Hotdata event team to activate the hackathon
account, or complete the billing activation yourself. Once activated:

```sh
pnpm login:hotdata
./sponsor-setup/hotdata/bin/hotdata workspaces list
./sponsor-setup/hotdata/bin/hotdata workspaces use <workspace-id>
pnpm check:hotdata
```

The cloud smoke test creates a database with a one-hour expiry, uploads only
three synthetic ticket rows, and queries it. Expected counts: 3 total, 2 resolved.
It does not upload the hackathon guide or personal files.

### Cognee

Cognee Cloud sign-in and credit redemption are complete. `DATAANDAI35` added
**$35** through a **$0 checkout**. The recorded balance after redemption was
**$44.99 of $45** including starter credits; auto recharge was off. This confirms
account credits, not a successful hosted ingestion run.

The API Keys page already has a default key. Copy its value and the actual tenant
API URL privately into the existing workspace `.env`, preserving RocketRide's
entries:

```dotenv
COGNEE_SERVICE_URL=
COGNEE_API_KEY=
```

Once those credentials are saved, run:

```sh
pnpm check:cognee:cloud
```

The Cloud test uploads only the synthetic `sample.txt` into a uniquely named
verification dataset, requires completed ingestion, and verifies all three
sample statements through graph recall. It consumes prepaid Cloud processing
credits and uses keyword retrieval to avoid an extra answer-generation call.
Sanitized receipts are saved in the Git-ignored
`sponsor-setup/memory/cloud-receipts/` directory. The doctor reports a verified
saved Cloud receipt only when its parsed status and both ingestion/recall flags
confirm success; it never reruns the test or treats credential presence as proof.
Cloud ingestion has not yet been executed.

Cloud mode uses `cognee.serve()` and the hosted tenant's model configuration; it
does not require a local `LLM_API_KEY`. Actual provider availability is confirmed
only by the smoke test. See the [Cloud SDK guide](https://docs.cognee.ai/cognee-cloud/connections/cloud-sdk)
and [credit billing documentation](https://docs.cognee.ai/cognee-cloud/functionality/account-and-billing).

For the separate local extraction path, set `LLM_API_KEY` in `.env`. The default
provider is OpenAI; alternatives also need matching `LLM_*` and `EMBEDDING_*`
settings. Then run:

```sh
sponsor-setup/memory/.venv/bin/python sponsor-setup/memory/cognee_ingest.py
```

The local test sends the synthetic `sample.txt` to the configured model provider and
may consume that provider's credits. The keyless demo uses a bundled graph and
does not verify new-text LLM extraction.

## RocketRide workflow

The official initialization completed against `https://staging.rocketride.ai`.
It created the private `.env`, `.rocketride/services-catalog.json`, 151 schemas,
platform packages, and local documentation. The bootstrap SDK archive is identical
to the client served by staging. Read `.rocketride/docs/ROCKETRIDE_README.md` before
building; use pnpm and the documented app scaffolder.

The VS Code workspace settings select the custom staging endpoint. CLI and IDE
OAuth sessions are separate; sign in and Save inside RocketRide's VS Code settings
to use the IDE monitor. Use a scratch app to claim your team's developer ID before
creating the real app. No team namespace, paid plan, or public app was created.

The synthetic echo pipeline passed staging validation with zero warnings. Its
execution test found no positive compute credit balance and did not start a task.
Redeem the event promo code on staging, then run `pnpm check:rocketride:pipeline`.
The test uses no LLM, sends one synthetic string, and terminates its task. No
credit purchase or promo redemption has been performed.

## Local database lifecycle

HydraDB runs in a Colima profile named `hackathon` (2 CPUs, 2 GiB RAM). It exposes
only localhost ports 7687, 8443, and 9090. Its data and generated token live in the
ignored `sponsor-setup/memory/.hydradb/` directory.

```sh
# Resume after a restart
colima start hackathon --activate=false --ssh-config=false
bash sponsor-setup/memory/hydradb_start.sh

# Stop while preserving data
docker --context colima-hackathon stop hackathon-hydradb
colima stop hackathon
```

## Sponsor-specific details

- [Cognee and HydraDB](sponsor-setup/memory/README.md)
- [Hotdata](sponsor-setup/hotdata/README.md)
- [RocketRide](sponsor-setup/rocketride/README.md)
- [Rote and Play](sponsor-setup/rote/README.md)

Credentials and runtime data are Git-ignored. Package locks, setup scripts,
synthetic fixtures, and documentation are ready to version. Cognee currently
uses its local graph backend; connecting Cognee's extracted graph to HydraDB and
orchestrating the five-tool application remains project implementation work.

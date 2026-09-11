# Hotdata setup

Installed locally: **Hotdata CLI 0.33.0 for macOS arm64** in `bin/hotdata`.
The binary came from the official GitHub release and its SHA-256 matched the release checksum:
`86898dbf3b4b75505f94d3a81cd6ae7a7c30e2fb8df3c0a8b0bfd6e1b5a9d95c`.
No Homebrew packages or global shell settings were changed.

## Finish authentication

Browser sign-in is now complete for the authorized account, and its workspace
exists. However, CLI authorization redirects to **Activate your account**:
Hotdata asks for a credit card and says charges apply beyond free credits.
The CLI still reports `Authenticated: No`. No payment method was added.
Ask the event's Hotdata team to activate the hackathon account, or complete
billing activation yourself, then run the login commands below again.

The [official event page](https://luma.com/qisv9xmg) advertises free credits for
first-time users; its sign-up link leads to the normal Hotdata login page.
No alternative activation control was visible in this account.

From the hackathon workspace:

```bash
./sponsor-setup/hotdata/bin/hotdata auth login
./sponsor-setup/hotdata/bin/hotdata auth status
./sponsor-setup/hotdata/bin/hotdata workspaces list
```

Login opens the browser. If you do not have an account, the official registration command is
`./sponsor-setup/hotdata/bin/hotdata auth register` (GitHub OAuth by default; `--email` uses email and password).
Registration has not been started by this setup.
If you have several workspaces, select the intended one with
`./sponsor-setup/hotdata/bin/hotdata workspaces use <workspace_id>`.

Alternatively, supply `HOTDATA_API_KEY` and `HOTDATA_WORKSPACE` through the process environment
or a local `.env` file. The CLI also supports `--workspace-id` explicitly.
Tokens should never be pasted into committed files or passed using the `--api-key` argument,
which can expose them in shell history and process listings.
The CLI normally stores browser login settings in `~/.hotdata/config.yml`.

## Query smoke test

After login, run:

```bash
bash sponsor-setup/hotdata/smoke.sh
```

This performs a read-only workspace check, creates a uniquely named temporary instant database
with a one-hour expiry, uploads the three synthetic rows from `data/tickets.csv`, and executes SQL.
Expected query result: `total_rows = 3`, `resolved_rows = 2`.
The script checks the actual returned JSON counts and writes
`reports/smoke-success.json` only after an exact successful result.
It uses cloud storage and query execution on your selected Hotdata workspace; run it against the
hackathon workspace/credits. It does not upload any project or personal data.

The installed CLI explicitly accepts CSV, JSON, and Parquet loads. Some older documentation
still says to convert CSV into Parquet first; that limitation does not appear in 0.33.0's CLI help.

For live data, add a source using `hotdata ingest sources add`, provide the source-specific
connection details/credentials, then create an ingest targeting a database. The authoritative
configuration schema is available through `hotdata ingest sources types` and
`hotdata ingest sources fields <family>`.

## Verification recorded during setup (2026-09-11)

- Release archive checksum: matched.
- `hotdata --version`: `hotdata 0.33.0`.
- CLI help for login, registration, workspace selection, create/load/query: passed.
- `hotdata auth status`: `Authenticated: No`.
- `hotdata --no-input query 'SELECT 1 AS ok' -o json`: exited 1 because no workspace/login was configured.
- `bash -n smoke.sh`: passed. Running it stopped at the read-only workspace check with `session expired or revoked`; its attempt to lock the global CLI session was also blocked by the sandbox.
- The live create/load/query smoke test has **not passed yet**; authentication and a workspace are required.
- No remote database was created by setup.

## Official references

- [Quick start](https://www.hotdata.dev/docs/quick-start)
- [CLI repository](https://github.com/hotdata-dev/hotdata-cli)
- [Pinned release v0.33.0](https://github.com/hotdata-dev/hotdata-cli/releases/tag/v0.33.0)
- [API authentication and workspace model](https://www.hotdata.dev/docs/core-concepts)
- [External data source setup](https://www.hotdata.dev/docs/pull-data)
- [Dashboard](https://app.hotdata.dev)

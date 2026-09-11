# Snyk security scans

The runner uses the local official CLI at `bin/snyk` and Organization `jksuyal`.
The setup targets Snyk CLI 1.1307.2 for macOS ARM64. Authenticate the CLI with
`sponsor-setup/snyk/bin/snyk auth` before scanning; Snyk Code must also be enabled
for this Organization. CLI scanning does not require a GitHub integration.

Verified on September 11: CLI checksum and version, OAuth authentication, Snyk
Code enablement, three completed scans, and the local MCP server handshake with
code/dependency tools. The baseline has zero source findings and six dependency
advisories; see the workspace `SECURITY.md`. Authentication and installation are
complete; the outstanding dependency findings are not resolved.

`.codex/config.toml` contains a project-scoped Snyk MCP connection using the
absolute CLI path, the lite profile, organization `jksuyal`, and a five-minute
tool timeout. Codex recognizes the configuration. Restart its MCP connection to
load the new tools in the active client; the CLI scripts work immediately.
`codex-mcp.toml` is a reference copy and needs local absolute paths when used by a
teammate. MCP trust protection remains enabled.

From the workspace root:

```bash
python3 sponsor-setup/snyk/scan.py code
python3 sponsor-setup/snyk/scan.py node
python3 sponsor-setup/snyk/scan.py python
python3 sponsor-setup/snyk/scan.py all
```

`--help` is offline and does not start a scan. `--timeout SECONDS` sets the limit
for each scan (default 600). The script resolves paths relative to itself, so it
also works when invoked from a different directory.

| Mode | Scope |
| --- | --- |
| `code` | Workspace source files, using `snyk code test` and the existing `.gitignore` rules |
| `node` | Root `package.json` / `pnpm-lock.yaml`, including development dependencies |
| `python` | `sponsor-setup/memory/requirements.txt`, resolving installed dependencies with `memory/.venv/bin/python` |
| `all` | Runs all three independently, including after findings or a failed scan |

The Python environment must already contain the complete dependency installation.
Missing dependencies are not silently skipped. The source scan honors the
existing exclusions for credentials, local data, downloaded tools and virtual
environments; no additional finding ignores are added. New application source
must remain outside ignored directories to be included in the source scan.

Each invocation creates a private timestamped directory under `reports/` with:

- Raw combined stdout/stderr logs per scan.
- Code SARIF and dependency JSON reports when Snyk produces them.
- `summary.json` with the exact command, scope, timestamps, elapsed time, raw
  Snyk exit code, status, and report-file availability for each attempt.

The runner exits `0` only when every requested scan completed with no findings,
`1` when completed scans found issues, and `2` if any scan could not complete.
Individual Snyk exit codes are preserved: `0` no findings, `1` findings, `2`
failure, `3` no supported project. Missing prerequisites, launch failures and
timeouts have a null Snyk exit code and a distinct status. A missing report file
alone never means clean; the raw Snyk exit code determines the scan status.
All findings remain in the evidence; there are no severity filters or automatic
fixes. Reports are local and gitignored; they can contain source references and
dependency details.

Scans contact Snyk: dependency scans send dependency graphs and project metadata;
Code uploads eligible source files for analysis. The runner does not read `.env`
or publish dashboard projects (`--report` / `monitor` are not used). Existing CLI
authentication and shell environment apply normally. No scan success is claimed
until the actual command and its exit status have been reviewed.

Official references:

- [CLI installation](https://docs.snyk.io/developer-tools/snyk-cli/install-the-snyk-cli)
- [Authentication](https://docs.snyk.io/developer-tools/snyk-cli/authenticate-to-use-the-cli)
- [Code scan behavior and exclusions](https://docs.snyk.io/scan-with-snyk/snyk-code)
- [Code command and exit codes](https://docs.snyk.io/developer-tools/snyk-cli/commands/code-test)
- [Dependency scan command](https://docs.snyk.io/developer-tools/snyk-cli/commands/test)
- [Python environment requirements](https://docs.snyk.io/supported-languages/supported-languages-list/python/snyk-cli-for-python)
- [Snyk data handling](https://docs.snyk.io/snyk-data-and-governance/how-snyk-handles-your-data)

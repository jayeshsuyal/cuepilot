# Modiqo Rote and Play

The official Rote **0.82.0** Apple Silicon CLI and companion daemon are installed in `bin/`.
Its release archive passed the sponsor's SHA-256 check. The `rote` launcher keeps all Rote
state inside this directory's ignored `.runtime/`. The official Playoffs installer also
installed **Play 0.4.98** into Codex, including its skills and prompt hook.

## Current verification

- `./rote --version`: passed (`rote 0.82.0`).
- `./rote info`: confirms that adapter, Play, workspace, and token paths are local.
- Official `modiqo/hello@0.2.2` contract: inspected successfully against the live registry.
  It declares nine process steps, public/local reads, no writes or adapter keys, and Python 3.
  Python 3 is present on this machine.
- Rote Google sign-in: verified for the user-authorized account.
- Hello execution: **9/9 stages OK**. Historical receipt: `reports/hello-success.json`;
  full live-source output: `reports/hello-output.txt`.
- Official Codex plugin install and all seven preflight checks: passed, including a second
  check from the normal shell environment without the installer's temporary PATH.
- A project-specific target API connection is still to be selected and verified.

## Use it

Run these from this directory:

```sh
python3 check.py
# Optional repeat of the already completed warm-up:
python3 smoke.py
./play --help
./play preflight --harness codex --json
```

Restart Codex to load newly installed skills and its Play plugin, then use `$play`.
No activation script or global Python override is needed. `smoke.py` runs the exact
inspected Hello version and saves a local success receipt only when the command succeeds
and reports all nine stages OK. Review its stage report for any degraded public sources.

`./rote setup` can connect the chosen application API. Before accepting its
agent-wiring steps, select the actual target API and check its requested scopes. The
hackathon project has not selected a target application yet, so no unrelated account API
has been connected.

## Tooling boundaries

The official Playoffs installer ran after the user authorized it and sign-in completed.
It installed `play@play-skills` for Codex, personal Rote skills, the Play runtime under
`~/.local/share/modiqo/play/skill`, and the Play-only Codex prompt hook. It preserved unrelated
settings and created a recovery snapshot. Its report is
`.play-bootstrap/runs/20260911T152643816369Z.json`.

The installer initially verified only within its temporary environment. To make normal
Codex use reliable, `configure-play-launchers.py` bound its four Play-only launchers and
prompt hook to this project's pinned Python environment and authenticated Rote wrapper.
No system Python or global PATH was changed. Backups and the binding receipt are in
`.play-bootstrap/` and `reports/play-launcher-binding.json`. The normal-environment check
is `reports/play-default-environment-preflight.json` (`ready: true`). Keep this workspace
directory in place while those launchers use its runtimes.

Browser automation and optional Tulving recurring Plays are not installed; neither is
needed by the verified Hello/API workflow setup. No recurring job was enabled.

`./install-local.sh` reinstalls the same pinned, checksummed official binary. The full
official guided installer is `curl -fsSL https://getrote.dev/playoffs/install.sh | sh`;
it installs Play into detected coding apps and may update personal configurations. For
updates, retain the workspace Python/Rote environment and rerun the focused launcher binding
afterward if the official installer replaces its generated launchers.

## Official sources

- [Rote release repository](https://github.com/modiqo/rote-releases)
- [Rote v0.82.0 release](https://github.com/modiqo/rote-releases/releases/tag/v0.82.0)
- [Playoffs setup and warm-up guide](https://www.modiqo.ai/blog/the-playoffs/enter-the-arena)
- [Run your first Play](https://www.modiqo.ai/docs/run-your-first-play)
- [Hello Play](https://play.modiqo.ai/modiqo/hello)
- [Full Play installer repository](https://github.com/modiqo/play)

The Playoffs guide describes a separate September 1–6 event. Its tooling setup is useful
for the September 11 hackathon, but its competition deadlines do not replace this
hackathon's supplied requirements. No Discord message or public submission has been sent.

#!/usr/bin/env python3
"""Check the isolated Rote CLI without printing identity or credentials."""
import json
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parent
CLI = ROOT / "rote"


def capture(*args):
    return subprocess.run(
        [str(CLI), *args], cwd=ROOT, text=True, capture_output=True, timeout=30
    )


def check():
    if not (ROOT / "bin" / "rote").is_file():
        return {"cli_installed": False, "signed_in": False}
    version = capture("--version")
    identity = capture("whoami")
    identity_text = identity.stdout + identity.stderr
    # v0.82.0 returns exit status 0 even for 'error: Not logged in'.
    signed_in = bool(re.search(r"(?m)^ok:\s+\S+", identity_text))
    return {
        "cli_installed": version.returncode == 0,
        "version": version.stdout.strip(),
        "signed_in": signed_in,
        "state_directory": str(ROOT / ".runtime"),
        "hello_play": "https://play.modiqo.ai/modiqo/hello@0.2.2",
        "warmup_recorded": (ROOT / "reports" / "hello-success.json").is_file(),
        "next_step": (
            "Connect the chosen project API" if signed_in and (ROOT / "reports" / "hello-success.json").is_file()
            else "Run smoke.py" if signed_in
            else "Run ./rote login --provider google (or github)"
        ),
    }


if __name__ == "__main__":
    result = check()
    print(json.dumps(result, indent=2))
    sys.exit(0 if result["cli_installed"] and result["signed_in"] else 2)

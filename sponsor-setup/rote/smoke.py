#!/usr/bin/env python3
"""Run the inspected, pinned official read-only Hello Play after sign-in."""
from datetime import datetime, timezone
import json
import re
import subprocess
import sys

from check import CLI, ROOT, check

HELLO = "https://play.modiqo.ai/modiqo/hello@0.2.2"

if not check()["signed_in"]:
    print("Rote sign-in is required. Run ./rote login --provider google (or github).")
    sys.exit(2)

# Run outside any Rote execution workspace; Rote creates the DAG workspace.
# Hello's inspected contract declares only local/public reads, no writes or keys.
result = subprocess.run(
    [str(CLI), "play", "run", HELLO, "--yes"], cwd=ROOT,
    text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
)
print(result.stdout, end="")
if result.returncode:
    sys.exit(result.returncode)

reports = ROOT / "reports"
reports.mkdir(exist_ok=True)
(reports / "hello-output.txt").write_text(result.stdout)
summary = re.search(r"Summary:\s*(\d+)/(\d+) completed,\s*0 failed,\s*0 blocked", result.stdout)
summary_ok = bool(summary and summary.group(1) == summary.group(2))
# Hello 0.2.2 uses its custom nine-stage presentation instead of the generic DAG summary.
hello_ok = bool(re.search(r"(?m)^\s*readings\s+.*\b9/9 ok\s*$", result.stdout))
hello_stages = result.stdout.split("\nSTAGES\n", 1)
hello_ok = hello_ok and len(hello_stages) == 2 and len(re.findall(r"(?m)^\s*[█]+\s+.+\s+ok\s*$", hello_stages[1])) == 9
if not (summary_ok or hello_ok):
    print("The CLI exited successfully, but its all-steps-completed summary was not found.")
    print("Review reports/hello-output.txt; no warm-up success receipt was created.")
    sys.exit(3)
(reports / "hello-success.json").write_text(json.dumps({
    "play": HELLO,
    "completed_at": datetime.now(timezone.utc).isoformat(),
    "exit_code": result.returncode,
}, indent=2) + "\n")

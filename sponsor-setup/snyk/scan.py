#!/usr/bin/env python3
"""Run explicit Snyk scans and preserve local evidence for every attempt."""

import argparse
import json
import os
from pathlib import Path
import subprocess
import time
from datetime import datetime, timezone


HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
MEMORY = ROOT / "sponsor-setup" / "memory"
SNYK = HERE / "bin" / "snyk"
ORG = "jksuyal"
MODES = ("code", "node", "python")


def now():
    return datetime.now(timezone.utc).isoformat()


def scan_spec(mode, output):
    common = [str(SNYK)]
    if mode == "code":
        return ROOT, common + [
            "code", "test", ".", "--org=" + ORG,
            "--sarif-file-output=" + str(output / "code.sarif")
        ], [ROOT / ".gitignore"], output / "code.sarif"
    if mode == "node":
        return ROOT, common + [
            "test", "--org=" + ORG, "--file=pnpm-lock.yaml", "--dev",
            "--json-file-output=" + str(output / "node.json")
        ], [ROOT / "package.json", ROOT / "pnpm-lock.yaml"], output / "node.json"
    return MEMORY, common + [
        "test", "--org=" + ORG, "--file=requirements.txt", "--package-manager=pip",
        "--command=" + str(MEMORY / ".venv" / "bin" / "python"),
        "--json-file-output=" + str(output / "python.json")
    ], [MEMORY / "requirements.txt", MEMORY / ".venv" / "bin" / "python"], output / "python.json"


def run_scan(mode, output, timeout):
    cwd, command, required, report = scan_spec(mode, output)
    log = output / (mode + ".log")
    result = {
        "mode": mode,
        "started_at": now(),
        "cwd": str(cwd),
        "command": command,
        "log": str(log),
        "report": str(report),
        "exit_code": None,
    }
    started = time.monotonic()
    with log.open("w", encoding="utf-8") as stream:
        missing = [str(path) for path in required if not path.is_file()]
        if not SNYK.is_file() or not os.access(SNYK, os.X_OK):
            missing.append(str(SNYK) + " (executable required)")
        if missing:
            result["status"] = "prerequisite_missing"
            stream.write("Missing prerequisites:\n" + "\n".join(missing) + "\n")
        else:
            try:
                # No shell, credential loading, result filtering, or dashboard publishing.
                completed = subprocess.run(
                    command, cwd=cwd, stdout=stream, stderr=subprocess.STDOUT,
                    stdin=subprocess.DEVNULL, timeout=timeout, check=False,
                )
                result["exit_code"] = completed.returncode
                result["status"] = {
                    0: "completed_no_findings",
                    1: "completed_with_findings",
                    2: "scan_failed",
                    3: "no_supported_projects",
                }.get(completed.returncode, "scan_failed")
            except subprocess.TimeoutExpired:
                result["status"] = "timed_out"
                stream.write("\nRunner timeout after %s seconds.\n" % timeout)
            except OSError as error:
                result["status"] = "launch_failed"
                stream.write("Could not launch Snyk: %s\n" % error)
    result.update({
        "finished_at": now(),
        "elapsed_seconds": round(time.monotonic() - started, 3),
        "report_created": report.is_file(),
    })
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=(*MODES, "all"), help="explicit scan scope")
    parser.add_argument("--timeout", type=int, default=600, metavar="SECONDS",
                        help="time limit per scan (default: 600)")
    args = parser.parse_args()
    if args.timeout < 1:
        parser.error("--timeout must be positive")

    os.umask(0o077)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    output = HERE / "reports" / stamp
    output.mkdir(parents=True, exist_ok=False)
    summary = {"started_at": now(), "organization": ORG, "scans": []}
    summary_path = output / "summary.json"
    print("Local scan evidence: " + str(output), flush=True)
    for mode in MODES if args.mode == "all" else (args.mode,):
        print("Running " + mode + " scan...", flush=True)
        result = run_scan(mode, output, args.timeout)
        summary["scans"].append(result)
        summary_path.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
        print("%s: %s (Snyk exit: %s)" %
              (mode, result["status"], result["exit_code"]), flush=True)

    codes = [item["exit_code"] for item in summary["scans"]]
    # Incomplete scans take precedence over findings; only all-zero results pass.
    exit_code = 2 if any(code not in (0, 1) for code in codes) else int(1 in codes)
    summary.update({"finished_at": now(), "runner_exit_code": exit_code})
    summary_path.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print("Summary: " + str(summary_path), flush=True)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())

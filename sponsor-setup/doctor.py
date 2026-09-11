#!/usr/bin/env python3
"""Read-only sponsor readiness report; network checks require --live.

Prints credential presence only, never credential values or raw account output.
Does not install packages, log in, start services, run demos, or create resources.
"""
import argparse
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
from urllib.error import HTTPError, URLError
from urllib.request import urlopen


SETUP = Path(__file__).resolve().parent
PROJECT = SETUP.parent
ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def env_from_files():
    """Read simple dotenv assignments without evaluating shell expansions."""
    values = dict(os.environ)
    for path in (PROJECT / ".env", SETUP / "hotdata" / ".env"):
        if not path.is_file():
            continue
        for line in path.read_text().splitlines():
            line = line.strip()
            if line.startswith("export "):
                line = line[7:].lstrip()
            key, separator, raw = line.partition("=")
            if not separator or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key.strip()):
                continue
            try:
                parts = shlex.split(raw, comments=True, posix=True)
            except ValueError:
                continue
            values.setdefault(key.strip(), " ".join(parts))
    return values


def has_secret(env, key):
    value = env.get(key, "").strip()
    return bool(value) and not (
        value.startswith(("<", "${"))
        or value.lower() in {"your_api_key", "your-api-key", "replace_me", "changeme"}
    )


def capture(args, env, timeout=12):
    try:
        result = subprocess.run(
            [str(item) for item in args], cwd=PROJECT, env=env,
            text=True, capture_output=True, timeout=timeout,
        )
        return result.returncode, ANSI.sub("", result.stdout + result.stderr)
    except (OSError, subprocess.TimeoutExpired):
        return 124, ""


def version_of(executable, env):
    if not executable or not Path(executable).is_file():
        return None
    code, output = capture([executable, "--version"], env)
    match = re.search(r"\b\d+\.\d+\.\d+(?:[-+][\w.-]+)?\b", output)
    return match.group(0) if code == 0 and match else None


def python_packages(env):
    python = SETUP / "memory" / ".venv" / "bin" / "python"
    if not python.is_file():
        return {}
    # Metadata avoids importing Cognee or opening its stores and telemetry.
    command = (
        "import importlib.metadata as m,json; "
        "names=('cognee','neo4j'); "
        "installed={d.metadata['Name'].lower():d.version for d in m.distributions()}; "
        "print(json.dumps({n:installed.get(n) for n in names}))"
    )
    code, output = capture([python, "-c", command], env)
    try:
        return json.loads(output) if code == 0 else {}
    except json.JSONDecodeError:
        return {}


def health(url):
    try:
        with urlopen(url, timeout=4) as response:
            return "HTTP " + str(response.status)
    except HTTPError as error:
        return "HTTP " + str(error.code)
    except (URLError, TimeoutError, OSError, ValueError):
        return "unreachable"


def receipt_exists(path, required):
    try:
        content = path.read_text()
        return all(fragment in content for fragment in required)
    except (OSError, UnicodeError):
        return False


def cloud_receipt_verified(path):
    """Accept only parsed receipts containing all completed verification flags."""
    try:
        receipt = json.loads(path.read_text())
    except (OSError, UnicodeError, json.JSONDecodeError):
        return False
    return (
        isinstance(receipt, dict)
        and receipt.get("status") == "passed"
        and receipt.get("ingest_completed") is True
        and receipt.get("recall_content_verified") is True
    )


def report(live):
    env = env_from_files()
    versions = python_packages(env)
    results = []

    cognee_version = versions.get("cognee")
    demo_recorded = receipt_exists(
        SETUP / "memory" / "logs" / "demo-output.txt",
        ["Success: Demo graph loaded", "Query: Who works at Anthropic?", "Query: What does cognee depend on?"],
    )
    cloud_recorded = any(
        cloud_receipt_verified(path)
        for path in (SETUP / "memory" / "cloud-receipts").glob("hackathon_setup_cloud_*.json")
    )
    cognee_credentials = "; ".join(
        key + " " + ("present" if has_secret(env, key) else "missing")
        for key in ("LLM_API_KEY", "COGNEE_SERVICE_URL", "COGNEE_API_KEY")
    )
    cognee_demo = "bundled keyless demo has a saved success log" if demo_recorded else "no bundled demo success log found"
    cognee_demo += (
        "; VERIFIED saved Cloud receipt (historical; not rerun)"
        if cloud_recorded else "; no verified Cloud ingestion/recall receipt found"
    )
    results.append({
        "sponsor": "Cognee",
        "local": "package " + cognee_version if cognee_version else "package missing",
        "credentials": cognee_credentials,
        "connection": "Cloud authentication and LLM/embedding services not checked; no Cloud or model requests made",
        "demo": cognee_demo,
        "next": "pnpm check:cognee:cloud (requires Cloud URL/key and available credits); local alternative: sponsor-setup/memory/.venv/bin/python sponsor-setup/memory/cognee_ingest.py",
    })

    docker = shutil.which("docker")
    driver = versions.get("neo4j")
    hydra_state = "database server not checked (use --live)"
    if live:
        http_base = env.get("HYDRADB_HTTP_URL") or "http://127.0.0.1:8443"
        ready_base = env.get("HYDRADB_ADMIN_URL") or "http://127.0.0.1:9090"
        hydra_state = "healthz " + health(http_base.rstrip("/") + "/healthz")
        hydra_state += "; readyz " + health(ready_base.rstrip("/") + "/readyz")
        hydra_state += "; authenticated query not checked"
    token_file = SETUP / "memory" / ".hydradb" / "auth-token"
    token_present = has_secret(env, "HYDRADB_AUTH_TOKEN") or (token_file.is_file() and token_file.stat().st_size > 0)
    results.append({
        "sponsor": "HydraDB",
        "local": ("Neo4j driver " + driver if driver else "Neo4j driver missing") + ("; Docker client found" if docker else "; Docker client missing"),
        "credentials": "token " + ("present" if token_present else "missing"),
        "connection": hydra_state,
        "demo": "graph round trip not run by doctor",
        "next": "bash sponsor-setup/memory/hydradb_start.sh; sponsor-setup/memory/.venv/bin/python sponsor-setup/memory/hydradb_smoke.py",
    })

    hotdata = SETUP / "hotdata" / "bin" / "hotdata"
    hotdata_version = version_of(hotdata, env)
    hotdata_state = "account/workspace not checked (use --live)"
    if live and hotdata_version:
        code, output = capture([hotdata, "--no-input", "auth", "status"], env, timeout=15)
        if code == 0 and re.search(r"Authenticated:\s+Yes\b", output):
            hotdata_state = "CLI reports authenticated; query not checked"
        elif re.search(r"Authenticated:\s+No\b", output):
            hotdata_state = "CLI reports not authenticated"
        else:
            hotdata_state = "auth status failed or timed out; raw account output withheld"
    config_present = (Path.home() / ".hotdata" / "config.yml").is_file()
    results.append({
        "sponsor": "hotdata.dev",
        "local": "CLI " + hotdata_version if hotdata_version else "CLI missing or failed",
        "credentials": "HOTDATA_API_KEY " + ("present" if has_secret(env, "HOTDATA_API_KEY") else "missing") + ("; CLI config exists" if config_present else "; no CLI config found"),
        "connection": hotdata_state,
        "demo": "cloud create/load/query not run by doctor",
        "next": "./sponsor-setup/hotdata/bin/hotdata auth login; bash sponsor-setup/hotdata/smoke.sh",
    })

    rocketride = PROJECT / "node_modules" / ".bin" / "rocketride"
    rocketride_version = version_of(rocketride, env)
    results.append({
        "sponsor": "RocketRide",
        "local": "CLI/SDK " + rocketride_version if rocketride_version else "CLI missing or failed",
        "credentials": "ROCKETRIDE_APIKEY " + ("present" if has_secret(env, "ROCKETRIDE_APIKEY") else "missing"),
        "connection": "staging/auth not checked here; run pnpm check:rocketride for read-only probes",
        "demo": "real pipeline run not checked",
        "next": "pnpm check:rocketride (after configuring staging API credentials)",
    })

    rote = SETUP / "rote" / "rote"
    rote_version = version_of(rote, env)
    rote_state = "account not checked (use --live)"
    if live and rote_version:
        code, output = capture([rote, "whoami"], env, timeout=15)
        if code == 0 and re.search(r"(?m)^ok:\s+\S+", output):
            rote_state = "CLI reports signed in; target application API not checked"
        elif "Not logged in" in output:
            rote_state = "CLI reports not signed in"
        else:
            rote_state = "identity check inconclusive or timed out; raw identity output withheld"
    rote_receipt = SETUP / "rote" / "reports" / "hello-success.json"
    results.append({
        "sponsor": "Modiqo / Rote",
        "local": "CLI " + rote_version if rote_version else "CLI missing or failed",
        "credentials": "account validation withheld in offline mode" if not live else "see identity check",
        "connection": rote_state,
        "demo": "Hello success receipt exists (historical; not rerun)" if rote_receipt.is_file() else "no Hello success receipt found",
        "next": (
            "python3 sponsor-setup/rote/smoke.py; choose an application API for project integration"
            if rote_receipt.is_file() or "reports signed in" in rote_state
            else "./sponsor-setup/rote/rote login --provider google; python3 sponsor-setup/rote/smoke.py"
        ),
    })
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="Allow read-only Hotdata/Rote account and HydraDB HTTP probes")
    parser.add_argument("--json", action="store_true", help="Print a machine-readable report")
    args = parser.parse_args()
    checks = report(args.live)
    if args.json:
        print(json.dumps({"mode": "live read-only" if args.live else "offline", "sponsors": checks, "integration_verified": False}, indent=2))
    else:
        print("Sponsor doctor — " + ("read-only live probes enabled" if args.live else "offline; no network probes"))
        for item in checks:
            print("\n" + item["sponsor"] + ": " + item["local"])
            print("  " + item["credentials"] + ". " + item["connection"] + ".")
            print("  Demo: " + item["demo"] + ".")
            print("  Next: " + item["next"])
        print("\nInstalled tooling, credentials, connectivity, and demo success are separate checks.")
        print("This report does not certify the complete five-sponsor integration.")


if __name__ == "__main__":
    main()

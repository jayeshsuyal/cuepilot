"""Record successful cue calls, export their trace, and replay with the real Rote CLI.

The Python transport performs one cue. Rote's exported DAG owns the sequence.
Generated plays remain local drafts: no registry publication or release is implied.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import sys
from uuid import uuid4
import urllib.error
import urllib.request

HERE = Path(__file__).resolve().parents[1]
ROOT = HERE.parent
PLAYS = HERE / "plays"
ROTE = ROOT / "sponsor-setup/rote/rote"
RUNTIME = ROOT / "sponsor-setup/rote/.runtime"
AUTHORED = PLAYS / "stage-sequence"
ACTIVE = PLAYS / "evidence/active.json"
_lock = asyncio.Lock()
_spec = importlib.util.spec_from_file_location("cuepilot_rote_transport", AUTHORED / "resources/cue.py")
_transport = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_transport)


def _result(status, operation, reason=None, **evidence):
    return {"provider": "rote", "status": status, "operation": operation,
            "evidence": evidence, "reason": reason}


def _sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _environment():
    env = os.environ.copy()
    # Parent API normally loads this. Standalone use reads only the required key.
    if not env.get("CUEPILOT_BRIDGE_TOKEN"):
        try:
            from dotenv import dotenv_values
            value = dotenv_values(HERE / ".env").get("CUEPILOT_BRIDGE_TOKEN")
            if value:
                env["CUEPILOT_BRIDGE_TOKEN"] = value
        except ImportError:
            pass
    # The authored helper needs only this one credential; Rote uses its existing
    # authenticated runtime. Avoid handing unrelated sponsor keys to subprocesses.
    keep = {key: value for key, value in env.items()
            if key in ("PATH", "HOME", "USER", "LOGNAME", "SHELL", "TMPDIR", "LANG",
                       "LC_ALL", "SYSTEMROOT", "CUEPILOT_BRIDGE_TOKEN")}
    keep["PATH"] = str(Path(sys.executable).parent) + os.pathsep + keep.get("PATH", "/usr/bin:/bin")
    return keep


def _redact(text, env):
    token = env.get("CUEPILOT_BRIDGE_TOKEN")
    return text.replace(token, "[REDACTED]") if token else text


def _write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_name(path.name + ".tmp-" + uuid4().hex)
    temporary.write_text(json.dumps(value, indent=2) + "\n")
    temporary.chmod(0o600)
    temporary.replace(path)


def _active_package():
    if not ACTIVE.is_file():
        raise ValueError("learned_play_required")
    try:
        active = json.loads(ACTIVE.read_text())
        name = active["package"]
        if not re.fullmatch(r"cuepilot-[A-Za-z0-9_-]+", name):
            raise ValueError()
        package = PLAYS / "learned" / name
        proof_path = package / "resources/proof.json"
        if _sha(proof_path) != active["proofSha256"]:
            raise ValueError()
        proof = json.loads(proof_path.read_text())
        if (proof["source"] != "rote-workspace-export-after-successful-cues" or
                [r["stepIndex"] for r in proof["receipts"]] != [0, 1, 2]):
            raise ValueError()
        for rel, expected in proof["files"].items():
            candidate = package / rel
            if not candidate.resolve().is_relative_to(package.resolve()) or _sha(candidate) != expected:
                raise ValueError()
        if not {"main.ts", "resources/cue.py", "deps.toml", "resources/recorded-export.txt"}.issubset(proof["files"]):
            raise ValueError()
        return package, proof
    except (OSError, KeyError, TypeError, ValueError):
        raise ValueError("learned_package_invalid") from None


def inspect():
    """Read local prerequisites and captured provenance; never execute a cue."""
    if not ROTE.is_file() or not os.access(ROTE, os.X_OK):
        return _result("blocked", "inspect", "rote_cli_missing")
    env = _environment()
    if not env.get("CUEPILOT_BRIDGE_TOKEN"):
        return _result("blocked", "inspect", "bridge_token_missing", cliInstalled=True)
    try:
        package, proof = _active_package()
    except ValueError as error:
        return _result("blocked", "inspect", str(error), cliInstalled=True,
                       learningAvailable=True, deployment="local")
    return _result("verified", "inspect", package=str(package.relative_to(ROOT)),
                   learnedFromRunId=proof["learnedFromRunId"], source=proof["source"],
                   packageIntegrity="verified", deployment="local", lifecycle="draft",
                   replayExecuted=False)


readiness = inspect


async def _cli(args, cwd, env, evidence, label, timeout=60):
    """Token is environment-only. Store redacted CLI evidence, never shell code."""
    evidence.mkdir(parents=True, exist_ok=True, mode=0o700)
    proc = await asyncio.create_subprocess_exec(
        str(ROTE), *args, cwd=str(cwd), env=env,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.communicate()
        raise ValueError("rote_timeout") from None
    out = _redact(stdout.decode("utf-8", "replace"), env)
    err = _redact(stderr.decode("utf-8", "replace"), env)
    _write_json(evidence / (label + ".json"), {
        "argv": args, "exitCode": proc.returncode, "stdout": out, "stderr": err,
    })
    if proc.returncode != 0:
        if "sandbox_apply" in out + err:
            raise ValueError("rote_nested_sandbox_blocked")
        raise ValueError("rote_" + label + "_failed")
    return out


def _run_status(run_id, base_url, env):
    request = urllib.request.Request(base_url + "/api/v1/runs/" + run_id,
                                     headers={"Authorization": "Bearer " + env["CUEPILOT_BRIDGE_TOKEN"]})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _transport.NoRedirect())
    try:
        with opener.open(request, timeout=8) as response:
            body = response.read(262145)
        if len(body) > 262144:
            raise ValueError()
        result = json.loads(body)
        if not isinstance(result, dict) or result.get("id") != run_id:
            raise ValueError()
        return result
    except (OSError, ValueError, urllib.error.URLError):
        raise ValueError("stage_status_unavailable") from None


def _confirmed_receipts(run, run_id):
    receipts = run.get("receipts", [])
    if (run.get("status") != "completed" or len(receipts) != 3 or
            [r.get("stepIndex") for r in receipts] != [0, 1, 2] or
            any(r.get("ok") is not True or r.get("runId") != run_id for r in receipts)):
        raise ValueError("stage_receipts_incomplete")
    return receipts


def _scalar(value):
    if value.startswith('"'):
        return json.loads(value)
    if value.startswith("'") and value.endswith("'"):
        return value[1:-1].replace("''", "'")
    return value


def _generalize_export(raw, run_id, base_url, helper):
    """Narrow, auditable transformation of Rote's *recorded* three process steps.

    Keep Rote's synthesized names and presentation. Replace captured input
    literals with declared parameters, package the helper, add strict ordering.
    Refuse any other trace shape instead of silently authoring a new procedure.
    """
    match = re.search(r"(/\*\*\n)(.*?)( \*/)", raw, re.S)
    if not match:
        raise ValueError("rote_export_shape_unrecognized")
    lines = match[2].splitlines()
    plain = "\n".join(line[3:] if line.startswith(" * ") else line[2:] if line == " *" else line for line in lines)
    if "\nsteps:\n" not in plain or "\nparameters:\n" not in plain:
        raise ValueError("rote_export_shape_unrecognized")
    prefix, steps = plain.split("\nsteps:\n", 1)
    steps = steps.removesuffix("\n---")
    names = list(re.finditer(r"(?m)^  ([A-Za-z0-9_-]+):$", steps))
    if len(names) != 3:
        raise ValueError("rote_export_requires_three_recorded_steps")
    blocks = []
    for index, name in enumerate(names):
        block = steps[name.end():names[index + 1].start() if index < 2 else len(steps)]
        if "    type: process.exec\n" not in block or "    argv:\n" not in block:
            raise ValueError("rote_export_shape_unrecognized")
        argv_block = block.split("    argv:\n", 1)[1]
        argv_block = re.split(r"(?m)^    [A-Za-z_]+:", argv_block, 1)[0]
        args = [_scalar(line) for line in re.findall(r"(?m)^    - (.*)$", argv_block)]
        expected = ["python3", str(helper), "--run-id", run_id, "--base-url", base_url, "--step-index", str(index)]
        normalized = [run_id if x == "$run_id" else base_url if x == "$base_url" else str(x) for x in args]
        if normalized != expected:
            raise ValueError("rote_recorded_command_mismatch")
        argv = ["python3", "@resource{cue.py}", "--run-id", "$run_id", "--base-url", "$base_url", "--step-index", str(index)]
        lines = ["  " + name[1] + ":", "    type: process.exec"]
        if index:
            lines.append("    depends_on: [" + names[index - 1][1] + "]")
        lines.append("    argv: " + json.dumps(argv))
        blocks.append("\n".join(lines))
    prefix = prefix.replace("flow_type: parallel", "flow_type: sequential")
    changed = prefix + "\nsteps:\n" + "\n".join(blocks) + "\n---"
    comment = "/**\n" + "\n".join(" * " + line for line in changed.splitlines()) + "\n */"
    return raw[:match.start()] + comment + raw[match.end():]


def _inputs(run_id, base_url):
    run_id = _transport.validate_run_id(run_id)
    base_url = _transport.loopback_url(base_url)
    env = _environment()
    if not ROTE.is_file():
        raise ValueError("rote_cli_missing")
    if not env.get("CUEPILOT_BRIDGE_TOKEN"):
        raise ValueError("bridge_token_missing")
    return run_id, base_url, env


async def _finish_package(run_id, base_url, env, evidence, captured):
    """Finalize an already-successful recorded trace without executing it again."""
    name = evidence.name
    helper = AUTHORED / "resources/cue.py"
    exported = evidence / "recorded-export.ts"
    raw = exported.read_text()
    main = _generalize_export(raw, run_id, base_url, helper)
    package = PLAYS / "learned" / name
    (package / "resources").mkdir(parents=True, mode=0o700, exist_ok=True)
    shutil.copyfile(helper, package / "resources/cue.py")
    shutil.copyfile(AUTHORED / "deps.toml", package / "deps.toml")
    shutil.copyfile(exported, package / "resources/recorded-export.txt")
    (package / "main.ts").write_text(main)
    target = "./" + str((package / "main.ts").relative_to(ROOT))
    await _cli(["play", "validate", target], ROOT, env, evidence, "validate")
    proof = {"source": "rote-workspace-export-after-successful-cues", "learnedFromRunId": run_id,
             "workspace": name, "receipts": captured, "deployment": "local", "lifecycle": "draft",
             "transformations": ["parameterized run_id and base_url", "packaged authored one-cue HTTP helper", "explicit recorded cue ordering"],
             "files": {rel: _sha(package / rel) for rel in ("main.ts", "resources/cue.py", "deps.toml", "resources/recorded-export.txt")}}
    _write_json(package / "resources/proof.json", proof)
    for path in package.rglob("*"):
        if path.is_file():
            path.chmod(0o400)
    _write_json(ACTIVE, {"package": name, "proofSha256": _sha(package / "resources/proof.json")})
    return _result("verified", "learn", learnedFromRunId=run_id, workspace=name,
                   package=str(package.relative_to(ROOT)), receipts=captured,
                   source=proof["source"], lifecycle="draft", deployment="local",
                   replayExecuted=False, commandEvidence=str(evidence.relative_to(ROOT)))


async def learn(run_id: str, base_url: str) -> dict:
    """Record this approved first execution, then export and parameterize it."""
    async with _lock:
        try:
            run_id, base_url, env = _inputs(run_id, base_url)
            if ACTIVE.exists():
                return _result("blocked", "learn", "learned_play_already_exists")
            before = await asyncio.to_thread(_run_status, run_id, base_url, env)
            if before.get("receipts"):
                return _result("blocked", "learn", "fresh_approved_run_required")
            name = "cuepilot-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + "-" + uuid4().hex[:8]
            evidence = PLAYS / "evidence" / name
            workspace = RUNTIME / "workspaces" / name
            await _cli(["init", name, "--seq"], ROOT, env, evidence, "init")
            await _cli(["workspace", "set", "run_id=" + run_id, "base_url=" + base_url], workspace, env, evidence, "params")
            captured = []
            helper = AUTHORED / "resources/cue.py"
            for index in range(3):
                recorded = await _cli(["proc", "run", "--", "python3", str(helper),
                                       "--run-id", run_id, "--base-url", base_url,
                                       "--step-index", str(index)], workspace, env, evidence, "record-" + str(index))
                reference = re.search(r"response_id: (@\d+)\b", recorded)
                if not reference:
                    raise ValueError("rote_capture_reference_missing")
                # proc run exits zero when *capture* worked, even if its child
                # failed. The recorded child status is the execution authority.
                if not re.search(r"(?m)^exit: code 0$", recorded):
                    await _cli(["query", reference[1], ".stderr.text", "-r"], workspace, env, evidence, "rejected-" + str(index))
                    raise ValueError("stage_cue_rejected")
                output = await _cli(["query", reference[1], ".stdout.text", "-r"], workspace, env, evidence, "receipt-" + str(index))
                receipt = json.loads(output)
                if (receipt.get("ok") is not True or receipt.get("runId") != run_id or
                        receipt.get("stepIndex") != index or not receipt.get("receiptSha256")):
                    raise ValueError("rote_capture_receipt_mismatch")
                captured.append({**receipt, "roteResponse": reference[1]})
            run = await asyncio.to_thread(_run_status, run_id, base_url, env)
            _confirmed_receipts(run, run_id)
            # Rote expands '~' anywhere in absolute export paths, including this
            # project's Hackathon~ name. Relative paths use its local-process root.
            exported = evidence / "recorded-export.ts"
            export_argument = os.path.relpath(exported, RUNTIME / "flows/local-process")
            await _cli(["workspace", "export", export_argument, "--params", "run_id,base_url",
                        "--description", "CuePilot sequence recorded from a successful approved rehearsal."],
                       workspace, env, evidence, "export")
            return await _finish_package(run_id, base_url, env, evidence, captured)
        except (OSError, ValueError, KeyError, TypeError) as error:
            reason = str(error) if isinstance(error, ValueError) else "rote_learning_failed"
            return _result("blocked" if reason in ("bridge_token_missing", "rote_cli_missing", "rote_nested_sandbox_blocked") else "failed",
                           "learn", reason)


async def replay(run_id: str, base_url: str) -> dict:
    """Run the exported Rote DAG on new approved input; no Python cue loop."""
    async with _lock:
        try:
            run_id, base_url, env = _inputs(run_id, base_url)
            package, proof = _active_package()
            before = await asyncio.to_thread(_run_status, run_id, base_url, env)
            if before.get("receipts"):
                return _result("blocked", "replay", "fresh_approved_run_required")
            name = "replay-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + "-" + uuid4().hex[:8]
            evidence = PLAYS / "evidence" / name
            target = "./" + str((package / "main.ts").relative_to(ROOT))
            out = await _cli(["play", "run", target, "run_id=" + run_id, "base_url=" + base_url],
                             ROOT, env, evidence, "replay", timeout=90)
            if not re.search(r"Summary: 3/3 completed, 0 failed, 0 blocked", out):
                raise ValueError("rote_replay_evidence_incomplete")
            run = await asyncio.to_thread(_run_status, run_id, base_url, env)
            receipts = _confirmed_receipts(run, run_id)
            rote_run = re.search(r"run_id:\s*(run_[A-Za-z0-9_.-]+)", out)
            report = {"runId": run_id, "roteRunId": rote_run[1] if rote_run else None,
                      "learnedFromRunId": proof["learnedFromRunId"], "newInput": run_id != proof["learnedFromRunId"],
                      "package": str(package.relative_to(ROOT)), "receipts": receipts,
                      "commandEvidence": str(evidence.relative_to(ROOT)), "deployment": "local"}
            _write_json(evidence / "verified.json", report)
            return _result("verified", "replay", **report)
        except (OSError, ValueError, KeyError, TypeError) as error:
            reason = str(error) if isinstance(error, ValueError) else "rote_replay_failed"
            blocked = reason in ("learned_play_required", "learned_package_invalid", "bridge_token_missing", "rote_cli_missing", "rote_nested_sandbox_blocked")
            return _result("blocked" if blocked else "failed", "replay", reason)

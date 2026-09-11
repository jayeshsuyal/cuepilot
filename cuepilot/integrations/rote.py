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
import signal
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
OPERATION_TIMEOUT = 105
CLEANUP_TIMEOUT = 5
PROOF_VERSION = 2
PACKAGE_FILES = {"main.ts", "resources/cue.py", "deps.toml", "resources/recorded-export.txt"}
_lock = asyncio.Lock()
_spec = importlib.util.spec_from_file_location("cuepilot_rote_transport", AUTHORED / "resources/cue.py")
_transport = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_transport)


def _result(status, operation, reason=None, **evidence):
    result = {"provider": "rote", "status": status, "operation": operation,
              "evidence": evidence, "reason": reason}
    if "procedure" in evidence:
        result["procedure"] = evidence["procedure"]
    return result


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


def _procedure(package, proof):
    return {"id": package.name, "sha256": _sha(package / "resources/proof.json")}


def _identity(run):
    """Bind reusable behavior to the current rule text and supported recipe.

    Speaker, run, show revision and approval hash are deliberately per-execution.
    The API still owns whether that exact plan is currently approved and ready.
    """
    if not isinstance(run, dict):
        raise ValueError("incompatible_current_recipe")
    plan = run.get("plan")
    if (not isinstance(plan, dict) or not isinstance(plan.get("recipeId"), str) or not plan["recipeId"] or
            type(plan.get("recipeVersion")) is not int or plan["recipeVersion"] != 1 or
            plan.get("cues") != [{"index": i, "scene": scene} for i, scene in
                                enumerate(("intro", "presentation", "holding"))] or
            any(type(cue.get("index")) is not int for cue in plan["cues"]) or
            not isinstance(run.get("notes"), str) or not run["notes"] or
            not isinstance(run.get("speakerId"), str) or not run["speakerId"] or
            plan.get("speakerId") != run["speakerId"] or
            run.get("executionMode") not in ("practice", "live") or
            type(plan.get("showRevision")) is not int or plan["showRevision"] < 1 or
            not isinstance(plan.get("hash"), str) or not re.fullmatch(r"[a-f0-9]{64}", plan["hash"])):
        raise ValueError("incompatible_current_recipe")
    identity = {"recipeId": plan["recipeId"], "recipeVersion": plan["recipeVersion"],
                "cues": plan["cues"], "noteSha256": hashlib.sha256(run["notes"].encode()).hexdigest()}
    if run["executionMode"] == "practice":
        if plan["recipeId"] != "speaker-segment-v1":
            raise ValueError("incompatible_current_recipe")
    else:
        memory = run.get("memoryProof")
        if (not isinstance(memory, dict) or memory.get("recipe_id") != plan["recipeId"] or
                memory.get("note_sha256") != identity["noteSha256"] or
                not isinstance(memory.get("graph_sha256"), str) or
                not re.fullmatch(r"[a-f0-9]{64}", memory["graph_sha256"]) or
                not isinstance(memory.get("source_id"), str) or not memory["source_id"]):
            raise ValueError("incompatible_current_recipe")
        identity["memoryProof"] = {key: memory[key] for key in
                                   ("recipe_id", "note_sha256", "graph_sha256", "source_id")}
    return identity


def _fresh_run(run):
    if (run.get("status") != "approved" or run.get("receipts") != [] or
            type(run.get("nextStep")) is not int or run["nextStep"] != 0):
        raise ValueError("fresh_approved_run_required")
    return _identity(run)


def _active_package():
    if not ACTIVE.is_file():
        raise ValueError("learned_play_required")
    try:
        active = json.loads(ACTIVE.read_text())
        name = active["package"]
        if not isinstance(name, str) or not re.fullmatch(r"cuepilot-[A-Za-z0-9_-]+", name):
            raise ValueError()
        package = PLAYS / "learned" / name
        proof_path = package / "resources/proof.json"
        if (ACTIVE.is_symlink() or package.is_symlink() or (package / "resources").is_symlink() or
                not package.resolve().is_relative_to((PLAYS / "learned").resolve()) or
                _sha(proof_path) != active["proofSha256"]):
            raise ValueError()
        proof = json.loads(proof_path.read_text())
        if not isinstance(proof, dict):
            raise ValueError()
        if proof.get("proofVersion") != PROOF_VERSION:
            raise ValueError("learned_package_upgrade_required")
        if (proof["source"] != "rote-workspace-export-after-successful-cues" or
                proof["workspace"] != name or proof["identity"] != _identity(proof["learnedRun"]) or
                proof["learnedRun"]["id"] != proof["learnedFromRunId"] or
                set(proof["files"]) != PACKAGE_FILES):
            raise ValueError()
        actual = set()
        for candidate in package.rglob("*"):
            if candidate.is_symlink():
                raise ValueError()
            if candidate.is_file():
                actual.add(str(candidate.relative_to(package)))
        if actual != PACKAGE_FILES | {"resources/proof.json"}:
            raise ValueError()
        for rel, expected in proof["files"].items():
            if _sha(package / rel) != expected:
                raise ValueError()
        # A pinned package does not silently retain an outdated transport or deps.
        if (_sha(AUTHORED / "resources/cue.py") != proof["files"]["resources/cue.py"] or
                _sha(AUTHORED / "deps.toml") != proof["files"]["deps.toml"]):
            raise ValueError("learned_package_upgrade_required")
        _match_captured(proof["receipts"], _confirmed_receipts(proof["learnedRun"], proof["learnedFromRunId"]),
                        proof["learnedFromRunId"])
        # Re-derive the auditable transformation, including its exact parameters,
        # from the pinned recording instead of trusting an arbitrary main.ts.
        expected_main = _generalize_export((package / "resources/recorded-export.txt").read_text(),
                                           proof["learnedFromRunId"], proof["baseUrl"], Path(proof["capturedHelper"]))
        if (package / "main.ts").read_text() != expected_main:
            raise ValueError()
        return package, proof
    except (OSError, KeyError, TypeError, ValueError) as error:
        if str(error) == "learned_package_upgrade_required":
            raise ValueError(str(error)) from None
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
                   replayExecuted=False, procedure=_procedure(package, proof), identity=proof["identity"])


readiness = inspect


async def _cli(args, cwd, env, evidence, label, timeout=60):
    """Isolate and kill the CLI process group on timeout or cancellation.

    Killing the wrapper alone leaves Rote's cue children running. A killed client
    cannot revoke an HTTP cue already accepted by the API: reconcile receipts.
    """
    evidence.mkdir(parents=True, exist_ok=True, mode=0o700)
    # Shield creation as well as communication: asyncio's own cancellation
    # cleanup during pipe setup kills only the direct child, losing its group.
    creation = asyncio.create_task(asyncio.create_subprocess_exec(
        str(ROTE), *args, cwd=str(cwd), env=env, start_new_session=True,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    ))
    proc = None
    communicate = None
    interrupted = None
    cleanup_confirmed = True
    try:
        proc = await asyncio.shield(creation)
        communicate = asyncio.create_task(proc.communicate())
        stdout, stderr = await asyncio.wait_for(asyncio.shield(communicate), timeout)
    except (asyncio.TimeoutError, asyncio.CancelledError) as error:
        interrupted = "cancelled" if isinstance(error, asyncio.CancelledError) else "timeout"
        if proc is None:
            # Recover the handle before propagating cancellation. Never cancel
            # the creation task, which would discard the group leader's PID.
            while proc is None:
                try:
                    proc = await asyncio.shield(creation)
                except asyncio.CancelledError:
                    if creation.cancelled():
                        # Event-loop shutdown can cancel even shielded children.
                        # A cancelled creation task cannot return a recoverable
                        # process handle; report uncertainty instead of spinning.
                        _write_json(evidence / (label + ".json"), {
                            "argv": args, "exitCode": None, "interrupted": interrupted,
                            "cleanupConfirmed": False, "stdout": "", "stderr": "",
                        })
                        raise
                    continue
            communicate = asyncio.create_task(proc.communicate())
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        cleanup_deadline = asyncio.get_running_loop().time() + CLEANUP_TIMEOUT
        while True:
            try:
                remaining = max(0, cleanup_deadline - asyncio.get_running_loop().time())
                stdout, stderr = await asyncio.wait_for(asyncio.shield(communicate), remaining)
                break
            except asyncio.CancelledError:
                # Repeated cancellation cannot release the execution lock while
                # cleanup is in progress, nor extend this absolute drain limit.
                if communicate.cancelled():
                    cleanup_confirmed = False
                    stdout, stderr = b"", b""
                    break
                continue
            except asyncio.TimeoutError:
                cleanup_confirmed = False
                communicate.cancel()
                stdout, stderr = b"", b""
                break
        _write_json(evidence / (label + ".json"), {
            "argv": args, "exitCode": proc.returncode, "interrupted": interrupted,
            "cleanupConfirmed": cleanup_confirmed,
            "stdout": _redact(stdout.decode("utf-8", "replace"), env),
            "stderr": _redact(stderr.decode("utf-8", "replace"), env),
        })
        if isinstance(error, asyncio.CancelledError):
            raise
        raise ValueError("rote_timeout" if cleanup_confirmed else "rote_cleanup_unconfirmed") from None
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


def _confirmed_receipts(run, run_id, before=None):
    receipts = run.get("receipts")
    if (run.get("status") != "completed" or type(run.get("nextStep")) is not int or
            run["nextStep"] != 3 or not isinstance(receipts, list) or len(receipts) != 3):
        raise ValueError("stage_receipts_incomplete")
    try:
        for index, receipt in enumerate(receipts):
            _transport.validate_receipt(receipt, run_id, index)
        if (len({r["id"] for r in receipts}) != 3 or
                any(receipts[i + 1]["stageRevision"] != receipts[i]["stageRevision"] + 1 for i in range(2))):
            raise ValueError()
        if before and (run.get("plan") != before.get("plan") or _identity(run) != _identity(before) or
                       run.get("executionMode") != before.get("executionMode")):
            raise ValueError()
    except ValueError:
        raise ValueError("stage_receipts_mismatch") from None
    return receipts


def _match_captured(captured, receipts, run_id):
    if not isinstance(captured, list) or len(captured) != 3:
        raise ValueError("rote_capture_receipt_mismatch")
    for index, (item, receipt) in enumerate(zip(captured, receipts)):
        if (not isinstance(item, dict) or item.get("ok") is not True or item.get("runId") != run_id or
                type(item.get("stepIndex")) is not int or item["stepIndex"] != index or
                item.get("requestId") != _transport.request_id(run_id, index) or
                item.get("receiptCanonicalSha256") != _transport.receipt_digest(receipt) or
                item.get("receiptId") != receipt["id"] or item.get("scene") != receipt["scene"] or
                type(item.get("stageRevision")) is not int or item["stageRevision"] != receipt["stageRevision"] or
                item.get("committedAt") != receipt["committedAt"] or
                not isinstance(item.get("receiptSha256"), str) or
                not re.fullmatch(r"[a-f0-9]{64}", item["receiptSha256"])):
            raise ValueError("rote_capture_receipt_mismatch")


def _replay_captures(out):
    lines = [line.removeprefix("CUEPILOT_REPLAY_EVIDENCE ") for line in out.splitlines()
             if line.startswith("CUEPILOT_REPLAY_EVIDENCE ")]
    try:
        if len(lines) != 1:
            raise ValueError()
        evidence = json.loads(lines[0])
        if (evidence["status"] != "succeeded" or not re.fullmatch(r"run_[A-Za-z0-9_.-]+", evidence["runId"]) or
                len(evidence["steps"]) != 3):
            raise ValueError()
        captures = []
        for step in evidence["steps"]:
            body = step["body"]
            if (step["status"] != "completed" or body["kind"] != "process.exec" or
                    body["status"]["spawned"] is not True or body["status"]["timed_out"] is not False or
                    body["status"]["exit"] != {"kind": "code", "code": 0} or
                    type(body["status"]["exit"]["code"]) is not int or
                    body["stdout"].get("truncated", False) is not False):
                raise ValueError()
            captures.append(json.loads(body["stdout"]["text"]))
        return evidence["runId"], captures
    except (AttributeError, KeyError, TypeError, ValueError):
        raise ValueError("rote_replay_evidence_incomplete") from None


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
    parameter_block = prefix.split("\nparameters:\n", 1)[1]
    parameter = r"- name: (run_id|base_url)\n  param_type: string\n  required: true\n  default: null\n  description: Play parameter\n  example: null\n  valid_values: null"
    if re.fullmatch(parameter + "\n" + parameter, parameter_block) is None or re.findall(r"(?m)^- name: (.*)$", parameter_block) != ["run_id", "base_url"]:
        raise ValueError("rote_export_parameters_mismatch")
    steps = steps.removesuffix("\n---")
    names = list(re.finditer(r"(?m)^  ([A-Za-z0-9_-]+):$", steps))
    if len(names) != 3:
        raise ValueError("rote_export_requires_three_recorded_steps")
    blocks = []
    if len({name[1] for name in names}) != 3:
        raise ValueError("rote_export_shape_unrecognized")
    for index, name in enumerate(names):
        block = steps[name.end():names[index + 1].start() if index < 2 else len(steps)]
        if "    type: process.exec\n" not in block or "    argv:\n" not in block:
            raise ValueError("rote_export_shape_unrecognized")
        if re.fullmatch(r"\n    type: process.exec\n    argv:\n(?:    - [^\n]+\n?)+(?:    depends_on:\n(?:    - [A-Za-z0-9_-]+\n?)*)?", block) is None:
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
    # The exported presentation hides restored-vs-new outcomes. Append a small
    # authored evidence renderer, using the installed SDK's typed process body.
    # This observes results only; Rote still owns all execution and ordering.
    handles = ", ".join("ctx.step(stepName(" + json.dumps(name[1]) + "))" for name in names)
    renderer = """
// CuePilot authored evidence renderer v2; no effects or replay loop.
const cuepilotSteps = [%s].map((step) => {
  if (step.outcome.status !== "completed") return {status: step.outcome.status, body: null};
  const body = step.outcome.output.body as {kind?: unknown; status?: unknown; stdout?: unknown};
  return {status: step.outcome.status, body: {kind: body.kind, status: body.status, stdout: body.stdout}};
});
console.log("CUEPILOT_REPLAY_EVIDENCE " + JSON.stringify({
  runId: ctx.run.run_id, status: ctx.run.status, steps: cuepilotSteps,
}));
""" % handles
    return raw[:match.start()] + comment + raw[match.end():] + renderer


def _inputs(run_id, base_url):
    run_id = _transport.validate_run_id(run_id)
    base_url = _transport.loopback_url(base_url)
    env = _environment()
    if not ROTE.is_file() or not os.access(ROTE, os.X_OK):
        raise ValueError("rote_cli_missing")
    token = env.get("CUEPILOT_BRIDGE_TOKEN", "")
    if not token or "\r" in token or "\n" in token:
        raise ValueError("bridge_token_missing")
    return run_id, base_url, env


async def _finish_package(run_id, base_url, env, evidence, captured, learned_run):
    """Finalize an already-successful recorded trace without executing it again."""
    name = evidence.name
    helper = AUTHORED / "resources/cue.py"
    exported = evidence / "recorded-export.ts"
    main = _generalize_export(exported.read_text(), run_id, base_url, helper)
    package = PLAYS / "learned" / name
    (package / "resources").mkdir(parents=True, mode=0o700, exist_ok=False)
    shutil.copyfile(helper, package / "resources/cue.py")
    shutil.copyfile(AUTHORED / "deps.toml", package / "deps.toml")
    shutil.copyfile(exported, package / "resources/recorded-export.txt")
    (package / "main.ts").write_text(main)
    target = "./" + str((package / "main.ts").relative_to(ROOT))
    await _cli(["play", "validate", target], ROOT, env, evidence, "validate")
    proof = {"proofVersion": PROOF_VERSION,
             "source": "rote-workspace-export-after-successful-cues", "learnedFromRunId": run_id,
             "learnedRun": {key: learned_run[key] for key in
                            ("id", "plan", "notes", "speakerId", "executionMode", "status", "nextStep", "receipts", "memoryProof")
                            if key in learned_run},
             "identity": _identity(learned_run), "baseUrl": base_url, "capturedHelper": str(helper),
             "workspace": name, "receipts": captured, "deployment": "local", "lifecycle": "draft",
             "transformations": ["parameterized run_id and base_url", "packaged authored one-cue HTTP helper",
                                 "explicit recorded cue ordering", "appended strict per-step execution evidence"],
             "files": {rel: _sha(package / rel) for rel in sorted(PACKAGE_FILES)}}
    _write_json(package / "resources/proof.json", proof)
    for path in package.rglob("*"):
        if path.is_file():
            path.chmod(0o400)
    _write_json(ACTIVE, {"package": name, "proofSha256": _sha(package / "resources/proof.json")})
    return _result("verified", "learn", learnedFromRunId=run_id, workspace=name,
                   package=str(package.relative_to(ROOT)), receipts=learned_run["receipts"], capturedReceipts=captured,
                   procedure=_procedure(package, proof), identity=proof["identity"],
                   source=proof["source"], lifecycle="draft", deployment="local",
                   replayExecuted=False, commandEvidence=str(evidence.relative_to(ROOT)))


async def _learn(run_id, base_url, env, evidence, state):
    if ACTIVE.exists():
        raise ValueError("learned_play_already_exists")
    before = await asyncio.to_thread(_run_status, run_id, base_url, env)
    _fresh_run(before)
    workspace = RUNTIME / "workspaces" / evidence.name
    await _cli(["init", evidence.name, "--seq"], ROOT, env, evidence, "init")
    await _cli(["workspace", "set", "run_id=" + run_id, "base_url=" + base_url], workspace, env, evidence, "params")
    captured = []
    helper = AUTHORED / "resources/cue.py"
    for index in range(3):
        state["executionAttempted"] = True
        recorded = await _cli(["proc", "run", "--", "python3", str(helper),
                               "--run-id", run_id, "--base-url", base_url,
                               "--step-index", str(index)], workspace, env, evidence, "record-" + str(index))
        references = re.findall(r"response_id: (@\d+)\b", recorded)
        if len(references) != 1 or references[0] in [r["roteResponse"] for r in captured]:
            raise ValueError("rote_capture_reference_missing")
        # A successful recording is not a successful child. Query typed status.
        raw = await _cli(["query", references[0], ".", "-r"], workspace, env, evidence, "receipt-" + str(index))
        try:
            body = json.loads(raw)
            if (body["kind"] != "process.exec" or body["status"]["spawned"] is not True or
                    body["status"]["timed_out"] is not False or
                    body["status"]["exit"] != {"kind": "code", "code": 0} or
                    type(body["status"]["exit"]["code"]) is not int or
                    body["stdout"].get("truncated", False) is not False):
                raise ValueError()
            receipt = json.loads(body["stdout"]["text"])
            if not isinstance(receipt, dict):
                raise ValueError()
        except (AttributeError, KeyError, TypeError, ValueError):
            raise ValueError("stage_cue_rejected") from None
        captured.append({**receipt, "roteResponse": references[0]})
    run = await asyncio.to_thread(_run_status, run_id, base_url, env)
    receipts = _confirmed_receipts(run, run_id, before)
    _match_captured(captured, receipts, run_id)
    exported = evidence / "recorded-export.ts"
    export_argument = os.path.relpath(exported, RUNTIME / "flows/local-process")
    await _cli(["workspace", "export", export_argument, "--params", "run_id,base_url",
                "--description", "CuePilot sequence recorded from a successful approved rehearsal."],
               workspace, env, evidence, "export")
    return await _finish_package(run_id, base_url, env, evidence, captured, run)


async def _replay(run_id, base_url, env, evidence, state):
    package, proof = _active_package()
    state["procedure"] = _procedure(package, proof)
    before = await asyncio.to_thread(_run_status, run_id, base_url, env)
    identity = _fresh_run(before)
    if run_id == proof["learnedFromRunId"]:
        raise ValueError("fresh_approved_run_required")
    if identity != proof["identity"]:
        raise ValueError("learned_recipe_mismatch")
    target = "./" + str((package / "main.ts").relative_to(ROOT))
    state["executionAttempted"] = True
    out = await _cli(["play", "run", target, "run_id=" + run_id, "base_url=" + base_url],
                     ROOT, env, evidence, "replay", timeout=90)
    rote_run, captured = _replay_captures(out)
    run = await asyncio.to_thread(_run_status, run_id, base_url, env)
    receipts = _confirmed_receipts(run, run_id, before)
    _match_captured(captured, receipts, run_id)
    # Detect accidental edits/replacement during execution too.
    current_package, current_proof = _active_package()
    if current_package != package or _procedure(current_package, current_proof) != state["procedure"]:
        raise ValueError("learned_package_changed")
    report = {"runId": run_id, "roteRunId": rote_run,
              "learnedFromRunId": proof["learnedFromRunId"], "newInput": True,
              "newSpeaker": before["speakerId"] != proof["learnedRun"]["speakerId"],
              "procedure": state["procedure"], "identity": identity,
              "package": str(package.relative_to(ROOT)), "receipts": receipts, "capturedReceipts": captured,
              "commandEvidence": str(evidence.relative_to(ROOT)), "deployment": "local", "replayExecuted": True}
    _write_json(evidence / "verified.json", report)
    return _result("verified", "replay", **report)


async def _operate(operation, run_id, base_url):
    if _lock.locked():
        return _result("blocked", operation, "rote_execution_busy", executionAttempted=False, reconciliationRequired=False)
    state = {"executionAttempted": False}
    evidence = None
    async with _lock:
        try:
            async with asyncio.timeout(OPERATION_TIMEOUT):
                run_id, base_url, env = _inputs(run_id, base_url)
                name = ("cuepilot-" if operation == "learn" else "replay-") + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + "-" + uuid4().hex[:8]
                evidence = PLAYS / "evidence" / name
                function = _learn if operation == "learn" else _replay
                result = await function(run_id, base_url, env, evidence, state)
                result["evidence"].update(executionAttempted=state["executionAttempted"], reconciliationRequired=False)
                return result
        except asyncio.CancelledError:
            if evidence:
                _write_json(evidence / "interrupted.json", {"operation": operation, "runId": run_id,
                            **state, "reconciliationRequired": state["executionAttempted"], "reason": "rote_cancelled"})
            raise
        except (OSError, ValueError, KeyError, TypeError) as error:
            reason = "rote_operation_timeout" if isinstance(error, TimeoutError) else (
                str(error) if isinstance(error, ValueError) and re.fullmatch(r"[a-z_]+", str(error)) else "rote_" + operation + "_failed")
            blocked = reason in ("learned_play_required", "learned_package_invalid", "learned_package_upgrade_required",
                                 "learned_play_already_exists", "learned_recipe_mismatch", "fresh_approved_run_required",
                                 "incompatible_current_recipe", "bridge_token_missing", "rote_cli_missing", "rote_nested_sandbox_blocked")
            result = _result("blocked" if blocked else "failed", operation, reason, **state,
                             reconciliationRequired=state["executionAttempted"])
            if evidence:
                result["evidence"]["commandEvidence"] = str(evidence.relative_to(ROOT))
                _write_json(evidence / "failed.json", result)
            return result


async def learn(run_id: str, base_url: str) -> dict:
    """Record this fresh approved first execution, then export and parameterize it."""
    return await _operate("learn", run_id, base_url)


async def replay(run_id: str, base_url: str) -> dict:
    """Run one exported Rote DAG on fresh approved input; no Python cue loop."""
    return await _operate("replay", run_id, base_url)

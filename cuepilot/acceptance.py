"""Explicit, stateful acceptance runs through the loopback API only."""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import time
from uuid import uuid4

import httpx
from dotenv import dotenv_values

HERE = Path(__file__).resolve().parent
BASE = "http://127.0.0.1:8787/api/v1/"
SCENES = ["intro", "presentation", "holding"]
LABELS = {"practice": "Local fixture plans; real manual API stage cues; no sponsor execution claimed.",
          "rote": "Local fixture plans; real Rote execution; no Cognee/Hotdata/RocketRide claim.",
          "live": "Sponsor-backed plans and RocketRide/Rote execution; no fixture fallback."}


class CheckError(Exception):
    def __init__(self, code, blocked=False):
        super().__init__(code)
        self.code, self.blocked = code, blocked


def require(condition, code, blocked=False):
    if not condition:
        raise CheckError(code, blocked)


def redact(value, secrets):
    if isinstance(value, dict):
        return {key: "[redacted]" if re.search(r"token|password|secret|authorization|api.?key", key, re.I)
                else redact(child, secrets) for key, child in value.items()}
    if isinstance(value, list):
        return [redact(child, secrets) for child in value]
    if isinstance(value, str):
        for secret in secrets:
            value = value.replace(secret, "[redacted]")
    return value


class Runner:
    def __init__(self, mode, seconds, env):
        self.mode, self.seconds = mode, seconds
        self.operator, self.bridge = env.get("CUEPILOT_OPERATOR_TOKEN", ""), env.get("CUEPILOT_BRIDGE_TOKEN", "")
        self.secrets = [str(v) for k, v in env.items() if v and len(str(v)) >= 6 and re.search(r"key|token|secret|password", k, re.I)]
        self.report = {"mode": mode, "label": LABELS[mode], "status": "running", "startedAt": datetime.now(timezone.utc).isoformat(),
                       "perRunTimeoutSeconds": seconds, "cases": []}
        self.client = None
        self.learned = None

    def require_fresh_learning(self):
        if self.mode in ("rote", "live"):
            pointer = HERE / "plays/evidence/active.json"
            require(not pointer.exists() and not pointer.is_symlink(),
                    "fresh_maya_learning_requires_explicit_active_pointer_archive", True)

    def verify_rote_learning_chain(self, run, case):
        """Bind this acceptance's new recording and replay to canonical receipts."""
        speaker, run_id = case["speaker"], run["id"]
        operation = "learn" if speaker == "maya" else "replay"
        record = self.trace(run, "rote")
        require(record.get("status") == "verified", "rote_evidence_missing", True)
        if speaker == "maya" and record.get("operation") == "replay":
            raise CheckError("fresh_maya_learning_requires_explicit_active_pointer_archive", True)
        require(record.get("operation") == operation, "rote_learning_sequence_mismatch")
        result = run.get("operations", {}).get("execute", {}).get("result", {})
        evidence = record.get("evidence")
        require(isinstance(evidence, dict) and result.get("provider") == "rote"
                and result.get("status") == "verified" and result.get("operation") == operation
                and result.get("evidence") == evidence, "rote_trace_execution_result_mismatch")
        require(evidence.get("executionAttempted") is True and evidence.get("reconciliationRequired") is False,
                "rote_execution_unconfirmed")
        procedure, package = evidence.get("procedure"), evidence.get("package")
        require(isinstance(procedure, dict) and isinstance(procedure.get("id"), str)
                and re.fullmatch(r"cuepilot-[A-Za-z0-9_-]+", procedure["id"])
                and isinstance(procedure.get("sha256"), str) and re.fullmatch(r"[a-f0-9]{64}", procedure["sha256"])
                and package == "cuepilot/plays/learned/" + procedure["id"], "rote_package_proof_missing")
        receipts, captured = run.get("receipts"), evidence.get("capturedReceipts")
        require(isinstance(receipts, list) and len(receipts) == 3 and evidence.get("receipts") == receipts
                and isinstance(captured, list) and len(captured) == 3, "rote_canonical_receipts_mismatch")
        for index, (receipt, capture) in enumerate(zip(receipts, captured)):
            require(isinstance(receipt, dict) and receipt.get("ok") is True and receipt.get("runId") == run_id
                    and type(receipt.get("stepIndex")) is int and receipt["stepIndex"] == index
                    and receipt.get("scene") == SCENES[index] and type(receipt.get("stageRevision")) is int
                    and receipt["stageRevision"] > 0 and isinstance(receipt.get("id"), str) and bool(receipt["id"])
                    and isinstance(receipt.get("committedAt"), str) and bool(receipt["committedAt"]),
                    "rote_canonical_receipts_mismatch")
            digest = hashlib.sha256(json.dumps(receipt, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()
            request_id = "rote-" + hashlib.sha256(run_id.encode()).hexdigest()[:32] + "-" + str(index)
            require(isinstance(capture, dict) and capture.get("ok") is True and capture.get("runId") == run_id
                    and type(capture.get("stepIndex")) is int and capture["stepIndex"] == index
                    and capture.get("requestId") == request_id and capture.get("receiptId") == receipt["id"]
                    and capture.get("receiptCanonicalSha256") == digest and capture.get("scene") == receipt["scene"]
                    and type(capture.get("stageRevision")) is int and capture["stageRevision"] == receipt["stageRevision"]
                    and capture.get("committedAt") == receipt["committedAt"]
                    and isinstance(capture.get("receiptSha256"), str) and re.fullmatch(r"[a-f0-9]{64}", capture["receiptSha256"]),
                    "rote_captured_receipts_mismatch")
        require(len({receipt["id"] for receipt in receipts}) == 3
                and all(receipts[i + 1]["stageRevision"] == receipts[i]["stageRevision"] + 1 for i in range(2)),
                "rote_canonical_receipts_mismatch")
        identity, plan = evidence.get("identity"), run.get("plan", {})
        require(isinstance(identity, dict) and isinstance(run.get("notes"), str)
                and identity.get("noteSha256") == hashlib.sha256(run["notes"].encode()).hexdigest()
                and all(identity.get(key) == plan.get(key) for key in ("recipeId", "recipeVersion", "cues")),
                "rote_recipe_identity_mismatch")
        if speaker == "maya":
            require(evidence.get("learnedFromRunId") == run_id and evidence.get("replayExecuted") is False
                    and evidence.get("source") == "rote-workspace-export-after-successful-cues", "maya_learning_source_mismatch")
            references = [item.get("roteResponse") for item in captured]
            require(all(isinstance(ref, str) and re.fullmatch(r"@\d+", ref) for ref in references)
                    and len(set(references)) == 3, "maya_recording_references_mismatch")
            self.learned = {"runId": run_id, "procedure": dict(procedure), "package": package,
                            "identity": identity, "receiptIds": [r["id"] for r in receipts]}
        else:
            require(speaker == "ravi" and self.learned is not None, "maya_learning_not_verified", True)
            require(run_id != self.learned["runId"] and evidence.get("learnedFromRunId") == self.learned["runId"]
                    and evidence.get("runId") == run_id and evidence.get("newInput") is True
                    and evidence.get("newSpeaker") is True and evidence.get("replayExecuted") is True
                    and isinstance(evidence.get("roteRunId"), str) and re.fullmatch(r"run_[A-Za-z0-9_.-]+", evidence["roteRunId"]),
                    "ravi_replay_source_or_input_mismatch")
            require(procedure == self.learned["procedure"] and package == self.learned["package"]
                    and identity == self.learned["identity"], "ravi_replayed_different_package")
            require(set(self.learned["receiptIds"]).isdisjoint(r["id"] for r in receipts), "ravi_reused_maya_receipts")
        case["roteLearningAcceptance"] = {"operation": operation, "learnedFromRunId": evidence["learnedFromRunId"],
                                         "procedure": procedure, "package": package, "canonicalReceiptsMatched": True}

    async def request(self, method, path, body=None, *, bridge=False, expected=(200,), timeout=20):
        token = self.bridge if bridge else self.operator
        headers = {"Authorization": "Bearer " + token} if method != "GET" else {}
        try:
            async with self.client.stream(method, path, headers=headers, json=body, timeout=timeout) as response:
                require(response.status_code in expected, "api_auth_or_runtime_blocked" if response.status_code in (401, 403, 503) else "unexpected_http_status_" + str(response.status_code), response.status_code in (401, 403, 503))
                chunks, size = [], 0
                async for chunk in response.aiter_bytes():
                    size += len(chunk)
                    require(size <= 2_000_000, "api_response_too_large")
                    chunks.append(chunk)
                return json.loads(b"".join(chunks))
        except (httpx.HTTPError, OSError):
            raise CheckError("local_api_unavailable_or_timed_out", True) from None
        except (ValueError, UnicodeError):
            raise CheckError("invalid_api_json") from None

    async def snapshot(self, run_id, case):
        run = await self.request("GET", "runs/" + run_id)
        stage = await self.request("GET", "stage")
        marker = {"stageRevision": stage.get("revision"), "scene": stage.get("scene"), "speakerId": stage.get("speakerId"),
                  "runStatus": run.get("status"), "receiptCount": len(run.get("receipts", []))}
        if not case["observations"] or case["observations"][-1] != marker:
            case["observations"].append(marker)
        case["canonicalRun"], case["stage"] = run, stage
        return run, stage

    async def patch_alex(self, status):
        show = await self.request("GET", "show")
        return await self.request("PATCH", "assets/slides-alex", {"status": status, "expectedRevision": show["revision"]})

    @staticmethod
    def trace(run, provider, operation=None):
        matches = [record for record in run.get("traces", []) if record.get("provider") == provider
                   and (operation is None or record.get("operation") == operation)]
        return matches[-1] if matches else {}

    async def prepare(self, speaker, case):
        run = await self.request("POST", "runs", {"speakerId": speaker, "executionMode": "live" if self.mode == "live" else "practice"}, expected=(201,))
        run_id = run["id"]
        require(isinstance(run_id, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,80}", run_id), "invalid_run_id")
        case["runId"] = run_id
        if self.mode == "live":
            while True:
                run, _ = await self.snapshot(run_id, case)
                if run.get("status") in ("blocked", "failed"):
                    raise CheckError("live_preparation_blocked", True)
                if run.get("status") == "needs_approval" and self.trace(run, "rocketride", "prepare").get("status") == "verified":
                    break
                await asyncio.sleep(.2)
        plan = run.get("plan") or {}
        require(run.get("status") == "needs_approval", "plan_not_ready", True)
        require(plan.get("cues") == [{"index": i, "scene": scene} for i, scene in enumerate(SCENES)], "unexpected_cue_plan")
        require(plan.get("speakerId") == speaker and plan.get("origin") == ("sponsor" if self.mode == "live" else "fixture"), "plan_origin_or_speaker_mismatch")
        require(isinstance(plan.get("hash"), str) and re.fullmatch(r"[a-f0-9]{64}", plan["hash"]), "invalid_plan_hash")
        current = await self.request("GET", "show")
        require(plan.get("showRevision") == current.get("revision"), "plan_revision_stale", True)
        case["approvedPlan"] = plan
        approved = await self.request("POST", "runs/" + run_id + "/approve", {"planHash": plan["hash"]})
        require(approved.get("status") == "approved", "approval_not_confirmed")
        return run_id

    async def practice(self, run_id, case, interrupt):
        steps = 1 if interrupt else 3
        for index in range(steps):
            receipt = await self.request("POST", "runs/" + run_id + "/advance", {"requestId": uuid4().hex})
            require(receipt.get("runId") == run_id and receipt.get("stepIndex") == index and receipt.get("scene") == SCENES[index], "manual_cue_receipt_mismatch")
            await self.snapshot(run_id, case)
        if interrupt:
            case["assetMutationAttempted"] = True
            await self.patch_alex("missing")
            case["rejectedCue"] = await self.request("POST", "runs/" + run_id + "/advance", {"requestId": uuid4().hex}, expected=(409,))
            case["rejectedCueHttpStatus"] = 409

    async def automated(self, run_id, case, interrupt):
        endpoint = "tools/execute" if self.mode == "rote" else "runs/" + run_id + "/execute"
        task = asyncio.create_task(self.request("POST", endpoint, {"runId": run_id} if self.mode == "rote" else {},
                                                bridge=self.mode == "rote", expected=(200,) if self.mode == "rote" else (202,), timeout=self.seconds))
        patched = False
        try:
            while True:
                run, stage = await self.snapshot(run_id, case)
                receipts = run.get("receipts", [])
                if interrupt and receipts and not patched:
                    require(len(receipts) == 1 and receipts[0].get("scene") == "intro", "interruption_window_missed")
                    require(stage.get("scene") != "presentation", "presentation_before_interruption")
                    committed = datetime.fromisoformat(receipts[0]["committedAt"].replace("Z", "+00:00"))
                    case["assetMutationAttempted"] = True
                    await self.patch_alex("missing")
                    elapsed = (datetime.now(timezone.utc) - committed).total_seconds()
                    case["interruptionDelaySeconds"] = round(elapsed, 4)
                    require(0 <= elapsed < 1.5, "interruption_exceeded_1_5_second_dwell")
                    patched = True
                if interrupt:
                    require(not any(row.get("scene") == "presentation" for row in receipts), "unexpected_presentation_receipt")
                if task.done():
                    case["executionResponse"] = task.result()
                finished = (task.done() if self.mode == "rote" else run.get("orchestration", {}).get("execute", {}).get("status") in ("verified", "blocked", "failed"))
                if finished:
                    if run.get("status") in ("blocked", "failed") and not (interrupt and patched):
                        raise CheckError("automated_execution_blocked", True)
                    break
                await asyncio.sleep(.05 if interrupt else .2)
            require(not interrupt or patched, "interruption_never_reached_intro", True)
        finally:
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def case(self, speaker, interrupt=False):
        case = {"speaker": speaker, "expected": "interrupted_on_holding" if interrupt else "completed_three_cues", "status": "running", "observations": []}
        self.report["cases"].append(case)
        started = time.monotonic()
        try:
            async with asyncio.timeout(self.seconds):
                run_id = await self.prepare(speaker, case)
                if self.mode == "practice":
                    await self.practice(run_id, case, interrupt)
                else:
                    await self.automated(run_id, case, interrupt)
                run, stage = await self.snapshot(run_id, case)
                verify = await self.request("POST", "tools/verify", {"runId": run_id}, bridge=True)
                case["verifyResponse"] = verify
                receipts = run.get("receipts", [])
                expected = ["intro"] if interrupt else SCENES
                require([row.get("scene") for row in receipts] == expected and [row.get("stepIndex") for row in receipts] == list(range(len(expected))), "canonical_receipts_mismatch")
                require(all(row.get("runId") == run_id for row in receipts), "receipt_run_mismatch")
                require(stage.get("scene") == "holding", "stage_not_holding")
                require(run.get("status") == ("blocked" if interrupt else "completed"), "canonical_run_status_mismatch")
                require(verify.get("ok") is (not interrupt) and verify.get("receipts") == receipts, "verify_response_mismatch")
                if self.mode in ("rote", "live") and not interrupt:
                    self.verify_rote_learning_chain(run, case)
                if self.mode == "live" and not interrupt:
                    require(self.trace(run, "rocketride", "execute").get("status") == "verified", "rocketride_execution_evidence_missing", True)
                    require(all(self.trace(run, p).get("status") == "verified" for p in ("cognee", "hydradb", "hotdata")), "sponsor_evidence_missing", True)
                case["status"] = "passed"
        except TimeoutError:
            case.update(status="blocked", reason="per_run_deadline_exceeded_remote_work_may_continue")
            raise CheckError(case["reason"], True) from None
        except CheckError as error:
            case.update(status="blocked" if error.blocked else "failed", reason=error.code)
            raise
        finally:
            case["elapsedSeconds"] = round(time.monotonic() - started, 3)
            if case.get("assetMutationAttempted"):
                try:
                    async with asyncio.timeout(25):
                        restored = await self.patch_alex("ready")
                    case["assetRestored"] = any(a.get("id") == "slides-alex" and a.get("status") == "ready" for a in restored.get("assets", []))
                    require(case["assetRestored"], "asset_restore_unconfirmed")
                except Exception:
                    case.update(status="failed", restorationError="slides_alex_restoration_requires_operator_review")
                    raise CheckError("slides_alex_restoration_requires_operator_review") from None

    async def run(self):
        require(bool(self.operator and self.bridge), "operator_and_bridge_tokens_required", True)
        self.require_fresh_learning()
        async with httpx.AsyncClient(base_url=BASE, follow_redirects=False, trust_env=False) as self.client:
            health = await self.request("GET", "health")
            require(health.get("ok") is True and health.get("service") == "CuePilot", "cuepilot_runtime_required", True)
            require(health.get("operatorConfigured") and health.get("bridgeConfigured"), "api_tokens_unconfigured", True)
            stage = await self.request("GET", "stage")
            require(stage.get("scene") == "holding", "stage_already_in_use", True)
            runs = await self.request("GET", "runs")
            require(not any(run.get("status") in ("queued", "needs_approval", "approved", "running") for run in runs), "unfinished_run_requires_operator_review", True)
            show = await self.request("GET", "show")
            for speaker in ("maya", "ravi", "alex"):
                require(any(s.get("id") == speaker and s.get("ready") is True and s.get("presentationAssetId") == "slides-" + speaker for s in show.get("speakers", [])), "synthetic_fixture_not_ready", True)
                require(any(a.get("id") == "slides-" + speaker and a.get("status") == "ready" and a.get("kind") == "slide" for a in show.get("assets", [])), "synthetic_fixture_not_ready", True)
            self.report["initialShow"] = show
            for speaker in ("maya", "ravi", "alex"):
                await self.case(speaker, interrupt=speaker == "alex")
            self.report["status"] = "passed"


def main():
    parser = argparse.ArgumentParser(description="Explicit local CuePilot acceptance; executing a mode changes the synthetic stage. Live mode uses sponsor services.")
    parser.add_argument("mode", choices=tuple(LABELS))
    parser.add_argument("--timeout", type=int, default=600, help="Per-run deadline, 30–899 seconds (default 600).")
    args = parser.parse_args()
    if not 30 <= args.timeout < 900:
        parser.error("--timeout must be between 30 and 899 seconds")
    # Match API precedence: process environment, root .env, then CuePilot .env.
    env = {**dotenv_values(HERE / ".env"), **dotenv_values(HERE.parent / ".env"), **os.environ}
    runner = Runner(args.mode, args.timeout, env)
    print(LABELS[args.mode])
    try:
        asyncio.run(runner.run())
    except CheckError as error:
        runner.report.update(status="blocked" if error.blocked else "failed", reason=error.code)
    except KeyboardInterrupt:
        runner.report.update(status="blocked", reason="operator_interrupted_remote_work_may_continue")
    except Exception:
        runner.report.update(status="failed", reason="acceptance_error_private_details_withheld")
    runner.report["finishedAt"] = datetime.now(timezone.utc).isoformat()
    directory = HERE / ".runtime/acceptance"
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    # Report filenames depend only on generated values, never CLI input.
    path = directory / (datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ") + "_" + uuid4().hex + ".json")
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w") as handle:
        json.dump(redact(runner.report, runner.secrets), handle, indent=2)
        handle.write("\n")
    print("Acceptance " + runner.report["status"] + ". Evidence: " + str(path))
    return 0 if runner.report["status"] == "passed" else 2 if runner.report["status"] == "blocked" else 1


if __name__ == "__main__":
    raise SystemExit(main())

"""Local CuePilot API. Stage mutations are ordered, approved and idempotent."""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager, contextmanager
from datetime import datetime, timezone
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import sqlite3
import time
from typing import Literal
from uuid import uuid4

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, Header, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
DEFAULT_NOTES = (
    "For every speaker, show their introduction, then their presentation, then "
    "return to holding. If a speaker or presentation is unavailable, stay on holding."
)
TEMPLATE = "speaker-segment-v1"


def now():
    return datetime.now(timezone.utc).isoformat()


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def completed_receipts(run):
    receipts = run.get("receipts", [])
    return (run.get("status") == "completed" and len(receipts) == 3
            and all(r.get("ok") is True and r.get("runId") == run["id"] and r.get("stepIndex") == i
                    and r.get("scene") == scene and type(r.get("stageRevision")) is int
                    for i, (r, scene) in enumerate(zip(receipts, ("intro", "presentation", "holding"))))
            and len({r["id"] for r in receipts}) == 3
            and 0 < receipts[0]["stageRevision"] < receipts[1]["stageRevision"] < receipts[2]["stageRevision"])


class DomainError(Exception):
    def __init__(self, code, message, status=409):
        self.code, self.message, self.status = code, message, status


def initial_show():
    return {
        "id": "demo", "revision": 1, "title": "CuePilot - From rehearsal to recall",
        "speakers": [
            {"id": "maya", "name": "Maya Chen", "title": "Opening speaker", "ready": True, "presentationAssetId": "slides-maya"},
            {"id": "ravi", "name": "Ravi Shah", "title": "Product demo", "ready": True, "presentationAssetId": "slides-ravi"},
            {"id": "alex", "name": "Alex Rivera", "title": "Closing speaker", "ready": True, "presentationAssetId": "slides-alex"},
        ],
        "assets": [{"id": f"slides-{sid}", "title": title, "kind": "slide", "status": "ready"}
                   for sid, title in [("maya", "Learn from rehearsal"), ("ravi", "Reuse the successful sequence"), ("alex", "Check before the next cue")]],
    }


def holding(revision=0, reason=None):
    return {"revision": revision, "scene": "holding", "speakerId": None,
            "title": "We'll be right with you", "subtitle": "CuePilot",
            "assetId": None, "reason": reason, "updatedAt": now()}


class Store:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with self.db() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS runs (id TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS receipts (
                  run_id TEXT NOT NULL, step INTEGER NOT NULL, request_id TEXT NOT NULL,
                  value TEXT NOT NULL, PRIMARY KEY(run_id, step), UNIQUE(run_id, request_id));
                CREATE TABLE IF NOT EXISTS receipt_requests (
                  run_id TEXT NOT NULL, request_id TEXT NOT NULL, step INTEGER NOT NULL,
                  PRIMARY KEY(run_id, request_id));
            """)
            db.execute("INSERT OR IGNORE INTO state VALUES (?,?)", ("show", json.dumps(initial_show())))
            db.execute("INSERT OR IGNORE INTO state VALUES (?,?)", ("stage", json.dumps(holding())))
            db.execute("INSERT OR IGNORE INTO state VALUES (?,?)", ("activeRun", "null"))
            db.execute("INSERT OR IGNORE INTO state VALUES (?,?)", ("rules", json.dumps({"notes": DEFAULT_NOTES, "sha256": None})))
        self.path.chmod(0o600)

    @contextmanager
    def db(self):
        db = sqlite3.connect(self.path, timeout=10)
        try:
            db.execute("BEGIN IMMEDIATE")
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    @staticmethod
    def state(db, key):
        return json.loads(db.execute("SELECT value FROM state WHERE key=?", (key,)).fetchone()[0])

    @staticmethod
    def set_state(db, key, value):
        db.execute("UPDATE state SET value=? WHERE key=?", (json.dumps(value), key))

    @staticmethod
    def read_run(db, run_id):
        row = db.execute("SELECT value FROM runs WHERE id=?", (run_id,)).fetchone()
        if row is None:
            raise DomainError("run_not_found", "Run does not exist.", 404)
        return json.loads(row[0])

    @staticmethod
    def save_run(db, run):
        run["updatedAt"] = now()
        db.execute("UPDATE runs SET value=? WHERE id=?", (json.dumps(run), run["id"]))

    def get_state(self, key):
        with self.db() as db:
            return self.state(db, key)

    def get_run(self, run_id):
        with self.db() as db:
            run = self.read_run(db, run_id)
            run["verifiedCompletion"] = (run["executionMode"] == "live" and completed_receipts(run)
                                         and all(run.get("operations", {}).get(key, {}).get("status") == "verified"
                                                 for key in ("execute", "remember-outcome")))
            return run

    def list_runs(self):
        with self.db() as db:
            return [json.loads(row[0]) for row in db.execute("SELECT value FROM runs ORDER BY rowid DESC LIMIT 50")]

    @staticmethod
    def ready(show, speaker_id):
        speaker = next((s for s in show["speakers"] if s["id"] == speaker_id), None)
        if speaker is None:
            raise DomainError("speaker_not_found", "Speaker does not exist.", 404)
        asset = next((a for a in show["assets"] if a["id"] == speaker["presentationAssetId"]), None)
        if not speaker["ready"]:
            raise DomainError("speaker_unavailable", "The speaker is not ready. Stage stays on holding.")
        if asset is None or asset["status"] != "ready":
            raise DomainError("presentation_unavailable", "The presentation is unavailable. Stage stays on holding.")
        return speaker, asset

    def create_run(self, speaker_id, notes, execution_mode):
        with self.db() as db:
            show = self.state(db, "show")
            if not any(s["id"] == speaker_id for s in show["speakers"]):
                raise DomainError("speaker_not_found", "Speaker does not exist.", 404)
            rules = self.state(db, "rules")
            notes = notes if notes is not None else rules["notes"] if execution_mode == "live" else DEFAULT_NOTES
            if execution_mode == "live":
                note_hash = hashlib.sha256(notes.encode()).hexdigest()
                if rules["sha256"] is not None and rules["sha256"] != note_hash:
                    show["revision"] += 1
                    self.set_state(db, "show", show)
                    active_id = self.state(db, "activeRun")
                    if active_id:
                        reason = "Production rules changed. Prepare and approve a new run."
                        active = self.read_run(db, active_id)
                        active.update(status="blocked", reason=reason)
                        self.save_run(db, active)
                        stage = self.state(db, "stage")
                        self.set_state(db, "stage", holding(stage["revision"] + 1, reason))
                        self.set_state(db, "activeRun", None)
                self.set_state(db, "rules", {"notes": notes, "sha256": note_hash})
            run = {"id": str(uuid4()), "showId": show["id"], "speakerId": speaker_id,
                   "executionMode": execution_mode, "status": "queued", "notes": notes,
                   "plan": None, "nextStep": 0, "receipts": [], "traces": [], "reason": None,
                   "createdAt": now(), "updatedAt": now()}
            db.execute("INSERT INTO runs VALUES (?,?)", (run["id"], json.dumps(run)))
        if execution_mode == "practice":
            self.add_trace(run["id"], {"provider": "local", "status": "fixture", "operation": "prepare",
                                     "evidence": {"template": TEMPLATE}, "reason": "Local UI rehearsal; sponsor pipeline has not executed."})
            return self.plan(run["id"])
        return run

    def add_trace(self, run_id, result, memory_operation=None):
        records = result.get("records", [result])
        with self.db() as db:
            run = self.read_run(db, run_id)
            for record in records:
                run["traces"].append({k: record.get(k) for k in ["provider", "status", "operation", "evidence", "reason"]})
            if memory_operation:
                proof = {key: result.get(key) for key in ("note_sha256", "graph_sha256", "recipe_id", "source_id")}
                valid = (result.get("status") == "verified" and result.get("supported_template") == TEMPLATE
                         and proof["note_sha256"] == hashlib.sha256(run["notes"].encode()).hexdigest()
                         and proof["source_id"] == run["showId"]
                         and isinstance(proof["graph_sha256"], str)
                         and re.fullmatch(r"[a-f0-9]{64}", proof["graph_sha256"])
                         and isinstance(proof["recipe_id"], str) and bool(proof["recipe_id"]))
                run.pop("memoryProof", None)
                if memory_operation == "ingest-memory":
                    run["ingestedMemoryProof"] = proof if valid else None
                elif valid and proof == run.get("ingestedMemoryProof"):
                    run["memoryProof"] = proof
                else:
                    valid = False
                if not valid:
                    run.update(status="blocked", reason="Memory evidence does not match this run's current rule and graph.")
            if result.get("status") == "verified" and isinstance(result.get("show_revision"), int):
                run["validatedShowRevision"] = result["show_revision"]
            if result.get("status") in ("failed", "blocked") and run["status"] != "completed":
                run["status"] = "blocked"
                run["reason"] = result.get("reason") or "A required sponsor operation is not verified."
            if run["status"] == "blocked" and self.state(db, "activeRun") == run_id:
                stage = self.state(db, "stage")
                self.set_state(db, "stage", holding(stage["revision"] + 1, run["reason"]))
                self.set_state(db, "activeRun", None)
            self.save_run(db, run)
        return run

    def claim_orchestration(self, run_id, phase):
        with self.db() as db:
            run = self.read_run(db, run_id)
            expected = "queued" if phase == "prepare" else "approved"
            if run["executionMode"] != "live" or run["status"] != expected:
                raise DomainError("orchestration_not_expected", "This run is not ready for that orchestration phase.")
            jobs = run.setdefault("orchestration", {})
            if phase in jobs:
                raise DomainError("orchestration_already_started", "This phase has already been dispatched. Inspect its run before retrying.")
            jobs[phase] = {"status": "running", "startedAt": now()}
            self.save_run(db, run)

    def finish_orchestration(self, run_id, phase, status):
        with self.db() as db:
            run = self.read_run(db, run_id)
            run.setdefault("orchestration", {}).setdefault(phase, {}).update(status=status, finishedAt=now())
            self.save_run(db, run)

    def claim_operation(self, run_id, operation):
        """Claim external work before awaiting it; repeated completed calls return evidence."""
        with self.db() as db:
            run = self.read_run(db, run_id)
            execution_validation = operation == "validate-show" and run["status"] in ("approved", "running", "completed")
            key = ("validate-execute" if execution_validation else "validate-prepare") if operation == "validate-show" else operation
            operations = run.setdefault("operations", {})
            previous = operations.get(key)
            if previous:
                if previous["status"] == "verified":
                    if operation != "validate-show" or previous["result"].get("show_revision") == self.state(db, "show")["revision"]:
                        return key, previous["result"]
                elif previous["status"] == "running":
                    raise DomainError("operation_in_progress", "This operation is already running.")
            if key == "remember-outcome":
                if not completed_receipts(run) or operations.get("execute", {}).get("status") != "verified":
                    raise DomainError("verified_execution_required", "Memory write-back requires a verified execution and completed receipts.")
            elif run["status"] in ("blocked", "failed", "completed"):
                raise DomainError("run_not_active", "Create a new run after reviewing this result.")
            prerequisites = {"ingest-memory": None, "recall-recipe": "ingest-memory", "validate-prepare": "recall-recipe", "execute": "validate-execute"}
            if key in ("ingest-memory", "recall-recipe", "validate-prepare") and (run["executionMode"] != "live" or run["status"] != "queued"):
                raise DomainError("prepare_not_expected", "Memory preparation requires a queued live run.")
            prerequisite = prerequisites.get(key)
            if run["executionMode"] == "live" and prerequisite and operations.get(prerequisite, {}).get("status") != "verified":
                raise DomainError("operation_prerequisite", "Complete the preceding verified operation first.")
            if key in ("execute", "validate-execute"):
                if run["status"] != "approved" or not run["plan"]:
                    raise DomainError("approved_plan_required", "This operation requires an approved plan.")
                show = self.state(db, "show")
                self.current_rule(db, run)
                self.ready(show, run["speakerId"])
                if run["plan"]["showRevision"] != show["revision"]:
                    raise DomainError("show_changed", "Show state changed after approval; prepare a new run.")
                if key == "execute":
                    if run["executionMode"] == "live" and (run.get("validatedShowRevision") != show["revision"] or run.get("executionValidatedPlanHash") != run["plan"]["hash"]):
                        raise DomainError("fresh_validation_required", "Validate current readiness after approval before execution.")
                    owner = self.state(db, "activeRun")
                    if owner is not None and owner != run_id:
                        raise DomainError("stage_busy", "Another segment owns the stage.")
                    self.set_state(db, "activeRun", run_id)
            operations[key] = {"status": "running", "startedAt": now()}
            self.save_run(db, run)
            return key, None

    def finish_operation(self, run_id, key, result):
        with self.db() as db:
            run = self.read_run(db, run_id)
            run.setdefault("operations", {}).setdefault(key, {}).update(status=result.get("status", "failed"), result=result, finishedAt=now())
            if key == "validate-execute" and result.get("status") == "verified" and run["plan"]:
                run["executionValidatedPlanHash"] = run["plan"]["hash"]
            self.save_run(db, run)

    def recover_interrupted(self):
        with self.db() as db:
            for (raw,) in db.execute("SELECT value FROM runs").fetchall():
                run = json.loads(raw)
                interrupted = [item for group in ("operations", "orchestration") for item in run.get(group, {}).values() if item.get("status") == "running"]
                if not interrupted:
                    continue
                for item in interrupted:
                    item.update(status="failed", finishedAt=now())
                if run["status"] != "completed":
                    run.update(status="blocked", reason="The service restarted during execution. Review receipts before starting a new run.")
                    if self.state(db, "activeRun") == run["id"]:
                        stage = self.state(db, "stage")
                        self.set_state(db, "stage", holding(stage["revision"] + 1, run["reason"]))
                        self.set_state(db, "activeRun", None)
                self.save_run(db, run)

    def plan(self, run_id):
        with self.db() as db:
            run = self.read_run(db, run_id)
            if run["status"] not in ("queued", "needs_approval"):
                raise DomainError("plan_frozen", "Create a new run to revise an approved plan.")
            show = self.state(db, "show")
            self.ready(show, run["speakerId"])
            if run["executionMode"] == "live":
                latest = {r["provider"]: r["status"] for r in run["traces"]}
                verified = {provider for provider, status in latest.items() if status == "verified"}
                if not {"cognee", "hydradb", "hotdata"}.issubset(verified) or not run.get("memoryProof"):
                    raise DomainError("evidence_required", "Live planning needs Cognee-derived cue rules, Hydra provenance and fresh Hotdata checks.")
                self.current_rule(db, run)
                if run.get("validatedShowRevision") != show["revision"]:
                    raise DomainError("fresh_validation_required", "Show state changed after the Hotdata query. Validate it again.")
            plan = {"id": str(uuid4()), "recipeId": run["memoryProof"]["recipe_id"] if run["executionMode"] == "live" else TEMPLATE, "recipeVersion": 1,
                    "showRevision": show["revision"], "speakerId": run["speakerId"],
                    "cues": [{"index": i, "scene": scene} for i, scene in enumerate(["intro", "presentation", "holding"])],
                    "origin": "fixture" if run["executionMode"] == "practice" else "sponsor"}
            plan["hash"] = digest({"plan": plan, "notes": run["notes"], "runId": run["id"]})
            run.update(plan=plan, status="needs_approval", reason=None)
            self.save_run(db, run)
            return run

    def approve(self, run_id, plan_hash):
        with self.db() as db:
            run = self.read_run(db, run_id)
            if run["status"] != "needs_approval" or not run["plan"]:
                raise DomainError("approval_not_expected", "This run has no pending plan to approve.")
            if not hmac.compare_digest(run["plan"]["hash"], plan_hash):
                raise DomainError("plan_changed", "Approval must match the exact displayed plan.")
            show = self.state(db, "show")
            self.current_rule(db, run)
            self.ready(show, run["speakerId"])
            if run["plan"]["showRevision"] != show["revision"]:
                raise DomainError("show_changed", "Show state changed. Prepare a new plan before approving.")
            run["status"] = "approved"
            self.save_run(db, run)
            return run

    def current_rule(self, db, run):
        if run["executionMode"] == "live" and self.state(db, "rules")["sha256"] != run.get("memoryProof", {}).get("note_sha256"):
            raise DomainError("production_rules_changed", "Production rules changed. Prepare and approve a new run.")

    def mutate_readiness(self, group, item_id, field, value, expected_revision):
        with self.db() as db:
            show = self.state(db, "show")
            if show["revision"] != expected_revision:
                raise DomainError("show_changed", "Refresh the show before changing readiness.")
            item = next((item for item in show[group] if item["id"] == item_id), None)
            if not item:
                raise DomainError("item_not_found", "Show item does not exist.", 404)
            if item[field] == value:
                return show
            item[field] = value
            show["revision"] += 1
            self.set_state(db, "show", show)
            stage = self.state(db, "stage")
            if stage["speakerId"]:
                try:
                    self.ready(show, stage["speakerId"])
                except DomainError as error:
                    self.set_state(db, "stage", holding(stage["revision"] + 1, error.message))
                    active_id = self.state(db, "activeRun")
                    if active_id:
                        active = self.read_run(db, active_id)
                        active.update(status="blocked", reason=error.message)
                        self.save_run(db, active)
                        self.set_state(db, "activeRun", None)
            return show

    def cancel(self, run_id):
        with self.db() as db:
            run = self.read_run(db, run_id)
            if run["status"] in ("completed", "blocked", "failed"):
                return run
            run.update(status="blocked", reason="Cancelled by the operator. Prepare a new run to continue.")
            if self.state(db, "activeRun") == run_id:
                stage = self.state(db, "stage")
                self.set_state(db, "stage", holding(stage["revision"] + 1, run["reason"]))
                self.set_state(db, "activeRun", None)
            run["traces"].append({"provider": "local", "status": "verified", "operation": "cancel",
                                   "reason": run["reason"], "evidence": {"committedReceipts": len(run["receipts"])}})
            self.save_run(db, run)
            return run

    def cue(self, run_id, step_index, request_id, practice_only=False):
        problem = None
        receipt = None
        with self.db() as db:
            run = self.read_run(db, run_id)
            if practice_only and run["executionMode"] != "practice":
                raise DomainError("live_driver_required", "Live cues are executed through RocketRide and Rote.")
            existing = db.execute("SELECT value,step FROM receipts WHERE run_id=? AND request_id=?", (run_id, request_id)).fetchone()
            if existing is None:
                existing = db.execute("SELECT r.value,r.step FROM receipt_requests a JOIN receipts r ON r.run_id=a.run_id AND r.step=a.step WHERE a.run_id=? AND a.request_id=?", (run_id, request_id)).fetchone()
            if existing:
                if step_index is not None and existing[1] != step_index:
                    raise DomainError("request_id_conflict", "The request ID already belongs to another cue.")
                return json.loads(existing[0])
            step = run["nextStep"] if step_index is None else step_index
            existing = db.execute("SELECT value FROM receipts WHERE run_id=? AND step=?", (run_id, step)).fetchone()
            if existing:
                db.execute("INSERT INTO receipt_requests VALUES (?,?,?)", (run_id, request_id, step))
                return json.loads(existing[0])
            if run["status"] not in ("approved", "running") or not run["plan"]:
                raise DomainError("approved_plan_required", "This cue requires an approved, active plan.")
            if run["executionMode"] == "live" and run.get("operations", {}).get("execute", {}).get("status") != "running":
                raise DomainError("live_driver_required", "Live cues require an active, claimed Rote execution.")
            if step != run["nextStep"] or step >= 3:
                raise DomainError("cue_out_of_order", "Execute only the next approved cue.")
            active_id = self.state(db, "activeRun")
            if active_id is not None and active_id != run_id:
                raise DomainError("stage_busy", "Another speaker's segment is active. Finish or hold that segment first.")
            show, stage = self.state(db, "show"), self.state(db, "stage")
            try:
                speaker, asset = self.ready(show, run["speakerId"])
                if run["plan"]["showRevision"] != show["revision"]:
                    raise DomainError("show_changed", "Show state changed after approval. Rehearsal must be checked again.")
                if run["executionMode"] == "live" and self.state(db, "rules")["sha256"] != run.get("memoryProof", {}).get("note_sha256"):
                    raise DomainError("rules_changed", "The current production rule differs from this approved procedure.")
            except DomainError as error:
                problem = error
                run.update(status="blocked", reason=error.message)
                self.set_state(db, "stage", holding(stage["revision"] + 1, error.message))
                self.set_state(db, "activeRun", None)
                self.save_run(db, run)
            if problem is None:
                scene = run["plan"]["cues"][step]["scene"]
                updated = holding(stage["revision"] + 1)
                if scene != "holding":
                    updated.update(scene=scene, speakerId=speaker["id"], title=speaker["name"] if scene == "intro" else asset["title"],
                                   subtitle=speaker["title"] if scene == "intro" else speaker["name"],
                                   assetId=None if scene == "intro" else asset["id"])
                self.set_state(db, "stage", updated)
                self.set_state(db, "activeRun", None if scene == "holding" else run_id)
                receipt = {"ok": True, "id": str(uuid4()), "runId": run_id, "stepIndex": step,
                           "scene": scene, "stageRevision": updated["revision"], "committedAt": now()}
                db.execute("INSERT INTO receipts VALUES (?,?,?,?)", (run_id, step, request_id, json.dumps(receipt)))
                run["receipts"].append(receipt)
                run["nextStep"] += 1
                run["status"] = "completed" if run["nextStep"] == 3 else "running"
                self.save_run(db, run)
        if problem:
            raise problem
        return receipt


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class NewRun(StrictModel):
    speakerId: str = Field(min_length=1, max_length=80)
    executionMode: Literal["practice", "live"] = "practice"
    notes: str | None = Field(default=None, min_length=1, max_length=10000)


class Approval(StrictModel):
    planHash: str = Field(min_length=64, max_length=64, pattern=r"^[a-f0-9]+$")


class Advance(StrictModel):
    requestId: str = Field(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9_-]+$")
    stepIndex: int | None = Field(default=None, ge=0, le=2)


class CueCommand(Advance):
    runId: str = Field(min_length=1, max_length=80)
    stepIndex: int = Field(ge=0, le=2)


class ToolCommand(StrictModel):
    runId: str = Field(min_length=1, max_length=80)


class AssetUpdate(StrictModel):
    status: Literal["ready", "missing"]
    expectedRevision: int = Field(ge=1)


class SpeakerUpdate(StrictModel):
    ready: bool
    expectedRevision: int = Field(ge=1)


def create_app(db_path=None, operator_token=None, bridge_token=None):
    load_dotenv(ROOT / ".env", override=False)
    load_dotenv(HERE / ".env", override=False)
    operator_token = operator_token or os.getenv("CUEPILOT_OPERATOR_TOKEN", "")
    bridge_token = bridge_token or os.getenv("CUEPILOT_BRIDGE_TOKEN", "")
    store = Store(db_path or HERE / ".runtime" / "cuepilot.sqlite3")
    store.recover_interrupted()
    tasks = set()
    run_tasks = {}

    @asynccontextmanager
    async def lifespan(_):
        yield
        pending = list(tasks)
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)

    app = FastAPI(title="CuePilot", version="0.1.0", lifespan=lifespan)
    app.state.store = store

    @app.exception_handler(DomainError)
    async def domain_error(_, error):
        return JSONResponse(status_code=error.status, content={"detail": {"code": error.code, "message": error.message}})

    @app.exception_handler(RequestValidationError)
    async def invalid_request(_, error):
        return JSONResponse(status_code=422, content={"detail": {"code": "invalid_request", "message": "Request fields do not match the API contract."}})

    @app.middleware("http")
    async def request_limits(request: Request, call_next):
        if request.method not in ("GET", "HEAD"):
            # Bound buffered JSON bodies, including requests without Content-Length.
            total = 0
            chunks = []
            async for chunk in request.stream():
                total += len(chunk)
                if total > 32768:
                    return JSONResponse(status_code=413, content={"detail": {"code": "request_too_large", "message": "Request exceeds 32 KiB."}})
                chunks.append(chunk)
            request._body = b"".join(chunks)
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    def auth_dependency(expected):
        async def authenticate(authorization: str | None = Header(default=None)):
            if not expected:
                raise DomainError("authentication_unconfigured", "Configure the local service token before mutations.", 503)
            supplied = authorization[7:] if authorization and authorization.startswith("Bearer ") else ""
            if not supplied or not hmac.compare_digest(expected.encode(), supplied.encode()):
                raise DomainError("unauthorized", "A valid service token is required.", 401)
        return authenticate

    operator = Depends(auth_dependency(operator_token))
    bridge = Depends(auth_dependency(bridge_token))

    async def launch_rocketride(run_id, phase):
        script = HERE / "integrations" / "rocketride.mjs"
        if not script.exists():
            store.add_trace(run_id, {"provider": "rocketride", "status": "blocked", "operation": phase,
                                      "reason": "RocketRide runner is not available.", "evidence": None})
            return
        status = "blocked"
        process = None
        communication = None

        async def stop_runner():
            if process is None or process.returncode is not None:
                return
            process.terminate()
            try:
                await asyncio.wait_for(asyncio.shield(communication), timeout=40)
            except asyncio.TimeoutError:
                if process.returncode is None:
                    process.kill()
                await process.wait()
                await communication

        try:
            process = await asyncio.create_subprocess_exec("node", str(script), "--run", run_id, "--phase", phase,
                                                          cwd=ROOT, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
            communication = asyncio.create_task(process.communicate())
            try:
                stdout, _ = await asyncio.wait_for(asyncio.shield(communication), timeout=540)
            except asyncio.TimeoutError:
                await stop_runner()
                raise DomainError("orchestration_timeout", "RocketRide run timed out.")
            # Canonical run/receipts, never the model's prose, determine success.
            report = json.loads(stdout) if len(stdout) <= 65536 else {}
            expected = "needs_approval" if phase == "prepare" else "completed"
            current = store.get_run(run_id)
            accepted = current["status"] == expected
            if phase == "execute":
                accepted = accepted and completed_receipts(current) and current.get("operations", {}).get("remember-outcome", {}).get("status") == "verified"
            status = "verified" if (process.returncode == 0 and report.get("ok") is True and accepted) else "blocked"
            evidence = {"runnerExitCode": process.returncode}
            for key in ("elapsedMs", "taskStarted", "taskStartAttempted", "freshExecution", "reconciliationRequired"):
                value = report.get(key)
                if isinstance(value, (int, float, bool)):
                    evidence[key] = value
            evidence["blockers"] = [code for code in report.get("blockers", []) if isinstance(code, str) and re.fullmatch(r"[A-Z0-9_]{1,100}", code)] if isinstance(report.get("blockers"), list) else []
            store.add_trace(run_id, {"provider": "rocketride", "status": status, "operation": phase,
                                      "reason": None if status == "verified" else "RocketRide execution needs configuration or failed; inspect its local check report.",
                                      "evidence": evidence})
        except asyncio.CancelledError:
            await stop_runner()
            store.add_trace(run_id, {"provider": "rocketride", "status": "failed", "operation": phase,
                                      "reason": "Orchestration was interrupted; review receipts before another run.", "evidence": None})
            raise
        except (OSError, DomainError, ValueError, TypeError):
            store.add_trace(run_id, {"provider": "rocketride", "status": "blocked", "operation": phase,
                                      "reason": "RocketRide could not complete this operation.", "evidence": None})
        finally:
            store.finish_orchestration(run_id, phase, status)

    def track(run_id, task):
        tasks.add(task)
        run_tasks.setdefault(run_id, set()).add(task)
        def finished(done):
            tasks.discard(done)
            run_tasks.get(run_id, set()).discard(done)
            if not run_tasks.get(run_id):
                run_tasks.pop(run_id, None)
        task.add_done_callback(finished)

    def dispatch(run_id, phase):
        store.claim_orchestration(run_id, phase)
        track(run_id, asyncio.create_task(launch_rocketride(run_id, phase)))

    async def perform(run_id, operation, provider, action, memory_operation=None):
        key, cached = store.claim_operation(run_id, operation)
        if cached is not None:
            return cached
        task = asyncio.create_task(perform_claimed(run_id, operation, provider, action, memory_operation, key))
        track(run_id, task)
        return await task

    async def perform_claimed(run_id, operation, provider, action, memory_operation, key):
        started = time.monotonic()
        try:
            result = await action()
            if not isinstance(result, dict) or result.get("status") not in ("verified", "failed", "blocked"):
                raise ValueError("invalid provider result")
            if operation == "execute" and result["status"] == "verified" and not completed_receipts(store.get_run(run_id)):
                raise ValueError("execution lacks matching completed receipts")
        except asyncio.CancelledError:
            result = {"provider": provider, "status": "failed", "operation": operation,
                      "reason": "Operation interrupted; inspect receipts before another run.", "evidence": None}
            store.add_trace(run_id, result, memory_operation)
            store.finish_operation(run_id, key, result)
            raise
        except Exception:
            result = {"provider": provider, "status": "failed", "operation": operation,
                      "reason": "Provider operation failed; private error details were withheld.", "evidence": None}
        result["elapsedMs"] = round((time.monotonic() - started) * 1000)
        updated = store.add_trace(run_id, result, memory_operation)
        if updated["status"] == "blocked" and result["status"] == "verified":
            result = {"provider": provider, "status": "blocked", "operation": operation,
                      "reason": updated["reason"], "evidence": None, "elapsedMs": result["elapsedMs"]}
        store.finish_operation(run_id, key, result)
        return result

    @app.get("/api/v1/health")
    def health():
        return {"ok": True, "service": "CuePilot", "version": "0.1.0", "operatorConfigured": bool(operator_token), "bridgeConfigured": bool(bridge_token)}

    @app.get("/api/v1/show")
    def show():
        return store.get_state("show")

    @app.get("/api/v1/stage")
    def stage():
        return store.get_state("stage")

    @app.get("/api/v1/runs")
    def runs():
        return store.list_runs()

    @app.get("/api/v1/runs/{run_id}")
    def run(run_id: str):
        return store.get_run(run_id)

    @app.post("/api/v1/runs", dependencies=[operator], status_code=201)
    async def new_run(command: NewRun):
        run = store.create_run(command.speakerId, command.notes, command.executionMode)
        if command.executionMode == "live":
            dispatch(run["id"], "prepare")
        return run

    @app.post("/api/v1/runs/{run_id}/approve", dependencies=[operator])
    def approve(run_id: str, command: Approval):
        return store.approve(run_id, command.planHash)

    @app.post("/api/v1/runs/{run_id}/advance", dependencies=[operator])
    def advance(run_id: str, command: Advance):
        return store.cue(run_id, command.stepIndex, command.requestId, practice_only=True)

    @app.post("/api/v1/runs/{run_id}/execute", dependencies=[operator], status_code=202)
    async def execute(run_id: str):
        run = store.get_run(run_id)
        if run["executionMode"] != "live" or run["status"] != "approved":
            raise DomainError("approved_live_run_required", "Execute requires an approved live run.")
        dispatch(run_id, "execute")
        return run

    @app.post("/api/v1/runs/{run_id}/cancel", dependencies=[operator])
    async def cancel(run_id: str):
        store.cancel(run_id)
        pending = list(run_tasks.get(run_id, ()))
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        return store.get_run(run_id)

    @app.patch("/api/v1/assets/{asset_id}", dependencies=[operator])
    def asset(asset_id: str, command: AssetUpdate):
        return store.mutate_readiness("assets", asset_id, "status", command.status, command.expectedRevision)

    @app.patch("/api/v1/speakers/{speaker_id}", dependencies=[operator])
    def speaker(speaker_id: str, command: SpeakerUpdate):
        return store.mutate_readiness("speakers", speaker_id, "ready", command.ready, command.expectedRevision)

    @app.post("/api/v1/tools/stage/cue", dependencies=[bridge])
    def tool_cue(command: CueCommand):
        return store.cue(command.runId, command.stepIndex, command.requestId)

    @app.post("/api/v1/tools/plan", dependencies=[bridge])
    def tool_plan(command: ToolCommand):
        return store.plan(command.runId)

    @app.post("/api/v1/tools/ingest-memory", dependencies=[bridge])
    async def tool_ingest(command: ToolCommand):
        from cuepilot.integrations.memory import ingest_note
        run = store.get_run(command.runId)
        return await perform(command.runId, "ingest-memory", "cognee", lambda: ingest_note(run["notes"], run["showId"]), "ingest-memory")

    @app.post("/api/v1/tools/recall-recipe", dependencies=[bridge])
    async def tool_recall(command: ToolCommand):
        from cuepilot.integrations.memory import recall_recipe
        run = store.get_run(command.runId)
        return await perform(command.runId, "recall-recipe", "hydradb", lambda: recall_recipe(run["showId"]), "recall-recipe")

    @app.post("/api/v1/tools/validate-show", dependencies=[bridge])
    async def tool_validate(command: ToolCommand):
        from cuepilot.integrations.hotdata import validate_show
        run = store.get_run(command.runId)
        return await perform(command.runId, "validate-show", "hotdata", lambda: validate_show(store.get_state("show"), run["speakerId"]))

    @app.post("/api/v1/tools/execute", dependencies=[bridge])
    async def tool_execute(command: ToolCommand):
        from cuepilot.integrations import rote
        run = store.get_run(command.runId)
        # Rote decides whether an actual learned play is available; no simulated replay.
        async def execute_rote():
            result = await rote.replay(run["id"], "http://127.0.0.1:8787")
            if result.get("status") == "blocked" and result.get("reason") == "learned_play_required":
                result = await rote.learn(run["id"], "http://127.0.0.1:8787")
            return result
        return await perform(command.runId, "execute", "rote", execute_rote)

    @app.post("/api/v1/tools/verify", dependencies=[bridge])
    async def tool_verify(command: ToolCommand):
        run = store.get_run(command.runId)
        good = completed_receipts(run)
        if run["executionMode"] == "live":
            good = good and run.get("operations", {}).get("execute", {}).get("status") == "verified"
            if good:
                from cuepilot.integrations.memory import record_successful_outcome
                execution = run["operations"]["execute"]["result"]
                proof = run.get("memoryProof", {})
                async def remember():
                    return await record_successful_outcome(
                        source_id=proof.get("source_id"), note_sha256=proof.get("note_sha256"),
                        graph_sha256=proof.get("graph_sha256"), recipe_id=proof.get("recipe_id"),
                        run=run, rote_result=execution, procedure=execution.get("evidence", {}).get("procedure"))
                result = await perform(command.runId, "remember-outcome", "hydradb", remember)
                good = result.get("status") == "verified"
        return {"ok": good, "runId": run["id"], "status": run["status"], "receipts": run["receipts"]}

    return app


def main():
    import uvicorn
    if not (HERE / ".env").exists():
        # Keys are local runtime state, never printed or committed.
        descriptor = os.open(HERE / ".env", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w") as handle:
            handle.write(f"CUEPILOT_OPERATOR_TOKEN={secrets.token_urlsafe(32)}\nCUEPILOT_BRIDGE_TOKEN={secrets.token_urlsafe(32)}\n")
    uvicorn.run(create_app(), host="127.0.0.1", port=8787, access_log=False)


if __name__ == "__main__":
    main()

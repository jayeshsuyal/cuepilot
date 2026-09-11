"""Local CuePilot API. Stage mutations are ordered, approved and idempotent."""
from __future__ import annotations

import asyncio
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import hmac
import json
import os
from pathlib import Path
import secrets
import sqlite3
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
            """)
            db.execute("INSERT OR IGNORE INTO state VALUES (?,?)", ("show", json.dumps(initial_show())))
            db.execute("INSERT OR IGNORE INTO state VALUES (?,?)", ("stage", json.dumps(holding())))
            db.execute("INSERT OR IGNORE INTO state VALUES (?,?)", ("activeRun", "null"))
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
            return self.read_run(db, run_id)

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

    def add_trace(self, run_id, result):
        records = result.get("records", [result])
        with self.db() as db:
            run = self.read_run(db, run_id)
            for record in records:
                run["traces"].append({k: record.get(k) for k in ["provider", "status", "operation", "evidence", "reason"]})
            if (result.get("supported_template") == TEMPLATE
                    and result.get("note_sha256") == hashlib.sha256(run["notes"].encode()).hexdigest()):
                run["supportedTemplate"] = TEMPLATE
            if result.get("status") == "verified" and isinstance(result.get("show_revision"), int):
                run["validatedShowRevision"] = result["show_revision"]
            if result.get("status") in ("failed", "blocked") and run["status"] != "completed":
                run["status"] = "blocked"
                run["reason"] = result.get("reason") or "A required sponsor operation is not verified."
            self.save_run(db, run)
        return run

    def plan(self, run_id):
        with self.db() as db:
            run = self.read_run(db, run_id)
            if run["status"] in ("approved", "running", "completed"):
                raise DomainError("plan_frozen", "Create a new run to revise an approved plan.")
            show = self.state(db, "show")
            self.ready(show, run["speakerId"])
            if run["executionMode"] == "live":
                latest = {r["provider"]: r["status"] for r in run["traces"]}
                verified = {provider for provider, status in latest.items() if status == "verified"}
                if not {"cognee", "hydradb", "hotdata"}.issubset(verified) or run.get("supportedTemplate") != TEMPLATE:
                    raise DomainError("evidence_required", "Live planning needs Cognee-derived cue rules, Hydra provenance and fresh Hotdata checks.")
                if run.get("validatedShowRevision") != show["revision"]:
                    raise DomainError("fresh_validation_required", "Show state changed after the Hotdata query. Validate it again.")
            plan = {"id": str(uuid4()), "recipeId": TEMPLATE, "recipeVersion": 1,
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
            self.ready(show, run["speakerId"])
            if run["plan"]["showRevision"] != show["revision"]:
                raise DomainError("show_changed", "Show state changed. Prepare a new plan before approving.")
            run["status"] = "approved"
            self.save_run(db, run)
            return run

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

    def cue(self, run_id, step_index, request_id, practice_only=False):
        problem = None
        receipt = None
        with self.db() as db:
            run = self.read_run(db, run_id)
            if practice_only and run["executionMode"] != "practice":
                raise DomainError("live_driver_required", "Live cues are executed through RocketRide and Rote.")
            existing = db.execute("SELECT value,step FROM receipts WHERE run_id=? AND request_id=?", (run_id, request_id)).fetchone()
            if existing:
                if step_index is not None and existing[1] != step_index:
                    raise DomainError("request_id_conflict", "The request ID already belongs to another cue.")
                return json.loads(existing[0])
            step = run["nextStep"] if step_index is None else step_index
            existing = db.execute("SELECT value FROM receipts WHERE run_id=? AND step=?", (run_id, step)).fetchone()
            if existing:
                return json.loads(existing[0])
            if run["status"] not in ("approved", "running") or not run["plan"]:
                raise DomainError("approved_plan_required", "This cue requires an approved, active plan.")
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
    notes: str = Field(default=DEFAULT_NOTES, min_length=1, max_length=10000)


class Approval(StrictModel):
    planHash: str = Field(min_length=64, max_length=64, pattern=r"^[a-f0-9]+$")


class Advance(StrictModel):
    requestId: str = Field(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9_-]+$")


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
    app = FastAPI(title="CuePilot", version="0.1.0")
    app.state.store = store
    tasks = set()

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
            if not supplied or not hmac.compare_digest(expected, supplied):
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
        try:
            process = await asyncio.create_subprocess_exec("node", str(script), "--run", run_id, "--phase", phase,
                                                          cwd=ROOT, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
            try:
                stdout, _ = await asyncio.wait_for(process.communicate(), timeout=420)
            except asyncio.TimeoutError:
                process.kill()
                await process.wait()
                raise DomainError("orchestration_timeout", "RocketRide run timed out.")
            # Canonical run/receipts, never the model's prose, determine success.
            status = "verified" if process.returncode == 0 else "blocked"
            store.add_trace(run_id, {"provider": "rocketride", "status": status, "operation": phase,
                                      "reason": None if status == "verified" else "RocketRide execution needs configuration or failed; inspect its local check report.",
                                      "evidence": {"runnerExitCode": process.returncode}})
        except (OSError, DomainError):
            store.add_trace(run_id, {"provider": "rocketride", "status": "blocked", "operation": phase,
                                      "reason": "RocketRide could not complete this operation.", "evidence": None})

    def dispatch(run_id, phase):
        task = asyncio.create_task(launch_rocketride(run_id, phase))
        tasks.add(task)
        task.add_done_callback(tasks.discard)

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
        return store.cue(run_id, None, command.requestId, practice_only=True)

    @app.post("/api/v1/runs/{run_id}/execute", dependencies=[operator], status_code=202)
    async def execute(run_id: str):
        run = store.get_run(run_id)
        if run["executionMode"] != "live" or run["status"] != "approved":
            raise DomainError("approved_live_run_required", "Execute requires an approved live run.")
        dispatch(run_id, "execute")
        return run

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
        result = await ingest_note(run["notes"], run["showId"])
        store.add_trace(command.runId, result)
        return result

    @app.post("/api/v1/tools/recall-recipe", dependencies=[bridge])
    async def tool_recall(command: ToolCommand):
        from cuepilot.integrations.memory import recall_recipe
        run = store.get_run(command.runId)
        result = await recall_recipe(run["showId"])
        store.add_trace(command.runId, result)
        return result

    @app.post("/api/v1/tools/validate-show", dependencies=[bridge])
    async def tool_validate(command: ToolCommand):
        from cuepilot.integrations.hotdata import validate_show
        run = store.get_run(command.runId)
        result = await validate_show(store.get_state("show"), run["speakerId"])
        store.add_trace(command.runId, result)
        return result

    @app.post("/api/v1/tools/execute", dependencies=[bridge])
    async def tool_execute(command: ToolCommand):
        from cuepilot.integrations import rote
        run = store.get_run(command.runId)
        if run["status"] not in ("approved", "running", "completed"):
            raise DomainError("approved_plan_required", "Rote execution needs an approved plan.")
        if run["status"] == "completed":
            return {"ok": True, "runId": run["id"], "receipts": run["receipts"]}
        # Rote decides whether an actual learned play is available; no simulated replay.
        result = await rote.replay(run["id"], "http://127.0.0.1:8787")
        if result.get("status") == "blocked" and result.get("reason") == "learned_play_required":
            result = await rote.learn(run["id"], "http://127.0.0.1:8787")
        store.add_trace(command.runId, result)
        return result

    @app.post("/api/v1/tools/verify", dependencies=[bridge])
    def tool_verify(command: ToolCommand):
        run = store.get_run(command.runId)
        good = run["status"] == "completed" and [r["stepIndex"] for r in run["receipts"]] == [0, 1, 2]
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

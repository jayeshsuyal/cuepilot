"""Narrow authenticated RocketRide ingress; upstream and exposed routes are fixed."""
from __future__ import annotations

import asyncio
import hmac
import json
import os
from pathlib import Path
import re
from uuid import UUID

from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
import httpx

HERE = Path(__file__).resolve().parent
UPSTREAM = "http://127.0.0.1:8787"
REQUEST_LIMIT = 4096
REQUEST_BODY_TIMEOUT = 5
PROGRESS_TIMEOUT = 5
RESPONSE_LIMIT = 65536
TOOL_NAMES = frozenset({"ingest-memory", "recall-recipe", "validate-show", "plan", "execute", "verify"})
RUN_STATUSES = frozenset({"queued", "needs_approval", "approved", "running", "completed", "blocked", "failed"})
PROVIDER_STATUSES = frozenset({"verified", "blocked", "failed", "fixture"})
PROVIDERS = frozenset({"cognee", "hydradb", "hotdata", "rocketride", "rote", "local"})
HEX_DIGEST = re.compile(r"^[a-f0-9]{64}$")
TIMESTAMP = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9:.+-]+(?:Z)?$")


class BridgeError(Exception):
    def __init__(self, code, status=502):
        self.code, self.status = code, status


def canonical_uuid(value):
    if not isinstance(value, str) or len(value) != 36:
        return False
    try:
        return str(UUID(value)) == value
    except ValueError:
        return False


def safe_error(code, status, *, uncertain=False):
    messages = {
        "authentication_unconfigured": "The bridge service token is not configured.",
        "unauthorized": "A valid bridge service token is required.",
        "route_not_found": "This route is not exposed by the bridge.",
        "method_not_allowed": "This method is not allowed for the bridge route.",
        "invalid_request": "Use the exact route and a JSON object containing only a canonical UUID runId.",
        "request_too_large": "The bridge request exceeds 4 KiB.",
        "request_timeout": "The bridge request body did not arrive within the allowed time.",
        "upstream_timeout": "The local show service timed out. Check the local run before retrying.",
        "upstream_rejected": "The local show service rejected the operation. Inspect the local run.",
        "upstream_unavailable": "The local show service is unavailable.",
        "upstream_invalid_response": "The local show service did not return a usable response.",
        "upstream_response_too_large": "The local show service response exceeds the bridge limit.",
    }
    payload = {"detail": {"code": code, "message": messages[code]}}
    if uncertain:
        payload["reconciliationRequired"] = True
    return JSONResponse(payload, status_code=status,
                        headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})


def bounded_integer(value, low=0, high=2**53 - 1):
    return type(value) is int and low <= value <= high


def enum_value(value, allowed):
    return isinstance(value, str) and value in allowed


def receipt_projection(receipts, run_id):
    if not isinstance(receipts, list) or len(receipts) > 3:
        raise BridgeError("upstream_invalid_response")
    selected = []
    previous_revision = 0
    for expected_step, receipt in enumerate(receipts):
        if not isinstance(receipt, dict):
            raise BridgeError("upstream_invalid_response")
        step = receipt.get("stepIndex")
        if (receipt.get("ok") is not True
                or not canonical_uuid(receipt.get("id")) or receipt.get("runId") != run_id
                or not bounded_integer(step, high=2) or step != expected_step
                or receipt.get("scene") != ("intro", "presentation", "holding")[step]
                or not bounded_integer(receipt.get("stageRevision"), low=1)
                or receipt["stageRevision"] <= previous_revision
                or not isinstance(receipt.get("committedAt"), str)
                or len(receipt["committedAt"]) > 48
                or not TIMESTAMP.fullmatch(receipt["committedAt"])):
            raise BridgeError("upstream_invalid_response")
        previous_revision = receipt["stageRevision"]
        selected.append({key: receipt[key] for key in
                         ("ok", "id", "runId", "stepIndex", "scene", "stageRevision", "committedAt")})
    return selected


def run_projection(data, run_id):
    if (data.get("id") != run_id or not enum_value(data.get("status"), RUN_STATUSES)
            or data.get("executionMode") not in ("practice", "live")):
        raise BridgeError("upstream_invalid_response")
    plan = data.get("plan")
    if plan is not None:
        if not isinstance(plan, dict) or not isinstance(plan.get("hash"), str) or not HEX_DIGEST.fullmatch(plan["hash"]):
            raise BridgeError("upstream_invalid_response")
        plan = {"hash": plan["hash"]}
    selected = {"id": run_id, "executionMode": data["executionMode"], "status": data["status"],
                "plan": plan, "receipts": receipt_projection(data.get("receipts"), run_id)}
    verified_completion = data.get("verifiedCompletion", False)
    if type(verified_completion) is bool:
        selected["verifiedCompletion"] = verified_completion
    return selected


def tool_projection(data, run_id):
    """The orchestrator needs verified state, never notes or raw provider evidence."""
    if data.get("id") == run_id:
        return run_projection(data, run_id)
    if "runId" in data and data["runId"] != run_id:
        raise BridgeError("upstream_invalid_response")
    status = data.get("status")
    if status is not None and not enum_value(status, RUN_STATUSES | PROVIDER_STATUSES):
        raise BridgeError("upstream_invalid_response")
    if status is None and type(data.get("ok")) is not bool:
        raise BridgeError("upstream_invalid_response")
    summary = {"runId": run_id}
    if status is not None:
        summary["status"] = status
    if "ok" in data:
        if type(data["ok"]) is not bool:
            raise BridgeError("upstream_invalid_response")
        summary["ok"] = data["ok"]
    else:
        summary["ok"] = status == "verified"
    if status in ("blocked", "failed"):
        summary["reason"] = "The local service blocked this operation; inspect the private local run."
    if "ready" in data:
        if type(data["ready"]) is not bool:
            raise BridgeError("upstream_invalid_response")
        summary["ready"] = data["ready"]
    if "show_revision" in data:
        if not bounded_integer(data["show_revision"], low=1):
            raise BridgeError("upstream_invalid_response")
        summary["show_revision"] = data["show_revision"]
    if data.get("supported_template") == "speaker-segment-v1":
        summary["supported_template"] = "speaker-segment-v1"
    if enum_value(data.get("provider"), PROVIDERS):
        summary["provider"] = data["provider"]
    if data.get("operation") in ("learn", "replay"):
        summary["operation"] = data["operation"]
    if "records" in data:
        if not isinstance(data["records"], list) or len(data["records"]) > 20:
            raise BridgeError("upstream_invalid_response")
        records = []
        for record in data["records"]:
            if (not isinstance(record, dict) or not enum_value(record.get("provider"), PROVIDERS)
                    or not enum_value(record.get("status"), PROVIDER_STATUSES)):
                raise BridgeError("upstream_invalid_response")
            records.append({"provider": record["provider"], "status": record["status"]})
        summary["records"] = records
    if "receipts" in data:
        summary["receipts"] = receipt_projection(data["receipts"], run_id)
    return summary


def operation_completed(operation, data):
    """A route name or an HTTP success alone cannot establish tool completion."""
    if data.get("status") in ("blocked", "failed") or data.get("ok") is False:
        return False
    if operation == "plan":
        return data.get("status") == "needs_approval"
    if operation == "verify":
        return data.get("ok") is True
    return data.get("status") == "verified"


def progress_projection(operation, data, canonical, run_id):
    """Return fixed operation names from fresh local state, never provider text."""
    run = run_projection(canonical, run_id)
    status = run["status"]
    progress = {"runStatus": status}
    # A cached successful result must not advance a run that was later stopped.
    if status in ("blocked", "failed"):
        return progress
    progress["completedOperation"] = operation
    next_operation = None
    if status == "queued":
        next_operation = {"ingest-memory": "recall-recipe", "recall-recipe": "validate-show",
                          "validate-show": "plan"}.get(operation)
    elif status == "approved" and operation == "validate-show":
        next_operation = "execute"
    elif status == "completed" and operation == "execute" and data.get("provider") == "rote":
        operations = canonical.get("operations")
        execution = operations.get("execute") if isinstance(operations, dict) else None
        if (isinstance(execution, dict) and execution.get("status") == "verified"
                and len(run["receipts"]) == 3):
            next_operation = "verify"
    if next_operation is not None:
        progress["nextOperation"] = next_operation
    elif ((operation == "plan" and status == "needs_approval")
          or (operation == "verify" and status == "completed")):
        progress["nextOperation"] = None
    return progress


def unique_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate_json_field")
        value[key] = item
    return value


async def request_body(request):
    if request.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/json":
        raise BridgeError("invalid_request", 400)
    if request.headers.get("content-encoding", "identity").lower() != "identity":
        raise BridgeError("invalid_request", 400)
    chunks, size = [], 0
    try:
        async with asyncio.timeout(REQUEST_BODY_TIMEOUT):
            async for chunk in request.stream():
                size += len(chunk)
                if size > REQUEST_LIMIT:
                    raise BridgeError("request_too_large", 413)
                chunks.append(chunk)
    except TimeoutError:
        raise BridgeError("request_timeout", 408) from None
    try:
        data = json.loads(b"".join(chunks), object_pairs_hook=unique_object)
    except (ValueError, UnicodeDecodeError):
        raise BridgeError("invalid_request", 400) from None
    if not isinstance(data, dict) or set(data) != {"runId"} or not canonical_uuid(data["runId"]):
        raise BridgeError("invalid_request", 400)
    return data


async def upstream_request(method, path, token, payload, transport):
    # Ingest can legitimately use its existing 150-second Cognee bound. This is
    # separate from, and cannot extend, the caller's own HTTP deadline.
    operation = path.rsplit("/", 1)[-1]
    deadline = 190 if operation == "ingest-memory" else 130 if operation in ("execute", "validate-show") else 30
    timeout = httpx.Timeout(connect=3, read=deadline - 5, write=5, pool=3)
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    if method == "POST":
        headers["Content-Type"] = "application/json"
    try:
        async with asyncio.timeout(deadline):
            async with httpx.AsyncClient(transport=transport, timeout=timeout, follow_redirects=False, trust_env=False) as client:
                async with client.stream(method, UPSTREAM + path, headers=headers, json=payload if method == "POST" else None) as response:
                    if not 200 <= response.status_code < 300:
                        if 400 <= response.status_code < 500:
                            raise BridgeError("upstream_rejected", response.status_code)
                        raise BridgeError("upstream_unavailable")
                    if response.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/json":
                        raise BridgeError("upstream_invalid_response")
                    if response.headers.get("content-length", "").isdigit() and int(response.headers["content-length"]) > RESPONSE_LIMIT:
                        raise BridgeError("upstream_response_too_large")
                    chunks, size = [], 0
                    async for chunk in response.aiter_bytes():
                        size += len(chunk)
                        if size > RESPONSE_LIMIT:
                            raise BridgeError("upstream_response_too_large")
                        chunks.append(chunk)
                    try:
                        result = json.loads(b"".join(chunks), object_pairs_hook=unique_object)
                    except (ValueError, UnicodeDecodeError):
                        raise BridgeError("upstream_invalid_response") from None
                    if not isinstance(result, dict):
                        raise BridgeError("upstream_invalid_response")
                    return result
    except (TimeoutError, httpx.TimeoutException):
        raise BridgeError("upstream_timeout", 504) from None
    except httpx.HTTPError:
        raise BridgeError("upstream_unavailable") from None


def create_app(bridge_token=None, transport=None):
    """`transport` is an in-memory test seam; production has no upstream override."""
    if bridge_token is None:
        load_dotenv(HERE.parent / ".env", override=False)
        load_dotenv(HERE / ".env", override=False)
        bridge_token = os.getenv("CUEPILOT_BRIDGE_TOKEN", "")
    app = FastAPI(title="CuePilot RocketRide bridge", docs_url=None, redoc_url=None,
                  openapi_url=None, redirect_slashes=False)

    @app.middleware("http")
    async def ingress_guard(request: Request, call_next):
        path = request.url.path
        method = request.method
        try:
            # Do not normalize encoded path variants into the allowlist.
            raw_path = request.scope.get("raw_path", b"")
            canonical_path = path.encode("ascii")
            if raw_path != canonical_path:
                return safe_error("route_not_found", 404)
        except UnicodeEncodeError:
            return safe_error("route_not_found", 404)
        if path == "/api/v1/health":
            allowed = "GET"
        elif path.startswith("/api/v1/runs/") and canonical_uuid(path.removeprefix("/api/v1/runs/")):
            allowed = "GET"
        elif path.startswith("/api/v1/tools/") and path.removeprefix("/api/v1/tools/") in TOOL_NAMES:
            allowed = "POST"
        else:
            return safe_error("route_not_found", 404)
        if method != allowed:
            return safe_error("method_not_allowed", 405)
        if request.scope.get("query_string"):
            return safe_error("invalid_request", 400)
        if not isinstance(bridge_token, str) or not bridge_token:
            return safe_error("authentication_unconfigured", 503)
        authorizations = request.headers.getlist("authorization")
        if len(authorizations) != 1:
            return safe_error("unauthorized", 401)
        authorization = authorizations[0]
        supplied = authorization[7:] if authorization.startswith("Bearer ") else ""
        if not supplied or not hmac.compare_digest(bridge_token.encode(), supplied.encode()):
            return safe_error("unauthorized", 401)
        lengths = request.headers.getlist("content-length")
        if len(lengths) > 1 or (lengths and not lengths[0].isdigit()):
            return safe_error("invalid_request", 400)
        if lengths and int(lengths[0]) > REQUEST_LIMIT:
            return safe_error("request_too_large", 413)
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    @app.exception_handler(BridgeError)
    async def bridge_error(request: Request, error):
        uncertain = request.method == "POST" and error.code.startswith("upstream_")
        return safe_error(error.code, error.status, uncertain=uncertain)

    @app.get("/api/v1/health")
    async def health():
        data = await upstream_request("GET", "/api/v1/health", bridge_token, None, transport)
        if data.get("ok") is not True or data.get("service") != "CuePilot":
            raise BridgeError("upstream_invalid_response")
        return {"ok": True, "service": "CuePilot"}

    @app.get("/api/v1/runs/{run_id}")
    async def run(run_id: str):
        data = await upstream_request("GET", f"/api/v1/runs/{run_id}", bridge_token, None, transport)
        return run_projection(data, run_id)

    @app.post("/api/v1/tools/{operation}")
    async def tool(operation: str, request: Request):
        payload = await request_body(request)
        data = await upstream_request("POST", f"/api/v1/tools/{operation}", bridge_token, payload, transport)
        run_id = payload["runId"]
        result = tool_projection(data, run_id)
        if operation_completed(operation, data):
            try:
                async with asyncio.timeout(PROGRESS_TIMEOUT):
                    canonical = await upstream_request("GET", f"/api/v1/runs/{run_id}", bridge_token, None, transport)
            except TimeoutError:
                raise BridgeError("upstream_timeout", 504) from None
            result.update(progress_projection(operation, data, canonical, run_id))
        return result

    return app


def main():
    import uvicorn
    uvicorn.run(create_app(), host="127.0.0.1", port=8788, access_log=False, server_header=False)


if __name__ == "__main__":
    main()

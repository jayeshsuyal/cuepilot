"""Publish a show snapshot and query readiness through Hotdata's documented API."""
from __future__ import annotations

import asyncio
from collections import OrderedDict
import csv
import hashlib
import io
import json
import os
import re
from time import monotonic
from uuid import uuid4

import httpx

API_URL = "https://api.hotdata.dev/"
COLUMNS = ["show_id", "show_revision", "speaker_id", "speaker_ready", "asset_id", "asset_status"]
SNAPSHOT_COLUMNS = {"record_type": "VARCHAR", "show_id": "VARCHAR", "show_revision": "BIGINT",
                    "item_id": "VARCHAR", "presentation_asset_id": "VARCHAR", "ready": "INTEGER", "asset_status": "VARCHAR"}
# Only references to snapshots proved by a completed query are retained. This is
# process-local, bounded, and expires before the requested 1h database lifetime.
# Every validation, including a hit, still performs a real Hotdata query.
_SNAPSHOTS = OrderedDict()
_SNAPSHOT_TTL = 55 * 60
_SNAPSHOT_LIMIT = 16


class HotdataError(Exception):
    def __init__(self, reason, blocked=False):
        super().__init__(reason)
        self.reason, self.blocked = reason, blocked


def _record(status, operation, evidence=None, reason=None):
    return {"provider": "hotdata", "status": status, "operation": operation, "evidence": evidence or {}, "reason": reason}


def _id(value):
    return isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", value)


def _snapshot(show, speaker_id):
    if (not isinstance(show, dict) or not _id(show.get("id")) or type(show.get("revision")) is not int
            or not 0 <= show["revision"] <= 2**63 - 1 or not _id(speaker_id)):
        raise HotdataError("Show ID, revision, and selected speaker ID must match the CuePilot contract.", True)
    speakers, assets = show.get("speakers"), show.get("assets")
    if not isinstance(speakers, list) or not isinstance(assets, list) or not (1 <= len(speakers) <= 100) or len(assets) > 100:
        raise HotdataError("A show snapshot requires 1–100 speakers and at most 100 assets.", True)
    seen_speakers, seen_assets = set(), set()
    rows = []
    for speaker in speakers:
        if (not isinstance(speaker, dict) or not _id(speaker.get("id")) or speaker["id"] in seen_speakers
                or type(speaker.get("ready")) is not bool or not _id(speaker.get("presentationAssetId"))):
            raise HotdataError("A speaker is malformed or duplicated in the current snapshot.", True)
        seen_speakers.add(speaker["id"])
        rows.append(["speaker", show["id"], show["revision"], speaker["id"], speaker["presentationAssetId"], int(speaker["ready"]), ""])
    for asset in assets:
        if (not isinstance(asset, dict) or not _id(asset.get("id")) or asset["id"] in seen_assets
                or asset.get("kind") != "slide" or asset.get("status") not in {"ready", "missing"}):
            raise HotdataError("An asset is malformed or duplicated in the current snapshot.", True)
        seen_assets.add(asset["id"])
        rows.append(["asset", show["id"], show["revision"], asset["id"], "", 0, asset["status"]])
    selected = next((speaker for speaker in speakers if speaker["id"] == speaker_id), None)
    if selected is None:
        raise HotdataError("The selected speaker is absent from the current show.", True)
    asset = next((asset for asset in assets if asset["id"] == selected["presentationAssetId"]), None)
    csv_text = io.StringIO(newline="")
    writer = csv.writer(csv_text)
    writer.writerow(SNAPSHOT_COLUMNS)
    writer.writerows(sorted(rows))
    expected = [show["id"], show["revision"], speaker_id, int(selected["ready"]), asset["id"] if asset else None, asset["status"] if asset else None]
    return csv_text.getvalue(), expected, len(rows)


async def _request(client, method, path, *, success_codes=(200,), **kwargs):
    async with client.stream(method, path, **kwargs) as response:
        if response.status_code >= 300:
            if response.status_code in (401, 403):
                raise HotdataError("Hotdata authentication or workspace permission was rejected; account activation may be required.", True)
            if response.status_code == 402:
                raise HotdataError("Hotdata account activation or credits are required.", True)
            raise HotdataError(f"Hotdata returned HTTP {response.status_code}.")
        if response.status_code not in success_codes:
            raise HotdataError("Hotdata accepted work without a verified synchronous completion response.")
        content, length = [], 0
        async for chunk in response.aiter_bytes():
            length += len(chunk)
            if length > 1_000_000:
                raise HotdataError("Hotdata response exceeded the bounded result size.")
            content.append(chunk)
    try:
        return json.loads(b"".join(content))
    except (ValueError, UnicodeError):
        raise HotdataError("Hotdata returned unsupported JSON.") from None


def _loaded_snapshot(result, database, row_count):
    """A 2xx response alone (in particular an async job) is not a load receipt."""
    if (not isinstance(result, dict) or result.get("connection_id") != database["default_connection_id"]
            or result.get("schema_name") != "main" or result.get("table_name") != "cuepilot_state"
            or type(result.get("row_count")) is not int or result["row_count"] != row_count):
        raise HotdataError("Hotdata load receipt did not confirm the complete snapshot in the expected table.")


def _queried_snapshot(result, expected):
    if (not isinstance(result, dict) or result.get("columns") != COLUMNS or result.get("truncated") is not False
            or not _id(result.get("query_run_id")) or not isinstance(result.get("rows"), list)
            or len(result["rows"]) != 1 or not isinstance(result["rows"][0], list)
            or len(result["rows"][0]) != len(expected)):
        raise HotdataError("Hotdata query returned an incomplete or unsupported readiness result.")
    # The current API distinguishes preview and total counts. row_count is only
    # a deprecated preview alias, so it cannot establish completeness by itself.
    for field in ("preview_row_count", "total_row_count"):
        if type(result.get(field)) is not int or result[field] != 1:
            raise HotdataError("Hotdata query did not return exactly one complete readiness row.")
    if "row_count" in result and (type(result["row_count"]) is not int or result["row_count"] != 1):
        raise HotdataError("Hotdata query returned conflicting row counts.")
    # bool compares equal to 0/1 in Python; no implicit coercion is evidence.
    if any(type(actual) is not type(wanted) or actual != wanted for actual, wanted in zip(result["rows"][0], expected)):
        raise HotdataError("Hotdata query did not return the exact current show revision, speaker, and asset snapshot.")
    if result.get("result_id") is not None and not _id(result["result_id"]):
        raise HotdataError("Hotdata query returned an invalid persisted-result identifier.")


def _cached_snapshot(cache_key):
    for key, entry in list(_SNAPSHOTS.items()):
        if entry["valid_until"] <= monotonic():
            _SNAPSHOTS.pop(key, None)
    entry = _SNAPSHOTS.get(cache_key)
    if entry:
        _SNAPSHOTS.move_to_end(cache_key)
    return entry


async def validate_show(show: dict, speaker_id: str) -> dict:
    """Live-only query; publish each distinct immutable snapshot in a 1h database.

    Top-level blocked can coexist with a verified query record when the actual
    queried speaker/presentation is unavailable. The stage must stay holding.
    """
    records = []
    cache_key = None
    try:
        data, expected, row_count = _snapshot(show, speaker_id)
        key, workspace = os.getenv("HOTDATA_API_KEY", "").strip(), os.getenv("HOTDATA_WORKSPACE", "").strip()
        if not key or not workspace:
            raise HotdataError("Hotdata requires HOTDATA_API_KEY and HOTDATA_WORKSPACE; the setup account still needs activation.", True)
        if not _id(workspace):
            raise HotdataError("Hotdata workspace ID is invalid.", True)
        snapshot_hash = hashlib.sha256(data.encode()).hexdigest()
        cache_key = (workspace, hashlib.sha256(key.encode()).hexdigest(), snapshot_hash)
        snapshot = _cached_snapshot(cache_key)
        reused = snapshot is not None
        async with asyncio.timeout(60):
            async with httpx.AsyncClient(base_url=API_URL, headers={"Authorization": "Bearer " + key, "X-Workspace-Id": workspace},
                                        timeout=httpx.Timeout(20, connect=5), follow_redirects=False, trust_env=False) as client:
                if snapshot is None:
                    valid_until = monotonic() + _SNAPSHOT_TTL
                    database = await _request(client, "POST", "v1/databases", success_codes=(201,),
                                              json={"name": "cuepilot_" + uuid4().hex[:16], "expires_at": "1h"})
                    if (not isinstance(database, dict) or not _id(database.get("id"))
                            or not _id(database.get("default_connection_id")) or database.get("default_catalog") != "default"
                            or database.get("default_schema") != "main"):
                        raise HotdataError("Hotdata database creation did not confirm the expected catalog and identifiers.")
                    database_id = database["id"]
                    loaded = await _request(client, "POST", f"v1/databases/{database_id}/schemas/main/tables/cuepilot_state/loads",
                                            json={"mode": "replace", "data": data, "format": "csv", "columns": SNAPSHOT_COLUMNS,
                                                  "async": False, "idempotency_key": snapshot_hash})
                    _loaded_snapshot(loaded, database, row_count)
                    snapshot = {"database_id": database_id, "valid_until": valid_until}
                    records.append(_record("verified", "publish_show_snapshot", {"database_id": database_id, "expires_after": "1h",
                                           "show_id": expected[0], "show_revision": expected[1], "snapshot_sha256": snapshot_hash,
                                           "row_count": row_count}))
                database_id = snapshot["database_id"]
                # The only interpolation is a previously allowlisted identifier string.
                # No caller can supply SQL, a table name, or an endpoint.
                query = ("SELECT s.show_id AS show_id, s.show_revision AS show_revision, s.item_id AS speaker_id, "
                         "s.ready AS speaker_ready, a.item_id AS asset_id, a.asset_status AS asset_status "
                         "FROM default.main.cuepilot_state s LEFT JOIN default.main.cuepilot_state a "
                         "ON a.record_type = 'asset' AND s.presentation_asset_id = a.item_id "
                         "AND s.show_id = a.show_id AND s.show_revision = a.show_revision "
                         "WHERE s.record_type = 'speaker' AND s.item_id = '" + speaker_id + "'")
                result = await _request(client, "POST", "v1/query", headers={"X-Database-Id": database_id}, json={"sql": query, "async": False})
        _queried_snapshot(result, expected)
        _SNAPSHOTS[cache_key] = snapshot
        _SNAPSHOTS.move_to_end(cache_key)
        while len(_SNAPSHOTS) > _SNAPSHOT_LIMIT:
            _SNAPSHOTS.popitem(last=False)
        evidence = dict(zip(COLUMNS, expected)) | {"snapshot_sha256": snapshot_hash, "database_id": database_id,
                                                  "snapshot_reused": reused, "query_run_id": result["query_run_id"]}
        if result.get("result_id") is not None:
            evidence["result_id"] = result["result_id"]
        ready = expected[3] == 1 and expected[4] is not None and expected[5] == "ready"
        reason = None if ready else "The queried speaker or presentation is unavailable; stay on holding."
        records.append(_record("verified", "query_speaker_asset_readiness", evidence, reason))
        return {"status": "verified" if ready else "blocked", "ready": ready, "show_revision": expected[1],
                "records": records, "reason": reason}
    except HotdataError as error:
        status, reason = ("blocked" if error.blocked else "failed"), error.reason
    except (TimeoutError, httpx.TimeoutException):
        status, reason = "failed", "Hotdata validation timed out; any created database has one-hour expiry requested."
    except Exception:
        status, reason = "failed", "Hotdata validation failed; credentials and remote error bodies were withheld."
    if cache_key is not None:
        _SNAPSHOTS.pop(cache_key, None)
    records.append(_record(status, "validate_show", reason=reason))
    return {"status": status, "ready": False, "records": records, "reason": reason}

"""Publish a show snapshot and query readiness through Hotdata's documented API."""
from __future__ import annotations

import asyncio
import csv
import hashlib
import io
import json
import os
import re
from uuid import uuid4

import httpx

API_URL = "https://api.hotdata.dev/"
COLUMNS = ["show_id", "show_revision", "speaker_id", "speaker_ready", "asset_id", "asset_status"]


class HotdataError(Exception):
    def __init__(self, reason, blocked=False):
        super().__init__(reason)
        self.reason, self.blocked = reason, blocked


def _record(status, operation, evidence=None, reason=None):
    return {"provider": "hotdata", "status": status, "operation": operation, "evidence": evidence or {}, "reason": reason}


def _id(value):
    return isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", value)


def _snapshot(show, speaker_id):
    if not isinstance(show, dict) or not _id(show.get("id")) or type(show.get("revision")) is not int or show["revision"] < 0 or not _id(speaker_id):
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
    writer.writerow(["record_type", "show_id", "show_revision", "item_id", "presentation_asset_id", "ready", "asset_status"])
    writer.writerows(rows)
    expected = [show["id"], show["revision"], speaker_id, int(selected["ready"]), asset["id"] if asset else None, asset["status"] if asset else None]
    return csv_text.getvalue(), expected


async def _request(client, method, path, **kwargs):
    async with client.stream(method, path, **kwargs) as response:
        if response.status_code >= 300:
            if response.status_code in (401, 403):
                raise HotdataError("Hotdata authentication or workspace permission was rejected; account activation may be required.", True)
            if response.status_code == 402:
                raise HotdataError("Hotdata account activation or credits are required.", True)
            raise HotdataError(f"Hotdata returned HTTP {response.status_code}.")
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


async def validate_show(show: dict, speaker_id: str) -> dict:
    """Live-only write/query; creates a separate 1h database for this snapshot.

    Top-level blocked can coexist with a verified query record when the actual
    queried speaker/presentation is unavailable. The stage must stay holding.
    """
    records = []
    try:
        data, expected = _snapshot(show, speaker_id)
        key, workspace = os.getenv("HOTDATA_API_KEY", "").strip(), os.getenv("HOTDATA_WORKSPACE", "").strip()
        if not key or not workspace:
            raise HotdataError("Hotdata requires HOTDATA_API_KEY and HOTDATA_WORKSPACE; the setup account still needs activation.", True)
        if not _id(workspace):
            raise HotdataError("Hotdata workspace ID is invalid.", True)
        snapshot_hash = hashlib.sha256(data.encode()).hexdigest()
        async with asyncio.timeout(60):
            async with httpx.AsyncClient(base_url=API_URL, headers={"Authorization": "Bearer " + key, "X-Workspace-Id": workspace},
                                        timeout=httpx.Timeout(20, connect=5), follow_redirects=False, trust_env=False) as client:
                database = await _request(client, "POST", "v1/databases", json={"name": "cuepilot_" + uuid4().hex[:16], "expires_at": "1h"})
                database_id = database.get("id") if isinstance(database, dict) else None
                if not _id(database_id):
                    raise HotdataError("Hotdata database creation did not return a valid identifier.")
                await _request(client, "POST", f"v1/databases/{database_id}/schemas/main/tables/cuepilot_state/loads",
                               json={"mode": "replace", "data": data, "format": "csv", "idempotency_key": snapshot_hash})
                records.append(_record("verified", "publish_show_snapshot", {"database_id": database_id, "expires_after": "1h",
                                       "show_id": show["id"], "show_revision": show["revision"], "snapshot_sha256": snapshot_hash}))
                # The only interpolation is a previously allowlisted identifier string.
                # No caller can supply SQL, a table name, or an endpoint.
                query = ("SELECT s.show_id AS show_id, s.show_revision AS show_revision, s.item_id AS speaker_id, "
                         "s.ready AS speaker_ready, a.item_id AS asset_id, a.asset_status AS asset_status "
                         "FROM default.main.cuepilot_state s LEFT JOIN default.main.cuepilot_state a "
                         "ON a.record_type = 'asset' AND s.presentation_asset_id = a.item_id "
                         "AND s.show_id = a.show_id AND s.show_revision = a.show_revision "
                         "WHERE s.record_type = 'speaker' AND s.item_id = '" + speaker_id + "'")
                result = await _request(client, "POST", "v1/query", headers={"X-Database-Id": database_id}, json={"sql": query})
        if (not isinstance(result, dict) or result.get("columns") != COLUMNS or result.get("truncated") is not False
                or result.get("rows") != [expected] or result.get("row_count", 1) != 1):
            raise HotdataError("Hotdata query did not return the exact current show revision, speaker, and asset snapshot.")
        evidence = dict(zip(COLUMNS, expected)) | {"snapshot_sha256": snapshot_hash, "database_id": database_id}
        for field in ("query_run_id", "result_id"):
            if _id(result.get(field)):
                evidence[field] = result[field]
        ready = expected[3] == 1 and expected[4] is not None and expected[5] == "ready"
        reason = None if ready else "The queried speaker or presentation is unavailable; stay on holding."
        records.append(_record("verified", "query_speaker_asset_readiness", evidence, reason))
        return {"status": "verified" if ready else "blocked", "ready": ready, "show_revision": show["revision"],
                "records": records, "reason": reason}
    except HotdataError as error:
        status, reason = ("blocked" if error.blocked else "failed"), error.reason
    except (TimeoutError, httpx.TimeoutException):
        status, reason = "failed", "Hotdata validation timed out; any created database expires after one hour."
    except Exception:
        status, reason = "failed", "Hotdata validation failed; credentials and remote error bodies were withheld."
    records.append(_record(status, "validate_show", reason=reason))
    return {"status": status, "ready": False, "records": records, "reason": reason}

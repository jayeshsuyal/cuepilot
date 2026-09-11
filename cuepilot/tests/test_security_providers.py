"""Offline provider acceptance gates; a passing mock is not sponsor evidence."""
import asyncio
import hashlib
import json
import os
from pathlib import Path
import secrets
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

import httpx

from cuepilot.integrations import hotdata, memory, rote


def ready_show():
    return {
        "id": "acceptance-show", "revision": 4,
        "speakers": [{"id": "maya", "ready": True, "presentationAssetId": "slides-maya"}],
        "assets": [{"id": "slides-maya", "kind": "slide", "status": "ready"}],
    }


def mock_http(handler):
    """Use real HTTP parsing with a transport that cannot open a socket."""
    client = httpx.AsyncClient
    return patch.object(httpx, "AsyncClient", side_effect=lambda *args, **kwargs: client(
        *args, transport=httpx.MockTransport(handler), **kwargs))


class FakeProcess:
    def __init__(self):
        self.returncode = None
        self.stopped = asyncio.Event()
        self.reaped = False

    async def communicate(self):
        await self.stopped.wait()
        self.reaped = True
        return b"", b""

    async def wait(self):
        await self.stopped.wait()
        self.reaped = True
        return self.returncode

    def kill(self):
        self.returncode = -9
        self.stopped.set()

    def terminate(self):
        self.returncode = -15
        self.stopped.set()


class ProviderSecurityAcceptance(unittest.IsolatedAsyncioTestCase):
    async def test_conflicting_unavailable_rule_cannot_publish_a_verified_recipe(self):
        note = "Introduction then presentation then holding. If unavailable, use holding."
        graph = {
            "nodes": [{"id": name, "label": label} for name, label in [
                ("intro", "Introduction"), ("slides", "Presentation"), ("hold", "Holding"),
                ("speaker", "Unavailable speaker"), ("asset", "Unavailable presentation"),
            ]],
            "edges": [{"source": source, "target": target, "label": label} for source, target, label in [
                ("intro", "slides", "precedes"), ("slides", "hold", "precedes"),
                ("speaker", "hold", "fallback_to"), ("asset", "hold", "fallback_to"),
                ("asset", "slides", "fallback_to"),
            ]],
        }
        provenance = {"note_sha256": hashlib.sha256(note.encode()).hexdigest(), "graph_sha256": "a" * 64}
        persist = AsyncMock(return_value={"receipt_id": "offline-receipt"})
        with patch.object(memory, "_extract_graph", AsyncMock(return_value=(graph, provenance))), \
                patch.object(memory, "_persist_graph", persist):
            result = await memory.ingest_note(note, "acceptance-show")
        self.assertNotEqual(result["status"], "verified", "Contradictory fallback rules must block planning.")
        persist.assert_not_awaited()

    async def test_cancelled_rote_operation_stops_and_reaps_its_process(self):
        process = FakeProcess()
        started = asyncio.Event()

        async def spawn(*args, **kwargs):
            started.set()
            return process

        with tempfile.TemporaryDirectory() as temporary, \
                patch.object(rote.asyncio, "create_subprocess_exec", side_effect=spawn):
            task = asyncio.create_task(rote._cli([], temporary, {}, Path(temporary) / "evidence", "cancel"))
            await asyncio.wait_for(started.wait(), timeout=1)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assertTrue(process.stopped.is_set(), "Cancellation must stop the process before another replay can start.")
        self.assertTrue(process.reaped, "Cancellation must wait for process cleanup.")

    async def test_timed_out_rote_operation_stops_and_reaps_its_process(self):
        process = FakeProcess()
        with tempfile.TemporaryDirectory() as temporary, \
                patch.object(rote.asyncio, "create_subprocess_exec", AsyncMock(return_value=process)):
            with self.assertRaisesRegex(ValueError, "rote_timeout"):
                await rote._cli([], temporary, {}, Path(temporary) / "evidence", "timeout", timeout=0.01)
        self.assertTrue(process.stopped.is_set())
        self.assertTrue(process.reaped)

    async def test_provider_error_bodies_and_credentials_are_not_public_evidence(self):
        secret = secrets.token_urlsafe(32)
        remote_body = "PRIVATE_PROVIDER_RESPONSE_" + secret
        environment = {
            "COGNEE_SERVICE_URL": "https://tenant.cognee.ai", "COGNEE_API_KEY": secret,
            "HOTDATA_API_KEY": secret, "HOTDATA_WORKSPACE": "acceptance-workspace",
        }
        observed_hosts = []

        def rejected(request):
            observed_hosts.append(request.url.host)
            return httpx.Response(403, text=remote_body)

        with patch.dict(os.environ, environment), mock_http(rejected):
            results = [await memory.ingest_note("Synthetic production note", "acceptance-show"),
                       await hotdata.validate_show(ready_show(), "maya")]
        self.assertEqual(observed_hosts, ["tenant.cognee.ai", "api.hotdata.dev"])
        for result in results:
            with self.subTest(provider=result["records"][0]["provider"]):
                self.assertEqual(result["status"], "blocked")
                self.assertNotIn(secret, json.dumps(result))
                self.assertNotIn(remote_body, json.dumps(result))

    async def test_hotdata_cannot_verify_an_old_revision_returned_by_provider(self):
        paths = []

        def stale_response(request):
            paths.append(request.url.path)
            if request.url.path == "/v1/databases":
                return httpx.Response(200, json={"id": "acceptance-database"})
            if request.url.path.endswith("/loads"):
                return httpx.Response(200, json={"ok": True})
            self.assertEqual(request.url.path, "/v1/query")
            return httpx.Response(200, json={
                "columns": ["show_id", "show_revision", "speaker_id", "speaker_ready", "asset_id", "asset_status"],
                "rows": [["acceptance-show", 3, "maya", 1, "slides-maya", "ready"]],
                "truncated": False, "row_count": 1,
            })

        with patch.dict(os.environ, {"HOTDATA_API_KEY": secrets.token_urlsafe(32), "HOTDATA_WORKSPACE": "acceptance-workspace"}), \
                mock_http(stale_response):
            result = await hotdata.validate_show(ready_show(), "maya")
        self.assertEqual(len(paths), 3)
        self.assertEqual(result["status"], "failed")
        self.assertFalse(result["ready"])
        self.assertFalse(any(record["status"] == "verified" and record["operation"] == "query_speaker_asset_readiness"
                             for record in result["records"]))


if __name__ == "__main__":
    unittest.main()

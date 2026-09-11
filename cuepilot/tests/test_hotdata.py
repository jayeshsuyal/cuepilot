"""Offline HTTP contract tests; these never establish live sponsor execution."""
import copy
import csv
import io
import json
import os
import unittest
from unittest.mock import patch

import httpx

from cuepilot.integrations import hotdata


SHOW = {
    "id": "show-1", "revision": 7,
    "speakers": [
        {"id": "maya", "ready": True, "presentationAssetId": "slides-maya"},
        {"id": "ravi", "ready": True, "presentationAssetId": "slides-ravi"},
    ],
    "assets": [
        {"id": "slides-maya", "kind": "slide", "status": "ready"},
        {"id": "slides-ravi", "kind": "slide", "status": "ready"},
    ],
}


def query_response(row=None, **changes):
    # Hotdata's documented synchronous /v1/query shape. A result may be complete
    # inline even when persistence is unavailable (result_id null).
    result = {
        "columns": ["show_id", "show_revision", "speaker_id", "speaker_ready", "asset_id", "asset_status"],
        "nullable": [False, False, False, False, True, True],
        "execution_time_ms": 3, "query_run_id": "qrun-test", "result_id": "result-test",
        "preview_row_count": 1, "row_count": 1, "total_row_count": 1, "truncated": False,
        "rows": [row or ["show-1", 7, "maya", 1, "slides-maya", "ready"]],
    }
    return result | changes


class HotdataTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.show = copy.deepcopy(SHOW)
        self.requests = []
        self.database_count = 0
        self.query_results = []
        self.load_result = None
        self.create_result = None
        self.idempotency_targets = {}
        self.hook = None
        hotdata._SNAPSHOTS.clear()
        self.environment = patch.dict(os.environ, {"HOTDATA_API_KEY": "offline-test-key", "HOTDATA_WORKSPACE": "work-test"}, clear=True)
        self.environment.start()
        self.real_client = httpx.AsyncClient
        self.client_patch = patch.object(hotdata.httpx, "AsyncClient", side_effect=self.client)
        self.client_patch.start()
        self.addCleanup(self.client_patch.stop)
        self.addCleanup(self.environment.stop)
        self.addCleanup(hotdata._SNAPSHOTS.clear)

    def client(self, **kwargs):
        self.assertFalse(kwargs["follow_redirects"])
        self.assertFalse(kwargs["trust_env"])
        return self.real_client(**kwargs, transport=httpx.MockTransport(self.respond))

    def respond(self, request):
        self.requests.append(request)
        self.assertEqual(request.url.host, "api.hotdata.dev")
        self.assertEqual(request.headers["X-Workspace-Id"], os.environ["HOTDATA_WORKSPACE"])
        self.assertEqual(request.headers["Authorization"], "Bearer " + os.environ["HOTDATA_API_KEY"])
        if self.hook:
            self.hook(request)
        if request.url.path == "/v1/databases":
            self.database_count += 1
            if self.create_result:
                return self.create_result
            return httpx.Response(201, json={
                "id": f"dbid-{self.database_count}", "default_catalog": "default", "default_schema": "main",
                "default_connection_id": "conn-test", "expires_at": "2026-09-12T00:00:00Z", "created": True,
            })
        if request.url.path.endswith("/loads"):
            load_key = json.loads(request.content)["idempotency_key"]
            previous_target = self.idempotency_targets.setdefault(load_key, request.url.path)
            if previous_target != request.url.path:
                return httpx.Response(409, json={"error": "Idempotency key used for a different destination"})
            if self.load_result:
                return self.load_result
            return httpx.Response(200, json={"connection_id": "conn-test", "schema_name": "main", "table_name": "cuepilot_state",
                                            "row_count": len(self.show["speakers"]) + len(self.show["assets"]), "arrow_schema_json": "{}"})
        self.assertEqual(request.url.path, "/v1/query")
        self.assertEqual(json.loads(request.content)["async"], False)
        result = self.query_results.pop(0) if self.query_results else query_response()
        return result if isinstance(result, httpx.Response) else httpx.Response(200, json=result)

    def paths(self):
        return [request.url.path for request in self.requests]

    async def test_publish_receipt_and_complete_current_query_are_both_required(self):
        result = await hotdata.validate_show(self.show, "maya")
        self.assertEqual(result["status"], "verified")
        self.assertTrue(result["ready"])
        self.assertEqual(result["show_revision"], 7)
        self.assertEqual(self.paths(), ["/v1/databases", "/v1/databases/dbid-1/schemas/main/tables/cuepilot_state/loads", "/v1/query"])
        self.assertEqual(json.loads(self.requests[0].content)["expires_at"], "1h")
        self.assertEqual(self.requests[-1].headers["X-Database-Id"], "dbid-1")
        publish, query = result["records"]
        self.assertEqual(publish["operation"], "publish_show_snapshot")
        self.assertEqual(publish["evidence"]["row_count"], 4)
        self.assertEqual(query["evidence"]["query_run_id"], "qrun-test")
        self.assertFalse(query["evidence"]["snapshot_reused"])

    async def test_inline_column_types_preserve_numeric_looking_ids(self):
        self.show = {"id": "0001", "revision": 7,
                     "speakers": [{"id": "001", "ready": True, "presentationAssetId": "002"}],
                     "assets": [{"id": "002", "kind": "slide", "status": "ready"}]}
        self.query_results = [query_response(["0001", 7, "001", 1, "002", "ready"])]
        result = await hotdata.validate_show(self.show, "001")
        self.assertEqual(result["status"], "verified")
        payload = json.loads(self.requests[1].content)
        self.assertEqual(payload["columns"]["show_id"], "VARCHAR")
        self.assertEqual(payload["columns"]["item_id"], "VARCHAR")
        self.assertEqual(payload["columns"]["presentation_asset_id"], "VARCHAR")
        self.assertEqual(payload["columns"]["show_revision"], "BIGINT")
        rows = list(csv.DictReader(io.StringIO(payload["data"])))
        self.assertEqual({row["item_id"] for row in rows}, {"001", "002"})
        self.assertEqual({row["show_id"] for row in rows}, {"0001"})

    async def test_incomplete_or_wrong_load_never_produces_publish_proof(self):
        for change in ({"row_count": 3}, {"row_count": True}, {"connection_id": "wrong"}, {"schema_name": "other"}, {"table_name": "other"}):
            with self.subTest(change=change):
                self.load_result = httpx.Response(200, json={"connection_id": "conn-test", "schema_name": "main",
                                                          "table_name": "cuepilot_state", "row_count": 4} | change)
                result = await hotdata.validate_show(self.show, "maya")
                self.assertEqual(result["status"], "failed")
                self.assertFalse(any(record["status"] == "verified" for record in result["records"]))
                self.assertNotIn("/v1/query", self.paths())
                self.assertFalse(hotdata._SNAPSHOTS)

    async def test_accepted_background_load_is_not_completed_publication(self):
        self.load_result = httpx.Response(202, json={"id": "job-test", "status": "pending", "status_url": "https://untrusted.invalid"})
        result = await hotdata.validate_show(self.show, "maya")
        self.assertEqual(result["status"], "failed")
        self.assertEqual(len(result["records"]), 1)
        self.assertNotIn("/v1/query", self.paths())

    async def test_incomplete_query_metadata_and_rows_cannot_verify_readiness(self):
        bad_results = [query_response(**change) for change in (
            {"preview_row_count": 2}, {"total_row_count": 2}, {"row_count": 2}, {"row_count": True},
            {"truncated": True}, {"query_run_id": None}, {"rows": []},
            {"rows": [["show-1", 7, "maya", 1, "slides-maya", "ready"]] * 2},
            {"columns": ["speaker_id", "show_revision"]}, {"result_id": "../bad"},
        )]
        for missing in ("preview_row_count", "total_row_count", "query_run_id", "truncated"):
            incomplete = query_response()
            incomplete.pop(missing)
            bad_results.append(incomplete)
        for response in bad_results:
            with self.subTest(response=response):
                self.query_results = [response]
                result = await hotdata.validate_show(self.show, "maya")
                self.assertEqual(result["status"], "failed")
                self.assertFalse(result["ready"])
                self.assertNotIn("show_revision", result)
                self.assertFalse(hotdata._SNAPSHOTS)

    async def test_complete_inline_result_does_not_require_persistence(self):
        self.query_results = [query_response(result_id=None, warning="Result persistence unavailable")]
        result = await hotdata.validate_show(self.show, "maya")
        self.assertEqual(result["status"], "verified")
        self.assertNotIn("result_id", result["records"][-1]["evidence"])

    async def test_deprecated_row_count_can_be_omitted_when_current_counts_are_complete(self):
        response = query_response()
        response.pop("row_count")
        self.query_results = [response]
        self.assertEqual((await hotdata.validate_show(self.show, "maya"))["status"], "verified")

    async def test_revision_asset_and_strict_value_type_mismatches_fail(self):
        for index, value in ((0, "other-show"), (1, 6), (1, 7.0), (2, "ravi"), (3, True), (4, "slides-ravi"), (5, "missing")):
            with self.subTest(index=index, value=value):
                row = ["show-1", 7, "maya", 1, "slides-maya", "ready"]
                row[index] = value
                self.query_results = [query_response(row)]
                result = await hotdata.validate_show(self.show, "maya")
                self.assertEqual(result["status"], "failed")
                self.assertFalse(result["ready"])

    async def test_actual_unready_speaker_or_missing_asset_is_blocked_with_verified_query(self):
        for state in ("unready", "missing", "absent"):
            with self.subTest(state=state):
                self.show = copy.deepcopy(SHOW)
                row = ["show-1", 7, "maya", 1, "slides-maya", "ready"]
                if state == "unready":
                    self.show["speakers"][0]["ready"] = False
                    row[3] = 0
                elif state == "missing":
                    self.show["assets"][0]["status"] = "missing"
                    row[5] = "missing"
                else:
                    self.show["assets"].pop(0)
                    row[4:] = [None, None]
                self.query_results = [query_response(row)]
                result = await hotdata.validate_show(self.show, "maya")
                self.assertEqual(result["status"], "blocked")
                self.assertFalse(result["ready"])
                self.assertEqual(result["records"][-1]["status"], "verified")

    async def test_second_speaker_uses_same_snapshot_but_runs_a_fresh_query(self):
        first = await hotdata.validate_show(self.show, "maya")
        # List ordering cannot change the snapshot identity.
        self.show["speakers"].reverse()
        self.show["assets"].reverse()
        self.query_results = [query_response(["show-1", 7, "ravi", 1, "slides-ravi", "ready"], query_run_id="qrun-second")]
        second = await hotdata.validate_show(self.show, "ravi")
        self.assertEqual(second["status"], "verified")
        self.assertEqual(self.database_count, 1)
        self.assertEqual(self.paths().count("/v1/query"), 2)
        self.assertEqual(len(second["records"]), 1)
        self.assertEqual(second["records"][0]["evidence"]["query_run_id"], "qrun-second")
        self.assertTrue(second["records"][0]["evidence"]["snapshot_reused"])
        self.assertEqual(first["records"][-1]["evidence"]["snapshot_sha256"], second["records"][-1]["evidence"]["snapshot_sha256"])

    async def test_revision_or_contents_change_gets_a_separate_database(self):
        await hotdata.validate_show(self.show, "maya")
        self.show["revision"] = 8
        self.query_results = [query_response(["show-1", 8, "maya", 1, "slides-maya", "ready"])]
        second = await hotdata.validate_show(self.show, "maya")
        self.assertEqual(second["show_revision"], 8)
        self.show["assets"][0]["status"] = "missing"
        self.query_results = [query_response(["show-1", 8, "maya", 1, "slides-maya", "missing"])]
        third = await hotdata.validate_show(self.show, "maya")
        self.assertEqual(third["status"], "blocked")
        self.assertEqual(self.database_count, 3)
        self.assertEqual(len({json.loads(request.content)["idempotency_key"] for request in self.requests if request.url.path.endswith("/loads")}), 3)

    async def test_restart_loads_same_snapshot_into_new_database_with_distinct_idempotency(self):
        first = await hotdata.validate_show(self.show, "maya")
        self.assertEqual(first["status"], "verified")
        # Simulate a process restart: remote completed loads survive, local
        # snapshot references do not. A reused load key would return HTTP 409.
        hotdata._SNAPSHOTS.clear()
        second = await hotdata.validate_show(self.show, "maya")
        self.assertEqual(second["status"], "verified")
        self.assertEqual(self.database_count, 2)
        creates = [json.loads(r.content) for r in self.requests if r.url.path == "/v1/databases"]
        self.assertNotEqual(creates[0]["name"], creates[1]["name"])
        loads = [json.loads(r.content) for r in self.requests if r.url.path.endswith("/loads")]
        self.assertEqual(loads[0]["data"], loads[1]["data"])
        self.assertNotEqual(loads[0]["idempotency_key"], loads[1]["idempotency_key"])
        for result, expected_database in ((first, "dbid-1"), (second, "dbid-2")):
            evidence = result["records"][-1]["evidence"]
            self.assertEqual(evidence["database_id"], expected_database)
            self.assertFalse(evidence["snapshot_reused"])
            self.assertEqual(result["records"][0]["operation"], "publish_show_snapshot")
        self.assertEqual(first["records"][-1]["evidence"]["snapshot_sha256"],
                         second["records"][-1]["evidence"]["snapshot_sha256"])
        self.assertEqual(self.paths().count("/v1/query"), 2)

    async def test_cache_expiry_and_workspace_or_token_change_force_new_publication(self):
        await hotdata.validate_show(self.show, "maya")
        for entry in hotdata._SNAPSHOTS.values():
            entry["valid_until"] = 0
        await hotdata.validate_show(self.show, "maya")
        os.environ["HOTDATA_WORKSPACE"] = "work-other"
        await hotdata.validate_show(self.show, "maya")
        os.environ["HOTDATA_API_KEY"] = "other-offline-key"
        await hotdata.validate_show(self.show, "maya")
        self.assertEqual(self.database_count, 4)

    async def test_cached_database_failure_is_failed_and_evicts_reference(self):
        await hotdata.validate_show(self.show, "maya")
        self.query_results = [httpx.Response(404, json={"error": {"message": "sensitive provider body"}})]
        failed = await hotdata.validate_show(self.show, "maya")
        self.assertEqual(failed["status"], "failed")
        self.assertFalse(failed["ready"])
        self.assertFalse(hotdata._SNAPSHOTS)
        self.assertNotIn("sensitive provider body", json.dumps(failed))
        self.assertEqual((await hotdata.validate_show(self.show, "maya"))["status"], "verified")
        self.assertEqual(self.database_count, 2)

    async def test_input_mutated_during_io_does_not_relabel_old_query_as_new_revision(self):
        def mutate(request):
            if request.url.path == "/v1/query":
                self.show["revision"] = 8
        self.hook = mutate
        result = await hotdata.validate_show(self.show, "maya")
        self.assertEqual(result["show_revision"], 7)
        self.assertEqual(result["records"][-1]["evidence"]["show_revision"], 7)

    async def test_missing_credentials_and_rejected_activation_remain_blocked(self):
        del os.environ["HOTDATA_API_KEY"]
        result = await hotdata.validate_show(self.show, "maya")
        self.assertEqual(result["status"], "blocked")
        self.assertFalse(self.requests)
        os.environ["HOTDATA_API_KEY"] = "offline-test-key"
        for code in (401, 402, 403):
            self.create_result = httpx.Response(code, json={"error": {"message": "sensitive provider body"}})
            result = await hotdata.validate_show(self.show, "maya")
            self.assertEqual(result["status"], "blocked")
            self.assertFalse(any(record["status"] in {"fixture", "verified"} for record in result["records"]))
            self.assertNotIn("sensitive provider body", json.dumps(result))

    async def test_sql_control_characters_are_rejected_before_provider_work(self):
        for identifier in ("maya' OR TRUE --", "maya\n", "../../other", "maya;DROP"):
            result = await hotdata.validate_show(self.show, identifier)
            self.assertEqual(result["status"], "blocked")
        self.assertFalse(self.requests)


if __name__ == "__main__":
    unittest.main()

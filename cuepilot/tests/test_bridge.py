"""Security contract for the externally reachable, narrowly scoped bridge."""

import asyncio
import json
import unittest
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient

from cuepilot.bridge import BridgeError, create_app, request_body


RUN_ID = "b7f9e4a1-60f6-4a7b-9f85-4d3efac182cb"
RECEIPT_ID = "3e378baf-bfed-49fd-aa31-fc861e126a27"
TOKEN = "bridge-test-token-for-local-contract-tests"
TOOL_NAMES = (
    "ingest-memory", "recall-recipe", "validate-show", "plan", "execute", "verify",
)
AUTH = {"Authorization": "Bearer " + TOKEN}
PRIVATE = "private-note-or-upstream-secret-must-not-escape"


def upstream_run():
    return {
        "id": RUN_ID,
        "executionMode": "live",
        "status": "completed",
        "notes": PRIVATE,
        "reason": PRIVATE,
        "traces": [{"evidence": {"token": PRIVATE}}],
        "operations": {"execute": {"status": "verified", "result": {"evidence": PRIVATE}}},
        "unexpected": {"nested": PRIVATE},
        "plan": {
            "hash": "a" * 64,
            "cues": [{"instruction": PRIVATE}],
            "evidence": PRIVATE,
        },
        "receipts": [{
            "ok": True,
            "id": RECEIPT_ID,
            "runId": RUN_ID,
            "stepIndex": 0,
            "scene": "intro",
            "stageRevision": 1,
            "committedAt": "2026-09-11T19:00:00Z",
            "evidence": PRIVATE,
            "unexpected": PRIVATE,
        }],
    }


def receipt_prefix(length=3):
    receipts = []
    for step in range(length):
        receipt = upstream_run()["receipts"][0]
        receipt.update(
            id=f"{step + 1:08x}-bfed-49fd-aa31-fc861e126a27",
            stepIndex=step,
            scene=("intro", "presentation", "holding")[step],
            stageRevision=step + 1,
        )
        receipts.append(receipt)
    return receipts


class ChunkedBody(httpx.AsyncByteStream):
    """An upstream response without a Content-Length header."""

    async def __aiter__(self):
        for _ in range(17):
            yield b"x" * 4096


class BridgeTests(unittest.TestCase):
    def setUp(self):
        self.requests = []
        self.handler = self.success
        self.client = TestClient(create_app(
            bridge_token=TOKEN,
            transport=httpx.MockTransport(self.dispatch),
        ))

    def tearDown(self):
        self.client.close()

    def dispatch(self, request):
        self.requests.append(request)
        return self.handler(request)

    def success(self, request):
        if request.url.path == "/api/v1/health":
            return httpx.Response(200, json={
                "ok": True, "service": "CuePilot", "version": "0.1.0",
                "operatorConfigured": True, "bridgeConfigured": True,
            })
        if request.url.path == f"/api/v1/runs/{RUN_ID}":
            return httpx.Response(200, json=upstream_run())
        if request.url.path.endswith("/plan"):
            return httpx.Response(200, json=upstream_run())
        return httpx.Response(200, json={
            "ok": True, "runId": RUN_ID, "status": "verified",
            "provider": "fixture", "operation": "bridge-contract-test",
            "evidence": {}, "receipts": [],
        })

    def test_every_allowed_route_requires_exact_bearer_token(self):
        routes = [("GET", "/api/v1/health"), ("GET", f"/api/v1/runs/{RUN_ID}")]
        routes += [("POST", f"/api/v1/tools/{name}") for name in TOOL_NAMES]
        for method, path in routes:
            for authorization in (None, "Bearer wrong", TOKEN, "Basic " + TOKEN,
                                  "Bearer " + TOKEN + "-suffix", "Bearer  " + TOKEN):
                with self.subTest(method=method, path=path, authorization=authorization):
                    headers = {} if authorization is None else {"Authorization": authorization}
                    response = self.client.request(method, path, headers=headers,
                                                   json={"runId": RUN_ID} if method == "POST" else None)
                    self.assertEqual(response.status_code, 401, response.text)
        self.assertEqual(self.requests, [])

    def test_only_the_eight_allowlisted_routes_reach_fixed_upstream(self):
        for method, path in [("GET", "/api/v1/health"), ("GET", f"/api/v1/runs/{RUN_ID}")]:
            response = self.client.request(method, path, headers=AUTH)
            self.assertEqual(response.status_code, 200, response.text)
        for name in TOOL_NAMES:
            response = self.client.post(f"/api/v1/tools/{name}", headers=AUTH, json={"runId": RUN_ID})
            self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(len(self.requests), 8)
        for request in self.requests:
            self.assertEqual(request.url.scheme, "http")
            self.assertEqual(request.url.host, "127.0.0.1")
            self.assertEqual(request.url.port, 8787)
            self.assertEqual(request.url.query, b"")
            self.assertEqual(request.headers["authorization"], AUTH["Authorization"])
            if request.method == "POST":
                self.assertEqual(json.loads(request.content), {"runId": RUN_ID})

    def test_operator_stage_and_documentation_routes_are_not_exposed(self):
        denied = [
            ("GET", "/"), ("GET", "/docs"), ("GET", "/redoc"),
            ("GET", "/openapi.json"), ("GET", "/api/v1/show"),
            ("GET", "/api/v1/stage"), ("GET", "/api/v1/runs"),
            ("POST", "/api/v1/runs"),
            ("POST", f"/api/v1/runs/{RUN_ID}/approve"),
            ("POST", f"/api/v1/runs/{RUN_ID}/advance"),
            ("POST", f"/api/v1/runs/{RUN_ID}/execute"),
            ("PATCH", "/api/v1/assets/slides-maya"),
            ("PATCH", "/api/v1/speakers/maya"),
            ("POST", "/api/v1/tools/stage/cue"),
            ("POST", "/api/v1/tools/unknown"),
            ("GET", "/api/v1/tools/execute"),
            ("POST", "/api/v1/health"),
            ("HEAD", "/api/v1/health"),
            ("OPTIONS", "/api/v1/health"),
            ("DELETE", f"/api/v1/runs/{RUN_ID}"),
        ]
        for method, path in denied:
            with self.subTest(method=method, path=path):
                response = self.client.request(method, path, headers=AUTH, follow_redirects=False)
                self.assertIn(response.status_code, (404, 405), response.text)
        self.assertEqual(self.requests, [])

    def test_run_ids_must_be_canonical_uuid_strings(self):
        invalid_ids = [RUN_ID.upper(), RUN_ID.replace("-", ""), "{" + RUN_ID + "}",
                       "not-a-uuid", "1", "urn:uuid:" + RUN_ID]
        for run_id in invalid_ids:
            with self.subTest(run_id=run_id):
                response = self.client.get(f"/api/v1/runs/{run_id}", headers=AUTH)
                self.assertIn(response.status_code, (400, 404, 422), response.text)
                response = self.client.post("/api/v1/tools/plan", headers=AUTH, json={"runId": run_id})
                self.assertIn(response.status_code, (400, 422), response.text)
        self.assertEqual(self.requests, [])

    def test_post_requires_exact_json_object_with_no_extra_fields(self):
        invalid_payloads = [None, [], RUN_ID, {}, {"runId": None}, {"runId": 1},
                            {"runId": RUN_ID, "baseUrl": "https://untrusted.invalid"},
                            {"runId": RUN_ID, "stepIndex": 0},
                            {"runId": RUN_ID, "operatorToken": PRIVATE}]
        for payload in invalid_payloads:
            with self.subTest(payload=payload):
                response = self.client.post("/api/v1/tools/plan",
                                            headers={**AUTH, "Content-Type": "application/json"},
                                            content=json.dumps(payload))
                self.assertIn(response.status_code, (400, 415, 422), response.text)
        response = self.client.post("/api/v1/tools/plan", headers={**AUTH, "Content-Type": "application/json"},
                                    content=b"{malformed")
        self.assertIn(response.status_code, (400, 422), response.text)
        response = self.client.post("/api/v1/tools/plan", headers={**AUTH, "Content-Type": "text/plain"},
                                    content=json.dumps({"runId": RUN_ID}))
        self.assertIn(response.status_code, (400, 415, 422), response.text)
        self.assertEqual(self.requests, [])

    def test_queries_cannot_change_upstream_target_or_tool_parameters(self):
        for method, path in [("GET", "/api/v1/health"), ("GET", f"/api/v1/runs/{RUN_ID}"),
                             ("POST", "/api/v1/tools/execute")]:
            with self.subTest(path=path):
                response = self.client.request(
                    method, path + "?runId=other&url=https://untrusted.invalid", headers=AUTH,
                    json={"runId": RUN_ID} if method == "POST" else None,
                )
                self.assertIn(response.status_code, (400, 404, 422), response.text)
        self.assertEqual(self.requests, [])

    def test_body_limit_is_enforced_before_forwarding(self):
        payload = json.dumps({"runId": RUN_ID}).encode()
        for body in (payload + b" " * (4097 - len(payload)), b"x" * 8192):
            with self.subTest(length=len(body)):
                response = self.client.post("/api/v1/tools/plan", content=body,
                                            headers={**AUTH, "Content-Type": "application/json"})
                self.assertEqual(response.status_code, 413, response.text)
        self.assertEqual(self.requests, [])

    def test_body_limit_allows_exactly_4096_bytes(self):
        payload = json.dumps({"runId": RUN_ID}).encode()
        response = self.client.post("/api/v1/tools/plan", content=payload + b" " * (4096 - len(payload)),
                                    headers={**AUTH, "Content-Type": "application/json"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(len(self.requests), 1)

    def test_client_headers_and_cookies_are_not_forwarded(self):
        response = self.client.post("/api/v1/tools/verify", json={"runId": RUN_ID}, headers={
            **AUTH, "Cookie": "session=" + PRIVATE, "X-Operator-Token": PRIVATE,
            "X-Forwarded-For": "203.0.113.5", "X-Forwarded-Host": "untrusted.invalid",
            "X-Original-URL": "/api/v1/tools/stage/cue", "Origin": "https://untrusted.invalid",
            "Host": "untrusted.invalid", "User-Agent": PRIVATE,
        })
        self.assertEqual(response.status_code, 200, response.text)
        request = self.requests[0]
        for name in ("cookie", "x-operator-token", "x-forwarded-for", "x-forwarded-host",
                     "x-original-url", "origin"):
            self.assertNotIn(name, request.headers)
        self.assertNotIn(PRIVATE, str(request.headers))
        self.assertEqual(request.headers["host"], "127.0.0.1:8787")
        self.assertTrue(request.headers["content-type"].startswith("application/json"))

    def test_get_run_returns_only_safe_canonical_summary(self):
        response = self.client.get(f"/api/v1/runs/{RUN_ID}", headers=AUTH)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json(), {
            "id": RUN_ID, "executionMode": "live", "status": "completed",
            "verifiedCompletion": False,
            "plan": {"hash": "a" * 64},
            "receipts": [{
                "ok": True, "id": RECEIPT_ID, "runId": RUN_ID, "stepIndex": 0, "scene": "intro",
                "stageRevision": 1, "committedAt": "2026-09-11T19:00:00Z",
            }],
        })
        self.assertNotIn(PRIVATE, response.text)

    def test_verified_completion_preserves_only_booleans_and_defaults_when_absent(self):
        absent = object()
        for value in (True, False, absent, None, 1, "true", {"verified": True}):
            with self.subTest(value=value):
                run = upstream_run()
                if value is not absent:
                    run["verifiedCompletion"] = value
                self.handler = lambda request: httpx.Response(200, json=run)
                response = self.client.get(f"/api/v1/runs/{RUN_ID}", headers=AUTH)
                self.assertEqual(response.status_code, 200, response.text)
                result = response.json()
                if value is absent:
                    self.assertIs(result["verifiedCompletion"], False)
                elif type(value) is bool:
                    self.assertIs(result["verifiedCompletion"], value)
                else:
                    self.assertNotIn("verifiedCompletion", result)
                self.assertNotIn("operations", result)
                self.assertNotIn(PRIVATE, response.text)

    def test_receipts_preserve_success_and_ordered_scene_prefixes(self):
        for length in range(4):
            with self.subTest(length=length):
                run = upstream_run()
                run["receipts"] = receipt_prefix(length)
                self.handler = lambda request: httpx.Response(200, json=run)
                response = self.client.get(f"/api/v1/runs/{RUN_ID}", headers=AUTH)
                self.assertEqual(response.status_code, 200, response.text)
                receipts = response.json()["receipts"]
                self.assertEqual([item["stepIndex"] for item in receipts], list(range(length)))
                self.assertEqual([item["scene"] for item in receipts],
                                 ["intro", "presentation", "holding"][:length])
                self.assertTrue(all(item["ok"] is True for item in receipts))
                self.assertNotIn(PRIVATE, response.text)

    def assert_receipts_rejected(self, receipts):
        run = upstream_run()
        run["receipts"] = receipts
        self.handler = lambda request: httpx.Response(200, json=run)
        response = self.client.get(f"/api/v1/runs/{RUN_ID}", headers=AUTH)
        self.assertEqual(response.status_code, 502, response.text)
        self.assertEqual(response.json()["detail"]["code"], "upstream_invalid_response")
        self.assertNotIn(PRIVATE, response.text)
        self.handler = lambda request: httpx.Response(200, json={
            "ok": True, "runId": RUN_ID, "status": "completed", "receipts": receipts,
        })
        response = self.client.post("/api/v1/tools/verify", headers=AUTH, json={"runId": RUN_ID})
        self.assertEqual(response.status_code, 502, response.text)
        self.assertEqual(response.json()["detail"]["code"], "upstream_invalid_response")
        self.assertNotIn(PRIVATE, response.text)

    def test_receipts_require_literal_true_success(self):
        for ok in (False, None, 1, "true"):
            with self.subTest(ok=ok):
                receipts = receipt_prefix(1)
                receipts[0]["ok"] = ok
                self.assert_receipts_rejected(receipts)
        receipts = receipt_prefix(1)
        del receipts[0]["ok"]
        self.assert_receipts_rejected(receipts)

    def test_receipts_reject_missing_duplicate_and_reordered_steps(self):
        for order in ([1], [0, 2], [1, 0], [0, 0], [0, 2, 1]):
            with self.subTest(order=order):
                full = receipt_prefix()
                self.assert_receipts_rejected([full[step] for step in order])

    def test_receipts_bind_each_step_to_its_expected_scene(self):
        for step, wrong_scene in enumerate(("holding", "intro", "presentation")):
            with self.subTest(step=step, wrong_scene=wrong_scene):
                receipts = receipt_prefix()
                receipts[step]["scene"] = wrong_scene
                self.assert_receipts_rejected(receipts)

    def test_receipts_require_strictly_increasing_stage_revisions(self):
        for revisions in ([1, 1], [3, 2], [1, 3, 2], [1, 0]):
            with self.subTest(revisions=revisions):
                receipts = receipt_prefix(len(revisions))
                for receipt, revision in zip(receipts, revisions):
                    receipt["stageRevision"] = revision
                self.assert_receipts_rejected(receipts)

    def test_upstream_response_headers_and_errors_are_not_exposed(self):
        for status in (400, 401, 403, 404, 409, 422, 500):
            with self.subTest(status=status):
                self.handler = lambda request: httpx.Response(status, json={"detail": PRIVATE}, headers={
                    "Set-Cookie": "session=" + PRIVATE, "X-Upstream-Debug": PRIVATE,
                    "WWW-Authenticate": PRIVATE,
                })
                response = self.client.get("/api/v1/health", headers=AUTH)
                self.assertGreaterEqual(response.status_code, 400)
                self.assertNotIn(PRIVATE, response.text)
                for name in ("set-cookie", "x-upstream-debug", "www-authenticate"):
                    self.assertNotIn(name, response.headers)

    def test_success_response_cannot_set_client_cookies(self):
        self.handler = lambda request: httpx.Response(200, json=upstream_run(), headers={
            "Set-Cookie": "session=" + PRIVATE, "X-Upstream-Debug": PRIVATE,
        })
        response = self.client.get(f"/api/v1/runs/{RUN_ID}", headers=AUTH)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertNotIn("set-cookie", response.headers)
        self.assertNotIn("x-upstream-debug", response.headers)

    def test_upstream_redirect_is_rejected_without_following_it(self):
        for status in (301, 302, 303, 307, 308):
            with self.subTest(status=status):
                self.requests.clear()
                self.handler = lambda request: httpx.Response(status, headers={
                    "Location": "https://untrusted.invalid/" + PRIVATE,
                    "Set-Cookie": "session=" + PRIVATE,
                })
                response = self.client.get("/api/v1/health", headers=AUTH, follow_redirects=False)
                self.assertEqual(response.status_code, 502, response.text)
                self.assertEqual(len(self.requests), 1)
                self.assertNotIn("location", response.headers)
                self.assertNotIn("set-cookie", response.headers)
                self.assertNotIn(PRIVATE, response.text)

    def test_upstream_timeout_has_a_generic_failure(self):
        def timeout(request):
            raise httpx.ReadTimeout(PRIVATE, request=request)
        self.handler = timeout
        response = self.client.get("/api/v1/health", headers=AUTH)
        self.assertIn(response.status_code, (502, 504), response.text)
        self.assertNotIn(PRIVATE, response.text)

    def test_upstream_oversize_response_is_rejected_before_redaction(self):
        def large_response(request):
            run = upstream_run()
            run["notes"] = "x" * 65536
            return httpx.Response(200, json=run)
        self.handler = large_response
        response = self.client.get(f"/api/v1/runs/{RUN_ID}", headers=AUTH)
        self.assertEqual(response.status_code, 502, response.text)
        self.assertLess(len(response.content), 4096)

    def test_chunked_upstream_response_cannot_bypass_size_limit(self):
        self.handler = lambda request: httpx.Response(200, stream=ChunkedBody(),
                                                      headers={"Content-Type": "application/json"})
        response = self.client.get("/api/v1/health", headers=AUTH)
        self.assertEqual(response.status_code, 502, response.text)
        self.assertLess(len(response.content), 4096)

    def test_malformed_upstream_json_has_a_generic_failure(self):
        self.handler = lambda request: httpx.Response(200, text="<html>" + PRIVATE + "</html>")
        response = self.client.get("/api/v1/health", headers=AUTH)
        self.assertEqual(response.status_code, 502, response.text)
        self.assertNotIn(PRIVATE, response.text)


class RequestBodyDeadlineTests(unittest.IsolatedAsyncioTestCase):
    async def test_slow_request_body_has_a_bounded_total_read_time(self):
        class SlowRequest:
            headers = {"content-type": "application/json"}
            cancelled = False

            async def stream(self):
                yield b'{"runId":'
                try:
                    await asyncio.Event().wait()
                finally:
                    self.cancelled = True

        request = SlowRequest()
        with patch("cuepilot.bridge.REQUEST_BODY_TIMEOUT", 0.005):
            with self.assertRaises(BridgeError) as raised:
                await asyncio.wait_for(request_body(request), timeout=0.25)
        self.assertEqual(raised.exception.code, "request_timeout")
        self.assertEqual(raised.exception.status, 408)
        self.assertTrue(request.cancelled)


if __name__ == "__main__":
    unittest.main()

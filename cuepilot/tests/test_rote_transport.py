"""Local transport checks; these do not constitute a Rote replay verification."""

from contextlib import contextmanager
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
import json
from pathlib import Path
import threading
import unittest
from unittest.mock import MagicMock, patch
import urllib.error


HELPER_PATH = Path(__file__).resolve().parents[1] / "plays/stage-sequence/resources/cue.py"
SPEC = importlib.util.spec_from_file_location("cuepilot_rote_transport", HELPER_PATH)
cue = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(cue)


def receipt_for(run_id="run-new", step_index=0):
    return {
        "ok": True,
        "id": "receipt-%s-%s" % (run_id, step_index),
        "runId": run_id,
        "stepIndex": step_index,
        "scene": ("intro", "presentation", "holding")[step_index],
        "stageRevision": 7 + step_index,
        "committedAt": "2026-09-11T20:00:00Z",
    }


@contextmanager
def local_stage(status=200, body=None, redirect=None):
    """Start an isolated HTTP endpoint on a new loopback port for this check."""
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def handle_request(self):
            length = int(self.headers.get("Content-Length", "0"))
            requests.append({
                "method": self.command,
                "path": self.path,
                "headers": dict(self.headers),
                "body": self.rfile.read(length),
            })
            data = body if body is not None else json.dumps(receipt_for()).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            if redirect is not None:
                self.send_header("Location", redirect)
            self.end_headers()
            self.wfile.write(data)

        do_POST = handle_request
        do_GET = handle_request

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
    thread.start()
    try:
        yield "http://127.0.0.1:%s" % server.server_port, requests
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


class ReceiptTests(unittest.TestCase):
    def test_all_three_canonical_receipts_are_accepted(self):
        for step in range(3):
            with self.subTest(step=step):
                receipt = receipt_for(step_index=step)
                self.assertIs(cue.validate_receipt(receipt, "run-new", step), receipt)

    def test_each_canonical_field_is_required(self):
        for field in receipt_for():
            with self.subTest(field=field):
                receipt = receipt_for()
                del receipt[field]
                with self.assertRaisesRegex(ValueError, "does not match"):
                    cue.validate_receipt(receipt, "run-new", 0)

    def test_receipt_rejects_wrong_identity_scene_and_noncanonical_types(self):
        invalid = {
            "ok": [False, 1, "true", None],
            "id": [None, "", " \t", 3, True],
            "runId": ["run-old", "", None],
            "stepIndex": [True, False, 0.0, "0", 1, -1, None],
            "scene": ["holding", "presentation", "INTRO", None],
            "stageRevision": [True, False, 7.0, "7", 0, -1, None],
            "committedAt": [None, "", " \t", 3, True],
        }
        for field, values in invalid.items():
            for value in values:
                with self.subTest(field=field, value=value):
                    receipt = receipt_for()
                    receipt[field] = value
                    with self.assertRaisesRegex(ValueError, "does not match"):
                        cue.validate_receipt(receipt, "run-new", 0)
        for value in (None, [], "receipt", True):
            with self.subTest(receipt=value), self.assertRaises(ValueError):
                cue.validate_receipt(value, "run-new", 0)

    def test_expected_step_and_run_inputs_are_validated_too(self):
        for index in (True, False, 0.0, "0", -1, 3, None):
            with self.subTest(index=index), self.assertRaises(ValueError):
                cue.validate_receipt(receipt_for(), "run-new", index)
        for run_id in ("", "../run", "run/path", "run?query", "run\n", None):
            with self.subTest(run_id=run_id), self.assertRaises(ValueError):
                cue.validate_receipt(receipt_for(), run_id, 0)

    def test_digest_matches_reordered_receipt_but_detects_changed_commit(self):
        receipt = receipt_for()
        reordered = json.loads(json.dumps(dict(reversed(list(receipt.items()))), indent=2))
        expected = hashlib.sha256(json.dumps(receipt, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        self.assertEqual(cue.receipt_digest(receipt), expected)
        self.assertEqual(cue.receipt_digest(reordered), expected)
        for field, value in (("id", "different"), ("stageRevision", 8), ("committedAt", "later")):
            with self.subTest(field=field):
                self.assertNotEqual(cue.receipt_digest({**receipt, field: value}), expected)

    def test_request_ids_preserve_retry_identity_and_separate_runs_and_steps(self):
        expected = "rote-%s-0" % hashlib.sha256(b"run-new").hexdigest()[:32]
        self.assertEqual(cue.request_id("run-new", 0), expected)
        self.assertEqual(cue.request_id("run-new", 0), cue.request_id("run-new", 0))
        self.assertEqual(len({cue.request_id(run, step) for run in ("run-new", "run-old") for step in range(3)}), 6)


class TransportTests(unittest.TestCase):
    def setUp(self):
        self.environment = patch.dict(cue.os.environ, {"CUEPILOT_BRIDGE_TOKEN": "transport-test-token"})
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def test_http_transport_returns_receipt_identity_and_both_digests(self):
        receipt = receipt_for()
        raw = json.dumps(dict(reversed(list(receipt.items()))), indent=2).encode()
        with local_stage(body=raw) as (base_url, requests):
            result = cue.send_cue("run-new", base_url, 0)
        self.assertEqual(len(requests), 1)
        request = requests[0]
        self.assertEqual(request["method"], "POST")
        self.assertEqual(request["path"], "/api/v1/tools/stage/cue")
        self.assertEqual(request["headers"]["Authorization"], "Bearer transport-test-token")
        self.assertEqual(json.loads(request["body"]), {
            "runId": "run-new", "stepIndex": 0, "requestId": cue.request_id("run-new", 0),
        })
        self.assertEqual(result, {
            "ok": True, "runId": "run-new", "stepIndex": 0,
            "requestId": cue.request_id("run-new", 0),
            "receiptSha256": hashlib.sha256(raw).hexdigest(),
            "receiptCanonicalSha256": cue.receipt_digest(receipt),
            "receiptId": receipt["id"], "stageRevision": receipt["stageRevision"],
            "scene": receipt["scene"], "committedAt": receipt["committedAt"],
        })
        self.assertNotIn("transport-test-token", json.dumps(result))

    def test_redirects_are_rejected_without_sending_another_request(self):
        for status in (301, 302, 303, 307, 308):
            with self.subTest(status=status), local_stage(status=status, redirect="/unexpected") as (base_url, requests):
                with self.assertRaisesRegex(ValueError, "HTTP %s" % status):
                    cue.send_cue("run-new", base_url, 0)
                self.assertEqual(len(requests), 1)
                self.assertEqual(requests[0]["path"], "/api/v1/tools/stage/cue")

    def test_api_errors_are_reported_without_returning_the_response_body(self):
        for status in (401, 403, 409, 500):
            with self.subTest(status=status), local_stage(status=status, body=b"private failure detail") as (base_url, requests):
                with self.assertRaises(ValueError) as caught:
                    cue.send_cue("run-new", base_url, 0)
                self.assertEqual(str(caught.exception), "stage API rejected cue (HTTP %s)" % status)
                self.assertEqual(len(requests), 1)

    def test_transport_rejects_invalid_json_large_and_mismatched_receipts(self):
        cases = [
            (b"not-json", "JSON receipt"),
            (b"\xff", "JSON receipt"),
            (b"x" * 65537, "size limit"),
            (json.dumps(receipt_for(run_id="run-old")).encode(), "does not match"),
            (json.dumps(receipt_for(step_index=1)).encode(), "does not match"),
            (json.dumps({**receipt_for(), "scene": "holding"}).encode(), "does not match"),
            (json.dumps({"ok": True, "runId": "run-new", "stepIndex": 0}).encode(), "does not match"),
        ]
        for raw, message in cases:
            with self.subTest(message=message, size=len(raw)), local_stage(body=raw) as (base_url, _):
                with self.assertRaisesRegex(ValueError, message):
                    cue.send_cue("run-new", base_url, 0)

    def test_network_failures_are_bounded_and_do_not_disclose_error_details(self):
        for failure in (urllib.error.URLError("private detail"), TimeoutError("private detail"), OSError("private detail")):
            with self.subTest(error=type(failure).__name__), patch.object(cue.urllib.request, "build_opener") as build:
                build.return_value.open.side_effect = failure
                with self.assertRaisesRegex(ValueError, "^stage API is unreachable$"):
                    cue.send_cue("run-new", "http://127.0.0.1:1234", 0)
                self.assertEqual(build.return_value.open.call_args.kwargs["timeout"], 12)

    def test_unsafe_inputs_fail_before_opening_a_connection(self):
        cases = [
            ("run-new", "https://example.com", 0),
            ("run-new", "http://127.0.0.1:8787/api", 0),
            ("run-new", "http://user:password@127.0.0.1", 0),
            ("run-new", "http://127.0.0.1?target=other", 0),
            ("../run", "http://127.0.0.1", 0),
            ("run-new", "http://127.0.0.1", True),
        ]
        with patch.object(cue.urllib.request, "build_opener") as build:
            for args in cases:
                with self.subTest(args=args), self.assertRaises(ValueError):
                    cue.send_cue(*args)
            for token in ("", "bad\rvalue", "bad\nvalue"):
                with self.subTest(token=token), patch.dict(cue.os.environ, {"CUEPILOT_BRIDGE_TOKEN": token}):
                    with self.assertRaisesRegex(ValueError, "unavailable"):
                        cue.send_cue("run-new", "http://127.0.0.1", 0)
            build.assert_not_called()

    def test_transport_disables_proxy_inheritance(self):
        opener = MagicMock()
        opener.open.return_value.__enter__.return_value.read.return_value = json.dumps(receipt_for()).encode()
        with patch.object(cue.urllib.request, "build_opener", return_value=opener) as build:
            cue.send_cue("run-new", "http://127.0.0.1", 0)
        proxy, redirect = build.call_args.args
        self.assertIsInstance(proxy, cue.urllib.request.ProxyHandler)
        self.assertEqual(proxy.proxies, {})
        self.assertIsInstance(redirect, cue.NoRedirect)


if __name__ == "__main__":
    unittest.main()

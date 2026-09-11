#!/usr/bin/env python3
"""One authored HTTP transport operation; Rote owns ordering and replay."""

import argparse
import hashlib
import ipaddress
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def loopback_url(value):
    if not isinstance(value, str) or any(char.isspace() for char in value):
        raise ValueError("base_url must be a loopback HTTP origin")
    try:
        parsed = urllib.parse.urlsplit(value)
        port = parsed.port
        host = parsed.hostname
        local = host == "localhost" or ipaddress.ip_address(host).is_loopback
    except (TypeError, ValueError):
        raise ValueError("base_url must be a loopback HTTP origin") from None
    if (not local or parsed.scheme not in ("http", "https") or
            parsed.username is not None or parsed.password is not None or
            parsed.query or parsed.fragment or parsed.path not in ("", "/")):
        raise ValueError("base_url must be a loopback HTTP origin")
    if port is not None and not 1 <= port <= 65535:
        raise ValueError("base_url has an invalid port")
    return value.rstrip("/")


def validate_run_id(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,79}", value):
        raise ValueError("invalid run_id")
    return value


def _validate_step_index(step_index):
    if type(step_index) is not int or step_index not in (0, 1, 2):
        raise ValueError("step_index must be 0, 1, or 2")
    return step_index


def request_id(run_id, step_index):
    """Keep retries stable while binding each transport operation to its run."""
    run_id = validate_run_id(run_id)
    _validate_step_index(step_index)
    return "rote-%s-%s" % (hashlib.sha256(run_id.encode()).hexdigest()[:32], step_index)


def validate_receipt(receipt, run_id, step_index):
    """Validate the stage API's canonical receipt for exactly one expected cue."""
    run_id = validate_run_id(run_id)
    _validate_step_index(step_index)
    if (not isinstance(receipt, dict) or receipt.get("ok") is not True or
            receipt.get("runId") != run_id or type(receipt.get("stepIndex")) is not int or
            receipt["stepIndex"] != step_index or
            receipt.get("scene") != ("intro", "presentation", "holding")[step_index] or
            type(receipt.get("stageRevision")) is not int or receipt["stageRevision"] < 1 or
            not isinstance(receipt.get("id"), str) or not receipt["id"].strip() or
            not isinstance(receipt.get("committedAt"), str) or not receipt["committedAt"].strip()):
        raise ValueError("stage receipt does not match this run and cue")
    return receipt


def receipt_digest(receipt):
    """Hash receipt content independently of JSON key order and wire whitespace."""
    canonical = json.dumps(receipt, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def send_cue(run_id, base_url, step_index):
    run_id = validate_run_id(run_id)
    base_url = loopback_url(base_url)
    _validate_step_index(step_index)
    token = os.environ.get("CUEPILOT_BRIDGE_TOKEN", "")
    if not token or "\r" in token or "\n" in token:
        raise ValueError("CUEPILOT_BRIDGE_TOKEN is unavailable")
    operation_id = request_id(run_id, step_index)
    payload = {"runId": run_id, "stepIndex": step_index, "requestId": operation_id}
    request = urllib.request.Request(
        base_url + "/api/v1/tools/stage/cue",
        data=json.dumps(payload).encode("utf-8"), method="POST",
        headers={"Content-Type": "application/json", "Authorization": "Bearer " + token},
    )
    # No proxy inheritance or redirects: a server-only credential stays on loopback.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    try:
        with opener.open(request, timeout=12) as response:
            raw = response.read(65537)
            if len(raw) > 65536:
                raise ValueError("stage receipt exceeds the size limit")
    except urllib.error.HTTPError as error:
        error.close()
        raise ValueError("stage API rejected cue (HTTP %s)" % error.code) from None
    except (urllib.error.URLError, TimeoutError, OSError):
        raise ValueError("stage API is unreachable") from None
    try:
        receipt = json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        raise ValueError("stage API did not return a JSON receipt") from None
    validate_receipt(receipt, run_id, step_index)
    return {
        "ok": True, "runId": run_id, "stepIndex": step_index,
        "requestId": operation_id, "receiptSha256": hashlib.sha256(raw).hexdigest(),
        "receiptCanonicalSha256": receipt_digest(receipt), "receiptId": receipt["id"],
        "stageRevision": receipt["stageRevision"], "scene": receipt["scene"],
        "committedAt": receipt["committedAt"],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--step-index", type=int, required=True, choices=(0, 1, 2))
    args = parser.parse_args()
    try:
        receipt = send_cue(args.run_id, args.base_url, args.step_index)
    except ValueError as error:
        print(str(error), file=sys.stderr)
        return 1
    print(json.dumps(receipt, separators=(",", ":")))
    # A visible rehearsal cue lasts 1.5 seconds. This authored display dwell is
    # deliberately identical during record/replay and is not an AI speed metric.
    if args.step_index < 2:
        time.sleep(1.5)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

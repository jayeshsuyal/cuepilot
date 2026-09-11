"""Verify fresh Cognee Cloud ingestion using only the synthetic sample file.

Uses cognee 1.5.4's public serve/remember/recall/disconnect API. Running this
script consumes the Cloud workspace's processing credits and keeps one uniquely
named verification dataset. It never deletes or resets existing datasets.
"""
from __future__ import annotations

import asyncio
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timezone
import hashlib
from importlib.metadata import version
import io
import json
import os
from pathlib import Path
import re
import sys
import time
from urllib.parse import urlsplit
from uuid import uuid4

from dotenv import load_dotenv

HERE = Path(__file__).resolve().parent
WORKSPACE = HERE.parents[1]


class VerificationFailure(Exception):
    """Contains only a fixed, non-sensitive diagnostic code."""


def normalize(text: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", text.casefold()))


def error_code(error: Exception) -> str:
    if isinstance(error, VerificationFailure):
        return str(error)
    # Inspect an upstream exception only to classify it. Never emit its text:
    # SDK HTTP errors can include raw server response bodies.
    message = str(error).casefold()
    if "402" in message or "insufficient credit" in message:
        return "cloud_credits_required"
    if "401" in message or "403" in message or "api key was rejected" in message:
        return "cloud_authentication_failed"
    if "llm" in message or "embedding" in message:
        return "cloud_model_provider_error"
    if isinstance(error, (TimeoutError, asyncio.TimeoutError)):
        return "cloud_request_timed_out"
    return "cloud_operation_failed"


async def verify(receipt: dict) -> None:
    load_dotenv(WORKSPACE / ".env", override=False)
    service_url = os.getenv("COGNEE_SERVICE_URL", "").strip()
    api_key = os.getenv("COGNEE_API_KEY", "").strip()
    if not service_url or not api_key:
        raise VerificationFailure("cloud_url_and_key_required")
    parsed = urlsplit(service_url)
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username
            or parsed.password or parsed.query or parsed.fragment):
        raise VerificationFailure("cloud_url_must_be_a_clean_https_base_url")

    sample = (HERE / "sample.txt").read_text(encoding="utf-8")
    lines = [line.strip() for line in sample.splitlines() if line.strip()]
    if len(lines) < 4 or not lines[0].startswith("This is synthetic setup data"):
        raise VerificationFailure("synthetic_sample_format_changed")
    required_statements = lines[1:]
    receipt["sample_sha256"] = hashlib.sha256(sample.encode()).hexdigest()
    receipt["sample_bytes"] = len(sample.encode())
    receipt["expected_statement_count"] = len(required_statements)
    receipt["sdk_version"] = version("cognee")

    # Avoid persisting raw SDK/server diagnostics or displaying account output.
    os.environ["TELEMETRY_DISABLED"] = "true"
    os.environ["COGNEE_LOG_FILE"] = "false"
    os.environ["LOG_LEVEL"] = "CRITICAL"
    os.environ["DATA_ROOT_DIRECTORY"] = str(HERE / ".data_storage")
    os.environ["SYSTEM_ROOT_DIRECTORY"] = str(HERE / ".cognee_system")
    os.environ["COGNEE_LOGS_DIR"] = str(HERE / "logs")

    with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
        import cognee
        from cognee.modules.search.types import SearchType

        connected = False
        try:
            receipt["phase"] = "connect"
            await cognee.serve(url=service_url, api_key=api_key)
            connected = True
            receipt["phase"] = "ingest"
            started = time.monotonic()
            ingestion = await cognee.remember(
                sample,
                dataset_name=receipt["dataset_name"],
                run_in_background=False,
            )
            receipt["ingest_seconds"] = round(time.monotonic() - started, 3)
            if not isinstance(ingestion, dict) or ingestion.get("status") != "completed":
                raise VerificationFailure("cloud_ingest_not_confirmed_completed")
            receipt["ingest_completed"] = True

            receipt["phase"] = "recall"
            started = time.monotonic()
            answers = await cognee.recall(
                query_text="ticket resolution",
                query_type=SearchType.CHUNKS_LEXICAL,
                datasets=[receipt["dataset_name"]],
                top_k=3,
            )
            receipt["recall_seconds"] = round(time.monotonic() - started, 3)
            if not isinstance(answers, list):
                raise VerificationFailure("cloud_recall_response_shape_changed")
            # Match stored graph text only, never echoed query/account metadata.
            texts = [
                answer["text"] for answer in answers
                if isinstance(answer, dict) and answer.get("source") == "graph"
                and isinstance(answer.get("text"), str)
            ]
            recalled = normalize(" ".join(texts))
            matches = [normalize(statement) in recalled for statement in required_statements]
            receipt["graph_result_count"] = len(texts)
            receipt["matched_statement_count"] = sum(matches)
            if not texts or not all(matches):
                raise VerificationFailure("cloud_recall_did_not_verify_all_sample_statements")
            receipt["recall_content_verified"] = True
            receipt["phase"] = "complete"
        finally:
            if connected:
                await cognee.disconnect()


def main() -> int:
    started = time.monotonic()
    stamp = datetime.now(timezone.utc)
    receipt = {
        "verified_at_utc": stamp.isoformat(),
        "dataset_name": f"hackathon_setup_cloud_{stamp:%Y%m%dT%H%M%S}_{uuid4().hex[:8]}",
        "sample_file": "sample.txt",
        "retrieval_strategy": "CHUNKS_LEXICAL",
        "status": "failed",
        "phase": "configuration",
    }
    try:
        asyncio.run(verify(receipt))
        receipt["status"] = "passed"
    except Exception as error:
        receipt["error_code"] = error_code(error)
    receipt["elapsed_seconds"] = round(time.monotonic() - started, 3)
    receipts = HERE / "cloud-receipts"
    receipts.mkdir(exist_ok=True)
    path = receipts / f"{receipt['dataset_name']}.json"
    path.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    os.chmod(path, 0o600)
    print(f"Cognee Cloud {receipt['status'].upper()}; receipt: {path}")
    if receipt["status"] != "passed":
        print(f"Reason: {receipt['error_code']}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

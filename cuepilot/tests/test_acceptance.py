"""Offline regression checks for acceptance provenance, with no stage effects."""
import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from cuepilot.acceptance import CheckError, Runner, SCENES


def run_fixture(speaker, operation, learned_from="acceptance-maya"):
    run_id = "acceptance-" + speaker
    receipts = [{"ok": True, "id": f"{run_id}-{i}", "runId": run_id, "stepIndex": i,
                 "scene": scene, "stageRevision": i + (1 if speaker == "maya" else 4),
                 "committedAt": f"2026-09-11T20:30:0{i}+00:00"} for i, scene in enumerate(SCENES)]
    captures = []
    for i, receipt in enumerate(receipts):
        captures.append({"ok": True, "runId": run_id, "stepIndex": i,
                         "requestId": "rote-" + hashlib.sha256(run_id.encode()).hexdigest()[:32] + "-" + str(i),
                         "receiptId": receipt["id"], "scene": receipt["scene"], "stageRevision": receipt["stageRevision"],
                         "committedAt": receipt["committedAt"], "receiptSha256": "a" * 64,
                         "receiptCanonicalSha256": hashlib.sha256(json.dumps(receipt, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
                         "roteResponse": "@" + str(i + 1)})
    plan = {"recipeId": "speaker-segment-v1", "recipeVersion": 1,
            "cues": [{"index": i, "scene": s} for i, s in enumerate(SCENES)]}
    evidence = {"learnedFromRunId": learned_from, "procedure": {"id": "cuepilot-test", "sha256": "b" * 64},
                "package": "cuepilot/plays/learned/cuepilot-test", "receipts": copy.deepcopy(receipts), "capturedReceipts": captures,
                "identity": {**plan, "noteSha256": hashlib.sha256(b"Synthetic acceptance note.").hexdigest()},
                "executionAttempted": True, "reconciliationRequired": False, "replayExecuted": operation == "replay",
                "source": "rote-workspace-export-after-successful-cues"}
    if operation == "replay":
        evidence.update(runId=run_id, roteRunId="run_mock_replay", newInput=True, newSpeaker=True)
    result = {"provider": "rote", "status": "verified", "operation": operation, "evidence": evidence, "reason": None}
    return {"id": run_id, "speakerId": speaker, "status": "completed", "receipts": receipts,
            "plan": plan, "notes": "Synthetic acceptance note.", "traces": [result],
            "operations": {"execute": {"status": "verified", "result": copy.deepcopy(result)}}}


def change_evidence(run, callback):
    callback(run["traces"][0]["evidence"])
    run["operations"]["execute"]["result"] = copy.deepcopy(run["traces"][0])


class AcceptanceEvidenceTests(unittest.TestCase):
    def learned_runner(self, mode="rote"):
        runner = Runner(mode, 600, {})
        runner.verify_rote_learning_chain(run_fixture("maya", "learn"), {"speaker": "maya"})
        return runner

    def test_fresh_learning_and_same_package_new_speaker_replay_pass_both_modes(self):
        for mode in ("rote", "live"):
            with self.subTest(mode=mode):
                runner = self.learned_runner(mode)
                case = {"speaker": "ravi"}
                runner.verify_rote_learning_chain(run_fixture("ravi", "replay"), case)
                self.assertTrue(case["roteLearningAcceptance"]["canonicalReceiptsMatched"])
                self.assertEqual(case["roteLearningAcceptance"]["learnedFromRunId"], "acceptance-maya")

    def test_two_replays_from_old_package_cannot_initialize_learning(self):
        for mode in ("rote", "live"):
            runner = Runner(mode, 600, {})
            with self.assertRaises(CheckError) as error:
                runner.verify_rote_learning_chain(run_fixture("maya", "replay", "unrelated-old-run"), {"speaker": "maya"})
            self.assertTrue(error.exception.blocked)
            self.assertIn("explicit_active_pointer_archive", error.exception.code)
            self.assertIsNone(runner.learned)

    def test_maya_learning_must_be_sourced_from_current_maya(self):
        with self.assertRaisesRegex(CheckError, "maya_learning_source_mismatch"):
            Runner("rote", 600, {}).verify_rote_learning_chain(run_fixture("maya", "learn", "other-run"), {"speaker": "maya"})

    def test_replay_rejects_wrong_source_package_hash_or_input_claim(self):
        changes = {
            "source": lambda e: e.update(learnedFromRunId="other-maya"),
            "new_input": lambda e: e.update(newInput=False),
            "new_speaker": lambda e: e.update(newSpeaker=False),
            "replay": lambda e: e.update(replayExecuted=False),
            "wrong_run": lambda e: e.update(runId="acceptance-maya"),
            "package": lambda e: (e.update(package="cuepilot/plays/learned/cuepilot-other"), e["procedure"].update(id="cuepilot-other")),
            "proof_hash": lambda e: e["procedure"].update(sha256="c" * 64),
        }
        for name, change in changes.items():
            with self.subTest(change=name):
                run = run_fixture("ravi", "replay")
                change_evidence(run, change)
                with self.assertRaises(CheckError):
                    self.learned_runner().verify_rote_learning_chain(run, {"speaker": "ravi"})

    def test_replay_rejects_capture_or_returned_receipt_mismatch(self):
        changes = {
            "returned": lambda e: e["receipts"][0].update(id="unrelated-receipt"),
            "capture_id": lambda e: e["capturedReceipts"][0].update(receiptId="unrelated-receipt"),
            "capture_hash": lambda e: e["capturedReceipts"][0].update(receiptCanonicalSha256="0" * 64),
            "capture_request": lambda e: e["capturedReceipts"][0].update(requestId="old-request"),
            "capture_revision": lambda e: e["capturedReceipts"][0].update(stageRevision=999),
        }
        for name, change in changes.items():
            with self.subTest(change=name):
                run = run_fixture("ravi", "replay")
                change_evidence(run, change)
                with self.assertRaises(CheckError):
                    self.learned_runner().verify_rote_learning_chain(run, {"speaker": "ravi"})

    def test_verified_trace_cannot_override_conflicting_execute_result(self):
        run = run_fixture("maya", "learn")
        run["operations"]["execute"]["result"]["status"] = "failed"
        with self.assertRaisesRegex(CheckError, "rote_trace_execution_result_mismatch"):
            Runner("rote", 600, {}).verify_rote_learning_chain(run, {"speaker": "maya"})

    def test_preflight_keeps_existing_pointer_unchanged(self):
        with tempfile.TemporaryDirectory() as directory, patch("cuepilot.acceptance.HERE", Path(directory)):
            pointer = Path(directory) / "plays/evidence/active.json"
            pointer.parent.mkdir(parents=True)
            pointer.write_text('{"package":"old-package"}')
            for mode in ("rote", "live"):
                with self.assertRaisesRegex(CheckError, "explicit_active_pointer_archive"):
                    Runner(mode, 600, {}).require_fresh_learning()
            Runner("practice", 600, {}).require_fresh_learning()
            self.assertEqual(pointer.read_text(), '{"package":"old-package"}')


class AcceptanceCaseRegressionTests(unittest.IsolatedAsyncioTestCase):
    async def test_case_cannot_pass_old_maya_replay_even_with_completed_cues(self):
        runner = Runner("rote", 600, {})
        run = run_fixture("maya", "replay", "old-recording")
        runner.prepare = AsyncMock(return_value=run["id"])
        runner.automated = AsyncMock()
        runner.snapshot = AsyncMock(return_value=(run, {"scene": "holding"}))
        runner.request = AsyncMock(return_value={"ok": True, "receipts": run["receipts"]})
        with self.assertRaises(CheckError):
            await runner.case("maya")
        self.assertEqual(runner.report["cases"][0]["status"], "blocked")
        self.assertIsNone(runner.learned)


if __name__ == "__main__":
    unittest.main()

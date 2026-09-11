"""Synthetic adapter regression checks; no Rote or shared stage calls."""

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from cuepilot.integrations import rote


BASE_URL = "http://127.0.0.1:18787"
SCENES = ("intro", "presentation", "holding")


def run_fixture(run_id="run-recorded", speaker="speaker-one", completed=False):
    return {
        "id": run_id, "speakerId": speaker, "executionMode": "practice",
        "notes": "Introduce the speaker, show the presentation, return to holding.",
        "status": "completed" if completed else "approved", "nextStep": 3 if completed else 0,
        "plan": {
            "recipeId": "speaker-segment-v1", "recipeVersion": 1,
            "speakerId": speaker, "showRevision": 1, "hash": "a" * 64,
            "cues": [{"index": index, "scene": scene} for index, scene in enumerate(SCENES)],
        },
        "receipts": [{
            "ok": True, "id": "receipt-%s-%s" % (run_id, index), "runId": run_id,
            "stepIndex": index, "scene": scene, "stageRevision": index + 1,
            "committedAt": "2026-01-01T00:00:0%sZ" % index,
        } for index, scene in enumerate(SCENES)] if completed else [],
    }


def captures_fixture(run):
    captures = []
    for receipt in run["receipts"]:
        wire = json.dumps(dict(reversed(list(receipt.items()))), indent=2).encode()
        captures.append({
            "ok": True, "runId": run["id"], "stepIndex": receipt["stepIndex"],
            "requestId": rote._transport.request_id(run["id"], receipt["stepIndex"]),
            "receiptSha256": hashlib.sha256(wire).hexdigest(),
            "receiptCanonicalSha256": rote._transport.receipt_digest(receipt),
            "receiptId": receipt["id"], "stageRevision": receipt["stageRevision"],
            "scene": receipt["scene"], "committedAt": receipt["committedAt"],
        })
    return captures


def live_run_fixture(*args, **kwargs):
    run = run_fixture(*args, **kwargs)
    run["executionMode"] = "live"
    run["plan"]["recipeId"] = "recipe-synthetic-memory"
    run["memoryProof"] = {
        "recipe_id": run["plan"]["recipeId"],
        "note_sha256": hashlib.sha256(run["notes"].encode()).hexdigest(),
        "graph_sha256": "c" * 64, "source_id": "source-synthetic-notes",
    }
    return run


def replay_fixture(captures):
    return {"status": "succeeded", "runId": "run_synthetic-replay", "steps": [{
        "status": "completed", "body": {
            "kind": "process.exec",
            "status": {"spawned": True, "timed_out": False, "exit": {"kind": "code", "code": 0}},
            "stdout": {"text": json.dumps(capture), "truncated": False},
        },
    } for capture in captures]}


def replay_output(evidence):
    return 'Untrusted presentation summary: success\nCUEPILOT_REPLAY_EVIDENCE ' + json.dumps(evidence) + '\n'


def export_fixture(helper, run_id="run-recorded", base_url=BASE_URL, parameterized=True):
    """Synthetic frontmatter in the installed exporter's observed YAML shape."""
    lines = ["@rote-frontmatter", "---", "name: synthetic-recording", "metadata:",
             "  flow_type: parallel", "  execution_model: steps_with_presentation", "parameters:"]
    for parameter in ("run_id", "base_url"):
        lines.extend(["- name: " + parameter, "  param_type: string", "  required: true",
                      "  default: null", "  description: Play parameter", "  example: null", "  valid_values: null"])
    lines.append("steps:")
    names = ("capture_intro", "capture_deck", "capture_holding")
    for index, name in enumerate(names):
        argv = ["python3", str(helper), "--run-id", "$run_id" if parameterized else run_id,
                "--base-url", "$base_url" if parameterized else base_url, "--step-index", str(index)]
        lines.extend(["  " + name + ":", "    type: process.exec", "    argv:"])
        lines.extend("    - " + json.dumps(argument) for argument in argv)
        if index:
            lines.append("    depends_on:")
            lines.extend("    - " + previous for previous in names[:index])
    lines.append("---")
    return "#!/usr/bin/env -S rote play run\n/**\n" + "\n".join(" * " + line for line in lines) + "\n */\n// Synthetic presentation fixture.\n"


class RunAndReceiptTests(unittest.TestCase):
    def test_live_identity_binds_memory_provenance_and_reuses_it_for_new_speaker(self):
        learned = live_run_fixture(completed=True)
        fresh = live_run_fixture("run-fresh", "speaker-two")
        identity = rote._identity(learned)
        self.assertEqual(identity["memoryProof"], learned["memoryProof"])
        self.assertEqual(identity, rote._fresh_run(fresh))
        for field, value in (("graph_sha256", "d" * 64), ("source_id", "different-source")):
            with self.subTest(field=field):
                changed = deepcopy(fresh)
                changed["memoryProof"][field] = value
                self.assertNotEqual(identity, rote._identity(changed))

    def test_live_identity_rejects_missing_stale_or_mismatched_memory_proof(self):
        valid = live_run_fixture()
        cases = []
        missing = deepcopy(valid)
        del missing["memoryProof"]
        cases.append(missing)
        stale_notes = deepcopy(valid)
        stale_notes["notes"] += " Changed rule without new provenance."
        cases.append(stale_notes)
        for field in valid["memoryProof"]:
            missing_field = deepcopy(valid)
            del missing_field["memoryProof"][field]
            cases.append(missing_field)
        for field, value in (("recipe_id", "another-recipe"), ("note_sha256", "d" * 64),
                             ("graph_sha256", "not-a-hash"), ("source_id", "")):
            invalid = deepcopy(valid)
            invalid["memoryProof"][field] = value
            cases.append(invalid)
        for run in cases:
            with self.subTest(run=run), self.assertRaisesRegex(ValueError, "incompatible_current_recipe"):
                rote._identity(run)

    def test_identity_allows_new_speaker_approval_and_show_revision(self):
        learned = run_fixture(completed=True)
        fresh = run_fixture("run-fresh", "speaker-two")
        fresh["plan"].update(hash="b" * 64, showRevision=9)
        self.assertEqual(rote._identity(learned), rote._fresh_run(fresh))
        fresh["notes"] += " Skip the introduction."
        self.assertNotEqual(rote._identity(learned), rote._fresh_run(fresh))

    def test_run_must_be_approved_empty_and_at_integer_step_zero(self):
        cases = [("status", status) for status in ("draft", "planned", "completed", "blocked", "executing")]
        cases += [("nextStep", value) for value in (False, 0.0, "0", 1, None)]
        cases += [("receipts", value) for value in (None, (), [{}])]
        for field, value in cases:
            with self.subTest(field=field, value=value):
                run = run_fixture()
                run[field] = value
                with self.assertRaisesRegex(ValueError, "fresh_approved_run_required"):
                    rote._fresh_run(run)

    def test_identity_rejects_incompatible_recipe_or_cue_template(self):
        changes = [
            {"recipeId": "speaker-segment-v2"}, {"recipeVersion": 2}, {"recipeVersion": True},
            {"speakerId": "different-speaker"}, {"showRevision": True}, {"hash": "invalid"},
            {"cues": [{"index": 0, "scene": "holding"}]},
            {"cues": [{"index": False, "scene": "intro"}, {"index": 1, "scene": "presentation"}, {"index": 2, "scene": "holding"}]},
        ]
        for change in changes:
            with self.subTest(change=change):
                run = run_fixture()
                run["plan"].update(change)
                with self.assertRaisesRegex(ValueError, "incompatible_current_recipe"):
                    rote._identity(run)

    def test_captured_receipts_match_canonical_content_across_wire_formatting(self):
        run = run_fixture(completed=True)
        captures = captures_fixture(run)
        self.assertNotEqual(captures[0]["receiptSha256"], captures[0]["receiptCanonicalSha256"])
        receipts = rote._confirmed_receipts(run, run["id"], run_fixture())
        self.assertIsNone(rote._match_captured(captures, receipts, run["id"]))

    def test_captured_receipts_reject_any_changed_identity_or_digest(self):
        run = run_fixture(completed=True)
        changes = {
            "ok": False, "runId": "run-other", "stepIndex": False, "requestId": "wrong-request",
            "receiptCanonicalSha256": "b" * 64, "receiptSha256": "not-a-hash", "receiptId": "wrong-id",
            "stageRevision": 9, "scene": "holding", "committedAt": "different-time",
        }
        for field, value in changes.items():
            with self.subTest(field=field):
                captured = captures_fixture(run)
                captured[0][field] = value
                with self.assertRaisesRegex(ValueError, "rote_capture_receipt_mismatch"):
                    rote._match_captured(captured, run["receipts"], run["id"])
        for captured in ([], captures_fixture(run)[:2], list(reversed(captures_fixture(run)))):
            with self.subTest(captured=captured), self.assertRaisesRegex(ValueError, "rote_capture_receipt_mismatch"):
                rote._match_captured(captured, run["receipts"], run["id"])

    def test_captured_revision_rejects_boolean_equal_to_integer_one(self):
        run = run_fixture(completed=True)
        captured = captures_fixture(run)
        captured[0]["stageRevision"] = True
        with self.assertRaisesRegex(ValueError, "rote_capture_receipt_mismatch"):
            rote._match_captured(captured, run["receipts"], run["id"])

    def test_completed_receipts_reject_duplicates_revision_gaps_or_changed_plan(self):
        before = run_fixture()
        variants = []
        duplicate = run_fixture(completed=True)
        duplicate["receipts"][1]["id"] = duplicate["receipts"][0]["id"]
        variants.append(duplicate)
        gap = run_fixture(completed=True)
        gap["receipts"][1]["stageRevision"] = 7
        variants.append(gap)
        changed = run_fixture(completed=True)
        changed["plan"]["hash"] = "b" * 64
        variants.append(changed)
        for run in variants:
            with self.subTest(run=run), self.assertRaisesRegex(ValueError, "stage_receipts_mismatch"):
                rote._confirmed_receipts(run, run["id"], before)


class ReplayEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.captured = captures_fixture(run_fixture(completed=True))
        self.evidence = replay_fixture(self.captured)

    def test_only_three_fresh_completed_processes_provide_replay_captures(self):
        self.assertEqual(rote._replay_captures(replay_output(self.evidence)), ("run_synthetic-replay", self.captured))

    def test_success_summary_cannot_hide_restored_skipped_or_failed_steps(self):
        for status in ("restored", "partial", "skipped", "failed", "blocked"):
            with self.subTest(status=status):
                evidence = deepcopy(self.evidence)
                evidence["steps"][1]["status"] = status
                with self.assertRaisesRegex(ValueError, "rote_replay_evidence_incomplete"):
                    rote._replay_captures(replay_output(evidence))

    def test_success_summary_cannot_hide_failed_timed_out_or_truncated_child(self):
        changes = [
            ("kind", "other.operation"),
            ("status", {"spawned": False, "timed_out": False, "exit": {"kind": "code", "code": 0}}),
            ("status", {"spawned": True, "timed_out": True, "exit": {"kind": "code", "code": 0}}),
            ("status", {"spawned": True, "timed_out": False, "exit": {"kind": "code", "code": 1}}),
            ("status", {"spawned": True, "timed_out": False, "exit": {"kind": "code", "code": False}}),
            ("status", {"spawned": True, "timed_out": False, "exit": {"kind": "signal", "signal": 9}}),
            ("stdout", {"text": json.dumps(self.captured[0]), "truncated": True}),
            ("stdout", {"text": "not JSON", "truncated": False}),
        ]
        for field, value in changes:
            with self.subTest(field=field, value=value):
                evidence = deepcopy(self.evidence)
                evidence["steps"][0]["body"][field] = value
                with self.assertRaisesRegex(ValueError, "rote_replay_evidence_incomplete"):
                    rote._replay_captures(replay_output(evidence))

    def test_missing_duplicate_or_malformed_evidence_fails_closed(self):
        cases = ["Success", replay_output(self.evidence) * 2, "CUEPILOT_REPLAY_EVIDENCE []",
                 "CUEPILOT_REPLAY_EVIDENCE invalid JSON"]
        malformed = deepcopy(self.evidence)
        malformed["steps"][0]["body"]["stdout"] = []
        cases.append(replay_output(malformed))
        for out in cases:
            with self.subTest(out=out), self.assertRaisesRegex(ValueError, "rote_replay_evidence_incomplete"):
                rote._replay_captures(out)


class ExportTests(unittest.TestCase):
    def setUp(self):
        self.helper = Path("/synthetic/resources/cue.py")
        self.raw = export_fixture(self.helper)

    def generalize(self, raw):
        return rote._generalize_export(raw, "run-recorded", BASE_URL, self.helper)

    def test_literal_and_parameter_exports_preserve_names_and_make_order_explicit(self):
        expected = self.generalize(self.raw)
        self.assertEqual(self.generalize(export_fixture(self.helper, parameterized=False)), expected)
        self.assertIn("flow_type: sequential", expected)
        self.assertNotIn(str(self.helper), expected)
        self.assertNotIn(BASE_URL, expected)
        self.assertIn('depends_on: [capture_intro]', expected)
        self.assertIn('depends_on: [capture_deck]', expected)
        for index, name in enumerate(("capture_intro", "capture_deck", "capture_holding")):
            self.assertIn('ctx.step(stepName("' + name + '"))', expected)
            argv = ["python3", "@resource{cue.py}", "--run-id", "$run_id", "--base-url", "$base_url", "--step-index", str(index)]
            self.assertIn("argv: " + json.dumps(argv), expected)
        self.assertEqual(expected.count("CUEPILOT_REPLAY_EVIDENCE "), 1)

    def test_parameter_contract_rejects_wrong_order_defaults_types_and_extra_fields(self):
        changes = [
            self.raw.replace("name: run_id", "name: wrong_parameter", 1),
            self.raw.replace("name: base_url", "name: run_id", 1),
            self.raw.replace("name: run_id", "name: swap", 1).replace("name: base_url", "name: run_id", 1).replace("name: swap", "name: base_url", 1),
            self.raw.replace("default: null", "default: recorded-run", 1),
            self.raw.replace("required: true", "required: false", 1),
            self.raw.replace("param_type: string", "param_type: number", 1),
            self.raw.replace("valid_values: null", "valid_values: null\n *   unexpected: true", 1),
        ]
        for raw in changes:
            with self.subTest(raw=raw), self.assertRaisesRegex(ValueError, "rote_export_parameters_mismatch"):
                self.generalize(raw)

    def test_command_mismatch_extra_steps_and_unexpected_execution_fields_rejected(self):
        changes = [
            self.raw.replace('"--step-index"\n *     - "1"', '"--step-index"\n *     - "2"', 1),
            self.raw.replace('"python3"', '"sh"', 1),
            self.raw.replace('"$run_id"', '"different-run"', 1),
            self.raw.replace("    type: process.exec", "    type: process.exec\n *     env: {}", 1),
            self.raw.replace("  capture_holding:", "  capture_deck:", 1),
            self.raw.replace(" * ---\n */", " *   unexpected_step:\n *     type: process.exec\n * ---\n */", 1),
        ]
        for raw in changes:
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                self.generalize(raw)


class PackageIntegrityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.plays = self.root / "plays"
        self.authored = self.plays / "stage-sequence"
        (self.authored / "resources").mkdir(parents=True)
        (self.authored / "resources/cue.py").write_text("# Synthetic pinned transport fixture.\n")
        (self.authored / "deps.toml").write_text("schema_version = 1\n")
        self.active = self.plays / "evidence/active.json"
        self.active.parent.mkdir()
        self.package = self.plays / "learned/cuepilot-synthetic"
        (self.package / "resources").mkdir(parents=True)
        self.run = run_fixture(completed=True)
        helper = self.authored / "resources/cue.py"
        raw = export_fixture(helper)
        (self.package / "resources/recorded-export.txt").write_text(raw)
        (self.package / "resources/cue.py").write_bytes(helper.read_bytes())
        (self.package / "deps.toml").write_bytes((self.authored / "deps.toml").read_bytes())
        (self.package / "main.ts").write_text(rote._generalize_export(raw, self.run["id"], BASE_URL, helper))
        self.proof = {
            "proofVersion": rote.PROOF_VERSION, "source": "rote-workspace-export-after-successful-cues",
            "workspace": self.package.name, "learnedFromRunId": self.run["id"], "learnedRun": self.run,
            "identity": rote._identity(self.run), "baseUrl": BASE_URL, "capturedHelper": str(helper),
            "receipts": captures_fixture(self.run), "files": {},
        }
        self.repin()
        for name, value in (("ROOT", self.root), ("PLAYS", self.plays), ("AUTHORED", self.authored), ("ACTIVE", self.active)):
            replacement = patch.object(rote, name, value)
            replacement.start()
            self.addCleanup(replacement.stop)

    def repin(self, refresh_files=True):
        if refresh_files:
            self.proof["files"] = {rel: rote._sha(self.package / rel) for rel in rote.PACKAGE_FILES}
        path = self.package / "resources/proof.json"
        path.write_text(json.dumps(self.proof))
        self.active.write_text(json.dumps({"package": self.package.name, "proofSha256": rote._sha(path)}))

    def test_exact_synthetic_package_is_accepted(self):
        package, proof = rote._active_package()
        self.assertEqual(package, self.package)
        self.assertEqual(proof, self.proof)

    def test_live_package_requires_the_captured_memory_proof(self):
        self.proof["learnedRun"] = live_run_fixture(completed=True)
        self.proof["identity"] = rote._identity(self.proof["learnedRun"])
        self.repin()
        self.assertEqual(rote._active_package()[1]["identity"]["memoryProof"], self.proof["learnedRun"]["memoryProof"])
        del self.proof["learnedRun"]["memoryProof"]
        self.repin()
        with self.assertRaisesRegex(ValueError, "learned_package_invalid"):
            rote._active_package()

    def test_old_schema_requires_upgrade(self):
        self.proof["proofVersion"] = 1
        self.repin()
        with self.assertRaisesRegex(ValueError, "learned_package_upgrade_required"):
            rote._active_package()

    def test_missing_active_pointer_requires_learning(self):
        self.active.unlink()
        with self.assertRaisesRegex(ValueError, "learned_play_required"):
            rote._active_package()

    def test_changed_proof_hash_is_rejected(self):
        path = self.package / "resources/proof.json"
        path.write_text(path.read_text() + " ")
        with self.assertRaisesRegex(ValueError, "learned_package_invalid"):
            rote._active_package()

    def test_extra_file_and_changed_package_file_are_rejected(self):
        extra = self.package / "unexpected.ts"
        extra.write_text("// Unexpected executable file.\n")
        with self.assertRaisesRegex(ValueError, "learned_package_invalid"):
            rote._active_package()
        extra.unlink()
        path = self.package / "main.ts"
        path.write_text(path.read_text() + "// Altered content.\n")
        with self.assertRaisesRegex(ValueError, "learned_package_invalid"):
            rote._active_package()

    def test_rehashed_arbitrary_main_is_rejected_by_export_rederivation(self):
        path = self.package / "main.ts"
        path.write_text(path.read_text() + "// Undeclared transformation.\n")
        self.repin()
        with self.assertRaisesRegex(ValueError, "learned_package_invalid"):
            rote._active_package()

    def test_transport_source_change_requires_package_upgrade(self):
        (self.authored / "resources/cue.py").write_text("# New transport version.\n")
        with self.assertRaisesRegex(ValueError, "learned_package_upgrade_required"):
            rote._active_package()

    def test_symlinked_package_content_is_rejected_even_with_matching_bytes(self):
        helper = self.package / "resources/cue.py"
        helper.unlink()
        helper.symlink_to(self.authored / "resources/cue.py")
        with self.assertRaisesRegex(ValueError, "learned_package_invalid"):
            rote._active_package()

    def test_nonobject_proof_fails_closed(self):
        self.proof = []
        self.repin(refresh_files=False)
        with self.assertRaisesRegex(ValueError, "learned_package_invalid"):
            rote._active_package()


class ReplayGateTests(unittest.IsolatedAsyncioTestCase):
    async def test_changed_live_graph_or_source_blocks_before_cli(self):
        learned = live_run_fixture(completed=True)
        proof = {"learnedFromRunId": learned["id"], "learnedRun": learned, "identity": rote._identity(learned)}
        with patch.object(rote, "_active_package", return_value=(rote.PLAYS / "learned/cuepilot-synthetic", proof)), \
                patch.object(rote, "_procedure", return_value={"id": "cuepilot-synthetic", "sha256": "a" * 64}), \
                patch.object(rote, "_run_status") as status, patch.object(rote, "_cli", new_callable=AsyncMock) as cli:
            for field, value in (("graph_sha256", "d" * 64), ("source_id", "changed-source")):
                with self.subTest(field=field):
                    before = live_run_fixture("run-fresh", "speaker-two")
                    before["memoryProof"][field] = value
                    status.return_value = before
                    state = {"executionAttempted": False}
                    with self.assertRaisesRegex(ValueError, "learned_recipe_mismatch"):
                        await rote._replay(before["id"], BASE_URL, {}, rote.PLAYS / "evidence/replay-synthetic", state)
                    self.assertFalse(state["executionAttempted"])
            cli.assert_not_awaited()

    async def test_replay_gates_block_before_cli_for_reused_changed_or_unapproved_input(self):
        learned = run_fixture(completed=True)
        proof = {"learnedFromRunId": learned["id"], "learnedRun": learned, "identity": rote._identity(learned)}
        changed_notes = run_fixture("run-fresh", "speaker-two")
        changed_notes["notes"] += " Changed rule."
        unapproved = run_fixture("run-fresh", "speaker-two")
        unapproved["status"] = "planned"
        changed_template = run_fixture("run-fresh", "speaker-two")
        changed_template["plan"]["recipeVersion"] = 2
        cases = [
            (run_fixture(), "fresh_approved_run_required"),
            (run_fixture("run-fresh", "speaker-two", completed=True), "fresh_approved_run_required"),
            (changed_notes, "learned_recipe_mismatch"),
            (changed_template, "incompatible_current_recipe"),
            (unapproved, "fresh_approved_run_required"),
        ]
        with patch.object(rote, "_active_package", return_value=(rote.PLAYS / "learned/cuepilot-synthetic", proof)), \
                patch.object(rote, "_procedure", return_value={"id": "cuepilot-synthetic", "sha256": "a" * 64}), \
                patch.object(rote, "_run_status") as status, patch.object(rote, "_cli", new_callable=AsyncMock) as cli:
            for before, reason in cases:
                with self.subTest(reason=reason):
                    status.return_value = before
                    state = {"executionAttempted": False}
                    with self.assertRaisesRegex(ValueError, reason):
                        await rote._replay(before["id"], BASE_URL, {}, rote.PLAYS / "evidence/replay-synthetic", state)
                    self.assertFalse(state["executionAttempted"])
            cli.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()

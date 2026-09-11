"""Adversarial release gates; provider stubs are never live sponsor evidence.

These tests intentionally fail while the corresponding safety gaps are open.
Every database is temporary. No listener, sponsor request, or real Rote process
is started. See docs/backend-acceptance.md for the separate live evidence gates.
"""
import asyncio
import hashlib
from pathlib import Path
import secrets
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

import httpx
from fastapi.testclient import TestClient

from cuepilot.api import DomainError, create_app


def memory_result(note, provider="cognee"):
    """Controlled adapter-boundary data, with explicit non-live provenance."""
    return {
        "status": "verified",
        "supported_template": "speaker-segment-v1",
        "note_sha256": hashlib.sha256(note.encode()).hexdigest(),
        "source_id": "demo",
        "graph_sha256": hashlib.sha256(b"acceptance-fixture-graph").hexdigest(),
        "recipe_id": "acceptance-fixture-recipe",
        "records": [{"provider": provider, "status": "verified",
                     "operation": "acceptance-stub", "reason": None,
                     "evidence": {"testOnly": True}}],
    }


def validation_result(revision):
    return {"status": "verified", "show_revision": revision, "ready": True,
            "records": [{"provider": "hotdata", "status": "verified",
                         "operation": "acceptance-stub", "reason": None,
                         "evidence": {"testOnly": True}}]}


class AcceptanceFixture:
    def setup_fixture(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db_path = Path(self.temp.name) / "acceptance.sqlite3"
        self.operator_token = secrets.token_urlsafe(32)
        self.bridge_token = secrets.token_urlsafe(32)
        self.operator = {"Authorization": "Bearer " + self.operator_token}
        self.bridge = {"Authorization": "Bearer " + self.bridge_token}
        # Never read or mutate the demo's private environment during acceptance.
        with patch("cuepilot.api.load_dotenv"):
            self.app = create_app(self.db_path, operator_token=self.operator_token,
                                  bridge_token=self.bridge_token)
        self.store = self.app.state.store

    def staged_live(self, speaker="maya", approve=False, finish_prepare=True):
        # Construct queued state without POST /runs launching real RocketRide.
        run = self.store.create_run(speaker, "Introduction, presentation, holding.", "live")
        self.store.claim_orchestration(run["id"], "prepare")
        for operation, result, proof_operation in (
            ("ingest-memory", memory_result(run["notes"]), "ingest-memory"),
            ("recall-recipe", memory_result(run["notes"], "hydradb"), "recall-recipe"),
            ("validate-show", validation_result(self.store.get_state("show")["revision"]), None),
        ):
            key, _ = self.store.claim_operation(run["id"], operation)
            self.store.add_trace(run["id"], result, proof_operation)
            self.store.finish_operation(run["id"], key, result)
        run = self.store.plan(run["id"])
        if finish_prepare:
            self.store.add_trace(run["id"], {"provider": "rocketride", "status": "verified",
                "operation": "prepare", "reason": None, "evidence": {"testOnly": True}})
            self.store.finish_orchestration(run["id"], "prepare", "verified")
        if approve:
            run = self.store.approve(run["id"], run["plan"]["hash"])
            key, _ = self.store.claim_operation(run["id"], "validate-show")
            result = validation_result(self.store.get_state("show")["revision"])
            self.store.add_trace(run["id"], result)
            self.store.finish_operation(run["id"], key, result)
        return run


class SecurityAcceptanceTests(AcceptanceFixture, unittest.TestCase):
    def setUp(self):
        self.setup_fixture()
        self.client = TestClient(self.app)
        self.addCleanup(self.client.close)

    def practice(self, speaker="maya", approve=True):
        response = self.client.post("/api/v1/runs", headers=self.operator,
                                    json={"speakerId": speaker, "executionMode": "practice"})
        self.assertEqual(response.status_code, 201, response.text)
        run = response.json()
        if approve:
            response = self.client.post(f"/api/v1/runs/{run['id']}/approve",
                                        headers=self.operator, json={"planHash": run["plan"]["hash"]})
            self.assertEqual(response.status_code, 200, response.text)
            run = response.json()
        return run

    def cue(self, run, step, request_id):
        return self.client.post("/api/v1/tools/stage/cue", headers=self.bridge,
                                json={"runId": run["id"], "stepIndex": step, "requestId": request_id})

    def tool(self, name, run):
        return self.client.post("/api/v1/tools/" + name, headers=self.bridge, json={"runId": run["id"]})

    def test_every_mutation_rejects_missing_and_wrong_role_token(self):
        run = self.practice(approve=False)
        routes = [
            ("POST", "/api/v1/runs", {"speakerId": "maya"}, self.bridge),
            ("POST", f"/api/v1/runs/{run['id']}/approve", {"planHash": run["plan"]["hash"]}, self.bridge),
            ("POST", f"/api/v1/runs/{run['id']}/advance", {"requestId": "unauthorized"}, self.bridge),
            ("POST", f"/api/v1/runs/{run['id']}/execute", {}, self.bridge),
            ("PATCH", "/api/v1/assets/slides-maya", {"status": "missing", "expectedRevision": 1}, self.bridge),
            ("PATCH", "/api/v1/speakers/maya", {"ready": False, "expectedRevision": 1}, self.bridge),
            ("POST", "/api/v1/tools/stage/cue", {"runId": run["id"], "stepIndex": 0, "requestId": "unauthorized"}, self.operator),
        ]
        routes.extend(("POST", "/api/v1/tools/" + name, {"runId": run["id"]}, self.operator)
                      for name in ("ingest-memory", "recall-recipe", "validate-show", "plan", "execute", "verify"))
        before = self.store.get_state("stage")
        for method, route, body, wrong_role in routes:
            for headers in ({}, wrong_role):
                with self.subTest(method=method, route=route, role="none" if not headers else "wrong"):
                    response = self.client.request(method, route, headers=headers, json=body)
                    self.assertEqual(response.status_code, 401, response.text)
                    self.assertNotIn(self.operator_token, response.text)
                    self.assertNotIn(self.bridge_token, response.text)
        self.assertEqual(self.store.get_state("stage"), before)
        self.assertEqual(self.store.get_run(run["id"])["status"], "needs_approval")

    def test_approval_from_another_run_cannot_authorize_cues(self):
        maya, ravi = self.practice(approve=False), self.practice("ravi", approve=False)
        response = self.client.post(f"/api/v1/runs/{ravi['id']}/approve", headers=self.operator,
                                    json={"planHash": maya["plan"]["hash"]})
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(self.cue(ravi, 0, "attempt").status_code, 409)
        self.assertEqual(self.store.get_state("stage")["revision"], 0)

    def test_practice_maya_then_ravi_and_missing_asset_requires_new_approval(self):
        receipt_ids = set()
        for speaker in ("maya", "ravi"):
            run = self.practice(speaker)
            self.assertEqual(run["plan"]["origin"], "fixture")
            self.assertTrue(all(t["status"] == "fixture" for t in run["traces"]))
            for index, scene in enumerate(("intro", "presentation", "holding")):
                response = self.cue(run, index, f"{speaker}-{index}")
                self.assertEqual(response.status_code, 200, response.text)
                receipt = response.json()
                self.assertEqual((receipt["runId"], receipt["stepIndex"], receipt["scene"]), (run["id"], index, scene))
                self.assertNotIn(receipt["id"], receipt_ids)
                receipt_ids.add(receipt["id"])
            self.assertTrue(self.tool("verify", run).json()["ok"])

        interrupted = self.practice("ravi")
        self.assertEqual(self.cue(interrupted, 0, "interrupt-intro").status_code, 200)
        response = self.client.patch("/api/v1/assets/slides-ravi", headers=self.operator,
                                     json={"status": "missing", "expectedRevision": 1})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.store.get_state("stage")["scene"], "holding")
        self.assertEqual(self.cue(interrupted, 1, "must-block").status_code, 409)
        self.assertFalse(self.tool("verify", interrupted).json()["ok"])
        response = self.client.patch("/api/v1/assets/slides-ravi", headers=self.operator,
                                     json={"status": "ready", "expectedRevision": 2})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.cue(interrupted, 1, "cannot-resume").status_code, 409)
        recovered = self.practice("ravi")
        self.assertNotEqual(recovered["plan"]["hash"], interrupted["plan"]["hash"])
        self.assertEqual(self.cue(recovered, 0, "new-approval").status_code, 200)

    def test_lost_response_retry_survives_restart_and_invalidation(self):
        run = self.practice()
        first = self.cue(run, 0, "lost-response").json()
        self.store.mutate_readiness("assets", "slides-maya", "status", "missing", 1)
        stage = self.store.get_state("stage")
        with patch("cuepilot.api.load_dotenv"):
            restarted = create_app(self.db_path, operator_token=self.operator_token, bridge_token=self.bridge_token)
        with TestClient(restarted) as client:
            response = client.post("/api/v1/tools/stage/cue", headers=self.bridge,
                                   json={"runId": run["id"], "stepIndex": 0, "requestId": "lost-response"})
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json(), first)
            self.assertEqual(client.get("/api/v1/stage").json(), stage)
            self.assertEqual(len(client.get(f"/api/v1/runs/{run['id']}").json()["receipts"]), 1)
        self.assertEqual(self.cue(run, 1, "lost-response").status_code, 409)

    def test_recalled_rule_for_other_note_invalidates_pending_live_plan(self):
        run = self.store.create_run("maya", "Introduction, presentation, holding.", "live")
        with patch("cuepilot.integrations.memory.ingest_note", AsyncMock(return_value=memory_result(run["notes"]))):
            response = self.tool("ingest-memory", run)
        self.assertEqual(response.status_code, 200, response.text)
        other_rule = memory_result("Different production rule: never show presentation.", "hydradb")
        with patch("cuepilot.integrations.memory.recall_recipe", AsyncMock(return_value=other_rule)):
            self.tool("recall-recipe", run)
        with patch("cuepilot.integrations.hotdata.validate_show", AsyncMock(return_value=validation_result(1))):
            self.tool("validate-show", run)
        response = self.tool("plan", run)
        self.assertGreaterEqual(response.status_code, 400,
                                "A verified record for a different note reused sticky supportedTemplate")
        self.assertEqual(self.store.get_run(run["id"])["receipts"], [])

    def test_bridge_cannot_recall_before_ingestion(self):
        run = self.store.create_run("maya", "Current rule", "live")
        recalled = AsyncMock(return_value=memory_result(run["notes"], "hydradb"))
        with patch("cuepilot.integrations.memory.recall_recipe", recalled):
            response = self.tool("recall-recipe", run)
        self.assertEqual(recalled.await_count, 0, "Out-of-order bridge call reached Hydra recall")
        self.assertGreaterEqual(response.status_code, 400)

    def test_bridge_cannot_ingest_over_an_approved_rule(self):
        run = self.staged_live(approve=True)
        ingest = AsyncMock(return_value=memory_result(run["notes"]))
        with patch("cuepilot.integrations.memory.ingest_note", ingest):
            response = self.tool("ingest-memory", run)
        self.assertEqual(ingest.await_count, 0, "Approved rule can still trigger a new ingest")
        self.assertTrue(response.status_code == 200 or 400 <= response.status_code < 500,
                        "A completed ingest may return cached evidence or reject the wrong phase")

    def test_live_approval_waits_for_verified_prepare_finalization(self):
        run = self.staged_live(finish_prepare=False)
        route = f"/api/v1/runs/{run['id']}/approve"
        body = {"planHash": run["plan"]["hash"]}
        before_stage = self.store.get_state("stage")
        response = self.client.post(route, headers=self.operator, json=body)
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(response.json()["detail"]["code"], "preparation_not_complete")
        self.assertEqual(self.store.get_run(run["id"])["status"], "needs_approval")
        self.assertEqual(self.store.get_state("stage"), before_stage)
        # An untracked live plan must not bypass mandatory sponsor preparation.
        with self.store.db() as db:
            current = self.store.read_run(db, run["id"])
            orchestration = current.pop("orchestration")
            self.store.save_run(db, current)
        self.assertEqual(self.client.post(route, headers=self.operator, json=body).status_code, 409)
        with self.store.db() as db:
            current = self.store.read_run(db, run["id"])
            current["orchestration"] = orchestration
            self.store.save_run(db, current)
        # A finished orchestration without its matching evidence is insufficient.
        self.store.finish_orchestration(run["id"], "prepare", "verified")
        self.assertEqual(self.client.post(route, headers=self.operator, json=body).status_code, 409)
        # Conversely, a trace cannot authorize approval before finalization ends.
        self.store.finish_orchestration(run["id"], "prepare", "running")
        self.store.add_trace(run["id"], {"provider": "rocketride", "status": "verified",
            "operation": "prepare", "reason": None, "evidence": {"testOnly": True}})
        self.assertEqual(self.client.post(route, headers=self.operator, json=body).status_code, 409)
        self.store.finish_orchestration(run["id"], "prepare", "verified")
        response = self.client.post(route, headers=self.operator, json=body)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["status"], "approved")

    def test_execute_cannot_overlap_prepare_even_for_a_prematurely_approved_run(self):
        run = self.staged_live(approve=True)
        # Model state admitted by the old approval endpoint while prepare was
        # still finishing, then exercise both public execution entry points.
        self.store.finish_orchestration(run["id"], "prepare", "running")
        subprocess = AsyncMock()
        replay = AsyncMock()
        with patch("cuepilot.api.asyncio.create_subprocess_exec", subprocess):
            response = self.client.post(f"/api/v1/runs/{run['id']}/execute", headers=self.operator)
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(response.json()["detail"]["code"], "preparation_not_complete")
        self.assertEqual(subprocess.await_count, 0)
        with patch("cuepilot.integrations.rote.replay", replay):
            response = self.tool("execute", run)
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(response.json()["detail"]["code"], "preparation_not_complete")
        self.assertEqual(replay.await_count, 0)
        current = self.store.get_run(run["id"])
        self.assertNotIn("execute", current["orchestration"])
        self.assertNotIn("execute", current["operations"])
        self.assertEqual(current["receipts"], [])

    def test_stale_show_blocks_before_starting_rote(self):
        run = self.staged_live(approve=True)
        self.store.mutate_readiness("assets", "slides-ravi", "status", "missing", 1)
        replay = AsyncMock(return_value={"provider": "rote", "status": "blocked", "operation": "replay",
                                       "evidence": {"testOnly": True}, "reason": "acceptance-stop"})
        with patch("cuepilot.integrations.rote.replay", replay):
            response = self.tool("execute", run)
        self.assertEqual(replay.await_count, 0, "Stale approved show reached Rote before validation")
        self.assertGreaterEqual(response.status_code, 400)

    def test_stage_owner_conflict_blocks_before_starting_rote(self):
        owner = self.practice("maya")
        self.assertEqual(self.cue(owner, 0, "owner-intro").status_code, 200)
        challenger = self.staged_live("ravi", approve=True)
        replay = AsyncMock(return_value={"provider": "rote", "status": "blocked", "operation": "replay",
                                       "evidence": {"testOnly": True}, "reason": "acceptance-stop"})
        with patch("cuepilot.integrations.rote.replay", replay):
            self.tool("execute", challenger)
        self.assertEqual(replay.await_count, 0, "Stage-busy challenger still started Rote")
        self.assertEqual(self.store.get_state("stage")["speakerId"], "maya")

    def test_bridge_cannot_report_live_completion_using_only_direct_stage_cues(self):
        run = self.staged_live(approve=True)
        responses = [self.cue(run, index, f"bypass-{index}") for index in range(3)]
        verified = self.tool("verify", run).json()
        self.assertFalse(verified["ok"],
                         "Direct bridge writes alone passed live verify without Rote or RocketRide evidence")
        self.assertTrue(any(response.status_code >= 400 for response in responses))

    def test_provider_success_without_receipts_never_means_completed(self):
        run = self.staged_live(approve=True)
        fake = {"provider": "rote", "status": "verified", "operation": "replay",
                "evidence": {"testOnly": True, "message": "All cues completed"}}
        with patch("cuepilot.integrations.rote.replay", AsyncMock(return_value=fake)):
            self.tool("execute", run)
        verified = self.tool("verify", run).json()
        self.assertFalse(verified["ok"])
        self.assertNotEqual(verified["status"], "completed")
        self.assertEqual(verified["receipts"], [])

    def test_reused_request_id_alias_cannot_accidentally_advance_operator(self):
        run = self.practice()
        self.assertEqual(self.cue(run, 0, "original").status_code, 200)
        alias = self.cue(run, 0, "retry-alias")
        self.assertEqual(alias.status_code, 200, alias.text)
        response = self.client.post(f"/api/v1/runs/{run['id']}/advance", headers=self.operator,
                                    json={"requestId": "retry-alias"})
        self.assertTrue(response.status_code >= 400 or response.json() == alias.json(),
                        "Acknowledged retry ID was forgotten and advanced the next cue")
        self.assertEqual(self.store.get_run(run["id"])["nextStep"], 1)

    def test_changed_production_rule_stops_an_active_live_procedure(self):
        run = self.staged_live(approve=True)
        self.store.claim_operation(run["id"], "execute")
        self.assertEqual(self.cue(run, 0, "current-rule-intro").status_code, 200)
        changed = self.store.create_run("ravi", "Changed rule: introduction then holding, never show presentation.", "live")
        self.assertEqual(changed["status"], "queued")
        self.assertEqual(self.store.get_state("show")["revision"], 2)
        self.assertEqual(self.store.get_state("stage")["scene"], "holding")
        self.assertEqual(self.store.get_run(run["id"])["status"], "blocked")
        self.assertEqual(self.cue(run, 1, "stale-rule-presentation").status_code, 409)
        self.assertEqual(len(self.store.get_run(run["id"])["receipts"]), 1)
        inherited = self.store.create_run("alex", None, "live")
        self.assertEqual(inherited["notes"], changed["notes"])

    def test_obsolete_queued_rule_cannot_plan_against_fresh_show_revision(self):
        run = self.store.create_run("maya", "Rule A", "live")
        self.store.create_run("ravi", "Rule B", "live")
        for operation, result, proof_operation in (
            ("ingest-memory", memory_result(run["notes"]), "ingest-memory"),
            ("recall-recipe", memory_result(run["notes"], "hydradb"), "recall-recipe"),
            ("validate-show", validation_result(2), None),
        ):
            key, _ = self.store.claim_operation(run["id"], operation)
            self.store.add_trace(run["id"], result, proof_operation)
            self.store.finish_operation(run["id"], key, result)
        with self.assertRaises(DomainError) as failure:
            self.store.plan(run["id"])
        self.assertEqual(failure.exception.code, "production_rules_changed")
        self.assertEqual(self.store.get_run(run["id"])["receipts"], [])


class ConcurrentExecutionAcceptanceTests(AcceptanceFixture, unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.setup_fixture()

    async def test_operator_cancel_waits_for_active_rote_cleanup_even_after_last_cue(self):
        for finish_cues in (False, True):
            with self.subTest(completed=finish_cues):
                run = self.staged_live(approve=True)
                entered, cleaned = asyncio.Event(), asyncio.Event()

                async def replay(*args):
                    if finish_cues:
                        for step in range(3):
                            self.store.cue(run["id"], step, f"cancel-fixture-{step}")
                    entered.set()
                    try:
                        await asyncio.Event().wait()
                    except asyncio.CancelledError:
                        await asyncio.sleep(0)
                        cleaned.set()
                        raise

                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url="http://testserver") as client:
                    with patch("cuepilot.integrations.rote.replay", side_effect=replay):
                        request = asyncio.create_task(client.post("/api/v1/tools/execute", headers=self.bridge, json={"runId": run["id"]}))
                        try:
                            await asyncio.wait_for(entered.wait(), 2)
                            response = await asyncio.wait_for(client.post(f"/api/v1/runs/{run['id']}/cancel", headers=self.operator), 2)
                            self.assertEqual(response.status_code, 200)
                            self.assertTrue(cleaned.is_set())
                            current = response.json()
                            self.assertEqual(current["operations"]["execute"]["status"], "failed")
                            self.assertEqual(len(current["receipts"]), 3 if finish_cues else 0)
                            self.assertEqual(current["status"], "completed" if finish_cues else "blocked")
                            self.assertFalse(current["verifiedCompletion"])
                            self.assertEqual(self.store.get_state("stage")["scene"], "holding")
                        finally:
                            request.cancel()
                            await asyncio.gather(request, return_exceptions=True)

    async def test_overlapping_bridge_execute_claims_rote_once(self):
        run = self.staged_live(approve=True)
        entered = asyncio.Event()
        second_entered = asyncio.Event()
        release = asyncio.Event()
        calls = []

        async def replay(*args):
            calls.append(args)
            (entered if len(calls) == 1 else second_entered).set()
            await release.wait()
            return {"provider": "rote", "status": "blocked", "operation": "replay",
                    "evidence": {"testOnly": True}, "reason": "acceptance-stop"}

        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url="http://testserver") as client:
            with patch("cuepilot.integrations.hotdata.validate_show", AsyncMock(return_value=validation_result(1))):
                checked = await client.post("/api/v1/tools/validate-show", headers=self.bridge, json={"runId": run["id"]})
            self.assertEqual(checked.status_code, 200, checked.text)
            with patch("cuepilot.integrations.rote.replay", side_effect=replay):
                first = asyncio.create_task(client.post("/api/v1/tools/execute", headers=self.bridge, json={"runId": run["id"]}))
                requests = [first]
                try:
                    await asyncio.wait_for(entered.wait(), 2)
                    second = asyncio.create_task(client.post("/api/v1/tools/execute", headers=self.bridge, json={"runId": run["id"]}))
                    requests.append(second)
                    # Either a safe rejection/join or a second adapter invocation.
                    observation = asyncio.create_task(second_entered.wait())
                    try:
                        await asyncio.wait({second, observation}, timeout=0.25, return_when=asyncio.FIRST_COMPLETED)
                    finally:
                        observation.cancel()
                        await asyncio.gather(observation, return_exceptions=True)
                finally:
                    release.set()
                    await asyncio.gather(*requests)
        self.assertEqual(len(calls), 1, "Overlapping execute requests started two Rote operations")


if __name__ == "__main__":
    unittest.main()

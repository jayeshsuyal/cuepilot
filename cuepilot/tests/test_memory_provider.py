"""Offline provider parsing and graph-proof checks; never contact a sponsor."""
from copy import deepcopy
from email import policy
from email.parser import BytesParser
import hashlib
import json
import unittest
from unittest.mock import AsyncMock, patch

import httpx

from cuepilot.integrations import memory


DATASET_ID = "87d422cd-2760-46b3-bfef-cd3991796a70"
NOTE = (
    "Introduction precedes Presentation; Presentation precedes Holding. "
    "Unavailable speaker and unavailable presentation both fall back to Holding."
)


def exported_graph():
    """Cognee visualization response with original IDs and extraction properties."""
    return {
        "nodes": [
            {"id": "entity-intro", "label": "Introduction", "type": "Entity",
             "properties": {"name": "Introduction", "description": "Welcome the speaker",
                            "metadata": {"source_document": "production-note", "offsets": [0, 12]}}},
            {"id": "entity-slides", "label": "Presentation", "type": "Entity",
             "properties": {"name": "Presentation", "description": "Show the speaker's slides"}},
            {"id": "entity-hold", "label": "Holding", "type": "Entity",
             "properties": {"name": "Holding", "description": "Keep the safe holding card visible"}},
            {"id": "entity-no-speaker", "label": "Unavailable speaker", "type": "Entity",
             "properties": {"name": "Unavailable speaker"}},
            {"id": "entity-no-slides", "label": "Unavailable presentation", "type": "Entity",
             "properties": {"name": "Unavailable presentation"}},
        ],
        "edges": [
            {"source": "entity-intro", "target": "entity-slides", "label": "precedes"},
            {"source": "entity-slides", "target": "entity-hold", "label": "precedes"},
            {"source": "entity-no-speaker", "target": "entity-hold", "label": "fallback_to"},
            {"source": "entity-no-slides", "target": "entity-hold", "label": "fallback_to"},
        ],
    }


def multipart_fields(request):
    multipart = BytesParser(policy=policy.default).parsebytes(
        b"Content-Type: " + request.headers["content-type"].encode() + b"\r\n\r\n" + request.content
    )
    return {part.get_param("name", header="content-disposition"): part
            for part in multipart.iter_parts()}


class CogneeExtractionTests(unittest.IsolatedAsyncioTestCase):
    async def extract(self, response, graph=None, statuses=(), *, public=False):
        requests = []
        real_client = httpx.AsyncClient
        status_responses = iter(statuses)

        def respond(request):
            requests.append(request)
            if request.method == "POST" and request.url.path == "/api/v1/remember":
                return httpx.Response(200, json=response(request) if callable(response) else response)
            if request.method == "GET" and request.url.path == "/api/v1/datasets/status":
                self.assertEqual(dict(request.url.params), {"dataset": DATASET_ID, "pipeline": "cognify_pipeline"})
                return httpx.Response(200, json=next(status_responses, {DATASET_ID: "running"}))
            if request.method == "GET" and request.url.path == f"/api/v1/datasets/{DATASET_ID}/graph":
                return httpx.Response(200, json=graph if graph is not None else exported_graph())
            raise AssertionError(f"Unexpected provider request: {request.method} {request.url.path}")

        def client_factory(**kwargs):
            return real_client(transport=httpx.MockTransport(respond), **kwargs)

        self.requests = requests
        with patch.object(memory, "_cloud_config", return_value=("https://offline.cognee.ai", "offline-test-key")), \
                patch.object(memory.httpx, "AsyncClient", side_effect=client_factory), \
                patch.object(memory, "COGNEE_POLL_INTERVAL", 0.001):
            if public:
                return await memory.ingest_note(NOTE, "show-provider-test")
            return await memory._extract_graph(NOTE, "show-provider-test")

    async def test_completed_remember_uploads_note_and_exports_the_returned_dataset(self):
        graph, provenance = await self.extract({"status": "completed", "dataset_id": DATASET_ID.upper()})

        self.assertEqual([(r.method, r.url.path) for r in self.requests], [
            ("POST", "/api/v1/remember"),
            ("GET", f"/api/v1/datasets/{DATASET_ID}/graph"),
        ])
        upload = self.requests[0]
        self.assertEqual(upload.headers["X-Api-Key"], "offline-test-key")
        multipart = BytesParser(policy=policy.default).parsebytes(
            b"Content-Type: " + upload.headers["content-type"].encode() + b"\r\n\r\n" + upload.content
        )
        self.assertTrue(multipart.is_multipart())
        fields = {part.get_param("name", header="content-disposition"): part
                  for part in multipart.iter_parts()}
        self.assertEqual(fields["data"].get_payload(decode=True), NOTE.encode())
        self.assertEqual(fields["data"].get_content_type(), "text/plain")
        self.assertTrue(fields["data"].get_filename().endswith(".txt"))
        self.assertEqual(fields["datasetName"].get_payload(decode=True).decode(), provenance["dataset_name"])
        self.assertEqual(fields["run_in_background"].get_payload(decode=True), b"false")
        self.assertIn("Do not add a fact", fields["custom_prompt"].get_payload(decode=True).decode())

        self.assertCountEqual(graph["nodes"], exported_graph()["nodes"])
        self.assertCountEqual(graph["edges"], exported_graph()["edges"])
        self.assertEqual(provenance["dataset_id"], DATASET_ID)
        self.assertEqual(provenance["source_id"], "show-provider-test")
        self.assertEqual(provenance["provider"], "cognee")
        self.assertIs(provenance["ingest_completed"], True)
        self.assertEqual(provenance["note_sha256"], hashlib.sha256(NOTE.encode()).hexdigest())
        canonical_graph = json.dumps(graph, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        self.assertEqual(provenance["graph_sha256"], hashlib.sha256(canonical_graph.encode()).hexdigest())
        self.assertEqual(memory._prove_template(graph)["supported_template"], "speaker-segment-v1")

    async def test_id_only_items_and_matching_optional_dataset_name_are_valid(self):
        def completed(request):
            return {"status": "completed", "dataset_id": DATASET_ID,
                    "dataset_name": multipart_fields(request)["datasetName"].get_payload(decode=True).decode(),
                    "items": [{"id": "26e53310-62ea-4bc7-913c-a38c6a60a185"}], "error": None}

        graph, provenance = await self.extract(completed)
        self.assertIs(provenance["ingest_completed"], True)
        self.assertEqual(memory._prove_template(graph)["supported_template"], "speaker-segment-v1")
        self.assertEqual(len(self.requests), 2)

    async def test_completed_status_with_explicit_error_cannot_export_a_graph(self):
        for error in ("private provider error detail", {"message": "private provider error detail"}):
            with self.subTest(error=error):
                with self.assertRaises(memory.ProviderError) as caught:
                    await self.extract({"status": "completed", "dataset_id": DATASET_ID, "error": error})
                self.assertTrue(caught.exception.blocked)
                self.assertNotIn("private provider error detail", caught.exception.reason)
                self.assertEqual(len(self.requests), 1)

    async def test_returned_dataset_name_must_match_the_uploaded_note_dataset(self):
        for dataset_name in ("unrelated-existing-dataset", "", None):
            with self.subTest(dataset_name=dataset_name):
                with self.assertRaises(memory.ProviderError) as caught:
                    await self.extract({"status": "completed", "dataset_id": DATASET_ID,
                                        "dataset_name": dataset_name})
                self.assertTrue(caught.exception.blocked)
                self.assertEqual(len(self.requests), 1)

    async def test_provider_reordering_does_not_change_provenance_graph_digest(self):
        graph = exported_graph()
        _, before = await self.extract({"status": "completed", "dataset_id": DATASET_ID}, graph)
        graph["nodes"].reverse()
        graph["edges"].reverse()
        _, after = await self.extract({"status": "completed", "dataset_id": DATASET_ID}, graph)
        self.assertEqual(before["graph_sha256"], after["graph_sha256"])

    async def test_background_or_incomplete_ingestion_never_requests_a_graph(self):
        for response in (
            {"status": "accepted", "dataset_id": DATASET_ID},
            {"status": "started", "dataset_id": DATASET_ID},
            {"status": "processing", "dataset_id": DATASET_ID},
            {"status": "failed", "dataset_id": DATASET_ID},
            {"dataset_id": DATASET_ID},
            [{"status": "completed", "dataset_id": DATASET_ID}],
        ):
            with self.subTest(response=response):
                with self.assertRaises(memory.ProviderError) as caught:
                    await self.extract(response)
                self.assertTrue(caught.exception.blocked)
                self.assertEqual(len(self.requests), 1)
                self.assertEqual(self.requests[0].method, "POST")

    async def test_completed_ingestion_requires_a_valid_dataset_uuid(self):
        for dataset_id in (None, "", "not-a-uuid", "../../other-tenant/graph", 123):
            with self.subTest(dataset_id=dataset_id):
                with self.assertRaises(memory.ProviderError) as caught:
                    await self.extract({"status": "completed", "dataset_id": dataset_id})
                self.assertTrue(caught.exception.blocked)
                self.assertEqual(len(self.requests), 1)
        with self.assertRaises(memory.ProviderError):
            await self.extract({"status": "completed"})
        self.assertEqual(len(self.requests), 1)

    async def test_completed_ingestion_with_unresolved_graph_is_blocked(self):
        graph = exported_graph()
        graph["edges"][0]["target"] = "entity-not-exported"
        with self.assertRaises(memory.ProviderError) as caught:
            await self.extract({"status": "completed", "dataset_id": DATASET_ID}, graph)
        self.assertTrue(caught.exception.blocked)
        self.assertEqual(len(self.requests), 2)

    async def test_running_polls_same_dataset_to_completed_before_export(self):
        graph, provenance = await self.extract(
            {"status": "running", "dataset_id": DATASET_ID},
            statuses=[{}, {DATASET_ID: "DATASET_PROCESSING_STARTED"},
                      {DATASET_ID: "DATASET_PROCESSING_COMPLETED"}],
        )
        self.assertEqual([r.url.path for r in self.requests], [
            "/api/v1/remember", *(["/api/v1/datasets/status"] * 3),
            f"/api/v1/datasets/{DATASET_ID}/graph",
        ])
        self.assertEqual(provenance["completion_status"], "DATASET_PROCESSING_COMPLETED")
        self.assertIs(provenance["ingest_completed"], True)
        self.assertEqual(memory._prove_template(graph)["supported_template"], memory.TEMPLATE)

    async def test_running_completion_supports_documented_short_status(self):
        _, provenance = await self.extract(
            {"status": "running", "dataset_id": DATASET_ID}, statuses=[{DATASET_ID: "completed"}],
        )
        self.assertEqual(provenance["completion_status"], "completed")

    async def test_failure_or_unknown_status_cannot_export_or_publish_hydra(self):
        for status in ("failed", "DATASET_PROCESSING_ERRORED", "unrecognized", {"status": "completed"}):
            with self.subTest(status=status), \
                    patch.object(memory, "_read_recipe", new=AsyncMock(return_value=None)), \
                    patch.object(memory, "_persist_graph", new=AsyncMock()) as persist:
                result = await self.extract({"status": "running", "dataset_id": DATASET_ID},
                                            statuses=[{DATASET_ID: status}], public=True)
                self.assertEqual(result["status"], "blocked")
                self.assertFalse(any(r.url.path.endswith("/graph") for r in self.requests))
                persist.assert_not_awaited()

    async def test_timeout_or_other_dataset_completion_cannot_publish_hydra(self):
        for status in ({DATASET_ID: "running"}, {"other-dataset": "DATASET_PROCESSING_COMPLETED"}):
            with self.subTest(status=status), \
                    patch.object(memory, "_read_recipe", new=AsyncMock(return_value=None)), \
                    patch.object(memory, "_persist_graph", new=AsyncMock()) as persist, \
                    patch.object(memory, "COGNEE_POLL_TIMEOUT", 0.01):
                result = await self.extract({"status": "running", "dataset_id": DATASET_ID},
                                            statuses=[status] * 100, public=True)
                self.assertEqual(result["status"], "blocked")
                self.assertIn("bounded wait", result["reason"])
                self.assertFalse(any(r.url.path.endswith("/graph") for r in self.requests))
                persist.assert_not_awaited()

    async def test_error_or_malformed_status_envelope_cannot_publish_hydra(self):
        for response in ([], {DATASET_ID: "completed", "error": "private detail"},
                         {DATASET_ID: {"cognify_pipeline": "completed"}}):
            with self.subTest(response=response), \
                    patch.object(memory, "_read_recipe", new=AsyncMock(return_value=None)), \
                    patch.object(memory, "_persist_graph", new=AsyncMock()) as persist:
                result = await self.extract({"status": "running", "dataset_id": DATASET_ID},
                                            statuses=[response], public=True)
                self.assertEqual(result["status"], "blocked")
                self.assertNotIn("private detail", result["reason"])
                self.assertFalse(any(r.url.path.endswith("/graph") for r in self.requests))
                persist.assert_not_awaited()

    async def test_resume_uses_original_receipt_and_never_uploads_again(self):
        requests = []

        def respond(request):
            requests.append(request)
            self.assertEqual(request.method, "GET")
            if request.url.path == "/api/v1/datasets/status":
                return httpx.Response(200, json={DATASET_ID: "DATASET_PROCESSING_COMPLETED"})
            self.assertEqual(request.url.path, f"/api/v1/datasets/{DATASET_ID}/graph")
            return httpx.Response(200, json=exported_graph())

        async with httpx.AsyncClient(base_url="https://offline.cognee.ai/", transport=httpx.MockTransport(respond)) as client:
            _, provenance = await memory._finish_extraction(
                client, {"status": "running", "dataset_id": DATASET_ID, "dataset_name": "existing-dataset"},
                "existing-dataset", NOTE, "show-provider-test",
            )
        self.assertEqual(len(requests), 2)
        self.assertEqual(provenance["dataset_name"], "existing-dataset")


class CogneeGraphProofTests(unittest.TestCase):
    def assert_blocked(self, graph, *, normalize=False):
        operation = memory._normalize_graph if normalize else memory._prove_template
        with self.assertRaises(memory.ProviderError) as caught:
            operation(graph)
        self.assertTrue(caught.exception.blocked)

    def test_normalization_preserves_cognee_ids_properties_and_relationship_labels(self):
        graph = exported_graph()
        original = deepcopy(graph)
        normalized = memory._normalize_graph(graph)
        self.assertCountEqual(normalized["nodes"], original["nodes"])
        self.assertCountEqual(normalized["edges"], original["edges"])
        self.assertEqual(graph, original)
        introduction = next(node for node in normalized["nodes"] if node["id"] == "entity-intro")
        self.assertEqual(introduction["properties"]["metadata"]["offsets"], [0, 12])
        self.assertIn({
            "source": "entity-no-speaker", "target": "entity-hold", "label": "fallback_to",
        }, normalized["edges"])

    def test_canonical_graph_retains_duplicate_edges_and_property_changes(self):
        graph = exported_graph()
        duplicated_edge = deepcopy(graph["edges"][0])
        graph["edges"].append(duplicated_edge)
        original = memory._normalize_graph(graph)
        original_digest = memory._digest(memory._json(original))
        graph["nodes"].reverse()
        graph["edges"].reverse()
        reordered = memory._normalize_graph(graph)
        self.assertEqual(reordered, original)
        self.assertEqual(reordered["edges"].count(duplicated_edge), 2)
        introduction = next(node for node in graph["nodes"] if node["id"] == "entity-intro")
        introduction["properties"]["description"] = "Changed introduction source fact"
        self.assertNotEqual(memory._digest(memory._json(memory._normalize_graph(graph))),
                            original_digest)

    def test_export_rejects_duplicate_ids_and_unresolved_edge_endpoints(self):
        duplicate = exported_graph()
        duplicate["nodes"].append(deepcopy(duplicate["nodes"][0]))
        self.assert_blocked(duplicate, normalize=True)
        for endpoint in ("source", "target"):
            with self.subTest(endpoint=endpoint):
                dangling = exported_graph()
                dangling["edges"][0][endpoint] = "missing-original-node-id"
                self.assert_blocked(dangling, normalize=True)

    def test_empty_graph_or_nodes_without_edges_are_not_proof(self):
        for graph in ({"nodes": [], "edges": []}, {"nodes": exported_graph()["nodes"], "edges": []}):
            with self.subTest(graph=graph):
                self.assert_blocked(graph, normalize=True)

    def test_supported_proof_refers_to_original_exported_edges(self):
        graph = exported_graph()
        proof = memory._prove_template(memory._normalize_graph(graph))
        self.assertEqual(proof["supported_template"], "speaker-segment-v1")
        self.assertEqual(proof["ordering_edges"], graph["edges"][:2])
        self.assertCountEqual(proof["fallback_edges"], graph["edges"][2:])

    def test_reverse_relationships_keep_original_export_direction_in_proof(self):
        graph = exported_graph()
        graph["edges"][0] = {"source": "entity-slides", "target": "entity-intro", "label": "follows"}
        graph["edges"][1] = {"source": "entity-hold", "target": "entity-slides", "label": "after"}
        proof = memory._prove_template(graph)
        self.assertEqual(proof["ordering_edges"], graph["edges"][:2])

    def test_combined_unavailability_rule_proves_both_guards(self):
        graph = exported_graph()
        graph["nodes"] = graph["nodes"][:3] + [{
            "id": "entity-either-unavailable", "label": "Speaker or presentation unavailable",
            "type": "Entity", "properties": {},
        }]
        graph["edges"] = graph["edges"][:2] + [{
            "source": "entity-either-unavailable", "target": "entity-hold", "label": "stay_on",
        }]
        proof = memory._prove_template(graph)
        self.assertEqual(proof["supported_template"], "speaker-segment-v1")
        self.assertEqual({edge["source"] for edge in proof["fallback_edges"]}, {"entity-either-unavailable"})

    def test_missing_any_ordering_or_guard_edge_cannot_be_replaced_by_note_text(self):
        for missing in range(4):
            with self.subTest(missing=missing):
                graph = exported_graph()
                graph["edges"].pop(missing)
                graph["nodes"][0]["properties"]["description"] = NOTE
                self.assert_blocked(graph)

    def test_unsupported_relationship_vocabulary_does_not_prove_a_rule(self):
        graph = exported_graph()
        graph["edges"][0]["label"] = "is related to"
        self.assert_blocked(graph)

    def test_conflicting_scene_order_is_rejected_despite_all_required_edges(self):
        for source, target in (("entity-intro", "entity-hold"), ("entity-slides", "entity-intro")):
            with self.subTest(source=source, target=target):
                graph = exported_graph()
                graph["edges"].append({"source": source, "target": target, "label": "precedes"})
                self.assert_blocked(graph)

    def test_duplicate_recognized_scene_entities_cannot_supply_ambiguous_proof(self):
        for label in ("speaker introduction", "presentation slides", "holding screen"):
            with self.subTest(label=label):
                graph = exported_graph()
                graph["nodes"].append({"id": "entity-other-scene", "label": label,
                                       "type": "Entity", "properties": {"description": "A different scene"}})
                self.assert_blocked(graph)

    def test_conflicting_unavailability_target_is_rejected_even_with_valid_guard(self):
        for source in ("entity-no-speaker", "entity-no-slides"):
            for target in ("entity-intro", "entity-slides"):
                with self.subTest(source=source, target=target):
                    graph = exported_graph()
                    graph["edges"].append({"source": source, "target": target, "label": "fallback_to"})
                    self.assert_blocked(graph)

    def test_combined_unavailability_cannot_also_fall_back_to_presentation(self):
        graph = exported_graph()
        graph["nodes"].append({"id": "entity-either", "label": "Speaker or presentation unavailable",
                               "type": "Entity", "properties": {}})
        graph["edges"].extend([
            {"source": "entity-either", "target": "entity-hold", "label": "fallback_to"},
            {"source": "entity-either", "target": "entity-slides", "label": "fallback_to"},
        ])
        self.assert_blocked(graph)

    def test_cloud_compact_entities_and_duplicate_taxonomy_are_preserved(self):
        graph = exported_graph()
        for node in graph["nodes"][3:]:
            node["label"] = node["label"].lower().replace(" ", "")
        graph["nodes"].extend([
            {"id": "type-no-speaker", "label": "unavailablespeaker", "type": "EntityType", "properties": {}},
            {"id": "type-no-slides", "label": "unavailablepresentation", "type": "EntityType", "properties": {}},
        ])
        graph["edges"].append({"source": "entity-no-speaker", "target": "type-no-speaker", "label": "is_a"})
        normalized = memory._normalize_graph(graph)
        proof = memory._prove_template(normalized)
        self.assertEqual(len(normalized["nodes"]), 7)
        self.assertEqual(len(normalized["edges"]), 5)
        self.assertCountEqual(proof["fallback_edges"], graph["edges"][2:4])
        self.assertCountEqual(normalized["nodes"], graph["nodes"])

    def test_taxonomy_or_document_edges_cannot_replace_any_entity_rule(self):
        for index in range(5):
            for node_type in ("EntityType", "DocumentChunk", ""):
                with self.subTest(index=index, node_type=node_type):
                    graph = exported_graph()
                    graph["nodes"][index]["type"] = node_type
                    self.assert_blocked(graph)

    def test_compact_aliases_do_not_allow_fuzzy_or_duplicate_entities(self):
        for label in ("unavailablepresentations", "speakerunavailable", "missingpresentations"):
            with self.subTest(label=label):
                graph = exported_graph()
                graph["nodes"][4]["label"] = label
                self.assert_blocked(graph)
        graph = exported_graph()
        graph["nodes"].append({"id": "another-unavailable", "label": "unavailablespeaker",
                               "type": "Entity", "properties": {}})
        self.assert_blocked(graph)
        graph = exported_graph()
        graph["nodes"][3]["label"] = "unavailablespeaker"
        graph["edges"].append({"source": "entity-no-speaker", "target": "entity-slides", "label": "fallback_to"})
        self.assert_blocked(graph)


if __name__ == "__main__":
    unittest.main()

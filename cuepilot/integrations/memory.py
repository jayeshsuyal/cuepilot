"""Cognee HTTP graph extraction and provenance-preserving local Hydra memory.

Importing this module performs no network requests or credential-file writes.
Public operations return evidence, never provider exceptions or credentials.
"""
from __future__ import annotations

import asyncio
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
from urllib.parse import urlsplit
from uuid import UUID, uuid4

import httpx

ROOT = Path(__file__).resolve().parents[2]
MAX_GRAPH_BYTES = 2_000_000
TEMPLATE = "speaker-segment-v1"


def _record(provider, status, operation, evidence=None, reason=None):
    return dict(provider=provider, status=status, operation=operation,
                evidence=evidence or {}, reason=reason)


def _result(status, records, reason=None, **extra):
    return dict(status=status, records=records, reason=reason, **extra)


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def _valid_id(value):
    return isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", value)


def _sha256(value):
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value)


class ProviderError(Exception):
    def __init__(self, reason, *, blocked=False):
        super().__init__(reason)
        self.reason, self.blocked = reason, blocked


def _cloud_config():
    url, key = os.getenv("COGNEE_SERVICE_URL", "").strip(), os.getenv("COGNEE_API_KEY", "").strip()
    if not url or not key:
        raise ProviderError("Cognee Cloud requires COGNEE_SERVICE_URL and COGNEE_API_KEY.", blocked=True)
    parsed = urlsplit(url)
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password
            or parsed.query or parsed.fragment or parsed.port not in (None, 443)
            or not (parsed.hostname == "cognee.ai" or parsed.hostname.endswith(".cognee.ai"))):
        raise ProviderError("Configure the HTTPS Cognee tenant URL from its API page.", blocked=True)
    return url.rstrip("/"), key


async def _http_json(client, method, path, **kwargs):
    """Stream a bounded JSON response; do not echo remote error bodies."""
    async with client.stream(method, path, **kwargs) as response:
        if response.status_code >= 300:
            if response.status_code in (401, 403):
                raise ProviderError("Cognee Cloud authentication or permission was rejected.", blocked=True)
            if response.status_code == 402:
                raise ProviderError("Cognee Cloud credits are required.", blocked=True)
            if response.status_code in (404, 405, 501):
                raise ProviderError("This Cognee tenant does not expose the required ingest or dataset graph API.", blocked=True)
            raise ProviderError(f"Cognee Cloud returned HTTP {response.status_code}.")
        chunks, length = [], 0
        async for chunk in response.aiter_bytes():
            length += len(chunk)
            if length > MAX_GRAPH_BYTES:
                raise ProviderError("Cognee response exceeds the bounded graph size.", blocked=True)
            chunks.append(chunk)
    try:
        return json.loads(b"".join(chunks))
    except (ValueError, UnicodeError):
        raise ProviderError("Cognee returned an unsupported JSON response.") from None


def _normalize_graph(graph):
    if not isinstance(graph, dict) or not isinstance(graph.get("nodes"), list) or not isinstance(graph.get("edges"), list):
        raise ProviderError("Cognee graph export must contain nodes and edges.", blocked=True)
    if not (1 <= len(graph["nodes"]) <= 200 and 1 <= len(graph["edges"]) <= 400):
        raise ProviderError("Cognee graph is empty or exceeds the 200-node / 400-edge limit.", blocked=True)
    nodes, edges, seen = [], [], set()
    for node in graph["nodes"]:
        if not isinstance(node, dict) or not isinstance(node.get("id"), str) or not node["id"] or len(node["id"]) > 256:
            raise ProviderError("Cognee graph has an invalid node identifier.", blocked=True)
        if node["id"] in seen or not isinstance(node.get("label"), str) or not isinstance(node.get("properties", {}), dict):
            raise ProviderError("Cognee graph has duplicate nodes or an unsupported node shape.", blocked=True)
        seen.add(node["id"])
        nodes.append({"id": node["id"], "label": node["label"], "type": node.get("type", ""), "properties": node.get("properties", {})})
    for edge in graph["edges"]:
        if (not isinstance(edge, dict) or edge.get("source") not in seen or edge.get("target") not in seen
                or not isinstance(edge.get("label"), str) or not edge["label"]):
            raise ProviderError("Cognee graph has an unresolved or unsupported relationship.", blocked=True)
        edges.append({key: edge[key] for key in ("source", "target", "label")})
    # Provider export order is not graph meaning; retain every edge, including duplicates.
    nodes.sort(key=lambda node: node["id"])
    edges.sort(key=lambda edge: (edge["source"], edge["target"], edge["label"]))
    return {"nodes": nodes, "edges": edges}


def _words(text):
    return " ".join(re.findall(r"[a-z0-9]+", text.lower()))


def _prove_template(graph):
    """A deliberately small grammar, not an LLM or a text-to-graph fallback."""
    aliases = {
        "intro": {"intro", "introduction", "speaker introduction", "speaker intro"},
        "presentation": {"presentation", "speaker presentation", "presentation slides"},
        "holding": {"holding", "holding card", "holding screen"},
        "speaker_unavailable": {"unavailable speaker", "speaker unavailable", "missing speaker"},
        "presentation_unavailable": {"unavailable presentation", "presentation unavailable", "missing presentation"},
        "either_unavailable": {"speaker or presentation unavailable", "unavailable speaker or presentation"},
    }
    kinds = {node["id"]: next((kind for kind, labels in aliases.items() if _words(node["label"]) in labels), None)
             for node in graph["nodes"]}
    if any(count > 1 for kind, count in Counter(kinds.values()).items() if kind is not None):
        raise ProviderError("Cognee graph has ambiguous duplicate cue or availability entities.", blocked=True)
    forward = {"precedes", "before", "followed by", "next", "next scene", "then"}
    reverse = {"follows", "after"}
    fallback = {"fallback to", "falls back to", "stay on", "stays on", "remain on", "remains on"}
    order, guards = {}, {}
    for edge in graph["edges"]:
        source, target, label = kinds[edge["source"]], kinds[edge["target"]], _words(edge["label"])
        if label in reverse:
            source, target = target, source
        if label in forward | reverse and source in {"intro", "presentation", "holding"} and target in {"intro", "presentation", "holding"}:
            if (source, target) not in {("intro", "presentation"), ("presentation", "holding")}:
                raise ProviderError("Cognee graph contains conflicting cue ordering.", blocked=True)
            order[(source, target)] = edge
        if label in fallback and source in {"speaker_unavailable", "presentation_unavailable", "either_unavailable"}:
            if target != "holding":
                raise ProviderError("Cognee graph contains conflicting unavailable fallback rules.", blocked=True)
            if source == "either_unavailable":
                guards["speaker_unavailable"] = guards["presentation_unavailable"] = edge
            elif source in {"speaker_unavailable", "presentation_unavailable"}:
                guards[source] = edge
    if len(order) != 2 or len(guards) != 2:
        raise ProviderError("Cognee graph does not prove introduction → presentation → holding and both unavailable → holding rules in the supported relationship grammar.", blocked=True)
    return {"supported_template": TEMPLATE, "ordering_edges": list(order.values()),
            "fallback_edges": list(guards.values()), "verification": "directed Cognee graph relationships"}


async def _extract_graph(note, source_id):
    base, key = _cloud_config()
    dataset_name = "cuepilot_" + _digest(source_id)[:12] + "_" + uuid4().hex[:12]
    # These are extraction vocabulary suggestions. Only returned edges can prove a plan.
    prompt = ("Extract only facts stated in the production note. For cue ordering, use scene entities "
              "Introduction, Presentation, Holding and directed precedes relationships. For an explicit "
              "unavailability rule, use Unavailable speaker and Unavailable presentation entities with "
              "fallback_to relationships to Holding. Do not add a fact or safety rule absent from the note.")
    async with httpx.AsyncClient(base_url=base + "/", headers={"X-Api-Key": key},
                                timeout=httpx.Timeout(90, connect=10), follow_redirects=False, trust_env=False) as client:
        result = await _http_json(client, "POST", "api/v1/remember",
                                 data={"datasetName": dataset_name, "custom_prompt": prompt},
                                 files={"data": ("text_" + _digest(note) + ".txt", note.encode(), "text/plain")})
        if not isinstance(result, dict) or result.get("status") != "completed":
            raise ProviderError("Cognee did not report completed ingestion; no graph was verified.", blocked=True)
        if result.get("error"):
            raise ProviderError("Cognee reported an ingestion error; no graph was verified.", blocked=True)
        if "dataset_name" in result and result["dataset_name"] != dataset_name:
            raise ProviderError("Cognee ingestion returned a different dataset name than the uploaded note.", blocked=True)
        try:
            dataset_id = str(UUID(str(result["dataset_id"])))
        except (KeyError, ValueError, TypeError):
            raise ProviderError("Cognee ingestion did not return a dataset UUID for graph export.", blocked=True) from None
        graph = _normalize_graph(await _http_json(client, "GET", f"api/v1/datasets/{dataset_id}/graph"))
    provenance = {"provider": "cognee", "dataset_id": dataset_id, "dataset_name": dataset_name,
                  "source_id": source_id, "note_sha256": _digest(note), "graph_sha256": _digest(_json(graph)),
                  "export_endpoint": "GET /api/v1/datasets/{dataset_id}/graph",
                  "ingest_completed": True, "exported_at": datetime.now(timezone.utc).isoformat()}
    return graph, provenance


def _hydra_config():
    url = os.getenv("HYDRADB_BOLT_URL", "bolt://127.0.0.1:7687")
    parsed = urlsplit(url)
    if parsed.scheme != "bolt" or parsed.hostname not in {"127.0.0.1", "::1", "localhost"} or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in ("", "/"):
        raise ProviderError("CuePilot HydraDB must use a configured loopback Bolt endpoint.", blocked=True)
    token = os.getenv("HYDRADB_AUTH_TOKEN", "").strip()
    token_path = ROOT / "sponsor-setup/memory/.hydradb/auth-token"
    if not token and token_path.is_file():
        token = token_path.read_text().strip()
    if not token:
        raise ProviderError("Local HydraDB credentials are missing; start its setup service.", blocked=True)
    return url, token, os.getenv("HYDRADB_GRAPH_ID", "default")


def _hydra_driver():
    from neo4j import AsyncGraphDatabase
    url, token, database = _hydra_config()
    return AsyncGraphDatabase.driver(url, auth=("neo4j", token), connection_timeout=5,
                                     connection_acquisition_timeout=5, max_transaction_retry_time=0), database


async def _run(session, query, **parameters):
    from neo4j import Query
    result = await session.run(Query(query, timeout=8), parameters)
    return await result.data()


def _vertex_id(receipt_id, original):
    return int(_digest(receipt_id + ":" + original)[:15], 16)


def _verify_envelope(envelope, source_id):
    graph = _normalize_graph(envelope["graph"])
    provenance = envelope["provenance"]
    if (envelope.get("version") != 2 or provenance.get("provider") != "cognee"
            or provenance.get("source_id") != source_id or provenance.get("ingest_completed") is not True
            or provenance.get("graph_sha256") != _digest(_json(graph))
            or not _sha256(provenance.get("note_sha256"))
            or not _valid_id(envelope.get("receipt_id"))):
        raise ProviderError("HydraDB recipe provenance or graph digest failed verification.")
    UUID(provenance["dataset_id"])
    if envelope.get("proof") != _prove_template(graph):
        raise ProviderError("HydraDB stored rule proof does not match its graph.")
    return graph, provenance


async def _verify_graph(session, envelope):
    """Check actual vertices and the exact relationship multiset, not just JSON."""
    graph, provenance = _verify_envelope(envelope, envelope["provenance"]["source_id"])
    receipt = envelope["receipt_id"]
    nodes = await _run(session, "MATCH (n:CuePilotMemory {receipt_id:$receipt}) RETURN n.id AS id, n.cognee_id AS original, n.source_id AS source, n.dataset_id AS dataset, n.note_sha256 AS note, n.graph_sha256 AS graph, n.payload AS payload", receipt=receipt)
    expected_nodes = [dict(id=_vertex_id(receipt, node["id"]), original=node["id"],
                           source=provenance["source_id"], dataset=provenance["dataset_id"],
                           note=provenance["note_sha256"], graph=provenance["graph_sha256"], payload=_json(node))
                      for node in graph["nodes"]]
    if Counter(map(_json, nodes)) != Counter(map(_json, expected_nodes)):
        raise ProviderError("HydraDB vertices or their Cognee provenance do not match the export.")
    edges = await _run(session, "MATCH (a:CuePilotMemory {receipt_id:$receipt})-[r:COGNEE_RELATION]->(b) RETURN a.id AS source, b.id AS target, b.receipt_id AS target_receipt, r.label AS label, r.dataset_id AS dataset, r.receipt_id AS receipt", receipt=receipt)
    expected_edges = [dict(source=_vertex_id(receipt, edge["source"]), target=_vertex_id(receipt, edge["target"]),
                           target_receipt=receipt, label=edge["label"], dataset=provenance["dataset_id"], receipt=receipt)
                      for edge in graph["edges"]]
    if Counter(map(_json, edges)) != Counter(map(_json, expected_edges)):
        raise ProviderError("HydraDB relationship traversal or provenance does not match the Cognee export.")


async def _upsert_vertices(session, kind, rows):
    """Use Hydra's guarded UNWIND form; equal-generation writes never replace proof.

    The server compares the guard and writes metadata in one transaction. A
    constant generation makes retries immutable; callers must check read-back.
    Query text and labels are fixed, and provider data travels only as parameters.
    """
    queries = {
        "memory": (
            "UNWIND $rows AS row MERGE (n {id:row.id}) "
            "SET n:CuePilotMemory, n.cognee_id=row.original, n.receipt_id=row.receipt, "
            "n.source_id=row.source, n.dataset_id=row.dataset, n.note_sha256=row.note, "
            "n.graph_sha256=row.graph, n.payload=row.payload, n.generation=row.generation, "
            "n.__hydradb_update_if_newer_by=row.generation"
        ),
        "recipe": (
            "UNWIND $rows AS row MERGE (n {id:row.id}) "
            "SET n:CuePilotRecipe, n.source_id=row.source, n.receipt_id=row.receipt, "
            "n.created_at=row.created, n.payload=row.payload, n.generation=row.generation, "
            "n.__hydradb_update_if_newer_by=row.generation"
        ),
        "outcome": (
            "UNWIND $rows AS row MERGE (n {id:row.id}) "
            "SET n:CuePilotOutcome, n.outcome_id=row.outcome, n.binding_sha256=row.binding, "
            "n.source_id=row.source, n.created_at=row.created, n.payload=row.payload, "
            "n.generation=row.generation, n.__hydradb_update_if_newer_by=row.generation"
        ),
    }
    await _run(session, queries[kind], rows=[dict(row, generation=1) for row in rows])


async def _persist_graph(graph, provenance, proof):
    """Publish last; an incomplete import has no recallable recipe."""
    receipt_id = uuid4().hex
    envelope = {"version": 2, "receipt_id": receipt_id, "graph": graph, "provenance": provenance, "proof": proof}
    _verify_envelope(envelope, provenance["source_id"])
    payload = _json(envelope)
    driver, database = _hydra_driver()
    async with driver:
        async with driver.session(database=database) as session:
            await _upsert_vertices(session, "memory", [
                dict(id=_vertex_id(receipt_id, node["id"]), original=node["id"], receipt=receipt_id,
                     source=provenance["source_id"], dataset=provenance["dataset_id"],
                     note=provenance["note_sha256"], graph=provenance["graph_sha256"], payload=_json(node))
                for node in graph["nodes"]])
            await _run(session,
                       "UNWIND $rows AS row "
                       "MATCH (a:CuePilotMemory {id:row.source}), (b:CuePilotMemory {id:row.target}) "
                       "CREATE (a)-[:COGNEE_RELATION {id:row.edge_id, label:row.label, "
                       "dataset_id:row.dataset, receipt_id:row.receipt}]->(b)",
                       rows=[dict(source=_vertex_id(receipt_id, edge["source"]),
                                  target=_vertex_id(receipt_id, edge["target"]),
                                  edge_id=_vertex_id(receipt_id, "edge:" + str(index)),
                                  label=edge["label"], dataset=provenance["dataset_id"], receipt=receipt_id)
                             for index, edge in enumerate(graph["edges"])])
            await _verify_graph(session, envelope)
            await _upsert_vertices(session, "recipe", [
                dict(id=_vertex_id(receipt_id, "receipt:" + receipt_id), source=provenance["source_id"],
                     receipt=receipt_id, created=provenance["exported_at"], payload=payload)])
            rows = await _run(session, "MATCH (r:CuePilotRecipe {receipt_id:$receipt}) RETURN r.payload AS payload", receipt=receipt_id)
            if rows != [{"payload": payload}]:
                raise ProviderError("HydraDB recipe read-back did not match the stored provenance.")
    return {"receipt_id": receipt_id, "graph_sha256": provenance["graph_sha256"],
            "node_count": len(graph["nodes"]), "edge_count": len(graph["edges"]), "read_back_verified": True}


async def _read_recipe(show_id):
    driver, database = _hydra_driver()
    async with driver:
        async with driver.session(database=database) as session:
            rows = await _run(session, "MATCH (r:CuePilotRecipe {source_id:$source}) RETURN r.payload AS payload, r.receipt_id AS receipt ORDER BY r.created_at DESC LIMIT 1", source=show_id)
            if not rows:
                return None
            envelope = json.loads(rows[0]["payload"])
            _verify_envelope(envelope, show_id)
            if envelope["receipt_id"] != rows[0]["receipt"]:
                raise ProviderError("HydraDB recipe identifier does not match its envelope.")
            await _verify_graph(session, envelope)
            return envelope


def _recipe_result(envelope, records):
    provenance = envelope["provenance"]
    return _result("verified", records, supported_template=TEMPLATE, recipe_id=envelope["receipt_id"],
                   source_id=provenance["source_id"], note_sha256=provenance["note_sha256"],
                   graph_sha256=provenance["graph_sha256"])


def _recall_record(envelope):
    return _record("hydradb", "verified", "recall_and_traverse_recipe", {
        "receipt_id": envelope["receipt_id"], "provenance": envelope["provenance"], "proof": envelope["proof"],
        "node_count": len(envelope["graph"]["nodes"]), "edge_count": len(envelope["graph"]["edges"]),
        "read_back_verified": True})


async def _reexport_graph(envelope):
    base, key = _cloud_config()
    provenance = envelope["provenance"]
    dataset_id = str(UUID(provenance["dataset_id"]))
    async with httpx.AsyncClient(base_url=base + "/", headers={"X-Api-Key": key},
                                timeout=httpx.Timeout(30, connect=5), follow_redirects=False, trust_env=False) as client:
        graph = _normalize_graph(await _http_json(client, "GET", f"api/v1/datasets/{dataset_id}/graph"))
    if _digest(_json(graph)) != provenance["graph_sha256"] or _prove_template(graph) != envelope["proof"]:
        raise ProviderError("The Cognee dataset graph changed; ingest and approve the current rule again.", blocked=True)
    return _record("cognee", "verified", "reexport_unchanged_graph", {
        "provenance": provenance, "graph_sha256": provenance["graph_sha256"], "note_sha256": provenance["note_sha256"],
        "reused_extraction": True, "checked_at": datetime.now(timezone.utc).isoformat()})


async def ingest_note(note: str, source_id: str) -> dict:
    """Extract a new note or re-export its exact existing graph; never fake reuse."""
    records, phase = [], "hydradb"
    if not isinstance(note, str) or not note.strip() or len(note.encode()) > 16_000 or not _valid_id(source_id):
        reason = "A nonempty production note (at most 16 KB) and a valid source/show ID are required."
        return _result("blocked", [_record("cognee", "blocked", "ingest_note", reason=reason)], reason)
    try:
        async with asyncio.timeout(150):
            # Confirm Hydra is usable before consuming hosted extraction credits.
            existing = await _read_recipe(source_id)
            if existing and existing["provenance"]["note_sha256"] == _digest(note):
                records.append(_recall_record(existing))
                phase = "cognee"
                records.append(await _reexport_graph(existing))
                return _recipe_result(existing, records)
            phase = "cognee"
            graph, provenance = await _extract_graph(note, source_id)
            records.append(_record("cognee", "verified", "ingest_and_export_graph", provenance | {"node_count": len(graph["nodes"]), "edge_count": len(graph["edges"])}))
            proof = _prove_template(graph)
            records.append(_record("cognee", "verified", "verify_cue_relationships", proof))
            phase = "hydradb"
            persisted = await _persist_graph(graph, provenance, proof)
            records.append(_record("hydradb", "verified", "persist_and_traverse_graph", persisted))
        return _recipe_result({"receipt_id": persisted["receipt_id"], "provenance": provenance}, records)
    except ProviderError as error:
        status, reason = ("blocked" if error.blocked else "failed"), error.reason
    except (TimeoutError, httpx.TimeoutException):
        status, reason = "failed", "Memory operation timed out; remote ingestion may still be running."
    except Exception:
        status, reason = "failed", "Memory provider operation failed; credentials and remote error bodies were withheld."
    records.append(_record(phase, status, "ingest_note", reason=reason))
    return _result(status, records, reason)


async def recall_recipe(show_id: str) -> dict:
    """Recheck stored Cognee provenance and actual local Hydra graph traversal."""
    try:
        if not _valid_id(show_id):
            raise ProviderError("A valid show ID is required.", blocked=True)
        async with asyncio.timeout(20):
            envelope = await _read_recipe(show_id)
        if envelope is None:
            raise ProviderError("HydraDB has no verified Cognee recipe for this show; ingest its production note first.", blocked=True)
        return _recipe_result(envelope, [_recall_record(envelope)])
    except ProviderError as error:
        status, reason = ("blocked" if error.blocked else "failed"), error.reason
    except TimeoutError:
        status, reason = "failed", "HydraDB recipe recall timed out."
    except Exception:
        status, reason = "failed", "HydraDB recipe could not be read and verified."
    return _result(status, [_record("hydradb", status, "recall_recipe", reason=reason)], reason)


def _binding(source_id, note_sha256, graph_sha256, recipe_id, procedure):
    if (not _valid_id(source_id) or not _valid_id(recipe_id) or not _sha256(note_sha256)
            or not _sha256(graph_sha256) or not isinstance(procedure, dict)
            or set(procedure) != {"id", "sha256"} or not _valid_id(procedure.get("id"))
            or not _sha256(procedure.get("sha256"))):
        raise ProviderError("Exact source, note/graph hashes, recipe, and immutable Rote procedure identity are required.", blocked=True)
    return dict(source_id=source_id, note_sha256=note_sha256, graph_sha256=graph_sha256,
                recipe_id=recipe_id, procedure=dict(procedure), supported_template=TEMPLATE)


async def _current_binding(binding):
    envelope = await _read_recipe(binding["source_id"])
    if (not envelope or envelope["receipt_id"] != binding["recipe_id"]
            or envelope["provenance"]["note_sha256"] != binding["note_sha256"]
            or envelope["provenance"]["graph_sha256"] != binding["graph_sha256"]):
        raise ProviderError("Outcome proof does not match the current source note and graph recipe.", blocked=True)
    return envelope


_RECEIPT_FIELDS = ("ok", "id", "runId", "stepIndex", "scene", "stageRevision", "committedAt")


def _successful_execution(run, rote_result, binding):
    if (not isinstance(run, dict) or run.get("status") != "completed" or run.get("executionMode") != "live"
            or run.get("showId") != binding["source_id"] or not _valid_id(run.get("id"))
            or not isinstance(run.get("notes"), str) or _digest(run["notes"]) != binding["note_sha256"]):
        raise ProviderError("Only a completed live run of this exact production note can publish an outcome.", blocked=True)
    plan = run.get("plan") or {}
    memory_proof = run.get("memoryProof") or {}
    if (any(memory_proof.get(field) != binding[field]
            for field in ("source_id", "note_sha256", "graph_sha256", "recipe_id"))
            or plan.get("recipeId") != binding["recipe_id"]):
        raise ProviderError("The completed run was planned against a different memory recipe or graph.", blocked=True)
    if (plan.get("origin") != "sponsor" or not _sha256(plan.get("hash"))
            or type(plan.get("showRevision")) is not int or plan["showRevision"] < 0
            or plan.get("cues") != [{"index": i, "scene": scene} for i, scene in enumerate(("intro", "presentation", "holding"))]):
        raise ProviderError("The completed run lacks the approved supported sponsor plan.", blocked=True)
    receipts = run.get("receipts")
    if not isinstance(receipts, list) or len(receipts) != 3:
        raise ProviderError("Three successful stage receipts are required.", blocked=True)
    clean = []
    for i, scene in enumerate(("intro", "presentation", "holding")):
        receipt = receipts[i]
        if (not isinstance(receipt, dict) or receipt.get("ok") is not True or receipt.get("runId") != run["id"]
                or type(receipt.get("stepIndex")) is not int or receipt["stepIndex"] != i or receipt.get("scene") != scene
                or not _valid_id(receipt.get("id")) or type(receipt.get("stageRevision")) is not int
                or not isinstance(receipt.get("committedAt"), str)):
            raise ProviderError("Stage receipts do not prove the three ordered successful cues.", blocked=True)
        datetime.fromisoformat(receipt["committedAt"])
        clean.append({field: receipt[field] for field in _RECEIPT_FIELDS})
    if (len({r["id"] for r in clean}) != 3 or clean[0]["stageRevision"] < 1
            or [r["stageRevision"] for r in clean] != list(range(clean[0]["stageRevision"], clean[0]["stageRevision"] + 3))):
        raise ProviderError("Stage receipt identities or revisions are inconsistent.", blocked=True)
    if (not isinstance(rote_result, dict) or rote_result.get("provider") != "rote"
            or rote_result.get("status") != "verified" or rote_result.get("operation") not in {"learn", "replay"}):
        raise ProviderError("Actual verified Rote learning or replay evidence is required.", blocked=True)
    evidence = rote_result.get("evidence") or {}
    if evidence.get("procedure") != binding["procedure"]:
        raise ProviderError("Rote evidence does not match the immutable procedure identity.", blocked=True)
    operation = rote_result["operation"]
    if evidence.get("learnedFromRunId" if operation == "learn" else "runId") != run["id"]:
        raise ProviderError("Rote evidence belongs to another run.", blocked=True)
    captured = evidence.get("receipts")
    if not isinstance(captured, list) or len(captured) != 3:
        raise ProviderError("Rote evidence lacks the matching stage receipts.", blocked=True)
    for i, item in enumerate(captured):
        if not isinstance(item, dict) or item.get("ok") is not True or item.get("runId") != run["id"] or type(item.get("stepIndex")) is not int or item["stepIndex"] != i:
            raise ProviderError("Rote captured a different run or cue.", blocked=True)
        # Learning's authored helper hashes the raw JSON HTTP receipt; replay
        # returns full API receipts. Both must match this completed run exactly.
        if all(field in item for field in _RECEIPT_FIELDS):
            matches = {field: item[field] for field in _RECEIPT_FIELDS} == clean[i]
        else:
            raw = json.dumps(receipts[i], ensure_ascii=False, allow_nan=False, separators=(",", ":"))
            matches = item.get("receiptSha256") == _digest(raw)
        if not matches:
            raise ProviderError("Rote receipt proof does not match the committed stage receipt.", blocked=True)
    return dict(run_id=run["id"], execution_mode="live", show_revision=plan["showRevision"], plan_sha256=plan["hash"],
                speaker_id=run["speakerId"], operation=operation, receipts=clean,
                receipts_sha256=_digest(_json(clean)), rote_run_id=evidence.get("roteRunId"))


async def _write_outcome(payload):
    outcome_id = _digest(_json({"binding": payload["binding"], "run_id": payload["execution"]["run_id"]}))
    envelope = dict(version=1, outcome_id=outcome_id, **payload)
    serialized = _json(envelope)
    driver, database = _hydra_driver()
    async with driver:
        async with driver.session(database=database) as session:
            await _upsert_vertices(session, "outcome", [
                dict(id=int(outcome_id[:15], 16), outcome=outcome_id,
                     binding=_digest(_json(payload["binding"])), source=payload["binding"]["source_id"],
                     created=datetime.now(timezone.utc).isoformat(), payload=serialized)])
            rows = await _run(session, "MATCH (o:CuePilotOutcome {id:$id}) RETURN o.outcome_id AS outcome, o.payload AS payload", id=int(outcome_id[:15], 16))
            if rows != [{"outcome": outcome_id, "payload": serialized}]:
                raise ProviderError("HydraDB outcome read-back conflicted with its immutable proof.")
    return envelope


async def _read_outcome(binding):
    driver, database = _hydra_driver()
    async with driver:
        async with driver.session(database=database) as session:
            rows = await _run(session, "MATCH (o:CuePilotOutcome {binding_sha256:$binding, source_id:$source}) RETURN o.outcome_id AS outcome, o.payload AS payload ORDER BY o.created_at DESC LIMIT 1",
                              binding=_digest(_json(binding)), source=binding["source_id"])
    if not rows:
        raise ProviderError("No successful outcome exists for this exact note, graph, recipe, and Rote procedure.", blocked=True)
    envelope = json.loads(rows[0]["payload"])
    execution = envelope["execution"]
    expected_id = _digest(_json({"binding": binding, "run_id": execution["run_id"]}))
    if (envelope.get("version") != 1 or envelope.get("binding") != binding
            or envelope.get("outcome_id") != expected_id or rows[0]["outcome"] != expected_id
            or envelope.get("execution_sha256") != _digest(_json(execution))
            or execution.get("receipts_sha256") != _digest(_json(execution["receipts"]))):
        raise ProviderError("HydraDB successful-outcome provenance failed verification.")
    return envelope


def _outcome_result(envelope, operation):
    evidence = dict(outcome_id=envelope["outcome_id"], **envelope["binding"], execution=envelope["execution"],
                    read_back_verified=True)
    return _result("verified", [_record("hydradb", "verified", operation, evidence)],
                   outcome_id=envelope["outcome_id"], **envelope["binding"], execution=envelope["execution"])


def _outcome_failure(error, operation):
    status = "blocked" if isinstance(error, ProviderError) and error.blocked else "failed"
    reason = error.reason if isinstance(error, ProviderError) else "HydraDB successful-outcome operation could not be verified."
    return _result(status, [_record("hydradb", status, operation, reason=reason)], reason)


async def record_successful_outcome(*, source_id: str, note_sha256: str, graph_sha256: str,
                                    recipe_id: str, run: dict, rote_result: dict, procedure: dict) -> dict:
    """Record trusted server receipts; the caller must supply the current run.

    This is an internal adapter, not an endpoint accepting client-authored proof.
    Practice outcomes and incomplete/failed Rote work cannot publish live memory.
    """
    try:
        binding = _binding(source_id, note_sha256, graph_sha256, recipe_id, procedure)
        execution = _successful_execution(run, rote_result, binding)
        async with asyncio.timeout(30):
            await _current_binding(binding)
            envelope = await _write_outcome(dict(binding=binding, execution=execution,
                                                 execution_sha256=_digest(_json(execution))))
            await _current_binding(binding)
        return _outcome_result(envelope, "record_successful_outcome")
    except Exception as error:
        return _outcome_failure(error, "record_successful_outcome")


async def recall_successful_outcome(source_id: str, *, note_sha256: str, graph_sha256: str,
                                    recipe_id: str, procedure: dict) -> dict:
    """Recall only a successful outcome bound to the current exact rule/procedure."""
    try:
        binding = _binding(source_id, note_sha256, graph_sha256, recipe_id, procedure)
        async with asyncio.timeout(30):
            await _current_binding(binding)
            envelope = await _read_outcome(binding)
        return _outcome_result(envelope, "recall_successful_outcome")
    except Exception as error:
        return _outcome_failure(error, "recall_successful_outcome")

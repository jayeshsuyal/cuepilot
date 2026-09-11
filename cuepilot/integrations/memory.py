"""Cognee HTTP graph extraction and provenance-preserving local Hydra memory.

Importing this module performs no network requests or credential-file writes.
Public operations return evidence, never provider exceptions or credentials.
"""
from __future__ import annotations

import asyncio
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
        if label in fallback and target == "holding":
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


async def _persist_graph(graph, provenance, proof):
    """Publish the recipe last: interrupted imports cannot be recalled as complete."""
    receipt_id = uuid4().hex
    def vertex_id(original):
        return int(_digest(receipt_id + ":" + original)[:15], 16)
    envelope = {"version": 1, "receipt_id": receipt_id, "graph": graph, "provenance": provenance, "proof": proof}
    payload = _json(envelope)
    driver, database = _hydra_driver()
    async with driver:
        async with driver.session(database=database) as session:
            for node in graph["nodes"]:
                await _run(session, "CREATE (n:CuePilotMemory {id:$id, cognee_id:$original, source_id:$source, dataset_id:$dataset, payload:$payload})",
                           id=vertex_id(node["id"]), original=node["id"], source=provenance["source_id"],
                           dataset=provenance["dataset_id"], payload=_json(node))
            for edge in graph["edges"]:
                await _run(session, "MATCH (a {id:$source}), (b {id:$target}) CREATE (a)-[:COGNEE_RELATION {label:$label, dataset_id:$dataset}]->(b)",
                           source=vertex_id(edge["source"]), target=vertex_id(edge["target"]), label=edge["label"], dataset=provenance["dataset_id"])
            # Graph topology must read back before publishing the complete envelope.
            for edge in graph["edges"]:
                rows = await _run(session, "MATCH (a {id:$source})-[r:COGNEE_RELATION]->(b {id:$target}) RETURN r.label AS label",
                                  source=vertex_id(edge["source"]), target=vertex_id(edge["target"]))
                if not any(row.get("label") == edge["label"] for row in rows):
                    raise ProviderError("HydraDB graph traversal did not match the Cognee export.")
            recipe_id = vertex_id("receipt:" + receipt_id)
            await _run(session, "CREATE (r:CuePilotRecipe {id:$id, source_id:$source, receipt_id:$receipt, created_at:$created, payload:$payload})",
                       id=recipe_id, source=provenance["source_id"], receipt=receipt_id,
                       created=provenance["exported_at"], payload=payload)
            rows = await _run(session, "MATCH (r {id:$id}) RETURN r.payload AS payload", id=recipe_id)
            if len(rows) != 1 or rows[0].get("payload") != payload:
                raise ProviderError("HydraDB recipe read-back did not match the stored provenance.")
    return {"receipt_id": receipt_id, "graph_sha256": provenance["graph_sha256"],
            "node_count": len(graph["nodes"]), "edge_count": len(graph["edges"]), "read_back_verified": True}


async def ingest_note(note: str, source_id: str) -> dict:
    """In live mode, ingest one note, verify its extracted rule, and persist it.

    This operation consumes hosted Cognee credits when configured and called.
    The caller must authorize the live operation; no network runs at import time.
    """
    records, phase = [], "cognee"
    if not isinstance(note, str) or not note.strip() or len(note.encode()) > 16_000 or not _valid_id(source_id):
        reason = "A nonempty production note (at most 16 KB) and a valid source/show ID are required."
        return _result("blocked", [_record("cognee", "blocked", "ingest_note", reason=reason)], reason)
    try:
        async with asyncio.timeout(150):
            graph, provenance = await _extract_graph(note, source_id)
            records.append(_record("cognee", "verified", "ingest_and_export_graph", provenance | {"node_count": len(graph["nodes"]), "edge_count": len(graph["edges"])}))
            proof = _prove_template(graph)
            records.append(_record("cognee", "verified", "verify_cue_relationships", proof))
            phase = "hydradb"
            persisted = await _persist_graph(graph, provenance, proof)
            records.append(_record("hydradb", "verified", "persist_and_traverse_graph", persisted))
        return _result("verified", records, supported_template=TEMPLATE, recipe_id=persisted["receipt_id"],
                       source_id=source_id, note_sha256=provenance["note_sha256"], graph_sha256=provenance["graph_sha256"])
    except ProviderError as error:
        status, reason = ("blocked" if error.blocked else "failed"), error.reason
    except (TimeoutError, httpx.TimeoutException):
        status, reason = "failed", "Memory operation timed out; remote ingestion may still be running."
    except Exception:
        status, reason = "failed", "Memory provider operation failed; credentials and remote error bodies were withheld."
    records.append(_record(phase, status, "ingest_note", reason=reason))
    return _result(status, records, reason)


async def _read_recipe(show_id):
    driver, database = _hydra_driver()
    async with driver:
        async with driver.session(database=database) as session:
            return await _run(session, "MATCH (r:CuePilotRecipe {source_id:$source}) RETURN r.payload AS payload ORDER BY r.created_at DESC LIMIT 1", source=show_id)


async def recall_recipe(show_id: str) -> dict:
    """Read local Hydra memory, rechecking export provenance and graph proof."""
    try:
        if not _valid_id(show_id):
            raise ProviderError("A valid show ID is required.", blocked=True)
        async with asyncio.timeout(20):
            rows = await _read_recipe(show_id)
        if not rows:
            raise ProviderError("HydraDB has no verified Cognee recipe for this show; ingest its production note first.", blocked=True)
        envelope = json.loads(rows[0]["payload"])
        graph = _normalize_graph(envelope["graph"])
        provenance = envelope["provenance"]
        if (envelope.get("version") != 1 or provenance.get("provider") != "cognee"
                or provenance.get("source_id") != show_id or provenance.get("ingest_completed") is not True
                or provenance.get("graph_sha256") != _digest(_json(graph))
                or not re.fullmatch(r"[0-9a-f]{64}", provenance.get("note_sha256", ""))):
            raise ProviderError("HydraDB recipe provenance or graph digest failed verification.")
        UUID(provenance["dataset_id"])
        proof = _prove_template(graph)
        evidence = {"receipt_id": envelope["receipt_id"], "provenance": provenance, "proof": proof,
                    "node_count": len(graph["nodes"]), "edge_count": len(graph["edges"])}
        return _result("verified", [_record("hydradb", "verified", "recall_recipe", evidence)],
                       supported_template=TEMPLATE, recipe_id=envelope["receipt_id"], source_id=show_id,
                       note_sha256=provenance["note_sha256"], graph_sha256=provenance["graph_sha256"])
    except ProviderError as error:
        status, reason = ("blocked" if error.blocked else "failed"), error.reason
    except TimeoutError:
        status, reason = "failed", "HydraDB recipe recall timed out."
    except Exception:
        status, reason = "failed", "HydraDB recipe could not be read and verified."
    return _result(status, [_record("hydradb", status, "recall_recipe", reason=reason)], reason)

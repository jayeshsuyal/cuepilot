"""Write two synthetic graph nodes, traverse their edge, and verify HTTP + Bolt."""
import json
import os
import secrets
from pathlib import Path
from urllib.parse import quote
from urllib.request import Request, urlopen

from dotenv import load_dotenv
from neo4j import GraphDatabase

HERE = Path(__file__).resolve().parent
load_dotenv(HERE.parents[1] / ".env", override=False)


def contains_vertex(value, expected):
    if isinstance(value, dict):
        return (value.get("type") == "vertex_id" and value.get("value") == expected) or any(
            contains_vertex(child, expected) for child in value.values()
        )
    if isinstance(value, list):
        return any(contains_vertex(child, expected) for child in value)
    return False


def main():
    token_path = HERE / ".hydradb" / "auth-token"
    token = os.getenv("HYDRADB_AUTH_TOKEN") or (token_path.read_text().strip() if token_path.exists() else "")
    if not token:
        raise SystemExit("Start the local HydraDB container or configure HYDRADB_AUTH_TOKEN first.")
    bolt_url = os.getenv("HYDRADB_BOLT_URL", "bolt://127.0.0.1:7687")
    http_url = os.getenv("HYDRADB_HTTP_URL", "http://127.0.0.1:8443")
    graph_id = os.getenv("HYDRADB_GRAPH_ID", "default")
    namespace = os.getenv("HYDRADB_NAMESPACE", "default")
    source, destination = secrets.randbits(50), secrets.randbits(50)
    read_query = f"MATCH (a {{id: {source}}})-[:SETUP_VERIFIED]->(b) RETURN b.id AS id"
    with GraphDatabase.driver(bolt_url, auth=("neo4j", token), connection_timeout=10) as driver:
        driver.verify_connectivity()
        with driver.session(database=graph_id) as session:
            session.run(
                "CREATE (a {id: $source})-[:SETUP_VERIFIED]->(b {id: $destination})",
                source=source,
                destination=destination,
            ).consume()
            row = session.run(read_query).single(strict=True)
            if row["id"] != destination:
                raise RuntimeError("Bolt graph round trip returned the wrong vertex.")
    request = Request(
        f"{http_url.rstrip('/')}/v1/graphs/{quote(graph_id, safe='')}/query",
        data=json.dumps({"cell_id": os.getenv("HYDRADB_CELL_ID", "cell-0"), "query": read_query}).encode(),
        headers={"Authorization": f"Bearer {token}", "X-Graph-Namespace": namespace, "Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(request, timeout=20) as response:
        payload = json.load(response)
    if not contains_vertex(payload, destination):
        raise RuntimeError("HTTP query did not return the vertex just written through Bolt.")
    print("HydraDB PASS: wrote two synthetic nodes, traversed their relationship, and read the result through Bolt and HTTP.")


if __name__ == "__main__":
    main()

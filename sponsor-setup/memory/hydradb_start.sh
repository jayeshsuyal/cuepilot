#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
umask 077
mkdir -p .hydradb/store .hydradb/cache .hydradb/docker-config
if [[ ! -s .hydradb/auth-token ]]; then
  .venv/bin/python -c 'import secrets; from pathlib import Path; Path(".hydradb/auth-token").write_text(secrets.token_hex(32) + "\n")'
fi
docker_host="${HYDRADB_DOCKER_HOST:-unix://$HOME/.colima/hackathon/docker.sock}"
docker_cmd=(docker --config "$PWD/.hydradb/docker-config" --host "$docker_host")
container_name="hackathon-hydradb"
if "${docker_cmd[@]}" container inspect "$container_name" >/dev/null 2>&1; then
  "${docker_cmd[@]}" start "$container_name" >/dev/null
  echo "HydraDB container started. Run hydradb_smoke.py to verify queries."
  exit 0
fi
"${docker_cmd[@]}" run --detach \
  --name "$container_name" \
  --user "$(id -u):$(id -g)" \
  -p 127.0.0.1:7687:7687 \
  -p 127.0.0.1:8443:8443 \
  -p 127.0.0.1:9090:9090 \
  -v "$PWD/.hydradb:/data" \
  -e CLOUD_PROVIDER=local \
  -e LOCAL_PATH=/data/store \
  -e GRAPH_NAMESPACE=default \
  -e GRAPH_ID=default \
  -e GRAPH_CELL_ID=cell-0 \
  -e GRAPH_CELLS=cell-0 \
  -e GRAPH_NODE_ID=node-0 \
  -e GRAPH_BOLT_NODE_ADDRESSES=node-0=127.0.0.1:7687 \
  -e GRAPH_ADVERTISED_BOLT_ADDR=127.0.0.1:7687 \
  -e GRAPH_DATA_CACHE_DIR=/data/cache \
  -e GRAPH_AUTH_TOKEN_FILE=/data/auth-token \
  -e GRAPH_ALLOW_PLAINTEXT=true \
  -e GRAPH_DATA_CACHE_BYTES=67108864 \
  -e RUST_MIN_STACK=33554432 \
  "${HYDRADB_IMAGE:-ghcr.io/hydra-db/hydradb@sha256:db78309a233be54662db29744047e985a39b51c45a270d1a1f47c31a62cdb709}" >/dev/null
echo "HydraDB container created. Run hydradb_smoke.py to verify queries."

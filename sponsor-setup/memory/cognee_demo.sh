#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
export TELEMETRY_DISABLED=true
export LOG_LEVEL=ERROR
export DATA_ROOT_DIRECTORY="$PWD/.data_storage"
export SYSTEM_ROOT_DIRECTORY="$PWD/.cognee_system"
export COGNEE_LOGS_DIR="$PWD/logs"
exec .venv/bin/cognee-cli demo

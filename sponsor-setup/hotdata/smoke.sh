#!/usr/bin/env bash
# Run explicitly after authentication. Creates a temporary cloud database,
# uploads only the three synthetic rows in data/tickets.csv, and queries it.
set -euo pipefail
umask 077
hotdata_setup_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
hotdata_cli="$hotdata_setup_dir/bin/hotdata"
hotdata_catalog="hackathon_smoke_$(date -u +%Y%m%d%H%M%S)_${RANDOM}"
command -v python3 >/dev/null
hotdata_query_output="$(mktemp "${TMPDIR:-/tmp}/hotdata-query.XXXXXX")"
trap 'rm -f -- "$hotdata_query_output"' EXIT
hotdata_workspace_args=(--no-input)
if [[ -n "${HOTDATA_WORKSPACE:-}" ]]; then
  hotdata_workspace_args+=(--workspace-id "$HOTDATA_WORKSPACE")
fi
hotdata_cli_version="$("$hotdata_cli" --version)"
printf '%s\n' "$hotdata_cli_version"
# Read-only authentication check; fail before creating data if login is missing.
"$hotdata_cli" --no-input workspaces list -o json >/dev/null
"$hotdata_cli" databases create \
  "${hotdata_workspace_args[@]}" \
  --name "$hotdata_catalog" --catalog "$hotdata_catalog" \
  --table tickets --expires-at 1h
"$hotdata_cli" databases load \
  "${hotdata_workspace_args[@]}" \
  --catalog "$hotdata_catalog" --table tickets \
  --file "$hotdata_setup_dir/data/tickets.csv" --format csv
"$hotdata_cli" query \
  "${hotdata_workspace_args[@]}" \
  "SELECT COUNT(*) AS total_rows, SUM(CASE WHEN status = 'resolved' THEN 1 ELSE 0 END) AS resolved_rows FROM ${hotdata_catalog}.public.tickets" \
  -o json >"$hotdata_query_output"
# CLI 0.33.0 QueryResponse uses columns: string[], rows: JSON[][],
# row_count, and truncated. This shape is verified in the pinned official source:
# https://github.com/hotdata-dev/hotdata-cli/blob/v0.33.0/src/commands/query.rs
python3 - "$hotdata_query_output" "$hotdata_setup_dir" "$hotdata_catalog" "$hotdata_cli_version" <<'PY'
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

output_path, setup_dir, catalog, cli_version = sys.argv[1:]
try:
    result = json.loads(Path(output_path).read_text())
except (OSError, UnicodeError, json.JSONDecodeError):
    raise SystemExit("Hotdata FAIL: the CLI did not return valid query JSON.")

if not isinstance(result, dict):
    raise SystemExit("Hotdata FAIL: expected a query-result object.")
columns, rows = result.get("columns"), result.get("rows")
if (
    columns != ["total_rows", "resolved_rows"]
    or not isinstance(rows, list)
    or len(rows) != 1
    or not isinstance(rows[0], list)
    or len(rows[0]) != 2
    or any(type(value) is not int for value in rows[0])
    or result.get("row_count") != 1
    or result.get("truncated") is not False
    or result.get("total_row_count") not in (None, 1)
):
    raise SystemExit("Hotdata FAIL: query JSON has an unexpected or incomplete result shape.")
if rows[0] != [3, 2]:
    raise SystemExit("Hotdata FAIL: the live query counts did not match 3 total and 2 resolved rows.")

reports = Path(setup_dir) / "reports"
reports.mkdir(exist_ok=True)
receipt = {
    "completed_at": datetime.now(timezone.utc).isoformat(),
    "cli_version": cli_version,
    "verified": True,
    "dataset": "data/tickets.csv",
    "catalog": catalog,
    "database_expiry": "1h from creation",
    "total_rows": rows[0][0],
    "resolved_rows": rows[0][1],
    "query_run_id": result.get("query_run_id"),
    "result_id": result.get("result_id"),
}
temporary_receipt = reports / (catalog + ".json.tmp")
temporary_receipt.write_text(json.dumps(receipt, indent=2) + "\n")
temporary_receipt.replace(reports / "smoke-success.json")
print("Hotdata PASS: live query verified exactly 3 total rows and 2 resolved rows.")
print("Success receipt: sponsor-setup/hotdata/reports/smoke-success.json")
print("The temporary database expires one hour after creation.")
PY

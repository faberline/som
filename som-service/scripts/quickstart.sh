#!/usr/bin/env bash
# Runs the commands of som-service/QUICKSTART.md. Every command of a `sh` block
# in that file must appear here as a whole line (tests/it/quickstart_doc.rs).
set -euo pipefail
cd "$(dirname "$0")/../.."

EXPECTED_STATUS="200 201 200 200 204"

WORK="$(mktemp -d)"
trap 'kill $(jobs -p) 2>/dev/null || true; rm -rf "$WORK"' EXIT

cargo build --locked --manifest-path som-service/Cargo.toml
SOM=som-service/target/debug/som-service

$SOM serve --port 8767 &
sleep 2
curl -fsS http://127.0.0.1:8767/health
$SOM compile som-service/tests/it/fixtures/defer-seven.cnl > "$WORK/bundle.json"
$SOM assemble "$WORK/bundle.json" > "$WORK/client-files.json"
python3 som-service/scripts/write_files.py "$WORK/client" < "$WORK/client-files.json"
curl -fsS -X POST http://127.0.0.1:8767/assemble -d "{\"bundle\":$(cat "$WORK/bundle.json"),\"emit\":\"server\"}" > "$WORK/server-files.json"
python3 som-service/scripts/write_files.py "$WORK/server" < "$WORK/server-files.json"

cargo build --locked --manifest-path "$WORK/client/Cargo.toml" --target-dir "$WORK/client-target"
cargo build --manifest-path "$WORK/server/Cargo.toml" --target-dir "$WORK/server-target"

mkdir -p "$WORK/app/src"
cp som-service/scripts/quickstart/app_Cargo.toml.txt "$WORK/app/Cargo.toml"
cp som-service/scripts/quickstart/app_main.rs.txt "$WORK/app/src/main.rs"
cargo build --manifest-path "$WORK/app/Cargo.toml" --target-dir "$WORK/app-target"

"$WORK/app-target/debug/quickstart_app" 8080 &
sleep 2
{
  curl -sS -w '\nstatus %{http_code}\n' -X PUT http://127.0.0.1:8080/v1/queues/jobs -H 'content-type: application/json' -d '{"max_in_flight":100,"max_dispatch_per_tick":10,"max_dispatches_per_second":50,"max_burst_size":50,"lease_ttl_ms":30000,"retry_backoff_ms":1000}'
  curl -sS -w '\nstatus %{http_code}\n' -X POST http://127.0.0.1:8080/v1/queues/jobs/tasks -H 'content-type: application/json' -d '{"task_id":"hello","target":{"url":"http://127.0.0.1:9/run"},"schedule_at":"2026-11-06T00:00:00Z"}'
  curl -sS -w '\nstatus %{http_code}\n' http://127.0.0.1:8080/v1/queues/jobs/tasks/hello
  curl -sS -w '\nstatus %{http_code}\n' http://127.0.0.1:8080/v1/queues
  curl -sS -w '\nstatus %{http_code}\n' -X DELETE http://127.0.0.1:8080/v1/queues/jobs/tasks/hello
} | tee "$WORK/requests.log"
kill $(jobs -p)

ACTUAL_STATUS="$(grep '^status ' "$WORK/requests.log" | cut -d' ' -f2 | tr '\n' ' ' | sed 's/ $//')"
if [ "$ACTUAL_STATUS" != "$EXPECTED_STATUS" ]; then
  echo "quickstart: expected status codes '$EXPECTED_STATUS', got '$ACTUAL_STATUS'" >&2
  exit 1
fi
echo "quickstart: ok"

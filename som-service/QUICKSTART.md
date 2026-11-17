# Quickstart

Go from an empty checkout to a running Defer server and one request of each
kind. Run every command from the repository root. You need Rust 1.96.1
(`rust-toolchain.toml` selects it), `curl` and `python3`.
`scripts/quickstart.sh` runs exactly these commands, and CI runs that script.

## 1. Build the compiler

```sh
WORK="$(mktemp -d)"
cargo build --locked --manifest-path som-service/Cargo.toml
SOM=som-service/target/debug/som-service
```

## 2. Start the compiler service and compile the CNL

The CNL fixture binds seven operations: `read_task_status`, `create_task`,
`put_queue`, `cancel_task`, `list_queues`, `get_queue` and `list_tasks`.

```sh
$SOM serve --port 8767 &
sleep 2
curl -fsS http://127.0.0.1:8767/health
$SOM compile som-service/tests/it/fixtures/defer-seven.cnl > "$WORK/bundle.json"
```

## 3. Assemble the client and the server

```sh
$SOM assemble "$WORK/bundle.json" > "$WORK/client-files.json"
python3 som-service/scripts/write_files.py "$WORK/client" < "$WORK/client-files.json"
curl -fsS -X POST http://127.0.0.1:8767/assemble -d "{\"bundle\":$(cat "$WORK/bundle.json"),\"emit\":\"server\"}" > "$WORK/server-files.json"
python3 som-service/scripts/write_files.py "$WORK/server" < "$WORK/server-files.json"
```

## 4. Build the generated client and server

The generated client has a `Cargo.lock`, so it builds with `--locked`. The
generated server has none, so it builds without it.

```sh
cargo build --locked --manifest-path "$WORK/client/Cargo.toml" --target-dir "$WORK/client-target"
cargo build --manifest-path "$WORK/server/Cargo.toml" --target-dir "$WORK/server-target"
```

## 5. Run the server

The generated server is a library: you give it a `Handler`. The small app in
`som-service/scripts/quickstart/` implements the handler with an in-memory
store.

```sh
mkdir -p "$WORK/app/src"
cp som-service/scripts/quickstart/app_Cargo.toml.txt "$WORK/app/Cargo.toml"
cp som-service/scripts/quickstart/app_main.rs.txt "$WORK/app/src/main.rs"
cargo build --manifest-path "$WORK/app/Cargo.toml" --target-dir "$WORK/app-target"
"$WORK/app-target/debug/quickstart_app" 8080 &
sleep 2
```

## 6. Send one request of each kind

Each command prints the body and then `status <code>`.

Update (set the queue policy):

```sh
curl -sS -w '\nstatus %{http_code}\n' -X PUT http://127.0.0.1:8080/v1/queues/jobs -H 'content-type: application/json' -d '{"max_in_flight":100,"max_dispatch_per_tick":10,"max_dispatches_per_second":50,"max_burst_size":50,"lease_ttl_ms":30000,"retry_backoff_ms":1000}'
```

Expected status: 200

Create (add a task):

```sh
curl -sS -w '\nstatus %{http_code}\n' -X POST http://127.0.0.1:8080/v1/queues/jobs/tasks -H 'content-type: application/json' -d '{"task_id":"hello","target":{"url":"http://127.0.0.1:9/run"},"schedule_at":"2026-11-06T00:00:00Z"}'
```

Expected status: 201

Read (one task status):

```sh
curl -sS -w '\nstatus %{http_code}\n' http://127.0.0.1:8080/v1/queues/jobs/tasks/hello
```

Expected status: 200

List (all queues):

```sh
curl -sS -w '\nstatus %{http_code}\n' http://127.0.0.1:8080/v1/queues
```

Expected status: 200

Delete (cancel the task):

```sh
curl -sS -w '\nstatus %{http_code}\n' -X DELETE http://127.0.0.1:8080/v1/queues/jobs/tasks/hello
```

Expected status: 204

## 7. Stop

```sh
kill $(jobs -p)
```

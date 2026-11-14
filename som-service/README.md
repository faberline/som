# Defer task-status client compiler

`som-service` (Rust) is the one supported path. It compiles a fixed English
request (CNL) or typed JSON into a Rust client and an axum server for Defer.
The compiler supports four operations: `task_status`, `task_create`,
`queue_put` and `task_cancel`. List operations (`queue_list`, `queue_get`,
`task_list`) are planned. The generated client preserves the native status
types, HTTP errors and response bytes.

The CNL example is [contract/task-status.cnl](contract/task-status.cnl).
Its typed input and expected DSL are fixed beside it. The local crate `cnl/` parses the source.
SOM checks names and types, selects the approved operation and emits code.

The client supports Scheduled, Leased, Succeeded, DeadLettered and Canceled.
Queue and task IDs are string arguments. The client encodes each path segment.
Empty, dot and dot-dot segments return an explicit client error before sending.
This is a client transport limit. Defer itself can store some of these names.
The client does not follow redirects. Its request timeout is 30 seconds.

## Run

Build from this repository root. Rust 1.96.1 was used for local acceptance.

```sh
cargo build --locked --manifest-path som-service/Cargo.toml
cargo run --locked --manifest-path som-service/Cargo.toml -- serve --port 8767
```

In another terminal, compile and assemble:

```sh
cargo run --locked --manifest-path som-service/Cargo.toml -- compile som-service/contract/task-status.cnl > /private/tmp/task-status.bundle.json
cargo run --locked --manifest-path som-service/Cargo.toml -- assemble /private/tmp/task-status.bundle.json > /private/tmp/task-status.files.json
```

The assemble response maps four relative paths to file contents:
`Cargo.toml`, `Cargo.lock`, `src/lib.rs` and `README.md`.
The generated library exposes:

```rust
let client = DeferClient::new("http://127.0.0.1:8080", Some("runtime-token"))?;
let status = client.read_task_status("jobs", "invoice-42").await?;
```

Supply the real base URL and bearer token at runtime. CNL contains no token.
Pass `None` for Defer open mode. HTTP failures retain status, raw bytes and an
optional native error object. Interrupted responses retain the received status
and body prefix. Debug and Display omit response bodies and credentials.

The new HTTP service exposes `/health`, `/compile` and `/assemble`.
Use `som.cnl.service.v1` or `som.prompt-json.service.v1` at `/compile`.
The existing `som` service and its old versions stay unchanged.

## CI

Workflow `som-service`, job `som-service checks` (check name `som-service / som-service checks`),
runs fmt, clippy and tests with `--locked` on every pull request that touches `som-service/**`
or the workflow, and on push to main. The repository owner can make this check required
in the branch protection of main.

## Fixed sources and ownership

- CNL syntax crate: `som-service/cnl/`, moved from nlp revision `2f7e519d4037033d0ef69629c8f8bdfba837794a`.
- Defer HTTP contract: `5f7adac1fe1333b33e3ec69a92b5ca4a634a97d0`.
- Defer's Core dependency: `029e765efaac4080bb9e8283005a9c6792953f54`.
- Old SOM still uses NLP `8ddb1f6df55eec0764ca50772b2c038441361727`.

The `cnl/` crate owns `parse_service_document` and service syntax report (CNL versions 1 and 2). Its old
API2 and Pest entry points remain supported. SOM owns the typed contract,
source maps, diagnostics, DSL, checked bundle assembly and Rust templates.
Defer owns task state, authorization and execution. This client adds no scheduler.

The service compiler has its own Cargo lock and asset family. Its source
fingerprint includes the actual `cnl/` sources, compiler code, contracts and client
templates. Saved bundles must match that fingerprint. The old compiler files,
two old lockfiles, assets and tests are retained unchanged.

## Local acceptance

Run the full local check with Python 3.11 or later:

```sh
/opt/homebrew/bin/python3 som-service/scripts/replay.py
```

This command uses the retained baseline under `.local/cnl-parser-replacement`
and the source guard under `.local/defer-task-status`. It writes a new temporary
evidence directory. The default native cache is
`/private/tmp/sift-pr29-cargo.giYPjY`. Override it with `--native-cargo-home`.
Use `--help` for the source, cache and evidence paths.

The command fetches fixed public build inputs, then runs locked offline builds.
It checks CNL and typed input, DSL, source maps, HTTP/CLI, saved bundles,
generated-client transport, the real pinned Defer router, both CNL entry points,
and the old 1,930-record SOM comparison. No expected result is regenerated.

The native harness uses a controlled clock and committed Raft commands.
It does not dispatch real target requests. It exercises the same public router,
authorization code and state transitions as the pinned Defer source.
See [CONTRACT.md](CONTRACT.md), [ACCEPTANCE.md](ACCEPTANCE.md), and
[acceptance/README.md](acceptance/README.md) for the exact checks and boundaries.

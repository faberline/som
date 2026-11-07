# Rust backend quickstart

## Development test host — 2026-10-28

SOM uses fixed rules: `CNL → DSL → Code`.
It needs no model training or Apple chip.
For repository development, edit locally and run the build, test and service commands below on a GitHub Actions runner.
Do not start them on the shared development computer.
In a runner, `127.0.0.1` refers to that runner's services.
Read [the current architecture](architecture.md) for test and platform rules.

This guide walks through verifying the pinned Rust toolchain and offline crate cache, generating a standalone Rust backend project with SOM's local service, and building, testing, and running it offline.

## 1. What you get

SOM generates standalone Rust (`axum` + `sqlx`) backend crates for prompt versions v1 through v7 across the five product shapes defined in [product-layout.json](../som-rs/contracts/rust-output-v1/product-layout.json):

- **`resource` (v1–v5 without capabilities, 11 files):** single-resource CRUD (v1–v4) or parent/child CRUD with `POST {parent}/with-children` atomic batch creation (v5) over PostgreSQL (`Cargo.toml`, `Cargo.lock`, `src/main.rs`, `src/lib.rs`, `src/models.rs`, `src/schemas.rs`, `src/api.rs`, `src/db.rs`, `migrations/0001_<revision>.sql`, `tests/api.rs`, and `README.md`).
- **`capability_postgresql` (v5 with capabilities or v6, `database postgresql`, 12 files):** adds `src/integrations.rs` for authentication (`jwt`, `firebase`, or `google_oauth`), object storage (`none`, `s3`, or `gcs`), and workflows/exports (`json_export`, `gzip_export`, `csv`, `decimal_summary`, or `statistics`), plus the `som_app` operator subcommands (`serve`, `migrate upgrade`, `migrate downgrade`, `check-config`).
- **`capability_mongodb` (v5 with capabilities or v6, `database mongodb`, 11 files):** same capability surface backed by MongoDB collections (`_som_counters` and `_som_revision`) without a SQL `migrations/` directory.
- **`flow_postgresql` (v7, `database postgresql`, 12 files):** multi-step business flows with authorization, non-nested database transactions, conditional branches (`if`), compensation/undo steps, idempotency, and leases in `src/flows.rs` (row and schema structs live together in `src/schemas.rs`).
- **`flow_mongodb` (v7, `database mongodb`, 11 files):** v7 flows over MongoDB without a SQL `migrations/` directory.

Browse the public CNL and JSON prompt examples (`v1`–`v7` and `v7-branch-null`) in [examples/index.json](../som-rs/contracts/compiler-v1/examples/index.json).

### Fixed crate identity and byte-identical manifests

As stated in [product-layout.json](../som-rs/contracts/rust-output-v1/product-layout.json), the generated Cargo package, library, and binary name is always `som_app`. The prompt's `package` name appears only in `README.md` (and in phase 2 cookie/Redis prefix strings). Because no prompt identifier enters `Cargo.toml`:

- Every `resource` product (v1–v5 without capabilities) emits `Cargo.toml` and `Cargo.lock` byte-for-byte equal to [reference/Cargo.toml](../som-rs/contracts/rust-output-v1/reference/Cargo.toml) and [reference/Cargo.lock](../som-rs/contracts/rust-output-v1/reference/Cargo.lock).
- Every capability and flow product emits `Cargo.toml` and `Cargo.lock` byte-for-byte equal to [reference-phase2/Cargo.toml](../som-rs/contracts/rust-output-v1/reference-phase2/Cargo.toml) and [reference-phase2/Cargo.lock](../som-rs/contracts/rust-output-v1/reference-phase2/Cargo.lock).

### Scope limits and target notes you meet first

- **`query_parameter_collision` (`limit` or `offset` filter field in v3):** In prompt v3, list routes expose the boolean filter field by its own name alongside fixed pagination parameters `limit` and `offset`. A v3 `query.filter_field` named `limit` or `offset` makes `?limit=5` ambiguous (`RS12_CNL_V3_FILTER_LIMIT` and `RS13_JSON_V3_FILTER_OFFSET` in [negative-cases.json](../som-rs/contracts/rust-output-v1/negative-cases.json); see also [scope.json](../som-rs/contracts/rust-output-v1/scope.json) and [phase2/naming.json](../som-rs/contracts/rust-output-v1/phase2/naming.json)). Since `compiler-v1` amendment 003 ([rust-compiler-contract-amendment-003.json](../som-rs/evidence/rust-compiler-contract-amendment-003.json), issue #58), `compiler-v1` refuses `limit` and `offset` as v3 filter fields for every target before target-specific checking, so `som write` exits `2` with HTTP 400 `INVALID_VALUE` at `/query/filter_field` (`prompt.query.filter_field: name collides with a generated Python identifier`); the Rust target's `query_parameter_collision` rule (`RS12`/`RS13`) is reached only after `compiler-v1`.
- **PostgreSQL `JSONB` signed zero (`-0.0`):** In v7 flows (`literal_type:float` in [phase2/constructs.json](../som-rs/contracts/rust-output-v1/phase2/constructs.json)), the compiled bundle and generated Rust code preserve negative zero (`-0.0_f64`, serialized as `-0.0` in HTTP responses), whereas PostgreSQL `JSONB` normalizes `-0.0` to `0.0` when persisting flow run records in `_som_flow_runs`.

## 2. Check the toolchain and the crates

The following pins and Homebrew paths record the earlier accepted host.
They are not an Apple hardware requirement for SOM.
M12 must qualify compatible runner tools before these exact commands work on another host.
Keep version, output and behavior checks when replacing host paths or binary hashes.
[toolchain.json](../som-rs/contracts/rust-output-v1/toolchain.json) retains those tool and dependency records:

- **Rust toolchain:** Homebrew Rust `1.96.1` at `/opt/homebrew/Cellar/rust/1.96.1/bin` (`rustc 1.96.1 (31fca3adb 2026-06-26)`, LLVM `22.1.8`, `cargo 1.96.1`, `rustfmt 1.9.0 (31fca3adbe 2026-06-26)`, `clippy 0.1.96 (31fca3adbe 2026-06-26)`, edition `2024`, host target `aarch64-apple-darwin`).
- **Phase 1 crates ([reference/Cargo.toml](../som-rs/contracts/rust-output-v1/reference/Cargo.toml), 204 packages in [reference/Cargo.lock](../som-rs/contracts/rust-output-v1/reference/Cargo.lock)):** `axum =0.8.9`, `chrono =0.4.44`, `serde =1.0.229`, `serde_json =1.0.154`, `sqlx =0.8.6`, and `tokio =1.53.3`, plus dev-dependencies `http-body-util =0.1.5` and `tower =0.5.3`.
- **Phase 2 crates ([reference-phase2/Cargo.toml](../som-rs/contracts/rust-output-v1/reference-phase2/Cargo.toml), 335 packages in [reference-phase2/Cargo.lock](../som-rs/contracts/rust-output-v1/reference-phase2/Cargo.lock)):** adds `base64 =0.22.1`, `flate2 =1.1.10`, `hmac =0.12.1`, `hyper =1.12.0`, `hyper-rustls =0.27.10`, `hyper-util =0.1.21`, `mongodb =3.9.1`, `ring =0.17.14`, and `sha2 =0.10.9`.
- **PostgreSQL and Python:** PostgreSQL `17.7 (Homebrew)` at `/opt/homebrew/opt/postgresql@17/bin` and Python `3.12.11` at `/Users/chrischeng/.pyenv/versions/3.12.11/bin/python3`.

Run [check_toolchain.py](../som-rs/scripts/rust_toolchain/check_toolchain.py) to verify every pinned Rust binary and the `rustfmt` SHA-256 against [toolchain.json](../som-rs/contracts/rust-output-v1/toolchain.json):

```sh
python3 som-rs/scripts/rust_toolchain/check_toolchain.py
```

A passing run exits `0` and prints:

```text
ok rustc
ok cargo
ok rustfmt
ok cargo-fmt
ok cargo-clippy
ok clippy-driver
ok formatter
```

Run [check_vendor.py](../som-rs/scripts/rust_vendor/check_vendor.py) to verify the offline vendor directory against the union of [reference/Cargo.lock](../som-rs/contracts/rust-output-v1/reference/Cargo.lock) and [reference-phase2/Cargo.lock](../som-rs/contracts/rust-output-v1/reference-phase2/Cargo.lock) (352 distinct crates):

```sh
python3 som-rs/scripts/rust_vendor/check_vendor.py --vendor .local/rust-output-phase2/vendor-002
```

A passing run prints `ok <name>-<version>` for each of the 352 crates, followed by `ok vendor.json`, `ok config.toml`, and the summary line:

```text
ok ahash-0.8.12
ok allocator-api2-0.2.21
...
ok zerovec-derive-0.11.6
ok zmij-1.0.23
ok vendor.json
ok config.toml
summary ok=352 problems=0
```

### Pointing Cargo at the vendored crates offline

As generated by [vendor_crates.py](../som-rs/scripts/rust_output_acceptance/vendor_crates.py), a vendor bundle directory (`<vendor-dir>`, such as `/absolute/path/to/.local/rust-output-phase2/vendor-002`) contains both `<vendor-dir>/vendor/` (the unpacked crates with `.cargo-checksum.json`) and `<vendor-dir>/cargo-home/config.toml`. You can either set `CARGO_HOME` to that `cargo-home` directory:

```sh
export CARGO_HOME="/absolute/path/to/.local/rust-output-phase2/vendor-002/cargo-home"
```

or copy the `[source]` replacement into `.cargo/config.toml` inside your generated project directory:

```toml
[source.crates-io]
replace-with = "vendored-sources"

[source.vendored-sources]
directory = "/absolute/path/to/.local/rust-output-phase2/vendor-002/vendor"

[net]
offline = true
```

## 3. Generate the project

First check whether the local service on `127.0.0.1:8765` is running:

```sh
curl http://127.0.0.1:8765/healthz
```

If `127.0.0.1:8765` is not listening, start the current default from the repository root:

```sh
som-rs/runtime/current/som serve --assets som-rs/compiler-assets/current --port 8765
```

`som write` requires the v0.2.0 default or later. `som-rs/runtime/current/som` is that default (candidate-007) since #81. With an older binary, use `som compile --target rust` plus `som assemble`.

Run `som write` with `--target rust` to compile a `.cnl` or `.prompt.json` source, assemble the Rust bundle, and write the generated Cargo project into a new or empty directory:

```sh
som-rs/runtime/current/som write som-rs/contracts/compiler-v1/examples/v1.cnl --target rust --out ~/work/items_rust
```

On success (`exit 0`), `som write` prints one JSON summary line on `stdout`:

```json
{"files":11,"out":"/Users/you/work/items_rust","status":"ok","target":"rust"}
```

Exit codes:

- `0` — compiled, assembled, and wrote all files to `--out`.
- `1` — local input, transport, or `--out` path error (`LOCAL_ERROR` JSON printed to `stderr`, no files written).
- `2` — HTTP rejection from `/v1/compile` or `/v1/assemble` (structured diagnostic JSON printed to `stderr`, no files written).

## 4. Build, test and run

Every generated Rust product includes a `README.md` naming the prompt package, prompt version, routes, environment variables, and offline commands from [toolchain.json](../som-rs/contracts/rust-output-v1/toolchain.json) and [phase2/services.json](../som-rs/contracts/rust-output-v1/phase2/services.json).

### Resource products (v1–v5 without capabilities)

In a generated `resource` directory (such as `~/work/items_rust` above), point `CARGO_HOME` (or `.cargo/config.toml`) at the offline vendor bundle and use the commands in the generated `README.md`:

```sh
cd ~/work/items_rust
export PATH="/opt/homebrew/Cellar/rust/1.96.1/bin:/opt/homebrew/opt/postgresql@17/bin:$PATH"
export CARGO_HOME="/absolute/path/to/.local/rust-output-phase2/vendor-002/cargo-home"

# Build offline against the pinned Cargo.lock
cargo build --locked --offline

# Run the generated integration tests (requires a local PostgreSQL database)
TEST_DATABASE_URL="postgres://postgres:postgres@127.0.0.1:5432/app_test" \
  cargo test --locked --offline -- --test-threads=1

# Run the HTTP service (migrates on startup and listens on SOM_APP_ADDR)
DATABASE_URL="postgres://postgres:postgres@127.0.0.1:5432/app" \
  SOM_APP_ADDR="127.0.0.1:8000" \
  cargo run --locked --offline
```

Environment variables for `resource` products:

- `DATABASE_URL` (required by `som_app`): PostgreSQL connection URL (`postgres://...` or `postgresql://...`). Missing `DATABASE_URL` prints one line to `stderr` and exits with status `2`.
- `SOM_APP_ADDR` (optional, default `127.0.0.1:8000`): socket address `som_app` binds when serving HTTP.
- `TEST_DATABASE_URL` (required by `tests/api.rs`): PostgreSQL connection URL for integration tests; each test resets `schema public`, runs `MIGRATOR`, and drives `som_app::router` in-process.

To run the full `som write --target rust` generation, local PostgreSQL lifecycle, offline `cargo build` and `cargo test`, `/healthz` check, and `POST` smoke requests for a `resource` product (v1–v5 without capabilities) in a single command, use [pg_run.py](../som-rs/scripts/rust_quickstart/pg_run.py):

```sh
python3 som-rs/scripts/rust_quickstart/pg_run.py \
  --som som-rs/runtime/current/som \
  --url http://127.0.0.1:18080 \
  --cnl som-rs/contracts/compiler-v1/examples/v1.cnl \
  --out ~/work/items_rust_pg_run \
  --vendor .local/rust-output-phase2/vendor-002 \
  --pg-port 55432 \
  --app-port 18081 \
  --create '/items={"title":"first"}'
```

### Capability and flow products (v5 with capabilities, v6, and v7)

Capability and flow products (such as [v6.cnl](../som-rs/contracts/compiler-v1/examples/v6.cnl) and [v7.cnl](../som-rs/contracts/compiler-v1/examples/v7.cnl)) build and test with the same offline Cargo flags (`cargo build --offline` / `cargo build --locked --offline` and `cargo test --offline -- --test-threads=1`) and expose the operator subcommands defined in [phase2/services.json](../som-rs/contracts/rust-output-v1/phase2/services.json) (`serve` does not auto-migrate):

```sh
som_app migrate upgrade
som_app migrate downgrade
som_app check-config
som_app serve
```

- **PostgreSQL shapes (`capability_postgresql`, `flow_postgresql`):** read `DATABASE_URL` and `SOM_APP_ADDR` (default `127.0.0.1:8000`) plus the capability settings listed in the generated `README.md` (for example `JWT_PUBLIC_KEY_PEM`, `JWT_ISSUER`, `JWT_AUDIENCE`), and read `TEST_DATABASE_URL` in `cargo test --offline`.
- **MongoDB shapes (`capability_mongodb`, `flow_mongodb`):** all public examples in `som-rs/contracts/compiler-v1/examples/` use `database postgresql`; when you generate a prompt with `Use database mongodb.`, its generated `README.md` reads `MONGODB_URL` and `MONGODB_DATABASE` (plus `SOM_APP_ADDR`) at runtime and `MONGODB_URL` plus `SOM_TEST_DATABASE_NAME` in `cargo test --offline`.

## 5. Change it and generate again

Generated Rust product files are deterministic for a given prompt and compiler release. When you edit your `.cnl` or `.prompt.json` file and run `som write --target rust` again, pass a new or empty `--out` directory (`som write` refuses any existing non-empty `--out` directory so it never overwrites existing files).

If you saved an intermediate `som.dsl-bundle.v2` bundle with `som compile --target rust`, remember that every bundle records the exact asset SHA-256 descriptors of the compiler release and target contract that produced it. Assembling an older saved bundle after an asset or default change is refused with `ASSET_HASH_MISMATCH`, so re-run `som write --target rust` (or `som compile --target rust`) from the `.cnl` or `.prompt.json` source.

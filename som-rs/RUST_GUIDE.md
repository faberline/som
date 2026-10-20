# Rust compiler guide

This guide describes the local default on port 8765: rust-output-candidate-006, switched on `2026-10-20T08:39:04.671848+00:00` (#57, #58).
It serves the Python target and the phase 2 Rust target. The post-switch check passed.
The #56 default (rust-output-candidate-004) was switched in at `2026-10-20T03:14:10.975162+00:00`.
The phase 1 Rust output default (rust-output-candidate-003 of #51) was switched in at `2026-10-16T05:54:52.297481+00:00`.
The compiler default before it (candidate 003 of #38–#44) was switched in at `2026-10-15T03:08:08.737006+00:00`.
The legacy M2 runtime, model and block links remain saved for rollback.

## Use the installed CLI

Run these commands from `/Users/chrischeng/faberlines/som`.
The installed CLI is `som-rs/runtime/current/som`. Its default URL is `http://127.0.0.1:8765`.
Do not start a second server on 8765.

```sh
som-rs/runtime/current/som compile som-rs/contracts/compiler-v1/examples/v7.cnl > /private/tmp/som-example.bundle.json
som-rs/runtime/current/som assemble /private/tmp/som-example.bundle.json > /private/tmp/som-example.files.json
som-rs/runtime/current/som generate som-rs/contracts/compiler-v1/examples/v7.prompt.json
```

Compile prints the bare bundle on stdout. Assemble prints the complete existing
status/files response. Generate keeps its existing JSON response interface.
The service returns files. The calling agent writes and checks them in its project.

Compile and assemble exit 0 on success. Local input/transport errors exit 1.
HTTP rejection exits 2. Their full error JSON goes to stderr.
Generate retains its response on stdout and exit 2 on HTTP rejection.

The installed path points to the immutable candidate binary
`runtime/versions/52658e13af1ac08659ef1dda7e333634f4ae3893731304e09edec6ca5f9b87c2/som`.
The previous binaries `runtime/versions/d2de75223496665cd563b51daa51741faa337c9cc2d46d8af8cc596b9f399b9b/som` (#56),
`runtime/versions/05f1bfe6eef7045f054b1323a193e8f7bea1b4afe4f287a47cd0d0f9347ae11c/som` (#51)
and `runtime/versions/3fad3cb7eafb35274f6a37f9a8a371f93807a6b5fedf3e9fe0e074cbf5bfef02/som` (#38–#44) are kept for recovery.
The legacy M2 binary also accepts the old `generate` command.

## Names that collide with generated Python

compiler-v1 refuses a resource, field or filter name that collides with an identifier the generated Python product
already uses (#58). The answer is INVALID_VALUE, HTTP 400, with the detail
`prompt.<path>: name collides with a generated Python identifier`, for the Python and the Rust target alike.
[selection-policy.json](contracts/compiler-v1/selection-policy.json) `python_name_collisions` lists the names per
version and database; a v3 filter field named limit or offset is one of them. Rename the field and compile again.
A field or foreign key named `self` is valid (#57).

Bundles carry the hashes of the assets that compiled them. A bundle compiled by an earlier default answers
ASSET_HASH_MISMATCH on assemble; compile its source again.

## Rust output

Add `--target rust` to compile, or `"target": "rust"` to the `/v1/compile` body. Without it the target is Python.
Assemble reads the target from the bundle and returns the files of a complete Cargo product.
The Rust target admits every prompt that compiler-v1 admits, v1 to v7: one-resource CRUD, parent and child,
v5 and v6 capabilities (PostgreSQL or MongoDB, authentication, object storage, workflows) and v7 flows
with transactions, branches and undo. See sections 10 and 11 of the [Rust output contract](contracts/rust-output-v1/CONTRACT.md).
The Rust target refuses nothing that compiler-v1 admits.
Valid sources are in [target-examples.json](contracts/rust-output-v1/target-examples.json) and the phase 2
[case inventory](contracts/rust-output-v1/phase2/case-inventory.json).

```sh
som-rs/runtime/current/som compile SOURCE.cnl --target rust > /private/tmp/som-rust.bundle.json
som-rs/runtime/current/som assemble /private/tmp/som-rust.bundle.json > /private/tmp/som-rust.files.json
```

`GET /healthz` lists `targets` and, under `rust_target`, the Rust asset hashes and the pinned rustfmt identity and SHA-256.
Rust assemble needs the rustfmt in `contracts/rust-output-v1/toolchain.json` (`/opt/homebrew/Cellar/rust/1.96.1`).
If that file is gone or changed, Rust assemble answers ASSET_UNAVAILABLE and Python is not affected.

### Rust products

Each product is one Cargo package named som_app. Its shape and file list follow
[product-layout.json](contracts/rust-output-v1/product-layout.json): 11 or 12 files.
Capability and flow products lock 335 crates in
[reference-phase2/Cargo.lock](contracts/rust-output-v1/reference-phase2/Cargo.lock).
Build them with that lock and the pinned toolchain. Offline builds need a vendored crate directory
(`scripts/rust_output_acceptance/vendor_crates.py`); otherwise cargo fetches from crates.io.

They read the same settings as the Python product
([services.json](contracts/rust-output-v1/phase2/services.json)), and `SOM_APP_ADDR` sets the listen address.
Subcommands: `serve`, `migrate upgrade`, `migrate downgrade` and `check-config`. `serve` does not migrate.
PostgreSQL products record their migration in `_som_rust_migrations`.

Known differences from the Python product:

- 422 bodies omit pydantic `input` and `ctx` members. Some Python 500 responses are 401 or 503.
- `rediss://` is not supported. Of the Redis URL query options only `db` is read.
- S3 credentials come from `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY` and `AWS_SESSION_TOKEN`, or from the shared
  credentials file (`AWS_SHARED_CREDENTIALS_FILE` or `~/.aws/credentials`, profile `AWS_PROFILE`).
  GCS credentials come from `GOOGLE_APPLICATION_CREDENTIALS` (service account or authorized user) or the metadata server, not `CLOUDSDK_CONFIG`.
- Firebase production tokens are verified with the securetoken JWKS URL, not the x509 cert endpoint.
- Timestamps print six fraction digits and `Z` for UTC, as pydantic does on a UTC host.
- A PostgreSQL flow journal stores JSONB, which drops the sign of `-0.0`, as the Python flow product does.
- README line widths differ from the Python README.

## HTTP requests

`GET /healthz` reports purpose, compiler/grammar versions, asset hashes,
formatter identity, loaded block families, body limits, and model_loaded=false.

`POST /v1/compile` requires an explicit format. It never guesses the input type.

For CNL, set `format` to `som.cnl.v1`.
Set `source` to the complete CNL text.
Use `contracts/compiler-v1/examples/v7.cnl` as a valid source.

For JSON, set `format` to `som.prompt-json.v1`.
Set `prompt` to the complete existing prompt object.
Use `contracts/compiler-v1/examples/v7.prompt.json` as a valid prompt.
Do not send that prompt file alone to `/v1/compile`.
Prompt versions v1–v7 retain their existing semantics.
Success returns `{"status":"ok","bundle":{...}}`.

`POST /v1/assemble` requires `{"bundle":{...}}` with the complete saved bundle.
Success returns `{"status":"ok","files":{"relative/path.py":"full source"}}`.
The bundle includes the full prompt, existing DSL, and versioned asset descriptors.
It needs matching local assets. A modified or incomplete plan is rejected.
The assembler does not add missing plan nodes or repair bindings.

`POST /v1/generate` accepts the existing prompt object directly.
It keeps the complete status/files interface. No format wrapper is used there.

Duplicate JSON keys and malformed shapes fail.
Each diagnostic has `code`, `prompt_pointer`, `cause`, and `allowed_form`.
Source fields are `line`, `column`, `end_line`, `end_column`, `byte_start`,
and `byte_end`. They are direct members of the diagnostic object.
Lines and columns start at 1. Byte offsets start at 0.
The end position is exclusive. Byte offsets count UTF-8 bytes.
Columns count Unicode scalar characters. They do not count UTF-8 bytes.
Use the frozen
[API schema](contracts/compiler-v1/api.schema.json) for exact fields.

## Write CNL

Use the exact clauses in [grammar.ebnf](contracts/compiler-v1/grammar.ebnf).
Start from [the versioned examples](contracts/compiler-v1/examples/index.json).
The [field map](contracts/compiler-v1/field-statement-map.json) maps every existing
prompt field to a clause. The full fixed-English request is not a JSON wrapper.

The calling agent supplies all business choices. Clauses cover fields, queries,
ordered operations, capabilities, references, branches, transactions, and undo.
Data strings can contain Unicode. JSON scalar literals preserve their types.
Negative float zero stays distinct in the compiler and generated Python literal.
Executable source and SQL clauses are not accepted. Literal strings stay data.
Missing or conflicting facts fail without repair.

### Semantic rules the grammar does not show

A clause can parse and still fail these rules. They come from
[section 3](contracts/compiler-v1/CONTRACT.md#3-prompt-families-and-exact-omission-rules)
and [section 4](contracts/compiler-v1/CONTRACT.md#4-existing-semantic-rules-stay-binding)
of the contract. Check them before the first compile.

- `Limit query results to N.` accepts only N from 20 to 100.
  The generated product uses 20 as its default page size.
- `Filter exactly on field F.` needs F to be an optional, nonunique bool
  with default false. Any other field fails with "invalid query field".
- A date filter also needs that boolean exact filter.
  The resource must then have exactly one date field.
  That field must be optional and nonunique with default null.
  Otherwise the compile fails with "date filtering requires a boolean filter and one date".
- Required filters depend on the version. v2 needs a nonunique string filter.
  v3 needs a boolean filter. v4 needs both a boolean filter and a date filter.
  v5–v7 can use the "Use no … filter." clauses instead.
- `decimal_summary` and `statistics` need an int or float child value field.
  `decimal_summary` also needs a required str child group field.
- `json_export`, `gzip_export`, and `csv` take no value or group field.
  Write "Use no workflow value field." and "Use no workflow group field.".
- A required field takes no default clause. Optional v3–v7 fields default to null.
  The only other default is an explicit false on an optional bool.
- Each related resource has 1 to 8 fields.
  Parent and child must use different tables and different paths.
- A path has one to three lowercase segments. Hyphens and underscores are allowed.

Each rejection returns `INVALID_VALUE`. Its diagnostic gives the `line` and
`prompt_pointer` of the failing clause.

### Parent and child routes

The child path is not served at the root. It is nested under one parent row.
Python and Rust products expose the same routes.
With parent path P and child path C, the routes are:

| Route | Meaning |
|---|---|
| `POST P`, `GET P` | Create a parent, list parents |
| `GET`, `PATCH`, `DELETE P/{parent_id}` | Read, update, delete one parent |
| `POST P/{parent_id}C`, `GET P/{parent_id}C` | Create a child, list one parent's children |
| `GET`, `PATCH`, `DELETE P/{parent_id}C/{child_id}` | Read, update, delete one child |
| `POST P/with-children` | Create a parent and its children together |
| `GET P/{parent_id}/export` | Run the selected workflow, when one is set |
| `GET /auth/me` | Return the caller identity, when authentication is set |
| `GET /healthz` | Health check, always present |

For example, `Use path "/projects".` on the parent and `Use path "/todos".` on
the child give `/projects/{parent_id}/todos` and `/projects/{parent_id}/todos/{child_id}`.
A v6 or v7 prompt serves only the operations it selects.

## Limits and output meaning

Compile and generate each accept at most 262144 HTTP body bytes.
Assemble accepts at most 2097152 body bytes. A successful compile must fit the
canonical assemble envelope in that bound. This is a transport limit.
No model sequence-token limit applies to the new path.

Existing file, block, flow, and legacy byte bounds remain. DSLv1 has at most 8
files. DSLv2/v3 has at most 12 files. A whole plan has at most 64 blocks.
Flow bounds remain 8 inputs, 8 actions, 2 branches, depth 2, and 2 nonnested transactions.
Legacy output byte gates remain. Compact output bytes are observed measurements.

Pure literal branch comparisons can compile to True or False. The admitted
comparison meaning is unchanged. Input and step references are not folded.
An empty 1 == 1 branch has no reachable false arm.

Existing PostgreSQL JSONB journals can normalize a persisted float-zero sign.
That runtime representation limit does not change the exact compiler, bundle,
canonical hash, or generated-literal checks. Runtime evidence records each sign loss.
Integer, float, Boolean, and all other values remain distinct in the checks.

## Build a separate development version

These commands build a model-free binary. They do not promote it.

```sh
cargo build --release --locked --no-default-features --manifest-path som-rs/Cargo.toml --bin som --bin som-compiler-assets
som-rs/target/release/som-compiler-assets --destination /private/tmp/som-new-compiler-assets --formatter .local/rust-compiler-goal/candidate-003/assets/ruff
som-rs/target/release/som serve --assets /private/tmp/som-new-compiler-assets --port 18766
```

Use a fresh asset destination and a free loopback port. The controller must own
the test process and its cleanup. The asset package uses approved current local
blocks and the pinned native formatter. Production does not start Python.
Python reference, quality, and behavior tools remain acceptance tools.

Do not start a development binary on 8765. The promoted default owns that port.
A new build needs its own same-source acceptance, M2 restore, canary, and rollback checks.
Root owns default switching and closeout. Read [RUST_HANDOFF.md](RUST_HANDOFF.md).

## Recovery command

To return to the #56 default (`d2de7522…`), use the #57 restore command in
[RUST_HANDOFF.md](RUST_HANDOFF.md#recovery-command). The exact preserved M2 launch command and owner checks are there too.

# Rust compiler guide

This guide describes implemented candidate 003. Final acceptance is pending.
Legacy M2 remains the default on port 8765. New compiler commands need the
candidate URL until a verified default switch is complete.

## Use the candidate CLI

Run these commands from `/Users/chrischeng/faberlines/som`.
The controller owns the candidate server. Do not start a second server on 18765.
Check its live owner before use. The saved launch receipt names that port.

```sh
.local/rust-compiler-goal/candidate-003/som compile som-rs/contracts/compiler-v1/examples/v7.cnl --url http://127.0.0.1:18765 > /private/tmp/som-example.bundle.json
.local/rust-compiler-goal/candidate-003/som assemble /private/tmp/som-example.bundle.json --url http://127.0.0.1:18765 > /private/tmp/som-example.files.json
.local/rust-compiler-goal/candidate-003/som generate som-rs/contracts/compiler-v1/examples/v7.prompt.json --url http://127.0.0.1:18765
```

Compile prints the bare bundle on stdout. Assemble prints the complete existing
status/files response. Generate keeps its existing JSON response interface.
The service returns files. The calling agent writes and checks them in its project.

Compile and assemble exit 0 on success. Local input/transport errors exit 1.
HTTP rejection exits 2. Their full error JSON goes to stderr.
Generate retains its response on stdout and exit 2 on HTTP rejection.

After verified promotion, the installed CLI will be
`som-rs/runtime/current/som`. Its default URL is `http://127.0.0.1:8765`.
That installed path and promotion evidence are pending. Do not assume they exist.

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

Do not start a development binary on 8765. Complete same-source acceptance,
fresh M2 restore, compiler canary, and failure/signal rollback before promotion.
Root owns default switching and closeout. Read [RUST_HANDOFF.md](RUST_HANDOFF.md).

# SOM local service

The model-free Rust compiler candidate is implemented. Full acceptance is
pending. The accepted legacy M2 model service remains the default.
The [dated progress index](evidence/rust-compiler-progress-2026-10-15.json)
records the implementation landing and the checks that remain open.

Read [RUST_GUIDE.md](RUST_GUIDE.md) for current compiler commands and HTTP shapes.
Read [RUST_STATUS.md](RUST_STATUS.md) for measured results and pending gates.
[RUST_ROADMAP.md](RUST_ROADMAP.md) defines the two current milestones.
[RUST_HANDOFF.md](RUST_HANDOFF.md) defines the next controller work.

The compiler parses fixed English CNL or an explicit versioned JSON prompt.
It emits a complete typed DSL bundle. The existing Rust backend checks that
bundle and expands approved blocks into Python files. No new Python parser,
model inference, or Python production process is needed.

## Historical model reports

The following build, training, milestone, and model-budget statements describe
the previous roadmap. They are not current
compiler acceptance results or instructions to resume M3 training.
The retained legacy runtime is still available for rollback.
> Historical model-roadmap document. On 2026-10-13 the human replaced this plan with
> [the Rust roadmap](RUST_ROADMAP.md). Old milestone records were archived and deleted.
> Read [the current handoff](RUST_HANDOFF.md). Preserve the results below; do not resume old M3 training.
> The Rust compiler candidate is implemented. Independent acceptance remains pending.
> The default remains legacy M2.

The development default now uses `flow-m2-candidate/smoke-r31-001` and
`flow-v7-compact`. See [CURRENT.md](CURRENT.md) for the persistent executable,
startup record and rollback proof. All twelve seen M2 flow cases and 314
legacy outputs pass the switch checks. M3 reference admission passed 2,700/2,700.
M3 training is interrupted. Its last complete development check passed 3/300.
See [M3_STATUS.md](M3_STATUS.md) for the current state and remaining gates.

The following pilot sections describe the older v1–v4 model path.

The flagship agent sends a `som.prompt.v1` through `som.prompt.v4` JSON object. It names the package,
resource, typed fields, ordered logic, errors, test cases, and migration.
See [prompt.json](examples/prompt.json) and
[query-prompt.json](examples/query-prompt.json). The agent does not choose block IDs.
The [v1 prompt schema](schemas/som.prompt.v1.schema.json) and
[v2 query schema](schemas/som.prompt.v2.schema.json) record the earlier wire
formats. The [v3 service schema](schemas/som.prompt.v3.schema.json) records
the standalone service request.
The [v4 date schema](schemas/som.prompt.v4.schema.json) adds an optional date
field and an exclusive date-before list filter.

The Rust model decoder returns `som.dsl.v1` or `som.dsl.v2`. The DSL contains only relative file paths,
approved block IDs, order, typed parameters, and local references. The
assembler rejects unknown fields, unknown blocks, unsafe paths, and source
code in the DSL. The Rust service returns a map of file paths to full source.
It never writes those files.
Typed references let the model point to prompt fields, such as
`{"$ref":"resource.fields"}`. The service copies the validated value into
the DSL before assembly. It also expands `{package}` in file paths.
The [v1 DSL schema](schemas/som.dsl.v1.schema.json) and
[v2 DSL schema](schemas/som.dsl.v2.schema.json) record the outer shapes.
The block registry checks each parameter type at runtime.

The first pilot model classifies each of five CRUD logic steps into an
approved API block. Its decoder orders those blocks inside a hash-checked DSL
template. That template names the required model, schema, test, and migration
blocks. The prompt must contain each of the five exact `logic` sentences in
the [schema](schemas/som.prompt.v1.schema.json), once each. The array order
sets the API block order. The `tests` array must contain the one exact test
sentence in the schema. Other wording returns `UNSUPPORTED_LOGIC` or
`UNSUPPORTED_TEST`. The new [query family](QUERY_V2.md) adds exact string
filtering and bounded page size. Its model handles both prompt versions.
The [standalone service family](SERVICE_V3.md) adds app startup, database
sessions, boolean filtering, defaults, timestamps, dependency files, and a
full Alembic workflow. Other task shapes need new training data and evaluation.
The [date-filter service](SERVICE_V4.md) was the previous development default.
The [current state record](CURRENT.md) names the M2 model, blocks, and checks.

The separate [business candidate](BUSINESS.md) accepts internal `som.prompt.v5`.
It supports two related resources and selects eleven operation blocks.
It adds typed identity, object storage, MongoDB, and workflow capabilities.
Its default promotion depends on the full local and paired token gates.

The [composable candidate](COMPOSABLE.md) adds internal `som.prompt.v6`.
It uses a variable operation list and emits all files, blocks, bindings, and dependencies.
The Rust validator checks each composition without filling missing choices.
This round evaluates a candidate only. `current` stays unchanged for later Claude checks.
Correctness passed. The latest Codex round saved 58.21% of total tokens overall.
Recipes saved 4.39% and missed the per-app target. The candidate is not accepted.
See the [complete result](evals/composable-2026-10-01/REPORT.md).

The separate [Python quality candidate](QUALITY.md) keeps those original
outputs. It adds strict typing and fixed quality checks for the generated
apps, tests and migrations. Its Codex comparison uses `gpt-6.1-sol`.
Full quality and behavior acceptance runs before token measurement.

## Build and run

Run these commands from the repository root. The current model is at
`som-rs/models/current`. It includes `config.json`, `tokenizer.json`,
`model.safetensors`, `dsl_template.json`, and `manifest.json`.
The service checks every file hash and the block library hash at startup.

```sh
CARGO_HOME=/private/tmp/som-cargo cargo build --release --manifest-path som-rs/Cargo.toml
som-rs/target/release/som serve
som-rs/target/release/som generate som-rs/examples/prompt.json
som-rs/target/release/som generate som-rs/examples/query-prompt.json
som-rs/target/release/som generate som-rs/examples/service-prompt.json
som-rs/target/release/som generate som-rs/examples/date-service-prompt.json
```

`serve` binds only `127.0.0.1:8765`. `GET /healthz` reports loaded model and
block state. `POST /v1/generate` accepts the prompt JSON. It returns
`{"status":"ok","files":{"relative/path.py":"complete source"}}` on success.
It returns `status`, `code`, and `detail` on rejection. `generate` only calls
the local HTTP service and prints this JSON. It exits with code 2 on rejection.

## Train and check

The [current training guide](../som-core/docs/product/training.md) explains
the composed candidate and its measured scope.
It also records the remaining research and acceptance work.

`som-train` starts with new tokenizer and GPT2 style model weights. It reads
validated prompt and DSL pairs. The operation mode exports five model files and their
hashes. `--hours` cannot exceed 168 hours. It does not read the held-out file.

```sh
UV_CACHE_DIR=/private/tmp/som-uv-cache uv sync --project som-core
som-core/.venv/bin/som-train --mode operation_blocks --data som-core/data/v1/train.jsonl --blocks som-code-python/data/blocks/v1/blocks.json --output /absolute/path/to/model --steps 3000 --context 256 --width 64 --heads 4 --layers 2 --max-new-tokens 1
PYTHONPATH=som-core/src som-code-python/.venv/bin/python som-core/scripts/verify_v1_postgres.py --heldout-index 0
CARGO_HOME=/private/tmp/som-cargo cargo test --manifest-path som-rs/Cargo.toml
python som-core/scripts/eval_v1.py --model-manifest som-rs/models/crud-v1/manifest.json --output /private/tmp/som-eval.json
```

The earlier standalone-service training input was
`som-core/data/crud-query-service-v3/train.jsonl`. Its block library is
`som-code-python/data/blocks/v3/blocks.json`. The frozen service tests are
in `som-core/data/service-v3/heldout.jsonl`. The training and test operation
orders do not overlap.

The frozen data contains 280 training tasks and 50 held-out block orders.
The orders do not overlap. The Python reference output passed generated
unittest, independent CRUD behavior, and Alembic upgrade/downgrade checks in
a real local PostgreSQL database for all 50 cases. Rust matches Python bytes
for all 50 combinations and rejection classes for 12 invalid cases.

The original CRUD model passed all 50 held-out tasks through the Rust HTTP
service. The independent PostgreSQL check also required the requested block
order and tested migrations. The final 50-case run had a maximum measured
generation time of 0.041 seconds, below the 60 second limit. The idle service
used about 53 MB RSS, below 1 GB. These numbers apply
to this small pilot model on this Mac.

The original [evaluation receipt](evals/crud-v1-2026-09-29.json) records the
model, block library, and held-out data hashes.

The v2 model handled the first two prompt versions. It matched the Python output
byte for byte for all 100 frozen cases. All 50 new query cases passed generated
unittest, independent API behavior, and real PostgreSQL migration checks.
The [exact-output receipt](evals/crud-query-v2-exact-2026-09-29.json) and
[query behavior receipt](evals/query-v2-2026-09-29.json) record the evidence.

The [scope growth guide](SCOPE_GROWTH.md) puts complete business correctness first.
Model size, memory, complete response time, and flagship tokens remain measured.
The previous 50 MB, 1 GB, and 100 ms values are historical references.
They are not current limits on capability expansion.
The [current budget receipt](evals/service-regression-v4-100-budget-2026-09-30.json)
records a 0.541 MB model, 53.559 MB peak resident memory, and 24.272 ms p95
on 100 warm sequential requests. This measurement excludes
PostgreSQL checks or flagship reasoning.

The [paired flagship evaluation](evals/flagship-paired-2026-09-29.json)
uses four held-out tasks with different field shapes. Both agents generated
each task directly and through SOM in fresh, isolated directories. The same
syntax, unittest, and independent SQLite checks ran in each path. All 16
packages then passed independent PostgreSQL checks. Full SOM responses
entered the agents' tool context and their installed files matched the
responses byte for byte. Prompt, returned source, checks, and retries count
toward total flagship tokens.

Codex used 518,301 tokens directly and 210,136 with SOM, a 59.5% reduction.
Claude Code used 671,585 directly and 305,026 with SOM, a 54.6% reduction.
Both exceeded the 30% target on these four v1 tasks. The earlier
[one-task pilot](evals/flagship-pilot-2026-09-29.json) had an unstructured
check workflow and increased Codex token use. The paired evaluation uses the
same prepared checks for both paths. The result applies to this narrow CRUD
contract; it does not establish token savings for other task families.

The [query-family paired evaluation](evals/flagship-query-paired-2026-09-29.json)
used four new held-out tasks and the same check protocol. All 16 outputs
passed independent PostgreSQL checks. Codex used 514,613 tokens directly and
268,077 with SOM, a 47.9% reduction. Claude Code used 691,165 directly and
293,652 with SOM, a 57.5% reduction. Both met the 30% target on this sample.
One Codex task saved only 2.9%; the four-task total does not promise savings
on every task. Prompt, full response, checks, and retries are included.

The v3 model matched all 150 old and new frozen outputs byte for byte. Its
50 new service tasks passed independent PostgreSQL checks. Each check used
a separate database. It tested generated unittest, migration upgrade and
downgrade, CRUD, filtering, pagination, defaults, timestamps, and restart
persistence. See the [exact receipt](evals/crud-query-service-v3-exact-2026-09-29.json)
and [service receipt](evals/service-v3-postgres-2026-09-29.json).

Four paired standalone services used the same task and checks for both
methods. Todo is case 0. The corrected Todo task uses a maximum page size
of 100. All 16 agent outputs passed independent PostgreSQL checks. Prompt,
full returned source, checks, and retries are counted. Token totals are:

| Case | Codex direct | Codex with SOM | Saved | Claude direct | Claude with SOM | Saved |
|---|---:|---:|---:|---:|---:|---:|
| Todo | 483,317 | 73,584 | 84.8% | 586,578 | 66,479 | 88.7% |
| Note | 306,992 | 120,664 | 60.7% | 573,761 | 86,840 | 84.9% |
| Ticket | 303,937 | 125,287 | 58.8% | 443,974 | 86,949 | 80.4% |
| Job | 246,861 | 151,437 | 38.7% | 497,398 | 114,589 | 77.0% |
| Total | 1,341,107 | 470,972 | 64.9% | 2,101,711 | 354,857 | 83.1% |

The [v3 paired receipt](evals/flagship-service-v3-paired-2026-09-29.json)
records the instruction hashes, usage, response equality, and checks.
These percentages describe this four-task sample.

Run `som-core/scripts/prepare_flagship_v1.py` to prepare paired task folders.
Pass `--cases som-core/data/query-v2/heldout.jsonl` for the query family.
Run `som-core/scripts/report_flagship_v1.py --verify-postgres` to recalculate
usage and independent correctness from completed agent runs. Pass the same
`--cases` path and `--model som-rs/models/crud-query-v2` for query tasks.
These Python scripts are evaluation tools. Production generation stays in Rust.
Use `prepare_flagship_v3.py` and `report_flagship_v3.py` for the v3 service
family.

The old Python `som` console entry point is removed. The old Python MCP
module cannot start its direct `candidate.py` writer from the command line.

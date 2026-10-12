# Date-filter service development record

`som.prompt.v4` adds one optional `date` field to the standalone CRUD service.
The field defaults to `null`. `query.date_field` names that field. The existing
boolean filter remains. `GET` on the list route accepts `before=YYYY-MM-DD`.
It selects rows with a date strictly earlier than that day. Rows with a `null`
date do not match. Both filters can be used together. The migration adds an
index on `(date_field, id)` beside the existing `(filter_field, id)` index.
These indexes are approved defaults for this task family. They have not been
benchmarked as the fastest choice for every workload.

The [Todo prompt](examples/date-service-prompt.json) and
[prompt schema](schemas/som.prompt.v4.schema.json) show the request. The service
still returns `som.dsl.v2` through the Rust assembler. It does not write files.
The new block library has 34 entries. All 28 v3 entries keep their old bytes.
The new list block is `api.list.boolean.date`. The model chooses among all
operation tokens in its tokenizer. The Rust service checks that the five
chosen blocks fit the validated prompt.

The model reads the five fixed logic sentences. Validated resource and field
values fill typed template slots. The new cases test both date-field assembly
and the model's selection of the new list operation. They do not test new
wording outside the v4 prompt contract.

Python froze 280 v4 training tasks and 50 new held-out tasks before training.
The held-out set includes Todo and ten other resource names. It varies fields
and operation order. The [corpus manifest](../som-core/data/date-service-v4/manifest.json)
records the task and block hashes. The combined training set also contains
the old v1, v2, and v3 tasks. The v4 model was trained from zero for 3,000
steps and includes a tokenizer, weights, template, and file hashes.

## Run the current service

From the repository root:

```sh
CARGO_HOME=/private/tmp/som-cargo cargo build --release --manifest-path som-rs/Cargo.toml --locked --offline
som-rs/target/release/som serve
som-rs/target/release/som generate som-rs/examples/date-service-prompt.json
```

The service listens only on `127.0.0.1:8765`. The `current` model and block
paths point to the frozen directories tested here. See the
[current state record](CURRENT.md) for the live default and separate checks.

## Evidence

The [exact output receipt](evals/crud-query-date-service-v4-exact-2026-09-30.json)
records 200/200 matches to Python reference bytes. These include the 150 old
held-out tasks. The [PostgreSQL receipt](evals/date-service-v4-postgres-2026-09-30.json)
records the 50 new services. Each case runs generated unittest, migration
upgrade and downgrade, API checks, and a restart persistence check against an
isolated database. The [budget receipt](evals/date-service-v4-budget-2026-09-30.json)
records a 0.541 MB model directory, 56.787 MB peak resident memory, and
34.247 ms warm sequential p95 over 200 requests. Startup time and flagship
agent work are outside this timing.

The rebuilt Rust decoder also served the old v3 model with its original block
library. It matched all 150 old outputs in the
[compatibility receipt](evals/v3-new-decoder-compat-2026-09-30.json).

## Previous and new tasks together

The [100-case manifest](../som-core/data/service-regression-v4/manifest.json)
joins the previous 50 v3 structural stress tasks with the 50 new v4 date
tasks. The same v4 model and block library served every request. All 100
matched Python reference bytes in the
[combined exact receipt](evals/service-regression-v4-100-exact-2026-09-30.json).
All 100 passed isolated PostgreSQL behavior and migration checks in the
[combined behavior receipt](evals/service-regression-v4-100-postgres-2026-09-30.json).
The [combined budget receipt](evals/service-regression-v4-100-budget-2026-09-30.json)
records 24.272 ms warm sequential p95, 53.559 MB peak resident memory, and
the same 0.541 MB model directory. No test database remained after the run.

The [date-task paired run](evals/date-service-paired-2026-09-30/results.md) is now
complete. All 16 final services passed. Codex used 55.6% fewer total tokens;
Claude Code used 75.7% fewer. These results cover four paired tasks per agent.
The Journal diagnostic repeat stays separate from the official result.

# Standalone CRUD service contract

> Historical research or model-era record. The current path uses fixed algorithms: `CNL → DSL → Code`.
> It needs no model training, model weights or Apple chip.
> The plans, default claims and commands below describe their original period.
> They do not direct current training or replace current compiler acceptance.
> Read [the current architecture](../docs/architecture.md).

`som.prompt.v3` asks for a complete FastAPI service. The agent names the
resource, fields, five ordered operations, test, migration, and boolean query.
It does not name code blocks. See the [Todo request](examples/service-prompt.json)
and [prompt schema](schemas/som.prompt.v3.schema.json).

The Todo request has a required `title`, a `description` with a `null` default,
and a `completed` flag with a `false` default. `created_at` and `updated_at`
are managed by the service and are read only. The list route accepts an exact
`completed` filter. It orders by ID. Its limit defaults to 20 and has a maximum
of 100. The migration adds an index on `(completed, id)`. That index is a
correct starting choice. It is not a claim that every data size uses it fastest.

The Rust model selects the five API operation blocks. A versioned template
adds the app, database, model, schemas, unittest, Alembic files, dependencies,
and instructions. The model and block library hashes are checked at startup.
The Rust assembler validates `som.dsl.v2` and returns complete file contents.
It never writes those files to the agent's project. The [DSL schema](schemas/som.dsl.v2.schema.json)
describes its structure. The assembler allows up to 12 safe `.py`, `.toml`,
`.ini`, or `.md` files, 64 block instances, and 65,536 UTF-8 output bytes.

The generated app reads `DATABASE_URL` and requires a `postgresql+psycopg`
URL. It reports a clear error if the value is absent. `/healthz` checks the
database connection. POST returns 201. DELETE returns 204. A missing row
returns 404. Invalid input returns 422. PATCH changes only submitted fields.
The app rejects `null` for `completed` and other nonnullable fields. Generated
unittest reads `TEST_DATABASE_URL` and requires an isolated PostgreSQL database.

## Run the service

From the repository root:

```sh
CARGO_HOME=/private/tmp/som-cargo cargo build --release --manifest-path som-rs/Cargo.toml
som-rs/target/release/som serve --model som-rs/models/crud-query-service-v3 --blocks som-code-python/data/blocks/v3/blocks.json
som-rs/target/release/som generate som-rs/examples/service-prompt.json
```

The service listens on `127.0.0.1:8765` by default. The CLI sends a local
HTTP request. The service supports the old v1 and v2 prompts and DSL too.

## Check the fixed case set

The Python reference assembler and the Rust assembler must return the same
bytes. The independent service check starts a new PostgreSQL database for
each case. It runs generated unittest, Alembic upgrade and downgrade, API
behavior, and a restart persistence check.

```sh
PYTHONPATH=som-core/src som-core/.venv/bin/python -m pytest som-core/tests/test_v1.py som-core/tests/test_v3.py -q
CARGO_HOME=/private/tmp/som-cargo cargo test --manifest-path som-rs/Cargo.toml --locked --offline
```

The [exact output receipt](evals/crud-query-service-v3-exact-2026-09-29.json),
[PostgreSQL receipt](evals/service-v3-postgres-2026-09-29.json), and
[resource receipt](evals/service-budget-v3-2026-09-29.json) record the current
case hashes and measurements. Python trains the model and runs reference checks.
Production generation uses Rust only.

## Untrained structural stress set

The [stress manifest](../som-core/data/service-stress-v3/manifest.json) freezes
50 new tasks. They use ten resource names and two to six fields. The cases
combine string, integer, float, and boolean fields with required values,
`null` defaults, `false` defaults, unique values, and different route and
operation orders. No prompt duplicates an earlier training or held-out prompt.
The model and block library stayed unchanged.

The current Rust service matched the Python reference bytes for all 50 tasks.
All 50 passed generated unittest, migration, and independent PostgreSQL API
checks. The [exact receipt](evals/service-stress-v3-exact-2026-09-30.json) and
[behavior receipt](evals/service-stress-v3-postgres-2026-09-30.json) record the
same frozen case hash. The [budget receipt](evals/service-stress-v3-budget-2026-09-30.json)
records a 0.538 MB model, 51.888 MB peak resident memory, and 48.17 ms warm
sequential p95.

This stress run did not repeat the Codex and Claude Code token comparison.
The earlier token saving result applies to its four paired service tasks.

This set tests field rendering, schemas, routes, and migrations. The current
model reads each of the five fixed operation sentences. The Rust template
copies field values into typed block slots. Field variety therefore does not
test whether the model understands new operations. A new operation needs a
new prompt contract, an approved block, and a new held-out evaluation.

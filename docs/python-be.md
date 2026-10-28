# Python backend quickstart

## Development test host — 2026-10-28

SOM uses fixed rules: `CNL → DSL → Code`.
It needs no model training or Apple chip.
For repository development, edit locally and run the build, test and service commands below on a GitHub Actions runner.
Do not start them on the shared development computer.
In a runner, `127.0.0.1` refers to that runner's services.
Read [the current architecture](architecture.md) for test and platform rules.

This guide walks through generating and running a standalone Python backend project with SOM's local service.

## 1. What you get

SOM generates Python backend code for prompt versions v1 through v7:

- **One resource (v1–v4):** single-resource CRUD over PostgreSQL (`models.py`, `schemas.py`, `api.py`, Alembic migration, and `unittest` suite; prompts v3 and v4 include the standalone FastAPI `app.py`, `database.py`, `alembic.ini`, `pyproject.toml`, and `README.md`).
- **Parent and child (v5–v7):** two related resources (`parent` and `child`) with foreign-key restriction on parent delete, plus atomic `POST {parent}/with-children` batch creation.
- **Capabilities (v5/v6):** explicit choices for database (`postgresql` or `mongodb`), authentication (`jwt`, `firebase`, or `google_oauth`), object storage (`none`, `s3`, or `gcs`), and export/import workflows (`json_export`, `gzip_export`, `csv`, `decimal_summary`, or `statistics`).
- **Flows (v7):** multi-step business flows over the parent/child and capability base, with explicit authorization, non-nested database transactions, conditional branches (`if`), and compensation/undo steps.

Browse the complete set of versioned CNL and JSON prompt examples in [examples/index.json](../som-rs/contracts/compiler-v1/examples/index.json).

### Scope limits you meet first

- **`max_limit 20..100`:** Every `Limit query results to N.` clause (or `query.max_limit` field) requires an integer `N` between `20` and `100` inclusive. Generated endpoints use `20` as their default page size.
- **Generated Python identifier collisions (#58):** The compiler refuses resource, field, filter, or foreign-key names that collide with identifiers already bound in the generated Python module (for example `sa`, `Mapped`, or a v3 filter field named `limit` or `offset`). The complete per-version and per-database collision tables live under `python_name_collisions` in [selection-policy.json](../som-rs/contracts/compiler-v1/selection-policy.json).
- **Child paths nested under the parent:** In parent/child prompts (v5–v7), the child `path` is never served at the root. With parent path `/projects` and child path `/todos`, child routes are served at `/projects/{parent_id}/todos` and `/projects/{parent_id}/todos/{child_id}`.

## 2. Start the local service

First check whether the local service on `127.0.0.1:8765` is already running:

```sh
curl http://127.0.0.1:8765/healthz
```

If `127.0.0.1:8765` is not listening, start the current default from the repository root:

```sh
som-rs/runtime/current/som serve --assets som-rs/compiler-assets/current --port 8765
```

Do not start a second server when `127.0.0.1:8765` is already up, and start it again after a reboot.

## 3. Write CNL

Save a minimal v1 request to `my.cnl` (copied verbatim from [v1.cnl](../som-rs/contracts/compiler-v1/examples/v1.cnl)):

```text
Use SOM CNL version 1.
Use prompt version 1.
Use package items.
Begin resource.
Use resource name Item.
Use table items.
Use path "/items".
Field title is required str and is unique.
End resource.
Use migration revision rev0000.
Use no previous migration revision.
Begin routes.
List rows in id order.
Create a row. Return 409 for a unique value conflict.
Update supplied fields. Return 404 if the row does not exist.
Get a row by id. Return 404 if it does not exist.
Delete a row. Return 404 if it does not exist.
End routes.
Begin checks.
Check CRUD, missing id, and unique conflict when supported.
End checks.
End request.
```

See [Write CNL in RUST_GUIDE.md](../som-rs/RUST_GUIDE.md#write-cnl), [grammar.ebnf](../som-rs/contracts/compiler-v1/grammar.ebnf), and [field-statement-map.json](../som-rs/contracts/compiler-v1/field-statement-map.json) for every clause and version rule.

## 4. Generate the project

`som write` requires the v0.2.0 default or later. `som-rs/runtime/current/som` is that default (candidate-007) since #81. With an older binary, use `som compile` plus `som assemble`.

Run `som write` to compile `my.cnl`, assemble the bundle, and write the generated files into a fresh or empty output directory:

```sh
som-rs/runtime/current/som write my.cnl --out ~/work/my_app
```

On success (`exit 0`), `som write` prints one JSON summary line on `stdout`:

```json
{"files":5,"out":"/Users/you/work/my_app","status":"ok","target":"python"}
```

Exit codes:

- `0` — compiled, assembled, and wrote all files to `--out`.
- `1` — local input, transport, or `--out` path error (`LOCAL_ERROR` JSON printed to `stderr`, no files written).
- `2` — HTTP rejection from `/v1/compile` or `/v1/assemble` (structured diagnostic JSON printed to `stderr`, no files written).

If a clause violates a semantic rule—for example, changing line 8 of `my.cnl` to `Field sa is required str and is unique.`—`som write` exits `2` and prints an `INVALID_VALUE` refusal to `stderr`:

```json
{
  "code": "INVALID_VALUE",
  "detail": "prompt.resource.fields[0].name: name collides with a generated Python identifier",
  "diagnostics": [
    {
      "allowed_form": "Rename it; this name shares a scope with an identifier of the generated Python code.",
      "byte_end": 148,
      "byte_start": 146,
      "cause": "prompt.resource.fields[0].name: name collides with a generated Python identifier",
      "code": "INVALID_VALUE",
      "column": 7,
      "end_column": 9,
      "end_line": 8,
      "line": 8,
      "prompt_pointer": "/resource/fields/0/name"
    }
  ],
  "status": "error"
}
```

## 5. Run it

Prompt v1 and v2 output is router, model, schema, migration, and test files to add to an existing FastAPI app; it has no `README.md` and cannot be run on its own, so use v3 or later for a standalone project.

Generate the standalone [v3.cnl](../som-rs/contracts/compiler-v1/examples/v3.cnl) example (`package todos`) into its own new directory and follow its generated `README.md`, which carries the exact `uv sync`, migration, `uvicorn`, and `unittest` instructions along with the `DATABASE_URL` and `TEST_DATABASE_URL` environment variables (both using the `postgresql+psycopg` scheme):

```sh
som-rs/runtime/current/som write som-rs/contracts/compiler-v1/examples/v3.cnl --out ~/work/todos_app
cd ~/work/todos_app
export DATABASE_URL="postgresql+psycopg://postgres:postgres@127.0.0.1:5432/todos_dev"
export TEST_DATABASE_URL="postgresql+psycopg://postgres:postgres@127.0.0.1:5432/todos_test"

uv sync
uv run alembic upgrade head
uv run uvicorn todos.app:app
uv run python -m unittest discover -s tests -v
```

To run the full local PostgreSQL lifecycle, migrations, unit tests, `/healthz` check, and `POST` smoke requests in a single command, use [pg_run.py](../som-rs/scripts/python_quickstart/pg_run.py) (for example, `python3 som-rs/scripts/python_quickstart/pg_run.py --som som-rs/runtime/current/som --url http://127.0.0.1:18080 --cnl som-rs/contracts/compiler-v1/examples/v3.cnl --out ~/work/todos_pg_run --pg-port 55432 --app-port 18081 --create '/todos={"title":"first"}'`).

For parent/child and JWT-enabled prompts (v5–v7), such as [v6.cnl](../som-rs/contracts/compiler-v1/examples/v6.cnl) (`package app_projects`), pass two `--create` arguments and reference `{1.id}` in the child path so the child row is created under the newly created parent's `id`:

```sh
python3 som-rs/scripts/python_quickstart/pg_run.py \
  --som som-rs/runtime/current/som \
  --url http://127.0.0.1:18080 \
  --cnl som-rs/contracts/compiler-v1/examples/v6.cnl \
  --out ~/work/tasks_pg_run \
  --pg-port 55432 \
  --app-port 18081 \
  --create '/projects={"name":"p"}' \
  --create '/projects/{1.id}/todos={"title":"t","external_code":"c1"}'
```

When the generated product requires JWT authentication, `pg_run.py` automatically generates a local-only test RSA keypair (`<out>/auth/private.pem` with mode `0600` and `<out>/auth/public.pem`) and signs a short-lived `RS256` token (`role="editor"`), wiring `JWT_PUBLIC_KEY_FILE`, `JWT_ISSUER`, `JWT_AUDIENCE`, `TEST_AUTH_HEADERS`, and `TEST_AUTH_COOKIES` for `alembic`, `unittest`, and `uvicorn` while redacting the token and private key from `stdout` and `<out>/result.json`. Note that `--python-bin` must point at a Python environment (`bin/` directory) that has the generated product's runtime and test dependencies installed (`fastapi`, `uvicorn`, `sqlalchemy`, `psycopg`, `alembic`, `pydantic`, plus `cryptography` and `PyJWT` for JWT products); v7 products use async SQLAlchemy and also require `greenlet`.

## 6. Change it and generate again

Generated product files are deterministic for a given prompt and compiler release. When you edit your `.cnl` file and run `som write` again, point `--out` to a new or empty directory (`som write` refuses a non-empty `--out` directory so it never overwrites existing files).

If you saved an intermediate bundle with `som compile`, note that bundles bind the exact asset hashes of the default that compiled them; assembling an older bundle after a default switch fails with `ASSET_HASH_MISMATCH`, so compile the `.cnl` or `.prompt.json` source again.

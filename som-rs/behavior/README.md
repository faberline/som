# Behavior suite (issue #46)

A black-box HTTP suite for phase 1 products. It never imports product code: it derives a request
script from the typed prompt, sends it to a started product on a fresh PostgreSQL database, and
checks each answer two ways.

- **Oracle.** An in-memory model of the rows predicts the status and normalized body of every
  request, so one product is checked alone (`python` and `faults` modes).
- **Parity.** The normalized transcripts of the Python and Rust products of one prompt must be
  equal step by step (`parity` mode).

The rules come from `contracts/rust-output-v1/behavior-normalization.json`. That file is frozen and
this suite follows it.

## Coverage

Every step has one category. A failure names its step and category.

| Category | What is checked |
|---|---|
| `crud` | create 201, get, patch, delete 204, and get after each |
| `not_found` | get, patch and delete of ids 2147483000, 0 and -1 answer 404 with the product detail; a non-integer path id answers 422 |
| `unique` | a taken unique value on create or patch answers 409, changes no row and adds none; deleted values are reusable |
| `validation` | missing, wrong-type and unknown members; empty and invalid JSON bodies; array, number, `null` and positional-array bodies; a JSON object sent as `text/plain` (POST and PATCH) |
| `defaults` | defaults of omitted members |
| `null` | null for nullable and required columns, on create and patch |
| `filter` | exact string and boolean filters, alone and with limit and offset |
| `date_filter` | `before` is strict, excludes null dates, combines with a filter, and refuses bad dates |
| `limit` | default 20, 1, the maximum, and refusals of 0, maximum + 1 and non-integers |
| `pagination` | offsets inside and past the rows, and refusals of -1 and non-integers |
| `order` | lists are in id order, also after deletes |
| `timestamps` | RFC 3339 with an offset; `created_at` never changes; `updated_at` never decreases and advances on a real update |
| `fallback` | unknown routes 404, wrong methods 405 |
| `scope` (v5) | child requests under a missing parent answer 404 and add nothing |
| `isolation` (v5) | a child is visible only under its own parent |
| `restrict` (v5) | deleting a parent with children answers 409 and keeps both |
| `batch` (v5) | batches of 1 and 20 children; 0 and 21 are refused |
| `batch_atomic` (v5) | a batch that fails on any child adds no parent and no child |
| `bounds` (v5) | 255 characters and the i32 limits are admitted; 256 characters and i32 ± 1 answer 422 |
| `healthz` (v5) | `GET /healthz` answers `{"status": "ok"}` |

Each resource gets 23 rows (three ways: every member, required members only, and nulls). That is
more than the default limit, so the pages split.

## Normalization

Only ids and timestamps are normalized. Everything else compares exactly.

- **ids.** Each table maps an id to `id#N` in creation order, including the foreign key to the
  parent. A request path uses the real id behind the token.
- **timestamps.** A value must be RFC 3339 with an offset. It becomes `<timestamp>`. Before
  that, the suite checks that `created_at <= updated_at`, that `created_at` is stable, and that
  `updated_at` never decreases (and advances or stays equal where the oracle says).
- **numbers.** Float fields compare as numbers. Int fields must be JSON integers. A bool is never
  a number.
- **objects and arrays.** Member order is ignored. Array order counts.
- **422.** Only the set of `loc[0]` values (`body`, `query`, `path`) is compared. A 422 body that is
  not FastAPI shaped fails.
- **other errors.** The whole body is compared, e.g. `{"detail": "not found"}`.
- **status and media type.** Always compared, also on a 204, which must have no body. v1–v4 Python
  answers its 204 with FastAPI's `JSONResponse`, which keeps `application/json`; v5 Python sends a
  bare `Response` with no media type. The Rust v1–v4 delete matches the former, and the Rust v5
  delete the latter.

## Divergences

These are the frozen divergences D1 to D6. Parity leaves them out of the shared script. In
`parity` mode `Run.strict()` then checks the Rust side of D1 to D5 alone:

- D1, lax coercion (`"1"` and `1.0` for an int, `"1.5"` for a float, `"true"` and `1` for a bool):
  Rust answers 422 `body`.
- D2, strings over 255 or ints outside i32 in v1 to v4: Rust answers 422 `body`. The contract
  compares D2 for v5, where Python already answers 422, so the `bounds` steps compare the two
  products.
- D3, path ids outside i32: Rust answers 404 with the not-found detail.
- D4, a repeated query parameter: Rust answers 422 `query`.
- D5, a trailing slash: Rust answers 404 `{"detail": "Not Found"}`.
- D6, `/healthz`, is in v5 only.

After these checks, the suite confirms that no row changed and none was added.

## Hosting

`hosts.py` owns every process.

- **PostgreSQL.** A fresh `initdb` cluster of the pinned 17.7 binaries (checked by sha256), with
  UTF8, UTC, `LC_ALL=C`, TCP on 127.0.0.1 only and no unix socket. Each product gets a fresh
  database.
- **v1 to v4 Python.** `create_router(get_session)` mounted on a plain `FastAPI()` under uvicorn.
- **v5 Python.** `<package>.app:app`.
- **Python migrations.** The Alembic revision runs through a harness env.
- **Rust.** Built with the pinned cargo, `--locked --offline`, into a shared `CARGO_TARGET_DIR`,
  then run as the debug `som_app` binary with `DATABASE_URL` and `SOM_APP_ADDR`. Its migrator runs
  at startup.
  - Every product is the `som_app` package at its workspace root, so cargo gives them all one
    fingerprint. Each build therefore first removes the `som_app` units.
- **Ports.** Taken from 18919–18999. 8765 and 18765 are never used.
- **Readiness.** A product counts as ready only when its own process listens on its port (checked
  with `lsof`). A product that stops listening before its script ends fails the case.
- **Cleanup.** Products, migrations and cargo builds run in their own process groups and are
  tracked. SIGTERM, SIGHUP and SIGINT (handlers set first thing) kill every tracked group, refuse
  new processes, and wait for the workers before PostgreSQL is stopped and the scratch directory
  is removed. A failed or interrupted PostgreSQL start is stopped too. SIGKILL cannot be handled.

Bundles come from a QA compiler (`--qa`, default `http://127.0.0.1:18901`). The suite refuses the
default service on 8765.

## Usage

```bash
python check_behavior.py python --out OUT                  # 115 frozen acceptance cases (112 v1–v4 + 3 v5)
python check_behavior.py faults --out OUT                  # seeded faults in faults.py
python check_behavior.py parity --out OUT --target-dir T   # 115 v1–v5 cases, Python against Rust
```

- `--only ID ...` limits the cases or faults. Unknown ids are refused.
- `--jobs N` runs cases in parallel. Rust builds are serialized.
- `--python` is an interpreter with fastapi, uvicorn, sqlalchemy, psycopg, alembic and pydantic.
- The report is `OUT/report-<mode>.json`. Raw exchanges go to `OUT/raw/` with their sha256 in the
  report. They stay local.
- The exit code is 0 only when everything passes. In `faults` mode, every fault must be caught in
  its category, and every category must have a caught fault.

## Known limits

- A seeded fault counts as caught when a failure has its category anywhere in the run, not on
  one named step.
- The timestamp rules (`created_at` stable, `updated_at` advances on a real update and stays on an
  empty patch) are stricter than the normalization and also fail a parity row. Both products follow
  them today.
- Ports are probed and released before a product binds them. Another program can take one in
  between; that fails the case as "did not start" and never passes it, because readiness checks
  the product's own pid.

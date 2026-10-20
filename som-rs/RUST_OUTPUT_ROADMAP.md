# SOM Rust output roadmap — 2026-10-15

This plan adds Rust as a second output language. The human approved it on 2026-10-15.
Python output stays supported and unchanged. Rust is added beside it.
The [compiler roadmap](RUST_ROADMAP.md) is complete. This plan builds on its accepted default.

`CNL → Rust parser and fixed rules → DSL bundle with an explicit target → target blocks → Python or Rust files`

The old model-era M3 is retired history. This M3 is a new milestone that reuses the title.

## Phase 1 scope

Phase 1 uses axum, sqlx and tokio with PostgreSQL.
It covers prompt v1–v5 requests:

- One resource with typed fields, required and optional values, defaults and null.
- Unique values with 409 conflicts, and 404 for a missing row.
- Create, get, list, partial update and delete.
- Exact filters, date filters, result limits, pagination and id order.
- Parent and child resources with a foreign key and restricted parent deletion.
- Scoped child lists, isolation, timestamps and the atomic 1–20 child batch.
- A schema migration for each product.

These capabilities stay Python-only in phase 1:
JWT authentication, MongoDB, object storage, workflows, flows, branches, transactions across actions and compensation.
A Rust request that uses one of them fails with a diagnostic. The compiler never falls back to Python.

## Target selection

The output target is an explicit fact supplied by the caller.
A request without a target means Python. Existing CNL, prompts, bundles and outputs stay byte-identical.
The bundle declares the target and the target asset versions and hashes.
The assembler refuses a bundle whose target assets do not match.
Prompt v1–v7 semantics do not change.

## Generated Rust product

Each product is one Cargo package. The planned files are:

| File | Content |
|---|---|
| `Cargo.toml` and `Cargo.lock` | Pinned crate versions and features |
| `src/main.rs` | Server startup from `DATABASE_URL`, then the embedded migrator |
| `src/lib.rs` | Module declarations, the router and the embedded migrator |
| `src/models.rs` | Row types |
| `src/schemas.rs` | Request and response types with field validation |
| `src/api.rs` | axum handlers and status mapping |
| `src/db.rs` | SQL statements and sqlx runtime-API query functions |
| `migrations/0001_<revision>.sql` | Schema migration |
| `tests/api.rs` | Product tests against `TEST_DATABASE_URL` |
| `README.md` | Run and test instructions |

Queries use the sqlx runtime API. Builds do not need a live database or offline query data.
The service keeps returning files. It does not run cargo or write into the caller project.
The pinned rustfmt binary is a declared asset, like the pinned ruff binary.
File, block and plan limits stay in force for each target.

## M3 — Rust output, phase 1

| Issue | Work | Depends on |
|---|---|---|
| [#45](https://github.com/faberline/som/issues/45) | Freeze the target contract, scope, product layout, pinned toolchain and acceptance rules. | None |
| [#46](https://github.com/faberline/som/issues/46) | Build a language-neutral HTTP behavior suite. Prove it on current Python output first. | [#45](https://github.com/faberline/som/issues/45) |
| [#47](https://github.com/faberline/som/issues/47) | Add target-aware planning. Keep Python bundles byte-identical. Refuse out-of-scope Rust requests. | [#45](https://github.com/faberline/som/issues/45) |
| [#48](https://github.com/faberline/som/issues/48) | Add Rust blocks and rendering for one resource CRUD with PostgreSQL. | [#47](https://github.com/faberline/som/issues/47) |
| [#49](https://github.com/faberline/som/issues/49) | Add Rust parent and child resources, restricted deletion and the atomic batch. | [#48](https://github.com/faberline/som/issues/48) |
| [#50](https://github.com/faberline/som/issues/50) | Run the Rust acceptance gates and the unchanged Python regressions. | [#46](https://github.com/faberline/som/issues/46), [#48](https://github.com/faberline/som/issues/48), [#49](https://github.com/faberline/som/issues/49) |
| [#51](https://github.com/faberline/som/issues/51) | Release the new default with canary and rollback checks. Finish the docs and evidence. | [#50](https://github.com/faberline/som/issues/50) |

[GitHub milestone 9](https://github.com/faberline/som/milestone/9) tracks this work.
#45 is done: the [Rust output contract](contracts/rust-output-v1/CONTRACT.md) is frozen by the [freeze receipt](evidence/rust-output-contract-freeze-2026-10-15.json).
#47 is done: target-aware compile requests are in [som-targets](som-targets), with [evidence](evidence/rust-output-target-planning-2026-10-15.json).
#48 is implemented: Rust blocks render v1–v4 one-resource CRUD products, and every recorded gate passes, with [evidence](evidence/rust-output-products-2026-10-15.json).
#46 is implemented: the [behavior suite](behavior/README.md) passes all 115 acceptance cases on Python, catches 25 seeded faults and finds no Python/Rust difference on the 112 v1–v4 cases, with [evidence](evidence/behavior-suite-2026-10-15.json).
The #48 gates then pass again with that parity report, with [evidence](evidence/rust-output-products-parity-2026-10-15.json).
#49 is implemented: Rust blocks render v5 parent and child products, all 115 cases pass every gate and parity, with [evidence](evidence/rust-related-2026-10-15.json).
#50 is implemented: every check in the [acceptance plan](contracts/rust-output-v1/acceptance-plan.json) passes on one recorded build with vendored crates, the Python regressions replay byte-identically (127 public products, 544 legacy jobs) and an independent audit finds no failed or unrun check, with [evidence](evidence/rust-output-acceptance-2026-10-16.json).
#51 is implemented: rust-output-candidate-003 passed the acceptance plan again with an independent audit, a rehearsal of the switch, every injected failure and the restore, and the approved switch on 8765; the post-switch check passed, with [evidence](evidence/rust-output-release-2026-10-16.json).
#45–#51 are closed as completed and [milestone 9](https://github.com/faberline/som/milestone/9) is closed, read back at 2026-10-16T06:02:20Z.
[Amendment 1](evidence/rust-output-contract-amendment-001.json) sets the timestamp DEFAULT to CURRENT_TIMESTAMP for v3 and v4 and now() for v5, as in the Python migrations.

### Target-aware service (#47)

[som-targets](som-targets) is a separate Cargo package. It uses the som library as an unchanged path dependency.
The som sources, Cargo.toml and Cargo.lock stay byte-identical, so the backend hash and every Python bundle keep their bytes.
Its `som` binary serves the same routes and CLI, plus the optional `target` member and `som compile --target`.

- Without target, or with target python, the reply is the compiler-v1 reply byte for byte.
- An out-of-scope Rust request gets UNSUPPORTED_TARGET in the frozen rule order.
- An in-scope v1–v5 Rust request gets a som.dsl-bundle.v2 bundle.

`som-targets/qa/check_targets.py` runs the #47 gate against a QA port and a read-only reference compiler.

### Rust products (#48)

The [Rust catalog](som-targets/catalog/rust-catalog.v1.json) lists the approved blocks: Cargo manifest and lockfile, startup, router, row types, request and response types, handlers, pool setup, migration, product tests and README.
`/assemble` dispatches on som.dsl-bundle.v2, applies the A1–A9 refusals, renders through the pinned rustfmt and returns files only.
The service never runs cargo and never writes into the caller project.

`som-targets/qa/check_rust_products.py` runs the #48 and #49 gate: bundle_checks, assemble_refusals and product_gates for every v1–v5 acceptance case.
Each product passes the rustfmt check, `cargo build --locked --offline`, clippy with warnings denied and its own tests on a fresh PostgreSQL database.
Without `--parity` the checker reports behavior parity as blocked and exits 2. With `--parity <#46 report>` it requires every case to pass parity with the same Rust files the gates built.

### Parent and child resources (#49)

The v5 blocks render a parent and a child table joined by a foreign key with `ON DELETE RESTRICT`.
Deleting a parent that still has children answers 409. Child routes are scoped to their parent: a missing parent answers 404, and a child under another parent is not found.
`POST {parent}/with-children` creates a parent with 1–20 children in one transaction; any failure rolls back the whole batch.
As in the v5 Python product, the tables default their timestamps to `now()`, `/healthz` answers, and a delete answers 204 with no media type.

## Acceptance

Freeze cases, checks and toolchain versions before implementing Rust blocks.
Record later amendments as amendments.

Each Rust product must pass these gates:

- rustfmt check
- `cargo build --locked --offline`
- `cargo clippy` with warnings denied
- its own tests

The vendored crate set and toolchain are acceptance tools. They are not used in production.
The same behavior suite runs against the Python and Rust products on a fresh PostgreSQL database.
Status codes and JSON bodies must match after the documented normalization of timestamps and ids.
The same bundle and pinned assets must produce the same Rust files.

Python stays unchanged:

- All 127 public products produce byte-identical output.
- All 544 historical jobs keep their accepted results.
- The legacy 100 and 214 case sets keep their scope.

Syntax checks, builds and byte equality do not replace behavior checks.
Missing results, failures and skipped checks remain explicit.

## M4 — Rust output, phase 2

Phase 2 makes the Rust output cover every prompt compiler-v1 admits:

- v5 with capabilities, v6 capabilities and v7 flows;
- the names that phase 1 refused: crate, self and super, names made only of underscores or not in snake case, a resource path with an empty segment or at /healthz, and the table _sqlx_migrations.

One refusal stays: a v3 filter field named limit or offset (query_parameter_collision).
Its query string is ambiguous, the Python product for it does not start, and compiler-v1 refuses the same names in v4 and v5.
This is a recorded deviation from the #52 issue text.

| Issue | Work | Depends on |
|---|---|---|
| [#52](https://github.com/faberline/som/issues/52) | Freeze the phase 2 amendment, the naming and service rules, the extended behavior suite and the pins. | None |
| [#53](https://github.com/faberline/som/issues/53) | Generate MongoDB, authentication, object storage and workflow products. Remove the naming refusals. Pin the crates. | [#52](https://github.com/faberline/som/issues/52) |
| [#54](https://github.com/faberline/som/issues/54) | Generate v7 flows, transactions, branches and undo. | [#52](https://github.com/faberline/som/issues/52), [#53](https://github.com/faberline/som/issues/53) |
| [#55](https://github.com/faberline/som/issues/55) | Run the phase 2 acceptance and the Python regressions. | [#53](https://github.com/faberline/som/issues/53), [#54](https://github.com/faberline/som/issues/54) |
| [#56](https://github.com/faberline/som/issues/56) | Release, docs and evidence. | [#55](https://github.com/faberline/som/issues/55) |

[GitHub milestone 10](https://github.com/faberline/som/milestone/10) tracks this work.

### Phase 2 contract (#52)

[Amendment 2](evidence/rust-output-contract-amendment-002.json) adds [section 10](contracts/rust-output-v1/CONTRACT.md) and the files in [contracts/rust-output-v1/phase2](contracts/rust-output-v1/phase2):

- constructs.json lists all 168 compiler-v1 constructs with their Rust form and owner issue. None is unassigned.
- naming.json defines two sets of names:
  - W1 to W6 keep the Python wire names, routes, tables, columns and query parameters.
  - R1 to R3 fix the Rust-internal names. A field without a usable Rust name gets a fixed name and a serde rename.
- services.json makes Rust read the same settings and local overrides as Python. It maps each operator command to a `som_app` subcommand.
- case-inventory.json names all 584 phase 2 cases by id, group and prompt hash:
  - 444 legacy jobs;
  - 115 CNL fixtures;
  - 3 public examples;
  - RS01 to RS22.
- pins.json pins mongod, redis, moto, the in-repo GCS, Firebase and OIDC stand-ins, uv, the acceptance venv and every suite file. PostgreSQL and the Rust toolchain keep their toolchain.json pins.
- behavior.json records the extended suite in [behavior/phase2](behavior/phase2) and its run on the accepted Python products.

The extended suite runs three checkers:

- the phase 1 HTTP suite;
- the som-core capability verifiers;
- the flow verifiers.

The verifiers run as adapted copies. Their language-specific steps go through phase2_host. behavior.json records every other adaptation and how many cases it touches.
Together the checkers cover:

- auth accept and reject;
- PostgreSQL and MongoDB persistence;
- object put and get;
- workflow outputs;
- step results, rollback, branches, undo, idempotency, leases and provider faults.

The accepted Python run passes 579 of 584 cases, including every legacy job, CNL fixture and public example.
Two earlier complete runs are recorded as superseded:

- run 001: a suite defect in binding existing objects, fixed before run 002;
- run 002: the host slept, and 12 lease, journal and heartbeat checks failed.

Five RS cases do not pass:

- RS07, RS14 and RS16: a field or foreign key named self makes the Python product answer 500. Rust follows the suite oracle. [#57](https://github.com/faberline/som/issues/57) tracks the Python fix.
- RS12 and RS13: the kept refusal. Their Python products do not start.

### Phase 2 progress (#53–#56)

- #53 is implemented: [amendment 3](evidence/rust-output-contract-amendment-003.json) fixes the five product shapes, the 335-crate phase 2 lock and the Rust mode of the suite. All 327 cases below v7 pass on Rust products, including the 90 capability tuples; RS12 and RS13 give the kept refusal ([evidence](evidence/rust-output-phase2-capabilities-2026-10-19.json)).
- #54 is implemented: all 257 v7 flow cases pass, with database and provider faults injected on every step with undo, lease and restart checks ([evidence](evidence/rust-output-phase2-flows-2026-10-20.json)).
- #55 is implemented: rust-output-candidate-004 passes every phase 2 check of the acceptance plan on one build (584/584, phase 1 115/115 unchanged), the Python regressions replay byte-identically (2005 exchanges, 127 public products, 544 legacy jobs) and an independent audit finds 35 passed, 0 failed, 0 unrun ([evidence](evidence/rust-output-acceptance-2026-10-20-phase2.json)).
- #56 is implemented: after a rehearsal of the switch, every injected failure and the restore, and the user's approval, candidate 004 became the default on 8765 on 2026-10-20; the post-switch check passed ([evidence](evidence/rust-output-release-2026-10-20.json)).

#53, #54, #55 and #56 stay open for review. [#57](https://github.com/faberline/som/issues/57) fixes the Python self defect after #56, with its own candidate, acceptance and release.

## Limits

Use local tools only. No paid agent runs, real cloud runs or model training.
Do not publish weights, binaries or raw measurements.
Keep the current default on 8765 until a qualified switch passes. #51 switched it on 2026-10-16 and #56 on 2026-10-20.
Python output stays byte-identical through M4.
Keep the legacy M2 rollback assets.

## Tracking

[Milestone 9](https://github.com/faberline/som/milestone/9) holds issues #45–#51.
The #45 contract is frozen. Later changes to it are numbered amendments. #47 added target selection and the Rust scope refusals. #48 generates v1–v4 Rust products. #46 runs behavior parity for them. #49 adds parent and child output. #50 records the acceptance run and the Python regressions. #51 makes the Rust output build the default.

[Milestone 10](https://github.com/faberline/som/milestone/10) holds issues #52–#56 for phase 2. #52 freezes amendment 2: the phase 2 constructs, naming and service rules, the case inventory, the pins and the extended behavior suite with its Python run. #53 and #54 generate the phase 2 products. #55 runs their acceptance. #56 makes the phase 2 build the default. #57 follows for the Python self defect.

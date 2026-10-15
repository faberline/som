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
| `src/main.rs` | Server startup from `DATABASE_URL` |
| `src/lib.rs` | Router assembly and shared state |
| `src/models.rs` | Row types |
| `src/schemas.rs` | Request and response types with field validation |
| `src/api.rs` | axum handlers and status mapping |
| `src/db.rs` | sqlx pool setup and migration startup |
| `migrations/<revision>_<package>.sql` | Schema migration |
| `tests/` | Product tests |
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

[GitHub milestone 9](https://github.com/faberline/som/milestone/9) tracks this work. All seven issues are open.

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

## Limits

Use local tools only. No paid agent runs, real cloud runs or model training.
Do not publish weights, binaries or raw measurements.
Keep the current default on 8765 until a qualified switch passes.
Keep the legacy M2 rollback assets.

## Tracking

[Milestone 9](https://github.com/faberline/som/milestone/9) holds issues #45–#51. No Rust output is implemented yet.

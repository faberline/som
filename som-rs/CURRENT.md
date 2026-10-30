# Current development state — 2026-10-22

> Current direction — 2026-10-28: fixed algorithms, `CNL → DSL → Code`.
> No model training, model weights or Apple chip is required.
> Develop locally. Run builds and tests on GitHub Actions runners.
> Homebrew paths and binary hashes below record older accepted tools.
> M12 owns their platform adaptation. Historical results are not a fresh portable acceptance run.
> Read [the current architecture](../docs/architecture.md).

The local default on `127.0.0.1:8765` is TypeScript candidate-001 since `2026-10-22T13:55:00.006599+00:00` (#65).
It serves the Python, Rust and TypeScript targets without loading a model. The post-switch check passed, with Python, Rust and TypeScript canaries.
`target typescript` now writes a Node.js 26.5 + Hono + `pg` product for prompt versions 1 to 5 with PostgreSQL; versions 6 and 7 and the other scope.json refusals answer 422 UNSUPPORTED_TARGET. Read [docs/typescript-be.md](../docs/typescript-be.md).
TypeScript assemble needs `SOM_TS_PRETTIER` in the environment of `som serve`. `runtime/default.json` does not record it, so a manual restart of 8765 must set `SOM_TS_PRETTIER=$PWD/.local/typescript-output/prettier-001/node_modules/prettier`, or TypeScript assemble answers 503 ASSET_UNAVAILABLE (#123).
`som write` compiles CNL, assembles the bundle and writes the product directory in one command (#79).
[docs/python-be.md](../docs/python-be.md) is the Python backend quickstart (#84).
A Python resource field or foreign key named `self` builds and runs (#57).
compiler-v1 refuses a name that collides with a generated Python identifier, such as a v3 filter field named limit or offset, with INVALID_VALUE (#58).
The Rust target covers every prompt compiler-v1 admits, v1 to v7.
The compiler assets are unchanged (`d95d93c7…`), so Python and Rust product files and bundles are byte-identical to candidate-007.
It is released as [v0.5.0](https://github.com/faberline/som/releases/tag/v0.5.0), tagged at the build commit `8c4a91fbb5`, notes only.
See the [release evidence](evidence/typescript-output-release-2026-10-22-issue65.json).

Earlier defaults, all kept for rollback:

- rust-output-candidate-007 (#81, [v0.2.0](https://github.com/faberline/som/releases/tag/v0.2.0)), binary `350369aa3f84aeed470d8f076418622fba9a65a9907b81e88ff868620fd1788e`, from `2026-10-21T17:09:02.500328+00:00`;
- rust-output-candidate-006 (#57, #58), binary `52658e13af1ac08659ef1dda7e333634f4ae3893731304e09edec6ca5f9b87c2`, from `2026-10-20T08:39:04.671848+00:00`;
- rust-output-candidate-004 (#56), binary `d2de75223496665cd563b51daa51741faa337c9cc2d46d8af8cc596b9f399b9b`, from `2026-10-20T03:14:10.975162+00:00`;
- rust-output-candidate-003 (#51), binary `05f1bfe6eef7045f054b1323a193e8f7bea1b4afe4f287a47cd0d0f9347ae11c`, from `2026-10-16T05:54:52.297481+00:00`;
- the Rust compiler (#38–#44), binary `3fad3cb7eafb35274f6a37f9a8a371f93807a6b5fedf3e9fe0e074cbf5bfef02`, from `2026-10-15T03:08:08.737006+00:00`. Issues #38–#44 and milestones 7 and 8 are closed;
- the legacy M2 runtime, model and block links.

`CNL → Rust parser and fixed rules → complete DSL bundle → existing Rust backend → Python or Rust files`

CNL uses fixed English clauses. Data strings can include Chinese.
The caller supplies each business choice. The compiler does not guess missing facts.
The new production path does not load models or start Python.
Python remains the default output language and an independent test tool. Add `--target rust` for a Cargo product, or `--target typescript` for an npm product.

The installed compiler uses separate `runtime/current` and `compiler-assets/current` links.
`som-rs/runtime/current` points to `versions/2bceb3a0a1de02b08c71eb557c0c3e32fe5ee7b646935e7821e9e07ab3d56e0c`.
`som-rs/compiler-assets/current` points to `versions/d95d93c764d95373480b580cfa672de91c55f8f06a5446a786bff61395aeccaa`.
The model link `som-rs/models/current` and the block link are unchanged legacy M2 rollback assets.
Read [the acceptance report](RUST_REPORT.md), [the guide](RUST_GUIDE.md) and [the operator handoff](RUST_HANDOFF.md).

## Preserved earlier default record

The text below is historical. Its state, commands, and measurements retain their original scope.

# Current development state

Recorded on 2026-10-05. SOM has no release in this repository. This document
records the model and block library used by the local default service. The
`current` paths point to tested, immutable directories:

| Current path | Target | SHA-256 |
|---|---|---|
| `som-rs/models/current` | `flow-m2-candidate/smoke-r31-001` | manifest `24ba15d15ff2dae6f6551c70f1dfc8876526c9f3d80dc4d6581fab6ad065f0b7` |
| `som-code-python/data/blocks/current` | `flow-v7-compact` | blocks `5a75971536ae02f627bb4a4e97737604cefc120e71293b8c10f7dcc442c460ea` |

Run `som-rs/runtime/versions/14cf0a67fbcc943639c9e0efb661d6aaf0de7b00289c05fc52819f9e97f239d6/som serve` from the repository root. It loads these
paths and listens on `127.0.0.1:8765`. The default `som generate` command
calls that address. The service and CLI still do not write product files.
Prompt and DSL version strings remain parsing contracts for old requests.
They are not release numbers.

The [switch receipt](evals/m3-flow-2026-10-05/r1/default-switch-001/result.json)
passes 12 actual Metal/HTTP/CLI flow cases and 314 exact legacy outputs.
The old v4 configuration also passed a fresh rollback startup test.
The [runtime record](runtime/default.json) saves the command, PID and logs.
M2 proves its twelve seen inputs. It does not prove new composition accuracy.
M3 remains a separate candidate until every new gate passes.

## Historical v4 measurements

The following measurements belong to the previous v4 default.

## Separate checks for the four stages

The [fixed 100-case set](../som-core/data/service-regression-v4/manifest.json)
contains the previous 50 v3 structural stress tasks and 50 new date tasks.
The two groups have separate results. Deterministic stages require every
case to pass.

| Stage | Kind | Previous 50 | New 50 | Evidence |
|---|---|---:|---:|---|
| HTTP prompt acceptance | Test | 50/50 | 50/50 | [default service exact run](evals/current-default-100-exact-2026-09-30.json) |
| Model operation selection | Model eval | 50/50 | 50/50 | `CARGO_HOME=/private/tmp/som-cargo cargo test --manifest-path som-rs/Cargo.toml --locked --offline --test model_selection -- --ignored` |
| Rust assembler output equals Python reference | Test | 50/50 | 50/50 | `CARGO_HOME=/private/tmp/som-cargo cargo test --manifest-path som-rs/Cargo.toml --locked --offline --test parity` |
| HTTP returns complete expected files | Test | 50/50 | 50/50 | [default service exact run](evals/current-default-100-exact-2026-09-30.json) |

The HTTP layer also returned the expected error envelope for ten invalid
prompts and one malformed JSON request. See the
[HTTP contract receipt](evals/current-http-contract-2026-09-30.json).
The Rust/Python parity tests cover twelve further invalid DSL fixtures from
the v3 and date corpora. The CLI returned the twelve exact Todo files through
the default address.

## Product behavior and size

PostgreSQL behavior is a separate check. The previous 50 tasks passed 50/50.
The new date tasks passed 50/50. Each used an isolated real database and ran
generated unittest, API checks, migration upgrade and downgrade, and a
restart check. See the [combined behavior receipt](evals/service-regression-v4-100-postgres-2026-09-30.json).

The [100-request budget receipt](evals/service-regression-v4-100-budget-2026-09-30.json)
records a 0.541 MB model directory, 53.559 MB peak resident memory, and
24.272 ms warm sequential p95. These measurements exclude service startup
and flagship agent work.

The [paired date-service eval kit](evals/date-service-paired-2026-09-30/README.md)
uses four frozen date tasks for each flagship agent. All 16 final services
passed independent PostgreSQL checks. The [full receipt](evals/date-service-paired-2026-09-30/receipt.json)
and [summary](evals/date-service-paired-2026-09-30/results.md) show:

| Agent | Direct tokens | SOM tokens | Reduction | Direct time | SOM time |
|---|---:|---:|---:|---:|---:|
| Codex | 994,162 | 441,116 | 55.6% | 1,039.0 s | 195.5 s |
| Claude Code | 1,299,103 | 315,579 | 75.7% | 716.2 s | 127.3 s |

Claude Code used 51 versus 17 reported rounds and $2.65 versus $0.72 in
CLI-reported reference cost. Codex does not report comparable rounds or dollar
cost in these logs. Both agents passed the four-task 30% token gate. Codex
case 01 reduced tokens by only 16.5%, so the aggregate hides a weaker case.
The [case 01 analysis](evals/date-service-paired-2026-09-30/analysis-journal.md)
records an isolated repeat with identical SOM output and much lower agent
input use. The official paired result remains unchanged.

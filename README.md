# som

## Goal

The mainline is **R1 — Rust API**: a Rust API ecosystem built on core.
`som-service` turns CNL or typed JSON into the DSL, then into a Rust client and an axum server.
It is built on core `openapi-codegen` and `service-http`.

The milestone order drives the development order. `aw` claims the lowest `som@X.Y.Z` first.

1. R1 — Rust API 主線 (`som@0.6.0`)
2. M6 — React 前端 (`som@0.7.0`)
3. M9 — SRE (`som@0.8.0`)
4. M10 — AI (`som@0.9.0`)
5. M12 — Rust CI (`som@0.10.0`)

The old Python output is read only in the archive [faberline/som-archive](https://github.com/faberline/som-archive). It is not the mainline.

## Supported path

There is one supported path: `som-service` (Rust). It compiles CNL or typed JSON into the DSL and then into a Rust client and an axum server. Read [som-service/README.md](som-service/README.md).
The old Python output is frozen and read only in the archive [faberline/som-archive](https://github.com/faberline/som-archive). It is not the supported path.

## Current state

The new Rust compiler passed independent acceptance and is the local default.
It was promoted at `2026-10-15T03:08:08.737006+00:00` and serves `127.0.0.1:8765` without loading a model.
The root post-switch check passed 76/76. Commit `3ced191137` published it to main.
Issues #38–#44 are closed as completed, and milestones 7 and 8 are closed.
The legacy M2 runtime, model and block links remain saved for rollback.

`CNL → Rust parser and fixed rules → complete DSL bundle → existing Rust backend → Python files`

CNL uses fixed English clauses. Data strings can include Chinese.
The caller supplies each business choice. The compiler does not guess missing facts.
The new production path does not load models or start Python.
Python remains the output language and an independent test tool.
Rust output with axum and sqlx is planned in [milestone 9](https://github.com/faberline/som/milestone/9).
Read the Rust output roadmap, `som-rs/RUST_OUTPUT_ROADMAP.md`, in the archive [faberline/som-archive](https://github.com/faberline/som-archive). The archive plan is [docs/archive-plan.md](docs/archive-plan.md).

The fresh complete gates passed 127 public products and 544 historical jobs.
Read the guide, the status, the acceptance report and the roadmap in the archive [faberline/som-archive](https://github.com/faberline/som-archive): `som-rs/RUST_GUIDE.md`, `som-rs/RUST_STATUS.md`, `som-rs/RUST_REPORT.md` and `som-rs/RUST_ROADMAP.md`. The repository rules are in [AGENTS.md](AGENTS.md).

## CNL ownership

som owns CNL syntax and CNL meaning: the parser, business rules, admission, lowering to the DSL and code generation.
The syntax crate `faberline-nlp-cnl` lives in `som-service/cnl/`. It was moved from [faberline/nlp](https://github.com/faberline/nlp) at revision `2f7e519d4037033d0ef69629c8f8bdfba837794a` without a change of grammar, AST, API versions or diagnostic spans.
A CNL syntax change is a som issue.
The frozen compiler-v1 contract, `som-rs/contracts/compiler-v1/grammar.ebnf`, is in the archive [faberline/som-archive](https://github.com/faberline/som-archive). New syntax is not added there.
The archive keeps the sealed nlp pin of the old compiler (`faberline-nlp-cnl` in `som-rs/Cargo.toml`, in [faberline/som-archive](https://github.com/faberline/som-archive)). It is not re-pinned; [#180](https://github.com/faberline/som/issues/180) is closed as not planned.

## Historical model roadmap and retained legacy service

This section is a historical snapshot of the previous model roadmap and the retained M2
service. It does not instruct new training and is not maintained.
The old Python output is described in `som-rs/README.md`, which is read only in the archive [faberline/som-archive](https://github.com/faberline/som-archive).
The live path is `som-service`: read [som-service/README.md](som-service/README.md).

## Archive

The old areas `som-rs/`, `som-core/` and `som-code-python/` are not in this repository. They moved with their history to the private repository [faberline/som-archive](https://github.com/faberline/som-archive). The source commit is `47e8b3f573882c4726fdb26bca23d2bf138f7820`. The old areas are read only there. How the move was made and checked: [docs/archive.md](docs/archive.md).

## License

MIT — see [LICENSE](LICENSE).

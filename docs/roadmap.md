# SOM scenario roadmap

This roadmap serves the [goal](goal.md): BE, FE, SRE, and AI code.
The [Python backend quickstart](python-be.md) walks through generating and running a Python backend project.
The [Rust backend quickstart](rust-be.md) walks through generating and building a Rust backend project offline.
It is a proposal. A person decides each track's scope and approves its contract.

## How a track runs

Every track follows the path the Rust target took ([Rust output roadmap](../som-rs/RUST_OUTPUT_ROADMAP.md)):

1. Scope. Decide which requests the track covers and which stay refused.
2. Contract. Freeze the product layout, toolchain, dependency pins, behavior suite, and acceptance plan. A person approves it.
3. Target selection. The caller names the target. Every other target refuses the request with a diagnostic.
4. Products. Generate the products, phase by phase.
5. Behavior. Run each product against the frozen behavior suite. A track with an existing language runs a parity check against it.
6. Acceptance. An independent run passes with zero failures and zero unrun checks. Existing outputs stay byte-identical.
7. Default. Release the build to the local service after a person approves it.

Only steps 3–6 become `agy:todo` issues, and only after the contract is frozen.
Steps 1, 2, and 7 need a person.

## Milestones and releases

On 2026-10-21 the user set the priority to Python BE, then React FE, then Rust, then the rest.
Each milestone ends with a usable release: a candidate, an independent acceptance, a switch of 8765 after the user approves, and a version tag.
The repository is public, so each tag and GitHub release also needs the user's yes.

| Milestone | Release | Usable result | Issues |
|---|---|---|---|
| — | [v0.1.0](https://github.com/faberline/som/releases/tag/v0.1.0) (released 2026-10-21) | candidate-006: Python and Rust BE for prompts v1–v7 | tag at the build commit `6116fed549`, notes only |
| [M5](https://github.com/faberline/som/milestone/11) | [v0.2.0](https://github.com/faberline/som/releases/tag/v0.2.0) (released 2026-10-22) | The current default, candidate-007: `som write` puts a Python project on disk, and a quickstart page runs it | [#79](https://github.com/faberline/som/issues/79), [#80](https://github.com/faberline/som/issues/80), release [#81](https://github.com/faberline/som/issues/81) |
| [M11](https://github.com/faberline/som/milestone/17) | v0.2.1 | `pg_run.py` runs the v3–v7 PostgreSQL + JWT examples end to end; scripts and docs only, 8765 unchanged | [#91](https://github.com/faberline/som/issues/91), [#95](https://github.com/faberline/som/issues/95) |
| [M6](https://github.com/faberline/som/milestone/12) | v0.3.0 | A React CRUD client for a generated Python BE (v1–v4) | [#68](https://github.com/faberline/som/issues/68); proposal in [react-output-v1-draft.md](react-output-v1-draft.md), waiting for approval |
| [M7](https://github.com/faberline/som/milestone/13) | v0.4.0 | `som write --target rust`, an offline build quickstart, and a one-command PostgreSQL run check | [#82](https://github.com/faberline/som/issues/82), [#92](https://github.com/faberline/som/issues/92) |
| [M8](https://github.com/faberline/som/milestone/14) | v0.5.0 | TypeScript BE phase 1 (v1–v5, PostgreSQL) | [#65](https://github.com/faberline/som/issues/65), [#74](https://github.com/faberline/som/issues/74)–[#76](https://github.com/faberline/som/issues/76), acceptance tooling [#97](https://github.com/faberline/som/issues/97)–[#99](https://github.com/faberline/som/issues/99) |
| [M9](https://github.com/faberline/som/milestone/15) | v0.6.0 | SRE | [#66](https://github.com/faberline/som/issues/66); needs scope |
| [M10](https://github.com/faberline/som/milestone/16) | v0.7.0 | AI | [#67](https://github.com/faberline/som/issues/67); needs scope |

The TypeScript phase 1 work that AGY already started (#74, #76) finishes.
Other TypeScript issues wait until the higher-priority issues are done.
A release that contains #74 before TypeScript phase 1 ships answers `target typescript` with 501 or 422, and its notes say that TypeScript is not supported yet.

## Tracks

The track sections below keep their original numbering. The order of work is the milestone table above.

### 1. BE in TypeScript

- Reuses the existing CNL, DSL bundle, and behavior suites. Only the output target is new.
- Chosen: Node.js 26.5.0, Hono, `pg` with hand-written SQL, hand-written validation, `node:test`, npm, tsc 5.9.3 and prettier.
- First phase: the Rust phase 1 scope (prompt v1–v5 with PostgreSQL), then capabilities and flows.
- Contract: [typescript-output-v1](../som-rs/contracts/typescript-output-v1/CONTRACT.md), frozen on 2026-10-21 after approval; amendment 001 sealed the npm lockfile, the vendored cache and the formatter hash ([#73](https://github.com/faberline/som/issues/73)). The draft and the options considered are in [typescript-output-v1-draft.md](typescript-output-v1-draft.md).
- Phase 1 issues: target selection and profile ([#74](https://github.com/faberline/som/issues/74)), bundle, catalog and renderer ([#75](https://github.com/faberline/som/issues/75)), host, product gates and parity ([#76](https://github.com/faberline/som/issues/76)), then the acceptance run. Its tooling: candidate formatter option ([#97](https://github.com/faberline/som/issues/97)), HTTP target and scope checks ([#98](https://github.com/faberline/som/issues/98)), HTTP bundle and assemble-refusal checks ([#99](https://github.com/faberline/som/issues/99)).
- Issue: [#65](https://github.com/faberline/som/issues/65).

### 2. SRE in Python, Rust, and TypeScript

- Needs a new scenario scope, and maybe new CNL clauses and a new DSL section.
- Candidate outputs: container and deployment files for a generated service, health and readiness checks, metrics and alert rules, operational CLIs, and runbooks.
- Decisions needed: which outputs are in scope, which platforms they target, and how they are tested locally without a cloud.
- Issue: [#66](https://github.com/faberline/som/issues/66).

### 3. AI in Python, Rust, and TypeScript

- Needs a new scenario scope, and maybe new CNL clauses and a new DSL section.
- Candidate outputs: services that call a model API, retrieval over a document store, tool-using agents, and evaluation harnesses.
- Decisions needed: which model APIs and SDKs, how secrets are supplied, and the local fake that replaces the paid API in tests.
- SOM itself still loads no model. Only the generated products call one.
- Issue: [#67](https://github.com/faberline/som/issues/67).

### 4. FE in React

- React only until the target is mature.
- Candidate first scope: a client for a generated BE product, with list, detail, create, edit, and delete pages for each resource.
- Decisions needed: language (TypeScript or JavaScript), build tool, router, data fetching, styling, and the test runner with a browser or DOM environment.
- Issue: [#68](https://github.com/faberline/som/issues/68).

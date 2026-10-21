# SOM scenario roadmap

This roadmap serves the [goal](goal.md): BE, FE, SRE, and AI code.
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

## Tracks

Proposed order: TypeScript BE, then SRE, then AI, then React FE.

### 1. BE in TypeScript

- Reuses the existing CNL, DSL bundle, and behavior suites. Only the output target is new.
- Chosen: Node.js 26.5.0, Hono, `pg` with hand-written SQL, hand-written validation, `node:test`, npm, tsc 5.9.3 and prettier.
- First phase: the Rust phase 1 scope (prompt v1–v5 with PostgreSQL), then capabilities and flows.
- Contract: [typescript-output-v1](../som-rs/contracts/typescript-output-v1/CONTRACT.md), frozen on 2026-10-21 after approval; amendment 001 sealed the npm lockfile, the vendored cache and the formatter hash ([#73](https://github.com/faberline/som/issues/73)). The draft and the options considered are in [typescript-output-v1-draft.md](typescript-output-v1-draft.md).
- Phase 1 issues: target selection and profile ([#74](https://github.com/faberline/som/issues/74)), bundle, catalog and renderer ([#75](https://github.com/faberline/som/issues/75)), host, product gates and parity ([#76](https://github.com/faberline/som/issues/76)), then the acceptance run.
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

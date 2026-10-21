# SOM goal

SOM's end goal is to generate code for four scenarios: backend (BE), frontend (FE), SRE, and AI.

| Scenario | Python | Rust | TypeScript | React |
|---|---|---|---|---|
| BE | supported | supported (axum, sqlx) | planned | — |
| SRE | planned | planned | planned | — |
| AI | planned | planned | planned | — |
| FE | — | — | — | planned |

- BE, SRE, and AI must each support Python, Rust, and TypeScript.
- FE starts with React only. Other frameworks wait until the React target is mature.

## Status on 2026-10-21

Only BE exists. It covers prompts v1–v7 with Python and Rust output.
The local service on `127.0.0.1:8765` serves both.
No TypeScript, SRE, AI, or FE output exists yet.

## Rules for new targets

- Each new language or scenario starts with its own frozen contract: product layout, toolchain, dependencies, and acceptance plan.
  A person approves the contract before implementation starts.
- New targets never change existing output. The 127 public products and 544 legacy jobs stay byte-identical in Python and Rust.
- A target is supported only after an independent acceptance run passes with zero failures and zero unrun checks.
- Local tools only. No paid or cloud services are needed to build or test a target.

## Suggested order

1. TypeScript BE. It reuses the existing DSL and the behavior harness, like the Rust target did.
2. SRE in Python, Rust, and TypeScript. Its DSL scope needs a decision first.
3. AI in Python, Rust, and TypeScript. Its DSL scope also needs a decision first.
4. React FE.

The order is a proposal. Each step's contract and scope are decided by a person.

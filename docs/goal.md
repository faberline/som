# SOM goal

SOM's end goal is to generate code for four scenarios: backend (BE), frontend (FE), SRE, and AI.
The user set this goal on 2026-10-21.

| Scenario | Python | Rust | TypeScript | React |
|---|---|---|---|---|
| BE | supported | supported (axum, sqlx) | planned | — |
| SRE | planned | planned | planned | — |
| AI | planned | planned | planned | — |
| FE | — | — | — | planned |

- BE, SRE, and AI must each support Python, Rust, and TypeScript.
- FE starts with React only. Other frameworks wait until the React target is mature.

## Status on 2026-10-22

Only BE exists. It covers prompts v1–v7 with Python and Rust output.
The local service on `127.0.0.1:8765` serves both. [v0.2.0](https://github.com/faberline/som/releases/tag/v0.2.0) added `som write` and the Python backend quickstart.
No TypeScript, SRE, AI, or FE output exists yet.

## Rules for every new target

- Each new language or scenario starts with its own frozen contract: product layout, toolchain, dependencies, and acceptance plan.
  A person approves the contract before implementation starts.
- New targets never change existing output. The 127 public products and 544 legacy jobs stay byte-identical in Python and Rust.
- The compiler never guesses a missing fact and never falls back to another target. An unsupported request fails with a diagnostic.
- A target is supported only after an independent acceptance run passes with zero failures and zero unrun checks.
- Local tools only. Building and testing a target needs no paid or cloud service.
  A target that talks to a cloud or model API is tested against a local fake or emulator.

The [roadmap](roadmap.md) lists the tracks, their open decisions, and their issues.
The [Python backend quickstart](python-be.md) shows how to generate and run a Python backend project.
The [Rust backend quickstart](rust-be.md) shows how to generate and build a Rust backend project offline.
The [TypeScript backend quickstart](typescript-be.md) shows how to generate and run a TypeScript backend project offline.

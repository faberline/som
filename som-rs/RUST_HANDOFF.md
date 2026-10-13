# Rust roadmap handoff — 2026-10-13

The roadmap reset is complete. Compiler implementation has not started.
Read [RUST_ROADMAP.md](RUST_ROADMAP.md) and begin with [#38](https://github.com/faberline/som/issues/38).
There are two open milestones and seven open implementation issues.
The old six milestones were deleted after their metadata was archived.
The 25 replaced issues are closed as not planned. The other 12 closed issues retain their histories.

## Local state

- Primary checkout: `/Users/chrischeng/faberlines/som`, branch `main`.
- Preserved worktree: `/Users/chrischeng/.codex/worktrees/76af/som`. Do not reset its seven existing changes.
- Legacy M2 model pointer: `flow-m2-candidate/smoke-r31-001`.
- Legacy M2 block pointer: `flow-v7-compact`.
- M2 service: `http://127.0.0.1:8765`. The reset health check passed. No service switch occurred.
- Old `m3` hourly automation: paused. Do not resume its model objective.
- Old R6 controller, gate, four workers and caffeinate helper: absent at reset. No stop signal was needed.
- R6 admission: 835 reported passes out of 2700 required. Incomplete. No R6 training or formal model evaluation.
- Historical ledger: five closed attempts, zero open attempts, 231398.2452680124 seconds charged. No budget change.

Raw reset records are in `/Users/chrischeng/faberlines/som/.local/roadmap-reset-2026-10-13`.
Original R6 records are in `som-rs/evals/m3-flow-2026-10-12/r6`.
Keep raw records, weights, runtimes and source archives local.
The [public evidence index](evidence/rust-roadmap-reset-2026-10-13.json) records hashes and scope.

## Next work

Use Rust for parsing, fixed planning and the service. Keep Python output and existing checkers.
Freeze the contract and acceptance cases in [#38](https://github.com/faberline/som/issues/38) before implementing [#39](https://github.com/faberline/som/issues/39) and [#40](https://github.com/faberline/som/issues/40).
Reuse the backend in [#41](https://github.com/faberline/som/issues/41). Complete [#42](https://github.com/faberline/som/issues/42) and [#43](https://github.com/faberline/som/issues/43) before [#44](https://github.com/faberline/som/issues/44).
Keep current as legacy M2 until new acceptance, rollback and canary evidence pass.
Old M3 instructions and old model gates are historical. They cannot override this roadmap.

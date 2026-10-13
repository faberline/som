# SOM Rust roadmap — 2026-10-13

This is the current development plan. The human approved two stages.
The compiler is not implemented or accepted by this roadmap reset.
The running default remains the accepted legacy M2 model service.

`CNL → Rust parser and fixed rules → DSL bundle → existing Rust code generator → Python files`

The first stage is the main change. The second stage keeps the current backend.
CNL uses fixed English sentences. Data text can include Chinese.
The flagship agent supplies business choices. The compiler does not guess them.
No Python parser, model training, weights or inference are needed in the new path.
Python remains the output language. Existing Python checks remain useful.

## M1 — CNL to DSL

[GitHub milestone 7](https://github.com/faberline/som/milestone/7) has three open issues.

| Issue | Work | Depends on |
|---|---|---|
| [#38](https://github.com/faberline/som/issues/38) | Freeze the English grammar, DSL bundle, defaults, examples and acceptance rules. | None |
| [#39](https://github.com/faberline/som/issues/39) | Parse CNL in Rust. Preserve typed values, order and scope. Report exact input errors. | [#38](https://github.com/faberline/som/issues/38) |
| [#40](https://github.com/faberline/som/issues/40) | Select approved blocks by fixed Rust rules. Emit the complete bundle and verify CNL/JSON equivalence. | [#38](https://github.com/faberline/som/issues/38), [#39](https://github.com/faberline/som/issues/39) |

The bundle contains the existing DSL and its full typed binding context.
It declares the grammar, contract, selection policy and block catalog versions and hashes.
It does not introduce a replacement semantic IR.
The existing prompt and DSL formats remain the inner formats.
The planner reads only the request, approved catalog and compiler rules.
It must not read corpora, saved answers or model weights.

M1 closes only when CNL covers the existing capabilities.
Valid input must produce a reproducible bundle.
Missing facts, conflicting facts, invalid types and invalid references must fail.
Errors must give a line, column, cause and allowed form.

## M2 — DSL to Code

[GitHub milestone 8](https://github.com/faberline/som/milestone/8) has four open issues.

| Issue | Work | Depends on |
|---|---|---|
| [#41](https://github.com/faberline/som/issues/41) | Feed checked bundles into the existing binding, rendering and formatting path. | [#40](https://github.com/faberline/som/issues/40) |
| [#42](https://github.com/faberline/som/issues/42) | Run the Rust service without model assets. Expose compile and assemble through HTTP and CLI. | [#41](https://github.com/faberline/som/issues/41) |
| [#43](https://github.com/faberline/som/issues/43) | Verify complete products, independent behavior, refusals and legacy compatibility. | [#42](https://github.com/faberline/som/issues/42) |
| [#44](https://github.com/faberline/som/issues/44) | Finish evidence and docs. Test fresh M2 rollback and compiler canary before default switching. | [#43](https://github.com/faberline/som/issues/43) |

Reuse block verification, `resolve(prompt, dsl)`, version-specific rendering and the pinned formatter.
Move compiler, catalog and quality asset loading out of the model directory dependency.
The backend must not add missing plan nodes or business steps.
A saved bundle and its declared local assets must be sufficient to generate all files.

These interfaces are planned. They are not available yet:

| CLI | HTTP | Result |
|---|---|---|
| `som compile <cnl-file>` | `POST /v1/compile` | Complete DSL bundle |
| `som assemble <bundle-file>` | `POST /v1/assemble` | Existing `status` and complete `files` response |
| Existing `som generate` | Existing `POST /v1/generate` | Compatible JSON prompt v1–v7 generation |

The CLI continues to call the local HTTP service.
CNL requires an explicit format marker. The service must not guess the format.
The server returns files. It does not write them into the caller project.
Health output must report the compiler purpose and asset hashes without claiming a loaded model.
New startup and generation must work without model weights or a Python production process.

## Acceptance and limits

Freeze compiler requirements, case selection and checks before implementation.
Cover fields, types, nulls, constants, operations, references, branches, transactions and compensation.
Verify order, scope, authorization, error behavior, recovery and invalid input.
The same input and pinned assets must produce the same canonical bundle and complete files.
Syntax, type checks and byte equality do not replace independent behavior tests.

Retain the existing 100 legacy cases and 214 cases comprising 14 services, 120 compositions and 80 former blind cases.
Report M2 formal114, development12, supplement12 and quality218 separately.
Formal114 and development12 are disjoint subsets of quality218.
Supplement12 is separate. This gives 230 distinct M2 products and 544 baseline jobs in total.
The original full behavior scope covers 126 products within quality218 and the 12 supplements.
The other 92 products retain their quality and parity scope. Do not call them full behavior passes.
New compiler cases need independent evidence. Old receipts are history, not new acceptance.

Keep the current file, block and flow structure limits.
Retire model sequence-token gates only in the new compiler path.
Compact output bytes remain an observation. Preserve the original legacy byte limits.
Measure time, memory and complete output bytes without inventing acceptance results.
Missing results, failures and skipped checks remain explicit.

This release connects existing capabilities. Multi-resource, multi-flow and new output languages are outside this scope.
No paid agent runs, real cloud runs or model-era token comparison gates are required.
Later capability extensions must add bounded contract, block and behavior tests.

## Reset and handoff

The old six milestones were archived and deleted.
The 25 previously open issues were closed as `not_planned` with replacement links.
The 12 already closed issues keep their original state, body and closure history.
New milestone titles reuse M1 and M2. GitHub numbers 7 and 8 identify the new records.
Old M1/M2 reports describe the previous model roadmap only.

The `m3` hourly automation is paused. Do not restart the retired M3 controller.
R6 stopped during admission after an AW refresh timeout. Its 835 recorded passes do not complete 2700 admissions.
R6 training and formal model evaluation did not run. Old M3 acceptance did not pass.
The five historical training attempts remain closed and unchanged.
Both current pointers remain legacy M2. The existing `76af/som` worktree changes remain preserved.
Models, runtimes, source archives and raw measurements remain local.

Read [the handoff](RUST_HANDOFF.md), [the evidence index](evidence/rust-roadmap-reset-2026-10-13.json),
[the old milestone archive](evidence/legacy-milestones-2026-10-13.md) and
[the exact new ticket plan](evidence/rust-roadmap-2026-10-13.json).
Start implementation with [#38](https://github.com/faberline/som/issues/38). All seven new issues remain open.

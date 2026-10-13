# M2 closeout — 2026-10-05

> Historical model-roadmap document. On 2026-10-13 the human replaced this plan with
> [the Rust roadmap](RUST_ROADMAP.md). Old milestone records were archived and deleted.
> Read [the current handoff](RUST_HANDOFF.md). Preserve the results below; do not resume old M3 training.
> New compiler implementation and acceptance remain pending. The default remains legacy M2.

All local acceptance gates pass on one unchanged r31 candidate.
GitHub issues #4–#6 and milestone 2 are closed.
The tracker readback is `evals/m2-flow-2026-10-04/r31/tracker-closeout-002/result.json`.
The complete audit is [evals/m2-flow-2026-10-04/r31/final-audit-002/result.json](evals/m2-flow-2026-10-04/r31/final-audit-002/result.json).

## Scope and decision

The human changed compact M2 final UTF-8 bytes to an observation.
The amendment is [evals/m2-flow-2026-10-04/r31/amendment.json](evals/m2-flow-2026-10-04/r31/amendment.json).
The policy is [contracts/m2-output-budget.v2.json](contracts/m2-output-budget.v2.json).
Keep 12 files, 64 blocks and at most 2,048 input and output tokens each.
Legacy formats keep their sealed size rules and exact output bytes.
All function, quality and native generation requirements remain fixed.

## Gate results

| Gate | Result | Evidence |
|---|---:|---|
| Normal assembly, strict quality and repeat formatting | 218/218 | [evals/m2-flow-2026-10-04/r31/training-prerequisite-001/result.json](evals/m2-flow-2026-10-04/r31/training-prerequisite-001/result.json) |
| Final Rust/Python file maps | 218/218 | [evals/m2-flow-2026-10-04/r31/all-artifact-parity-002/result.json](evals/m2-flow-2026-10-04/r31/all-artifact-parity-002/result.json) |
| Formal independent HTTP behavior | 114/114 | [evals/m2-flow-2026-10-04/r31/formal-acceptance-001/result.json](evals/m2-flow-2026-10-04/r31/formal-acceptance-001/result.json) |
| Frozen capability supplements | 12/12 | [evals/m2-flow-2026-10-04/r31/supplementary-acceptance-003/result.json](evals/m2-flow-2026-10-04/r31/supplementary-acceptance-003/result.json) |
| Old PostgreSQL cases | 100/100 | [evals/m2-flow-2026-10-04/r31/legacy100-001/result.json](evals/m2-flow-2026-10-04/r31/legacy100-001/result.json) |
| Standard and composition regressions | 214/214 | [evals/m2-flow-2026-10-04/r31/legacy-functional-audit-001/result.json](evals/m2-flow-2026-10-04/r31/legacy-functional-audit-001/result.json) |
| Fresh native legacy generation and archive equality | 314/314 | [evals/m2-flow-2026-10-04/r31/legacy-generation-002/result.json](evals/m2-flow-2026-10-04/r31/legacy-generation-002/result.json) |
| New seen-fixture Rust Metal, HTTP and CLI path | 12/12 | [evals/m2-flow-2026-10-04/r31/metal-e2e-002/result.json](evals/m2-flow-2026-10-04/r31/metal-e2e-002/result.json) |
| Codec, programs and typed refusals | 201/201 | [evals/m2-flow-2026-10-04/r31/codec-parity-002/result.json](evals/m2-flow-2026-10-04/r31/codec-parity-002/result.json) |
| Machine-readable structural schemas | 261/261 | [evals/m2-flow-2026-10-04/r31/schema-checks-002/result.json](evals/m2-flow-2026-10-04/r31/schema-checks-002/result.json) |
| Frozen legacy formats, refusals and quality contracts | 46 tests, zero skipped | [evals/m2-flow-2026-10-04/r31/legacy-format-tests-001/result.json](evals/m2-flow-2026-10-04/r31/legacy-format-tests-001/result.json) |
| Selectable block contracts | 150 full-plan blocks plus standalone INI | [evals/m2-flow-2026-10-04/r31/block-contract-coverage-002/result.json](evals/m2-flow-2026-10-04/r31/block-contract-coverage-002/result.json) |
| Owned process and database cleanup | passed | [evals/m2-flow-2026-10-04/r31/cleanup-002/result.json](evals/m2-flow-2026-10-04/r31/cleanup-002/result.json) |

Quality uses Ruff 0.16.6, strict mypy 2.3.1, Pylint 4.0.9 and isort 9.0.2.
Ruff format and Ruff check are separate command receipts.
Formal checks use real isolated PostgreSQL and MongoDB transactions.
They also use Redis and local identity and versioned object providers.
They test committed-write failures, rollback, compensation, stale leases,
recovery, concurrent writes, owners, CSRF, restart and migrations.
No product source is patched during checks.
The 12 extra products cover atomic capabilities absent from the formal corpus.

## Model evidence and its limits

The test model is [models/flow-m2-candidate/smoke-r31-001/](models/flow-m2-candidate/smoke-r31-001/).
It has two layers, width 64, four heads and seed 17.
Its vocabulary is 1,024 and its context is 4,096 tokens.
Only the 12 frozen development inputs are trained.
Their actual inputs and name-free structures do not overlap the formal set.
The first exact full-sequence checkpoint is step 2,000.
The earlier 1,000-step checkpoint and failures remain archived.

The 114 formal cases use reference DSL and prove product behavior.
The 12 model cases use actual native Metal output and prove the seen-input path.
They retain prompt, actual input, token IDs, raw output, DSL and final files.
Rust restores the model choices without adding or replacing blocks.
Observed native descendants contain no Python process.
Neither the server nor CLI writes generated project files.
This does not prove formal model accuracy or generalization.
Those checks and full training belong to M3.

## Observed size, memory and time

| Measurement | Value |
|---|---:|
| Largest prerequisite final output | 82,153 UTF-8 bytes |
| Complete model directory, including legacy assets | 53,514,434 bytes |
| Native peak resident memory | 335,036,416 bytes |
| Complete generation on the 12 seen fixtures | 6.785–8.039 seconds |

These are observations. They are not performance gates.
Stage timings remain in the Metal result.

## Integrity and preserved work

The source seal is [evals/m2-flow-2026-10-04/r31/source-seal-001/seal.json](evals/m2-flow-2026-10-04/r31/source-seal-001/seal.json).
Its SHA-256 is `3b92765f3eb2b6489532664d4ea3e3a5ea47c112745287505721a1ca0e29b077`.
The complete audit SHA-256 is `632f79eb66d45f5dd819939d685a8f764abb716c81199c461c8c21280dee8eb5`.
All results bind the same candidate source and fixed checker versions.
Build files and an old fixture environment disappeared during the run.
The cause is not proven. Their failure reports remain unchanged.
The rebuild was not byte-identical to the earlier executables.
All native gates ran again on the retained rebuilt executables.
The whole 214-case child checker passed before its later wrapper failed.
A separate audit verifies all 214 products and complete checker receipts.
The later 12-case supplement gate ran again and passed in a restored pinned environment.
Some original execution handles were lost in a desktop interruption.
Their receipts preserve actual child exit codes and a null parent exit code.
No parent exit code is invented.
The first aggregate audit used a wrong historical health path.
Its failure receipt remains. The complete corrected audit passes.
The source, model, codec, contracts, formatter and checker files are hashed.
The cleanup result confirms stopped owned services and zero PostgreSQL test databases.

All 33 protected assets and 130 original quality blocks are unchanged.
The default health and both `current` links are unchanged.
The default remains `crud-query-date-service-v4`.
Original worktree changes, failed rounds and direct-code baselines remain.
No paid agent evaluation, real cloud validation or M3 full training is run.
These artifacts are local work and have not been committed or pushed.

## Next phase

Read [M3_HANDOFF.md](M3_HANDOFF.md).
M3 needs new disjoint training, development and formal data.
Do not count these seen fixtures as blind model accuracy.
Claude gaps, real cloud, token adoption and `current` remain in M5.

## GitHub count observation

Fresh issue reads show #4, #5 and #6 are closed.
The open-issue list is empty. The closed list contains all three issues.
The milestone readback shows its state is closed.
Its separate summary counters still report 3 open and 0 closed.
The tracker evidence preserves this mismatch. It does not replace the counters.
Acceptance uses the individual states and complete issue lists.

# M3 handoff — 2026-10-12

M2 passed. Issues #4–#6 and milestone 2 are closed on GitHub.
Read [the M2 closeout](M2_CLOSEOUT.md) before work starts.
M3 remains open. The r6 round uses `flow-m3-r10` and v3 input bindings.
All 2,700 training and development reference products passed admission.
The controller and trainer stopped after an interruption.
The last complete checkpoint is step 74,000. Its raw check passed 3/300.
The log reaches step 75,000. This is not a complete checkpoint acceptance.
All original results and weights remain saved.
The shared time ledger still has an unfinished attempt. Settle it before
any restart. Do not reset its one-week total or drop failed attempts.
The model size, optimizer, seed and acceptance gates remain fixed.
The dataset has 2,400 training, 300 development and 300 formal cases.
Formal model checks, full protection and M3 promotion remain unrun.
Read [the current status](M3_STATUS.md) before work starts.

## Start the next session

1. Use the repository at `/Users/chrischeng/faberlines/som` on `main`.
   Read [M3_STATUS.md](M3_STATUS.md) and
   [the ticket plan](plans/m3-work-breakdown-2026-10-11.json).
2. Start [#21](https://github.com/faberline/som/issues/21).
   Record the interruption and settle the unfinished time-ledger attempt.
   A null elapsed value does not mean zero time.
3. Complete [#22](https://github.com/faberline/som/issues/22).
   Verify the saved M2 executable, health and rollback entry.
4. Complete [#23](https://github.com/faberline/som/issues/23).
   Diagnose the 3/300 raw development result from train and development data.
   Keep formal cases outside diagnosis and checkpoint selection.
5. Follow the ticket dependencies for training and acceptance.
   Keep the earliest raw 300/300 development checkpoint rule.
   Keep the formal Rust Metal constrained 300/300 rule.

The human approved landing the exact 602-file source list on main.
Weights, executables and raw evaluation records remain on this workstation.
No training restart is part of this handoff.
Check AW ownership before a new session writes.
The old session verified its AW identity and worktree claim during landing.
Register the new session's own identity. Do not reuse the old private token.
The local handoff record stores the final AW save and release receipts.
The local operational record is
`.local/main-landing-2026-10-11/HANDOFF.md`.
The human approved a development switch in the M3 plan.
The [switch receipt](evals/m3-flow-2026-10-05/r1/default-switch-001/result.json)
passes the M2 default change, its rollback startup and all 326 native cases.

## Accepted assets

- Source: `evals/m2-flow-2026-10-04/r31/source-seal-001/`.
- Complete gates: `evals/m2-flow-2026-10-04/r31/final-audit-002/result.json`.
- Tracker readback: `evals/m2-flow-2026-10-04/r31/tracker-closeout-002/result.json`.
- Seen-fixture test model: `models/flow-m2-candidate/smoke-r31-001/`.
- Blocks: `../som-code-python/data/blocks/flow-v7-compact/`.
- Contract: `contracts/related-flow.v1.md` and the v7/v3 schemas.
- Byte policy: `contracts/m2-output-budget.v2.json`.

Keep prompt, actual input, raw output, DSL and final files.
Keep all source, model, decoder, formatter, checker and dependency hashes.
Keep original failures, direct-code baselines and existing worktree changes.
The runtime build was lost. Retained rebuilt executables passed all native gates.
The whole 214-case child gate passed before its wrapper's later failure.
That failure remains. A separate complete audit verifies the 214 results.
All 12 later supplements pass in the restored pinned fixture.

## What M2 proves

All 218 normal products pass strict quality and final byte parity.
All 114 formal cases and 12 atomic supplements pass independent behavior.
All old 100 and 214 service regressions pass.
All 314 fresh native legacy outputs match sealed files.
All 12 actual seen-fixture sequences pass Rust Metal, HTTP and CLI.
The test model has 427,776 parameters and reached exact sequences at step 2,000.
Only the 12 frozen development inputs were trained.
This proves the seen-input path. It does not prove blind model accuracy.

## #7: Disjoint data

Use new training, development and formal inputs for full training.
Check actual model inputs and name-free composition structures for overlap.
Keep formal inputs out of training and checkpoint selection.
Keep Experiment, Observation, Document and Review out of training.
Cover every supported atom and valid capability pair in training.
Hold back unseen complete combinations for formal tests.
Report App, operation, unique-input and unique-combination counts separately.
The M2 fixtures and previously inspected Apps remain regression cases.
Preserve all existing data and evidence.

## #8: Reproducible full training

Train from scratch in Python. Use seed 17.
Use two layers, width 64 and four attention heads.
Use a 1,024-token vocabulary and a 4,096-token context.
Keep input and output at or below 2,048 tokens each.
Check complete development sequences every 1,000 steps.
Keep the earliest checkpoint with all development sequences correct.
Limit cumulative training to one week.
Save tokenizer, weights, config, data and all file hashes.
Use a new model directory. Preserve the M2 test weights.

## #9: Native decoding and failures

Keep inference, decoding, validation and assembly in Rust.
Preserve each model block, order, transaction and reference choice.
Wrong or missing choices must fail. Do not insert answers or use legacy fallback.
Accept legal alternatives. Reject unsupported requirements clearly.
Repeat native Metal, HTTP, CLI, byte parity, refusal and behavior gates.
Retain strict Ruff, mypy, Pylint and isort requirements.
Keep 12 files, 64 blocks and the model token limits.
Record final UTF-8 bytes, time and memory as observations.

## Deferred work

The development default now uses the accepted M2 model and compact blocks.
Keep its persistent executable and v4 rollback record.
Keep old direct-code measurements. Add a baseline only for a new App.
Paid agent comparisons, Claude gaps, real cloud checks, token adoption
and formal adoption remain in M5.

The prior detailed handoff is preserved at
`evals/m2-flow-2026-10-04/r31/closeout-docs-001/M3_HANDOFF-before.md`.

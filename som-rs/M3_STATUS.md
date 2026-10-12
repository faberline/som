# M3 status — 2026-10-11

M3 is not complete. The accepted development model remains M2.
The current M3 round is `m3-flow-2026-10-05/r6`.
Its dataset is `som-core/data/flow-m3-r10`.
Its candidate is `models/flow-m3-candidate/r6-r10-001`.

## Verified progress

| Gate | Result | Scope |
|---|---|---|
| Data split | Pass | 2,400 train, 300 development, 300 formal cases |
| Split overlap | Zero | Full input, name-free structure and operation/reference graph |
| Capability coverage | 90 combinations | Training and each held-out split |
| Reference admission | 2,700/2,700 | Strict quality and independent behavior; train and development only |
| Python/Rust model input | 2,700/2,700 | v3 bindings; legacy input and targets unchanged |
| Legal token sets | 135/135 | Reference parity checks; no formal model accuracy claim |
| Normal assembler and refusals | 201/201 | 163 valid and 38 invalid inputs |
| Pretraining output protection | 544/544 | Byte checks only; no new full behavior acceptance |
| Last complete raw development check | 3/300 | Checkpoint 74,000 |
| Latest training log | Step 75,000 | No complete acceptance receipt for this step |
| Formal constrained model generation | Unrun | Requires the earliest raw 300/300 development checkpoint |
| Formal raw model comparison | Unrun | Must use the same selected checkpoint |
| Final regressions and default switch | Unrun | M3 remains open |

The two test Apps remain outside training and development.
They are Experiment/Observation and Document/Review.
The input limit is 2,048 tokens. The output limit is 2,048 tokens.
The r10 training maxima are 982 input and 1,680 output tokens.

## Interruption and next step

The stored controller state is `failed_or_interrupted`.
It records `KeyboardInterrupt('M3 controller interrupted')`.
The original controller PID 34521 and trainer PID 24247 are absent.
Do not treat their old launch records as live processes.

The time ledger has one unfinished attempt.
Its three closed attempts used 33,090.980, 40,931.669 and 18,142.853 seconds.
The interrupted attempt still has `elapsed_seconds: null`.
This value does not mean zero time. Settle it from retained evidence first.
The shared limit stays at 604,800 seconds.

First record the interruption and settle the time ledger.
Then diagnose the low raw complete-sequence score from train and development.
Do not use formal inputs for diagnosis or checkpoint selection.
Keep the fixed model size and all acceptance rules.
Verify source identity and admission before any new training action.
If recovery needs new source, use a new sealed round.
Keep the old failures and receipts.

## Development service

`models/current` points to `flow-m2-candidate/smoke-r31-001`.
`../som-code-python/data/blocks/current` points to `flow-v7-compact`.
Both pointers remain unchanged.
The local health request failed during this handoff check.
Check the saved M2 executable and startup record before restoring service.
See [CURRENT.md](CURRENT.md) for the accepted command and rollback proof.

## Evidence and local assets

The [progress index](evidence/m3-2026-10-11-progress.json) stores source hashes,
receipt hashes and the scope of each result.
It does not replace full acceptance evidence.

Raw evaluation records stay under `evals/` on the original workstation.
Weights and checkpoints stay under `models/`.
Executables and environments stay under `runtime/`.
These local assets are excluded from Git. They are not deleted.
The historical `som-core/data/composed/train.jsonl` also stays local.
It exceeds GitHub's file limit. Its original manifest stays in the repository.
Some native and historical tests need these retained local assets.
A fresh clone has source and test data, but it does not have trained weights.

## Remaining work

The data parent [#7](https://github.com/faberline/som/issues/7) is complete.
Training parent [#8](https://github.com/faberline/som/issues/8) stays open.
Native acceptance parent [#9](https://github.com/faberline/som/issues/9) stays open.
M3 stays open. Its 19 child tickets have 2 complete and 17 open results.
Each ticket has one bounded scope, dependencies and acceptance checks.

| Ticket | State | Work |
|---|---|---|
| [#19](https://github.com/faberline/som/issues/19) | Complete | Data split and zero overlap |
| [#20](https://github.com/faberline/som/issues/20) | Complete | Reference quality and behavior admission |
| [#21](https://github.com/faberline/som/issues/21) | Open | Interruption and time accounting |
| [#22](https://github.com/faberline/som/issues/22) | Open | M2 service and rollback entry |
| [#23](https://github.com/faberline/som/issues/23) | Open | Raw development failure diagnosis |
| [#24](https://github.com/faberline/som/issues/24) | Open | Fixed-model training and earliest checkpoint |
| [#25](https://github.com/faberline/som/issues/25) | Open | Candidate manifest and load data |
| [#26](https://github.com/faberline/som/issues/26) | Open | Constraint sets, alternatives and refusals |
| [#27](https://github.com/faberline/som/issues/27) | Open | Selected-model Metal, HTTP and CLI |
| [#28](https://github.com/faberline/som/issues/28) | Open | Formal constrained generation of 300 plans |
| [#29](https://github.com/faberline/som/issues/29) | Open | Formal behavior and quality, cases 1–100 |
| [#30](https://github.com/faberline/som/issues/30) | Open | Formal behavior and quality, cases 101–200 |
| [#31](https://github.com/faberline/som/issues/31) | Open | Formal behavior and quality, cases 201–300 |
| [#32](https://github.com/faberline/som/issues/32) | Open | Same-checkpoint raw formal comparison |
| [#33](https://github.com/faberline/som/issues/33) | Open | Final byte parity and refusal categories |
| [#34](https://github.com/faberline/som/issues/34) | Open | Full legacy 100 + 214 protection |
| [#35](https://github.com/faberline/som/issues/35) | Open | Full M2 protection matrix |
| [#36](https://github.com/faberline/som/issues/36) | Open | Full-scope audit, evidence and report |
| [#37](https://github.com/faberline/som/issues/37) | Open | Safe promotion, tracker readback and cleanup |

Start with [#21](https://github.com/faberline/som/issues/21) to settle the time ledger.
Restore the M2 entry with [#22](https://github.com/faberline/som/issues/22).
Then use [#23](https://github.com/faberline/som/issues/23) for model diagnosis.
The [machine-readable plan](plans/m3-work-breakdown-2026-10-11.json)
stores the same ticket IDs and dependencies.
Do not close M3 or switch its default from partial checks.

Paid agent runs, real cloud checks and adoption decisions remain deferred.

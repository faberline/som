# M3 status — 2026-10-12

M3 is not complete. The accepted development model remains M2.
The current M3 round is `m3-flow-2026-10-05/r6`.
Its dataset is `som-core/data/flow-m3-r10`.
Its candidate is `models/flow-m3-candidate/r6-r10-001`.
The recovery work for #21, #22 and #23 is complete.
Their results are saved on GitHub. All three tickets are closed as completed.
The [recovery index](evidence/m3-2026-10-12-recovery.json) binds the new receipts.
Recovery receipts stay in the `76af/som` worktree on this workstation.
The original model and runtime assets stay in `/Users/chrischeng/faberlines/som`.

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

The shared time ledger now has five closed attempts and no unfinished attempt.
The first three attempts are unchanged.
The interrupted attempt is charged 138,741.475 seconds.
This is conservative wall time through a fresh check of process absence.
It includes sleep, failure and downtime. The exact stop time is unknown.
The original ledger with its null elapsed value remains archived.
The diagnosis adds 491.268 seconds to the same ledger.
The total is 231,398.245 seconds, or 64.28 hours.
The remaining time is 373,401.755 seconds, or 103.72 hours.
The shared limit stays at 604,800 seconds. No attempt was dropped.

The saved checkpoint has weights and a manifest.
It has no saved Adam state, random-number state or batch position.
Exact continuation from step 74,000 is not supported by the retained evidence.
The trainer exit code remains unknown.
The original launch arguments, interruption, log and checkpoint are archived.

## Development diagnosis

The full raw replay remains 3/300 at checkpoint 74,000.
All 300 first-error positions and token choices match the original receipt.
They also match checks with the correct output prefix supplied to the model.
One separate case gives the same output alone and in a batch.
All 2,700 train and development inputs pass lossless encoding, v3 semantic
recovery, tokenizer and output loss-mask checks.
Formal inputs were not opened for diagnosis. No optimizer update was made.

The average development token accuracy is 99.23%.
The flow section is 95.08%. The other sections are 99.94%.
The flow section contains 93.23% of the token errors.
A fixed 16-case training sample passes only 1/16 complete raw sequences.
This sample is a diagnosis control. It is not full training-set accuracy.
An eight-case source-swap control raises average loss from 0.02236 to 0.08962.
The model uses the input, but it has not learned complete flow composition.
These controls found no encoding, loss-mask or evaluator error.

| First error | Failed cases | Later aligned token edits |
|---|---:|---:|
| Block or operation choice | 133 | 680 |
| Node order or name | 16 | 2,407 |
| Transaction or undo | 6 | 853 |
| Reference | 101 | 7,067 |
| End token | 0 | 0 |
| Structure or length | 21 | 12,181 |
| Argument or schema | 20 | 1,143 |

The first-error column covers all 297 failed cases once.
The later column counts aligned token edits, not independent failures.
Repeated tokens can make an alignment ambiguous.
The refined v2 error summary corrects extra-token classification.
The original r1 summary remains preserved.
All 300 outputs contain an end token.
It appears earlier than the reference in 117 cases and later in 123 cases.
Only 60 outputs have the reference length. Only three are fully correct.

The testable repair proposal is to emit the root keys as `flow`, `files`,
then `version` in a new M3 training target policy.
This puts flow information first while the model still emits every file and block.
The current flow-key offset is 1,126–1,392 tokens. The proposed offset is one.
All 2,700 reordered reference sequences decode to the same plans and keep
the same output token lengths. No accuracy improvement is claimed.
This proposal has not changed the trainer or the frozen targets.
It requires a new sealed source round and complete admission before training.
Keep the fixed model, Adam settings, seed and every acceptance rule.
Keep formal cases outside diagnosis and checkpoint selection.

## Development service

`models/current` points to `flow-m2-candidate/smoke-r31-001`.
`../som-code-python/data/blocks/current` points to `flow-v7-compact`.
Both pointers remain unchanged.
The retained M2 executable was started again on `127.0.0.1:8765`.
Its fresh health response matches the accepted M2 response.
One new prompt and one legacy prompt return complete 12-file CLI responses.
Both responses equal their saved responses exactly.
The saved v4 rollback also starts on test port 18765 and passes its old CLI case.
The owned rollback test process was stopped. The M2 default remains running.
These are entry checks. They do not replace the full M3 acceptance gates.
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
M3 stays open. Its 19 child tickets have five complete and 14 open results.
Each ticket has one bounded scope, dependencies and acceptance checks.

| Ticket | State | Work |
|---|---|---|
| [#19](https://github.com/faberline/som/issues/19) | Complete | Data split and zero overlap |
| [#20](https://github.com/faberline/som/issues/20) | Complete | Reference quality and behavior admission |
| [#21](https://github.com/faberline/som/issues/21) | Complete | Interruption and time accounting |
| [#22](https://github.com/faberline/som/issues/22) | Complete | M2 service and rollback entry |
| [#23](https://github.com/faberline/som/issues/23) | Complete | Raw development failure diagnosis |
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

GitHub readback confirms #21–#23 are closed as completed.
The human authorized document publication and continued training under #24.
Next, #24 requires a chosen source policy, a new source seal and complete
admission before fresh training. No training restart occurred in this recovery.
The [machine-readable plan](plans/m3-work-breakdown-2026-10-11.json)
stores the same ticket IDs and dependencies.
Do not close M3 or switch its default from partial checks.

Paid agent runs, real cloud checks and adoption decisions remain deferred.

# M2 flow work — 2026-10-05

M1 and M2 have passed. Their issues and milestones are closed.
See [the M2 closeout](M2_CLOSEOUT.md) and [M3 handoff](M3_HANDOFF.md).
The accepted compact source and evidence are under `evals/m2-flow-2026-10-04/r31/`.
Output bytes are observations. The other acceptance gates remain fixed.
The default stays unchanged.

## Historical r1 prototype — 2026-10-02

The sections below retain the original prototype results and failures.
They do not describe the accepted r31 candidate.

## Implemented draft

- `som.prompt.v7` declares typed inputs, ordered actions, branches,
  transaction scopes and compensation.
- `som.dsl.v3` separates generation bindings, runtime results and program
  symbols. The validator preserves block choices and refuses wrong choices.
- Python and Rust share the semantic rules and compact token codec.
  The codec exposes resource types, dataflow, permissions and transactions.
- New blocks provide a shared flow module, database journals, revision
  checks, leases, run ownership and version-aware object compensation.
  These runtime blocks remain prototypes until the full behavior checks pass.
- The flow routes are part of the generated service. They do not change
  SOM's generation API or make its CLI write project files.
- The Rust `flow_plan` model path is prepared. It has no accepted test
  weights yet. Production v7 requests return `MODEL_UNAVAILABLE`.
- Original block objects, model files, decoders' old output behavior,
  paid-agent baselines and failed attempts remain preserved.

The public structural schemas are
[prompt v7](contracts/som.prompt.v7.schema.json) and
[DSL v3](contracts/som.dsl.v3.schema.json).
The semantic rules are in
[related-flow.v1](contracts/related-flow.v1.md).
Passing a structural schema does not prove semantic or runtime correctness.

## Verified evidence

All counts below come from saved results in
`som-rs/evals/m2-flow-2026-10-02/r1/`.

| Check | Result | Limit of the evidence |
|---|---:|---|
| Structural schema checks | 261 passed | 253 valid structures and 8 invalid structures |
| Python contract and compatibility tests | 90 passed | Includes operation subsets and 17 GCS boundary tests |
| Rust tests | 16 passed, 3 ignored | Ignored tests do not count as Metal acceptance |
| Python/Rust semantic and lowering parity | 162 passed | 127 valid plans and 35 rejection cases |
| Scalar type and float spelling parity | 20 passed | Includes bool/int, float/int and signed-zero rejections |
| Python/Rust formatted draft byte parity | 127/127 | Plans came from the reference, not a new model |
| Repeated formatting | 127/127 unchanged | Final draft bytes are stable |
| Budget rejection parity | 127/127 | Both reject with `SCOPE_TOO_LARGE` |
| Static quality draft samples | 3/3 | PostgreSQL, MongoDB and GCS samples; not the full matrix |
| New Rust binary old-output replay | 314/314 | All final files equal the M1 archive |
| v7 generation gate | HTTP and CLI checked | Rejection is explicit; CLI exits with code 2 |

Each quality sample passed Ruff 0.16.6, formatting, strict mypy 2.3.1,
Pylint 4.0.9 and isort 9.0.2. The fixed profile stayed intact.
No new exception was added. The original approved SDK exceptions still apply.

The replay used the archived model on Metal with the new Rust binary.
It covered 14 services, 120 compositions, 80 former blind compositions and
100 legacy tasks. The complete responses also match the old archived
responses. Those bytes retain M1's independent behavior evidence.
This replay does not test the new flow runtime or new `flow_plan` weights.

GCS boundary checks prove that missing or foreign ownership keeps an object.
Bad generations and malformed metadata produce a controlled error.
Deletion uses the exact owned generation. These are isolated helper tests.
They do not replace real provider failure and recovery checks.

The scalar repair distinguishes `true`, `1` and `1.0`.
It also preserves negative zero and Python's exponent spelling.
It changes the new flow path. The legacy serializer stays intact.

V7 now uses its async PostgreSQL pool for health and CRUD reads.
One connection helper shares a live transaction and closes temporary reads.
These changes removed 473 bytes from the largest PostgreSQL draft.
The fixed quality checks still pass on the three samples.

## First product gate failed

The fixed limit is **65,536 formatted bytes**, across the complete output.
It includes tests, migrations, settings and instructions.
The assembler still enforces 12 files and 64 blocks.
Model input and output each remain limited to 2,048 tokens.

| Probe | Formatted bytes | Files | Blocks | Input / output tokens | Budget |
|---|---:|---:|---:|---:|---|
| New 114 formal cases | 85,484–106,445 | At most 12 | At most 64 | Within 2,048 each | 0/114 passed |
| Eight fields per resource, PostgreSQL | 107,422 | 12 | 58 | 733 / 1,655 | Failed |
| Eight fields per resource, MongoDB | 105,370 | 11 | 52 | 733 / 1,485 | Failed |

All supported formal refusals count as failures.
The 127 diagnostic passes are **not** product passes.
No accepted new product, model accuracy or full runtime result is claimed.

The formal cases exceed the limit by 19,948–40,909 bytes.
The largest PostgreSQL probe must lose at least 41,886 bytes.
Raising the limit is not allowed.

The largest PostgreSQL draft has about 23 KiB of flow code, 16 KiB of database
code and 21 KiB of integration code. Its app, schemas, models, migration,
tests and settings add the remaining size. A shared flow module and one
selected workflow helper already replaced repeated helpers.
That change was insufficient. The next implementation must refactor the
shared runtime and duplicated boundaries while keeping all required behavior,
typing and tool rules. It must keep the standalone output contract.

## Sealed next-stage data

The current corpus is `som-core/data/flow-v7-r10/`.
It contains 12 development smoke cases and 114 formal cases.
The formal set has all 90 database/auth/storage/workflow combinations and
24 semantic cases. Actual model inputs and structures with names removed
have zero overlap between development and formal sets.
Experiment, Observation, Document and Review remain excluded from training.

`acceptance-seal-002/` freezes the schemas, contract, source, public block
metadata, positive cases, 25 prompt rejections and independent check needs.
It was made before any new test weights.
The specification archive SHA-256 is:

`440a139ed98c69bf6e74ef428a8dbdc26eb85bc07f444d84f2872b76faa92d43`

The development and formal prompt files equal r9 byte for byte.
Adding the separate v7 database block changed scalar binding indices.
R10 updates the actual model-input ledger and binds it to the block hashes.
The seal now recomputes both overlap checks before it accepts that ledger.
It does not trust stored zero-overlap counts alone.

Earlier corpus revisions, seals and failed checks remain intact.
Formal cases may not train test weights or select a checkpoint.
Development smoke weights cannot prove formal accuracy or generalization.

## Remaining M2 gates

1. Reduce the final output to every fixed budget. Recheck the maximum
   capability and field combinations first.
2. Check all final artifacts with the quality tools. Repeat byte parity,
   rejection parity, format stability and old-output checks.
3. Train development-only test weights. Prove full Rust Metal decoding,
   validation and assembly. Complete the v7 manifest and HTTP admission path.
4. Run the independent full local behavior matrix. It must cover branch
   paths, same-session commits, late failure, revision conflicts, exact
   provider versions, lost replies, interruption, restart, duplicate keys,
   concurrent recovery, owner access, CSRF and migrations.
5. Close issues #4–#6 and M2 only after all gates pass.

The existing local fixture needs version-aware GCS and failure controls for
M2. The saved check requirements are not a completed runtime oracle.
There is no new flow memory or generation-time measurement yet.
Missing measurements remain unavailable, not zero.

## Saved receipts

- `snapshot-002/result.json`: hashes, preservation checks and all gate states.
- `snapshot-002/source-supplement.tar.gz`: exact source and Rust binaries.
  SHA-256:
  `dc35975b84d27cfa09899fc9fc48864841a8a9b48569f5267b169f4d540b962c`.
- `legacy-new-binary-002/comparison.json`: old-output replay and complete chains.
- `semantic-parity-008/result.json`: semantic and rejection classifications.
- `scalar-parity-004/result.json`: concrete scalar and float cases.
- `artifact-parity-005/result.json`: draft byte identity and budget refusals.
- `schemas-004/result.json`: structural schema checks.
- `contract-tests-012.log` and `rust-tests-004.log`: test results.
- `quality-diagnostic-postgresql-005/`, `quality-diagnostic-mongodb-006/`
  and `quality-diagnostic-gcs-002/`: complete static check logs.
- `budget-010/result.json`: maximum field probes and all formal cases.
- `generation-gate-002/`: health, HTTP rejection and CLI response.

Earlier failures and invalidated runs remain saved.
`artifact-parity-004` changed its catalog during execution and is invalid.
`contract-tests-011` exposed the old binding ledger after the block change.
`schemas-003` exposed the stale schema enum. Both guards were repaired.
Those runs do not count as passing evidence.

All work is local and uncommitted. No paid-agent runs or full M3 training
started. `current` and the permanent direct-agent data remain unchanged.
M5 still owns the Claude Experiment gap, real cloud checks, token adoption
and any default switch.

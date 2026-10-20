# Rust compiler acceptance report — 2026-10-20

## Python self-name fix release (#57, #58) — 2026-10-20

Since `2026-10-20T08:39:04.671848+00:00` the default on `127.0.0.1:8765` is rust-output-candidate-006.
A Python resource field or foreign key named `self` now builds and runs (#57).
compiler-v1 refuses a name that collides with a generated Python identifier with INVALID_VALUE, for example a v3 filter
field named limit or offset (#58), so the Rust target refuses nothing that compiler-v1 admits.
The post-switch check passed 6/6 checks with all 10 canary cases.
Binary SHA-256: `52658e13af1ac08659ef1dda7e333634f4ae3893731304e09edec6ca5f9b87c2`, built from commit `6116fed549`.
Asset manifest `d95d93c764d95373480b580cfa672de91c55f8f06a5446a786bff61395aeccaa`, Python backend `581ce38f…`.
Python product files are byte-identical; only the bundle asset descriptors and payload hash changed
(rust-output-v1 amendment 004). Bundles assembled by an earlier default get ASSET_HASH_MISMATCH: compile them again.
The contract changes are compiler-v1 amendments 003 and 004 and rust-output-v1 amendment 004, each sealed after an independent review.
See the [acceptance evidence](evidence/rust-output-acceptance-2026-10-20-issue57.json) and the
[release evidence](evidence/rust-output-release-2026-10-20-issue57.json).

| #57/#58 check | Actual result |
|---|---|
| Python replay on candidate 006 | 2005/2005 exchanges; product files of 127/127 public products and 544/544 legacy jobs (legacy100 100, legacy214 214, quality218 218, supplement12 12) unchanged; 671 bundles assembled again after the expected asset break |
| `self` cases | 18 positive cases (v1 to v7, PostgreSQL and MongoDB, fields, filter fields and foreign keys) passed in Python and passed their Rust gates; 48/48 negative cases passed |
| Name collisions (#58) | refused with INVALID_VALUE; no admitted example (16), CNL fixture (238), typed fixture (7), public product (127) or legacy job (544) is affected |
| Rust target unchanged | phase 2 584/584, 582 product file sets equal; RS12 and RS13 now refused by compiler-v1 |
| Other gates | Python controls 32 passed, validate cases 6, CLI 39/39, target negatives 32/32, target examples 24/24, source forms 817/817 |
| Independent audit | 16 passed, 0 failed, 0 unrun |
| Canary inputs | 5 Python cases (29 steps: smoke12, legacy100 and 3 `self` cases), 5 Rust cases (25 steps), 2 M2 cases |
| Rust canaries on the local emulators | example-v1 and example-v5: product gates 2/2; RS03_JSON_V6, RS04_JSON_V7 and CAP_MONGODB_FIREBASE_GCS_CSV: behavior 3/3 |
| Rehearsal | switch, restore to the #56 default and the 4 injected failures (after stop, start, pointer, commit) all passed; 8765 unchanged |
| Live switch | committed with no rollback under the user's standing instruction; previous owner pid 507 stopped, new owner pid 61463 |
| Post-switch check | owner, health, pointers, legacy rollback assets, canaries, owner after: all passed |

Not run: the som-core tests that need fastapi (the local venv lacks it and PyPI is not approved); compiler-v1 amendment 004 records them.

## Rust output phase 2 release (#56) — 2026-10-20

From `2026-10-20T03:14:10.975162+00:00` to the #57 switch, the default on `127.0.0.1:8765` was rust-output-candidate-004.
It serves the Python target and the phase 2 Rust target: every prompt compiler-v1 admits, except a v3 filter field named limit or offset.
The post-switch check passed 6/6 checks with all 7 canary cases.
Binary SHA-256: `d2de75223496665cd563b51daa51741faa337c9cc2d46d8af8cc596b9f399b9b`, built from commit `69bb1fe682`.
The asset manifest (`872eb4dc…`) and the Python backend (`1c182570…`) are unchanged, and Python output is byte-identical.
The phase 1 default (`05f1bfe6…`), the compiler default before it (`3fad3cb7…`) and the legacy M2 rollback assets are kept.
See the [release evidence](evidence/rust-output-release-2026-10-20.json).

| #56 check | Actual result |
|---|---|
| Phase 2 acceptance (#55) on candidate 004 | 584/584 phase 2 cases, only RS12 and RS13 refused; 115/115 phase 1 cases unchanged ([evidence](evidence/rust-output-acceptance-2026-10-20-phase2.json)) |
| Python regressions (#55) | 2005/2005 replayed exchanges, 127/127 public products, 544/544 legacy jobs |
| Independent audit (#55) | 35 passed, 0 failed, 0 unrun |
| Canary inputs | 2 Python cases (14 steps) from a stand-in on a harness port, 5 Rust cases (25 steps), 2 M2 cases |
| Rust canaries on the local emulators | example-v1 and example-v5: product gates 2/2; RS03_JSON_V6, CAP_MONGODB_FIREBASE_GCS_CSV and RS04_JSON_V7: behavior 3/3 |
| Rehearsal | switch, restore to the phase 1 default and the 4 injected failures (after stop, start, pointer, commit) all passed; 8765 unchanged |
| Live switch | committed with no rollback after the user's approval; previous owner pid 96083 stopped, new owner pid 507 |
| Post-switch check | owner, health, pointers, legacy rollback assets, canaries, owner after: all passed |

Not done in #56: [#57](https://github.com/faberline/som/issues/57), the Python 500 on a field or foreign key named self (fixed in the section above).

## Rust output release (#51) — 2026-10-16

From `2026-10-16T05:54:52.297481+00:00` to the #56 switch, the default on `127.0.0.1:8765` was rust-output-candidate-003.
It served the Python and Rust targets. The independent post-switch check passed 6/6 checks with every Python and Rust canary.
Binary SHA-256: `05f1bfe6eef7045f054b1323a193e8f7bea1b4afe4f287a47cd0d0f9347ae11c`, built from commit `24c0466eb7`.
The asset manifest is unchanged: `872eb4dc162e54c0cf4ada196652417161cffc3cb4a0382d987fc668f94d416e`.
Python output is byte-identical: the pinned replies of the previous default replay exactly on the new one.
The previous compiler default (`3fad3cb7…`) and the legacy M2 rollback assets are kept. See the [release evidence](evidence/rust-output-release-2026-10-16.json).

| #51 check | Actual result |
|---|---|
| Acceptance plan on candidate 003 | 13/13 steps passed, no blockers ([run summary in the evidence](evidence/rust-output-release-2026-10-16.json)) |
| Independent audit | 10/10 checks, 97/97 subchecks passed; the auditor is the #50 auditor with constants changed only |
| Canary inputs | 2 Python cases (14 steps), 2 Rust cases (10 steps), 2 M2 cases |
| Rehearsal | switch, restore and the 4 injected failures (after stop, start, pointer, commit) all passed; 8765 unchanged |
| Live switch | committed with no rollback after the user's approval |
| Post-switch check | owner, health, pointers, legacy rollback assets, canaries, owner after: all passed |

## Compiler acceptance (#38–#44) — 2026-10-14

The new Rust compiler passed independent acceptance and is the local default.
It was promoted at `2026-10-15T03:08:08.737006+00:00` and serves `127.0.0.1:8765` without loading a model.
The root post-switch check passed 76/76. Commit `3ced191137` published it to main.
Issues #38–#44 are closed as completed, and milestones 7 and 8 are closed.
The legacy M2 runtime, model and block links remain saved for rollback.

`CNL → Rust parser and fixed rules → complete DSL bundle → existing Rust backend → Python files`

CNL uses fixed English clauses. Data strings can include Chinese.
The caller supplies each business choice. The compiler does not guess missing facts.
The new production path does not load models or start Python.
Python remains the output language and an independent test tool.

## Candidate identity

Candidate binary SHA-256: `3fad3cb7eafb35274f6a37f9a8a371f93807a6b5fedf3e9fe0e074cbf5bfef02`.
Source receipt SHA-256: `1e80e26f0128e99fe7fa3c4fcf6edae354e51bf75b13f3df70833107906d78bd`.
Canonical asset manifest SHA-256: `872eb4dc162e54c0cf4ada196652417161cffc3cb4a0382d987fc668f94d416e`.
Raw asset manifest file SHA-256: `d215d30b5ab379c0a7ff2b5191393e39106a722403863181c77d2b5ea2a7c020`.
Independent audit SHA-256: `071c5ddc09e6f879ab2079cdcfa6ab6f75a60040efbcb2516568a1431b11b792`.
Independent audit completed at `2026-10-15T02:47:46.475001+00:00`.
The fixed evidence dates identify the cohort, not the acceptance completion date.
The independent audit passed 1215 checks, with zero failures or unrun checks.
Its counts include evidence-integrity checks. They are not all product test cases.
The first final audit run with the original auditor failed because it read newline-terminated raw base64 files strictly.
That run failed the legacy raw-response check, the discharge check derived from it, and the additive check that requires the original audit to pass.
That failed output is preserved. The amended auditor ignores ASCII whitespace only and adds negative tests; its qualification is recorded with the audit execution.

## Acceptance

| Check | Actual result |
|---|---|
| Rust build and focused Rust tests | Release build and 37 tests passed |
| Runtime boundary | 37/37 passed |
| Structural and refusal gaps | 31/31 passed |
| HTTP diagnostics | 101/101 passed |
| Frozen contract sections | 119 CNL/JSON pairs, 40 typed cases, 15 CNL refusals, 11 canonical vectors, 14 bundle cases passed |
| Complete public products | Fresh 127/127 passed |
| Historical protection | Fresh 544/544 jobs passed; 540 distinct prompts |
| Renderer extension | 11/11 compile, strict quality, and focused behavior checks passed |
| Switch and recovery rehearsals | Six isolated real-binary scenarios passed; 1,206 independent raw checks passed |
| Post-commit M2 restore | Nine fresh isolated lifecycle variants met their expected outcomes; actual wait and independent raw review passed |
| Performance observation | 84 successful requests and 84 complete response comparisons |
| Live default switch | Committed with no rollback; fresh M2 clone and compiler canaries passed before the change; two post-switch canary rounds passed on 8765 |
| Root post-switch check | 76/76 passed: owner, binary, assets, both current links, health, full HTTP, old/new CLI generate, CNL/JSON compile and saved-bundle assemble |
| Main publication | Commit `3ced191137` pushed to `main`; live remote `main` read back |
| GitHub closeout | #38–#44 closed as completed with evidence comments; milestones 7 and 8 closed with 0 open issues; read back at `2026-10-15T03:16:50.837095+00:00` |

## Scope and preserved history

The 544 jobs comprise legacy100, regression214, quality218, and supplement12.
Regression214 comprises 14 services, 120 compositions, and 80 former blind cases.
Formal114 and development12 are disjoint subsets of quality218.
They are not extra jobs. Supplement12 is a separate group.
Within quality218, 126 products have original full behavior checks.
The other 92 have quality and parity checks only.
The 12 supplements also have full behavior checks.
The inventory keeps three duplicate prompt groups and their distinct jobs or plans.
This release does not claim 544 identical behavior scopes.

The original legacy003 runner remains incomplete and failed.
Its durable receipt has 214 passes and 100 child-import tool failures.
The first quality case has no durable outcome. It remains one unknown case.
The other 229 cases remain unrun in that attempt.
A separate real diagnostic also failed on SQLAlchemy quoted_name keys.
These observation keys are string subclasses. The first evidence writer rejected them.
Both failures came from checker launch or evidence handling.
They do not prove a product failure.
A bounded additive adapter fixes child imports and typed evidence writes.
Run004 remains failed and interrupted.
It records 325 passes, one failure, one interrupted case, and 217 unrun jobs.
The actual worker and controller exited with code one.
The blind80-0023 failure has an unknown cause. No cause or fix is inferred.
A separate same-source v3 diagnostic passed its full quality and behavior checks.
Its actual worker exited with code zero. Its independent review passed 37 checks.
It is a historical prerequisite. It does not count toward the 544 gate.
Its pass does not change the original failure or prove its cause.
The new run005 executes all 544 jobs again. It does not copy old passing rows.

The original public run remains 125/127 and failed.
Its two reference comparisons are preserved.
A bounded checker correction transforms one raw AST assignment span in each case.
The proof preserves the rest of the file and the full program meaning.
The whole 127-case cohort was then run again with the same candidate and assets.
The new complete result does not combine old passing rows with diagnostic reruns.

The unchanged full-M2 checker passes one renderer case and fails ten.
Those exact historical failures remain failures.
Fresh scoped compile, quality, and focused behavior checks pass all eleven.
They do not turn the ten old failures into full-M2 passes.

Nine rows remain UNRUN in the original frozen-suite receipt:
`bundle_saved_replay`, `equivalent_cnl_json`, `all_versions`, `checked_variant`,
`local_asset_tamper`, `self_signed_asset_tamper`, `missing_asset`,
`structural_limits`, and `large_valid_compact_output`.
The final independent audit checks their separate runtime, gap, public,
and legacy evidence. It does not rewrite the original frozen result.

Compiler, bundle, and generated-literal checks preserve float-zero signs.
PostgreSQL JSONB can store -0.0 as 0.0. Recorded durable-value checks keep
this representation limit explicit. They do not claim runtime bit preservation.

No new resource shapes, multiple flows, new output languages, paid agents,
or real cloud services are part of this release.
The retired training archive was verified in Drive before exact cleanup.
The cleanup removed 55 named dataset files and retained 37 files.
The [cleanup and Drive index](evidence/rust-training-data-archive-cleanup-2026-10-14.json)
records all 92 member hashes, archive identity, and restore instructions.
The accepted model and block aliases, source seals, and raw logs remain preserved.
Restore retired members into an isolated historical checkout before replay.
Do not overwrite an active checkout from the archive.

Model token limits are retired only on the new compiler path.
Existing file, block, flow, and legacy output limits remain.
Compact output size and measured speed are observations, not acceptance thresholds.

The original 42-file contract freeze preceded parser implementation.
Amendment001 records a bounded policy correction.
Amendment002 records a fixture correction during implementation.
The amended set is not described as wholly frozen before implementation.
Candidate002 and all failed or partial checker runs remain historical evidence.

The qualified switch ran once on the real installation and committed.
Before changing the default it passed a fresh M2 restore clone and a fresh compiler canary.
Each used full HTTP, old CLI, and new CLI responses. No rollback event occurred.
Operator005 already tested failure and signal recovery in isolated repositories.
No production fault injection was used.
The separate root post-switch check then passed 76/76 without signals or pointer changes.
Commit `3ced191137` then published source, documents, and indexes to main.
Issues #38–#44 and milestones 7 and 8 were closed after evidence review and read back.

Read [the guide](RUST_GUIDE.md), [the roadmap](RUST_ROADMAP.md),
[the report](RUST_REPORT.md), and [the handoff](RUST_HANDOFF.md).
The [acceptance index](evidence/rust-compiler-acceptance-2026-10-14.json)
binds the candidate, source, tools, raw local receipts, and independent audit.
The [criterion map](evidence/rust-compiler-criteria-2026-10-14.json)
links all 77 issue requirements to their evidence.
Models, source archives, runtime assets, and raw measurements stay local.

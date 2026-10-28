# Rust hosted readiness (#141)

This proposal prepares GitHub tests for the existing Rust products.
It does not change the frozen acceptance contracts.
All tests for this change run on GitHub-hosted runners.
No build, product test, service or emulator runs on the developer's computer.

## What runs now

The `Rust hosted readiness` workflow has two jobs:

1. `Readiness fixture tests` tests the new tool against temporary fake files.
   It uses only Python's standard library on `ubuntu-24.04`.
   The Python interpreter on this runner is a test tool.
   It is not the pinned interpreter for product acceptance.
2. `Frozen product prerequisites (blocked until provisioned)` reads the manifest
   and source files. It does not start any command, network request or service.
   It fails when a prerequisite is blocked. This failure remains visible in CI.

The workflow records the tested PR head and saves the test log and JSON report
as separate artifacts. A green fixture job proves only the readiness tool.
The product suites remain `not_run`, including when all readiness checks pass.

The workflow runs these commands on GitHub:

```sh
python3 -m unittest discover -s som-rs/scripts/hosted_readiness -p 'test_*.py' -v
python3 som-rs/scripts/hosted_readiness/preflight.py \
  --repo . \
  --manifest som-rs/scripts/hosted_readiness/readiness.json \
  --out .local/hosted-readiness/report.json
```

Exit 0 means that the declared readiness checks pass.
Exit 1 means that a prerequisite is blocked.
Exit 2 means that an argument, manifest or path is unsafe or invalid.
The report must be new. Output inside the repository is allowed only under
`.local/hosted-readiness/`. Ports 8765 and 18765 are refused.
The port argument is checked but never opened.

## Source and complete inventories

`readiness.json` binds the public source at commit
`b78da02ba5746d1992d876915bcd64e3feb16685` by SHA-256.
SHA-256 is a digest that changes when file bytes change.
The source commit names the inventory baseline, not the later PR head.
The tool compares current file bytes with the manifest hashes.
It also compares every ordered case ID. Equal counts alone do not pass.

The source map includes the current frozen file map in
`som-rs/evidence/rust-output-contract-amendment-006.json`, the amendment receipts,
acceptance tools, behavior tools, public fixtures and emulator source.
Historical `sources` maps can retain hashes from earlier amendments.
The latest sealed receipt, not a stale historical map, supplies current pins.

| Inventory | Source | Complete set |
|---|---|---|
| Phase 1 public examples | `contracts/rust-output-v1/case-inventory.json` | 8 rows; 5 accepted |
| Phase 1 positive fixtures | Same file | 7 rows; 4 accepted |
| Phase 1 coverage | Same file | 6 rows; 6 accepted |
| Phase 1 legacy | Same file | 100 accepted rows |
| Phase 2 | `contracts/rust-output-v1/phase2/case-inventory.json` | 584 rows: 582 products and 2 compiler refusals |
| Negative cases | `contracts/rust-output-v1/negative-cases.json` | 32 rows |
| Target examples | `contracts/rust-output-v1/target-examples.json` | 24 rows |

Paths in this table are relative to `som-rs/`.
Phase 1 has 115 accepted cases in total.
Phase 2 covers 444 legacy jobs, 118 public cases and 22 RS cases.
Its checker families are `verify_all` (312), `flow` (257) and `phase1_suite` (15).
The frozen plan also requires 127 public Python products and 544 historical
Python jobs. Their complete named recordings are unavailable in this checkout.
They remain blocked. The manifest does not invent their IDs or expected bytes.

## Runner and asset decisions

| Requirement | Owning source | Hosted state |
|---|---|---|
| Apple Silicon macOS; Homebrew Rust 1.96.1; exact cargo, rustfmt and clippy hashes | `som-rs/contracts/rust-output-v1/toolchain.json` | Not provisioned |
| PostgreSQL 17.7 with exact executable hashes | Same file | Not provisioned |
| Python 3.12.11, interpreter hash and package versions | `som-rs/contracts/rust-output-v1/phase2/pins.json` | No approved offline package source |
| MongoDB 7.0.28, Redis 8.4.0, moto 5.2.3 and uv 0.9.9 | Same file | Not provisioned |
| Complete crate union for both reference Cargo.lock files | `toolchain.json` and `scripts/rust_output_acceptance/vendor_crates.py` | Local cache is not a hosted source |
| Accepted Python and Rust recordings, asset index and baseline compiler | Existing acceptance runners and sealed acceptance evidence | No approved hosted distribution route |
| S3, GCS, Firebase and OIDC stand-ins | Public `som-core/scripts/` sources and phase 2 pins | Source is present; runtime prerequisites remain blocked |

The frozen host is `aarch64-apple-darwin`.
The candidate builder also uses macOS `/usr/bin/time -l`.
An Ubuntu fixture test cannot count as that product acceptance run.
A different platform or tool identity needs a numbered contract amendment and
independent review before it can supply formal acceptance evidence.

The repository's source license is MIT.
Dependency archives and redistributed binaries need their own provenance and
license checks. Crate archives must match every checksum in the two lockfiles.
The current vendor tool can create compile-failing stubs for other platforms.
Those stubs do not establish support for a new platform.

Local recordings, models, raw prompts, secrets, runtime links and caches must not
be uploaded by this workflow. Their absence does not prove that they are private.
It proves that this checkout has no verified distribution route for them.
The report records an asset's blocking reason without probing local host paths.

## Proposed full gate run

This section records the required follow-up. It does not enable product tests.
`readiness.json` records the ordered step names.
The hashed existing runners remain the authority for each argv and case list.
Do not replace a full gate with a fixture, sample or mocked service response.

| Gate | Existing entry point | Required order |
|---|---|---|
| Phase 1 | `som-rs/scripts/rust_output_acceptance/run_acceptance.py --candidate CANDIDATE --vendor VENDOR --out NEW_DIR` | contract, python-controls, validate-cases, targets, cli, python-replay, asset-index, runtime-acceptance, behavior-python, behavior-faults, behavior-parity, rust-products, perf |
| Phase 2 | Same entry point with `--phase 2`; the baseline and Python recording paths are fixed in that runner | The first 12 phase 1 steps, then phase1-unchanged, phase2-products, phase2-summary |
| Name and asset changes | `som-rs/scripts/rust_output_acceptance/run_issue57.py --candidate CANDIDATE --vendor VENDOR --out NEW_DIR` | contract, python-replay, python-controls, validate-cases, targets, cli, rust-products, rust-unchanged, issue57, name-collisions |
| Independent audits | `som-rs/scripts/rust_output_acceptance/audit/audit055.py` and `audit057.py` | Read the frozen plan and raw results after the complete run |

The plan's later amendments also require the applicable unchanged Python and
Rust baseline checks. Their baseline assets remain blocked here.
React development and React product acceptance remain on hold.

The future runner must use one recorded candidate for all applicable gates.
It must keep source, binary, formatter, lockfile and asset hashes.
It must keep every case result, raw stdout and stderr, argv, working directory,
environment changes, UTC times, elapsed time, exit code and output hashes.
It must record each failed, missing, skipped or timed-out case as blocking.
It must preserve refusal diagnostics and compare product bytes unchanged.
An independent audit must report zero failures and zero unrun checks.

The future provisioner must create each database and emulator in its own
temporary run directory. It must use loopback QA ports, with no test traffic to
8765 or 18765. Existing baseline calls must be routed to an owned QA baseline
through a reviewed runner change. Only a process started by that run may be
stopped. A timeout must keep logs, mark remaining checks unrun and stop those
owned processes. It must never stop a listener found by port alone.
The current preflight starts no child process and needs no process cleanup.

Before full online testing can start, record the runner and distribution
decisions, the appropriate milestone, any required platform amendment, and a
complete public or access-controlled asset inventory with licenses and hashes.
Then implement provisioning and a runner that preserves the frozen gate set.
Keep #141 open until those decisions and readiness checks are independently
accepted. Do not reopen the completed historical Rust release milestones.

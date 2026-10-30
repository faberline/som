# Documentation audit — 2026-10-28

## Scope

Reviewed the complete set of 85 tracked prose files at source `118852f1e81edc389bd00d05641b781227b82169`.
The set includes Markdown readmes, guides, product documents, plans, contracts and archived reports.
The two new documents, this audit and [architecture.md](architecture.md), are also part of this change.
Generated local files, ignored model assets and external linked reports are outside this set.
Scanned the full text for model-training requirements, Apple hardware restrictions and old pipeline descriptions.
Also inspected the current compiler boundaries and machine-readable platform pins.
This is a documentation audit. It is not product acceptance or a query of the live default service.

## Results

- Current guidance now states fixed `CNL → DSL → Code` algorithms.
  No model training, model weights, MLX, Metal or Apple chip is needed for that path.
- The root and component readmes now distinguish compiler tools from historical model research.
  The former training roadmaps and old M7 plans have clear history labels.
- Quickstarts distinguish earlier host-specific tool records from architecture requirements.
  Repository development builds and tests, including `cargo test`, belong on GitHub runners.
- Frozen contracts, archived reports, reference products and evidence files retain their bytes.
  Their recorded host and test results do not prove current runner support.

## Platform work assigned to M12

[The Rust milestone](https://github.com/faberline/som/milestone/18) owns the platform work.
[Issue #141](https://github.com/faberline/som/issues/141) tracks the current readiness work.
The coordinator assigned the shared quality loader and Rust/TypeScript formatters to Rust.
React M6 owns its release and acceptance work.
This document change does not claim that the implementation limits below are fixed.

| Owning source or record | Finding | Required follow-up |
|---|---|---|
| [quality.rs](../som-rs/src/quality.rs), [compiler.rs](../som-rs/src/compiler.rs) | Native formatter format and digest come from an older host package. | Qualify platform tools and update asset descriptors with output checks. |
| [Rust formatter](../som-rs/som-targets/src/rust/format.rs), [toolchain.json](../som-rs/contracts/rust-output-v1/toolchain.json) | Exact Homebrew rustfmt path and binary hash. | Use verified runner tools through a reviewed amendment. |
| [TypeScript formatter](../som-rs/som-targets/src/typescript/format.rs), [TypeScript toolchain](../som-rs/contracts/typescript-output-v1/toolchain.json) | Exact Homebrew Node and libnode paths and hashes. | Qualify a runner-native formatter and its tool record. |
| [React toolchain](../som-rs/contracts/react-output-v1/toolchain.json), [lockfile](../som-rs/contracts/react-output-v1/reference/package-lock.json) | Native build helpers and caches have host-specific packages. | Supply the runner's packages and verify lockfile, cache and output rules. |
| [Phase 2 pins](../som-rs/contracts/rust-output-v1/phase2/pins.json) | Historical tool and emulator identities. | Record equivalent runner tools and run the complete behavior suites. |

Do not remove a required behavior test to make a platform check pass.
Do not use historical model assets as a new compiler dependency.
A missing real input remains a blocker for its dependent check.
A non-Apple host alone is not a blocker for the fixed algorithm.

## Validation

The checks here read documentation and Git state only.
No local build, Cargo test, product test, training or service ran.

- `git diff --check`: passed.
- Added relative link targets: checked against the checkout.
- Protected contract, evidence and implementation paths: no changes in this branch.
- `python3 scripts/meta/project_docs_contract.py check som-code-python --format json`: passed.
  Its warnings say that `uv run` gate commands were not executed by the document checker.
- The same document check for `som-core`: still fails with the same 20 distinct findings as the base source.
  There are no new findings. The old status layout, absent virtualenv executables and absent historical report links remain.
  The history labels do not reconstruct those old assets or claim fresh acceptance.
- `aw metadoc check`: unavailable in this checkout because no `aw.toml` is present.

## Complete file inventory

Every tracked prose file is listed once below.
“Preserved” means reviewed without rewriting the recorded bytes.

| File | Audit action |
|---|---|
| [README.md](../README.md) | Updated guidance or history label |
| [docs/goal.md](goal.md) | Updated guidance or history label |
| [docs/python-be.md](python-be.md) | Updated guidance or history label |
| [docs/react-output-v1-draft.md](react-output-v1-draft.md) | Updated guidance or history label |
| [docs/roadmap.md](roadmap.md) | Updated guidance or history label |
| [docs/rust-be.md](rust-be.md) | Updated guidance or history label |
| [docs/typescript-be.md](typescript-be.md) | Updated guidance or history label |
| [docs/typescript-output-v1-draft.md](typescript-output-v1-draft.md) | Updated guidance or history label |
| [som-code-python/CONTRIBUTING.md](../som-code-python/CONTRIBUTING.md) | Updated guidance or history label |
| [som-code-python/README.md](../som-code-python/README.md) | Updated guidance or history label |
| [som-code-python/ROADMAP.md](../som-code-python/ROADMAP.md) | Updated guidance or history label |
| [som-code-python/STATUS.md](../som-code-python/STATUS.md) | Updated guidance or history label |
| [som-code-python/data/README.md](../som-code-python/data/README.md) | Updated guidance or history label |
| [som-code-python/data/user/README.md](../som-code-python/data/user/README.md) | Updated guidance or history label |
| [som-code-python/docs/product/README.md](../som-code-python/docs/product/README.md) | Updated guidance or history label |
| [som-code-python/docs/product/corpus.md](../som-code-python/docs/product/corpus.md) | Updated guidance or history label |
| [som-code-python/docs/product/data-pipeline.md](../som-code-python/docs/product/data-pipeline.md) | Updated guidance or history label |
| [som-code-python/docs/product/snippet-isa.md](../som-code-python/docs/product/snippet-isa.md) | Updated guidance or history label |
| [som-core/CONTRIBUTING.md](../som-core/CONTRIBUTING.md) | Updated guidance or history label |
| [som-core/README.md](../som-core/README.md) | Updated guidance or history label |
| [som-core/ROADMAP.md](../som-core/ROADMAP.md) | Updated guidance or history label |
| [som-core/STATUS.md](../som-core/STATUS.md) | Updated guidance or history label |
| [som-core/docs/product/README.md](../som-core/docs/product/README.md) | Updated guidance or history label |
| [som-core/docs/product/generation.md](../som-core/docs/product/generation.md) | Updated guidance or history label |
| [som-core/docs/product/training-legacy.md](../som-core/docs/product/training-legacy.md) | Updated guidance or history label |
| [som-core/docs/product/training.md](../som-core/docs/product/training.md) | Updated guidance or history label |
| [som-core/docs/reference/architecture.md](../som-core/docs/reference/architecture.md) | Updated guidance or history label |
| [som-core/docs/reference/layer-records.md](../som-core/docs/reference/layer-records.md) | Updated guidance or history label |
| [som-core/docs/reference/layer-sft-research.md](../som-core/docs/reference/layer-sft-research.md) | Updated guidance or history label |
| [som-rs/BUSINESS.md](../som-rs/BUSINESS.md) | Updated guidance or history label |
| [som-rs/COMPOSABLE.md](../som-rs/COMPOSABLE.md) | Updated guidance or history label |
| [som-rs/CURRENT.md](../som-rs/CURRENT.md) | Updated guidance or history label |
| [som-rs/M1_CLOSEOUT.md](../som-rs/M1_CLOSEOUT.md) | Existing history label checked |
| [som-rs/M2_CLOSEOUT.md](../som-rs/M2_CLOSEOUT.md) | Existing history label checked |
| [som-rs/M2_COMPACT_WORK.md](../som-rs/M2_COMPACT_WORK.md) | Updated guidance or history label |
| [som-rs/M2_FLOW_WORK.md](../som-rs/M2_FLOW_WORK.md) | Updated guidance or history label |
| [som-rs/M3_HANDOFF.md](../som-rs/M3_HANDOFF.md) | Existing history label checked |
| [som-rs/M3_STATUS.md](../som-rs/M3_STATUS.md) | Existing history label checked |
| [som-rs/M7_CNL_PLAN.md](../som-rs/M7_CNL_PLAN.md) | Existing history label checked |
| [som-rs/QUALITY.md](../som-rs/QUALITY.md) | Updated guidance or history label |
| [som-rs/QUERY_V2.md](../som-rs/QUERY_V2.md) | Updated guidance or history label |
| [som-rs/README.md](../som-rs/README.md) | Updated guidance or history label |
| [som-rs/RUST_GUIDE.md](../som-rs/RUST_GUIDE.md) | Updated guidance or history label |
| [som-rs/RUST_HANDOFF.md](../som-rs/RUST_HANDOFF.md) | Updated guidance or history label |
| [som-rs/RUST_OUTPUT_ROADMAP.md](../som-rs/RUST_OUTPUT_ROADMAP.md) | Updated guidance or history label |
| [som-rs/RUST_REPORT.md](../som-rs/RUST_REPORT.md) | Updated guidance or history label |
| [som-rs/RUST_ROADMAP.md](../som-rs/RUST_ROADMAP.md) | Updated guidance or history label |
| [som-rs/RUST_STATUS.md](../som-rs/RUST_STATUS.md) | Updated guidance or history label |
| [som-rs/SCOPE_GROWTH.md](../som-rs/SCOPE_GROWTH.md) | Updated guidance or history label |
| [som-rs/SERVICE_V3.md](../som-rs/SERVICE_V3.md) | Updated guidance or history label |
| [som-rs/SERVICE_V4.md](../som-rs/SERVICE_V4.md) | Updated guidance or history label |
| [som-rs/behavior/README.md](../som-rs/behavior/README.md) | Updated guidance or history label |
| [som-rs/contracts/compiler-v1/CONTRACT.md](../som-rs/contracts/compiler-v1/CONTRACT.md) | Preserved frozen contract |
| [som-rs/contracts/compiler-v1/selection-policy.md](../som-rs/contracts/compiler-v1/selection-policy.md) | Preserved frozen contract |
| [som-rs/contracts/python-quality-v1/contract.md](../som-rs/contracts/python-quality-v1/contract.md) | Preserved frozen contract |
| [som-rs/contracts/react-output-v1/CONTRACT.md](../som-rs/contracts/react-output-v1/CONTRACT.md) | Preserved frozen contract |
| [som-rs/contracts/related-business.v1.md](../som-rs/contracts/related-business.v1.md) | Preserved frozen contract |
| [som-rs/contracts/related-flow.v1.md](../som-rs/contracts/related-flow.v1.md) | Preserved frozen contract |
| [som-rs/contracts/rust-output-v1/CONTRACT.md](../som-rs/contracts/rust-output-v1/CONTRACT.md) | Preserved frozen contract |
| [som-rs/contracts/typescript-output-v1/CONTRACT.md](../som-rs/contracts/typescript-output-v1/CONTRACT.md) | Preserved frozen contract |
| [som-rs/evidence/legacy-milestones-2026-10-13.md](../som-rs/evidence/legacy-milestones-2026-10-13.md) | Preserved evidence |
| [som-rs/evidence/react-output-contract-archive/CONTRACT-before-amendment-001.md](../som-rs/evidence/react-output-contract-archive/CONTRACT-before-amendment-001.md) | Preserved evidence |
| [som-rs/evidence/react-output-contract-archive/CONTRACT-before-amendment-002.md](../som-rs/evidence/react-output-contract-archive/CONTRACT-before-amendment-002.md) | Preserved evidence |
| [som-rs/evidence/rust-compiler-contract-archive/CONTRACT-before-amendment-003.md](../som-rs/evidence/rust-compiler-contract-archive/CONTRACT-before-amendment-003.md) | Preserved evidence |
| [som-rs/evidence/rust-compiler-contract-archive/CONTRACT-before-amendment-004.md](../som-rs/evidence/rust-compiler-contract-archive/CONTRACT-before-amendment-004.md) | Preserved evidence |
| [som-rs/evidence/rust-compiler-contract-archive/coverage-and-scope-before-amendment-003.md](../som-rs/evidence/rust-compiler-contract-archive/coverage-and-scope-before-amendment-003.md) | Preserved evidence |
| [som-rs/evidence/rust-compiler-contract-archive/selection-policy-before-amendment-003.md](../som-rs/evidence/rust-compiler-contract-archive/selection-policy-before-amendment-003.md) | Preserved evidence |
| [som-rs/evidence/rust-output-contract-archive/CONTRACT-before-amendment-002.md](../som-rs/evidence/rust-output-contract-archive/CONTRACT-before-amendment-002.md) | Preserved evidence |
| [som-rs/evidence/rust-output-contract-archive/CONTRACT-before-amendment-003.md](../som-rs/evidence/rust-output-contract-archive/CONTRACT-before-amendment-003.md) | Preserved evidence |
| [som-rs/evidence/rust-output-contract-archive/CONTRACT-before-amendment-004.md](../som-rs/evidence/rust-output-contract-archive/CONTRACT-before-amendment-004.md) | Preserved evidence |
| [som-rs/evidence/rust-output-contract-archive/CONTRACT-before-amendment-005.md](../som-rs/evidence/rust-output-contract-archive/CONTRACT-before-amendment-005.md) | Preserved evidence |
| [som-rs/evidence/rust-output-contract-archive/CONTRACT-before-amendment-006.md](../som-rs/evidence/rust-output-contract-archive/CONTRACT-before-amendment-006.md) | Preserved evidence |
| [som-rs/evidence/typescript-output-contract-archive/CONTRACT-before-amendment-001.md](../som-rs/evidence/typescript-output-contract-archive/CONTRACT-before-amendment-001.md) | Preserved evidence |
| [som-rs/evidence/typescript-output-contract-archive/CONTRACT-before-amendment-002.md](../som-rs/evidence/typescript-output-contract-archive/CONTRACT-before-amendment-002.md) | Preserved evidence |
| [som-rs/evidence/typescript-output-v1/reference-product-001/README.md](../som-rs/evidence/typescript-output-v1/reference-product-001/README.md) | Preserved evidence |
| [som-rs/plans/m7/acceptance.md](../som-rs/plans/m7/acceptance.md) | Updated guidance or history label |
| [som-rs/plans/m7/contract.md](../som-rs/plans/m7/contract.md) | Updated guidance or history label |
| [som-rs/plans/m7/parsers.md](../som-rs/plans/m7/parsers.md) | Updated guidance or history label |
| [som-rs/scripts/compiler_acceptance/README.md](../som-rs/scripts/compiler_acceptance/README.md) | Updated guidance or history label |
| [som-rs/scripts/compiler_acceptance/legacy544_amendment/QUALIFICATION.md](../som-rs/scripts/compiler_acceptance/legacy544_amendment/QUALIFICATION.md) | Preserved qualification |
| [som-rs/scripts/compiler_acceptance/legacy544_amendment_v2/QUALIFICATION.md](../som-rs/scripts/compiler_acceptance/legacy544_amendment_v2/QUALIFICATION.md) | Preserved qualification |
| [som-rs/scripts/compiler_acceptance/renderer_behavior/README.md](../som-rs/scripts/compiler_acceptance/renderer_behavior/README.md) | Updated guidance or history label |
| [som-rs/scripts/compiler_acceptance/renderer_behavior/references/REVIEW.md](../som-rs/scripts/compiler_acceptance/renderer_behavior/references/REVIEW.md) | Preserved qualification |
| [som-rs/scripts/rust_output_acceptance/README.md](../som-rs/scripts/rust_output_acceptance/README.md) | Updated guidance or history label |
| [som-rs/tests/compiler-v1/coverage-and-scope.md](../som-rs/tests/compiler-v1/coverage-and-scope.md) | Preserved frozen contract |

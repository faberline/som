# Rust hosted tests and readiness (#141, M12)

SOM uses fixed rules for `CNL -> DSL -> Code`.
It needs no model, weights or training.
Apple Silicon is not a requirement.
[M12](https://github.com/faberline/som/milestone/18) owns this work.

Develop source in a local worktree. Push it to GitHub.
Run all builds and tests on GitHub runners.
Do not run them on the developer host.

## What runs on GitHub

The `Rust hosted readiness` workflow has these jobs:

- Two Cargo jobs run the complete default-feature suites on Ubuntu 24.04.
  One tests `som-rs/Cargo.toml`. The other tests `som-rs/som-targets/Cargo.toml`.
  The normal `cargo test` command also includes documentation tests.
  The retired `legacy-model` feature stays disabled.
- The fixture job tests the readiness tool against temporary fake files.
- The public input job reconstructs 127 public inputs and all 544 historical jobs.
  It checks source, prompt, CNL and known request hashes.
  It uses the reviewed public fixture file for 11 historical inputs and two DSLs.
  A separate step proves the remaining public checked DSL against its accepted hash.
  It maps all 699 frozen Rust cases. It preserves all 22 native negative requests.
  It adds nine typed companions from public examples and five CNL companions.
  Every Rust baseline JSON request must match its accepted complete request hash.
  These steps prepare input data. Compiler semantics and full products stay separate.
- The compile experiment compares all 1,342 Python and 699 Rust compile replies.
  It starts one owned target-aware candidate and keeps every raw exchange.
  It checks the child process and local socket before each request.
  It stops its own child and saves a cleanup receipt.
  It then checks 1,342 default Python assemblies, six checked Python plans
  and all 697 Rust product file maps. It keeps every complete product directory.
  This unsealed experiment does not pass product acceptance.
- The runtime qualification job builds pinned PostgreSQL, Python and Redis sources.
  It checks pinned MongoDB and uv archive members on Ubuntu 22.04.
  It records the first binary hashes and keeps runtime readiness false.
  It starts no service. Independent review and Python dependencies remain required.
- The product readiness job reads source hashes and named inventories.
  It starts no product, service or emulator.

The Cargo jobs install Rust 1.96.1 and record versions and binary hashes.
They fetch public crates from the committed locks.
They then run `cargo test --locked --offline` without test filters.
A failure stays a failed job. No test is ignored to make the job pass.
Each job saves its tested commit and raw logs.

The native adapters use trusted Linux Ruff, rustfmt and Node artifacts.
Quality tests load generated compiler assets. The old model-directory test path
is removed. macOS keeps its existing trusted artifacts.
Native Ruff, rustfmt, Node and Prettier still need version and integrity checks.
Removing a platform limit must preserve formatter checks and product behavior.
The [native tool proposal](native-tools.json) records public Linux artifact pins.
The [provisioning plan](PROVISIONING.md) defines the next implementation step.
The versioned runtime pins are in contracts/native-tools-v1.
The Cargo jobs provision those tools and generate assets before testing.
Their execution receipts remain separate from product acceptance.

## What readiness means

The manifest and report use version 2.
The tool refuses a missing whole inventory group, asset group or gate section.
It also refuses a changed inventory selector or a missing or reordered runner step.
The report hashes each complete gate section from the frozen acceptance plan.
It reports a changed or new unmapped section as blocked.
Changing only a source hash cannot hide a changed gate.

The tool compares every ordered case ID. Equal counts alone do not pass.
The current manifest binds 221 candidate source files.
The manifest's `source_commit` names that baseline.
The workflow's `tested-sha.txt` names the actual tested PR commit.
These serve different purposes.

| Inventory | Complete set |
|---|---|
| Phase 1 public examples | 8 rows; 5 accepted |
| Phase 1 positive fixtures | 7 rows; 4 accepted |
| Phase 1 coverage | 6 accepted rows |
| Phase 1 legacy | 100 accepted rows |
| Phase 2 | 584 rows: 582 products and 2 compiler refusals |
| Negative cases | 32 rows |
| Target examples | 24 rows |

Phase 1 has 115 accepted cases.
The Python regression scope has 127 public products and 544 historical jobs.
Their reviewed synthetic input data and accepted hash records are public.
The tool does not invent missing IDs, response bytes or results.
It does not read local models, private data or host caches.

Exit 0 means the declared readiness checks pass.
Exit 1 means a required input or mapped gate is blocked.
Exit 2 means an argument, manifest or path is invalid.
Readiness never means product acceptance.
The report always states `acceptance_passed: false` and `product_suites: not_run`.
The manifest cannot enable product acceptance.

The hash exporter reads one reviewed local source index on the developer host.
It writes hashes and public source locations only.
It does not run the compiler or any test.
It preserves all named Python and Rust rows, including duplicates and refusals.
It records missing public inputs and unrun reconstruction steps.
It does not export prompts, DSL, response bodies, product files or local paths.
The body-bearing review packet is never a hash-exporter input.
The committed hash export has a separate fixed digest in the public input tool.
GitHub executes the unchanged public generator after checking all source hashes.
The public input artifact contains only inputs derived from public source.
No test or product can treat this partial input proof as full acceptance.

The human approved the exact synthetic supplement fixture file for public use.
Its fixed digest binds all 11 typed rows and two complete checked DSLs.
The input job checks every named row and all 454 known historical request hashes.
It records old blocked labels as history and current availability separately.
Unpublished files and local source paths are not evidence of secret content.
Hash checks prove data integrity. They are not encryption.

The standalone checked DSL proof uses the accepted historical compact block library.
It checks ten exact source modules and six data files before and after the run.
It calls the public checked-plan builder and validator on GitHub.
It preserves the accepted full DSL hash and writes a separate proof manifest.
It runs no product compiler, assembler, formatter or service.

The accepted TypeScript #65 baseline hash file keeps 1,342 Python result rows
and 2,093 Rust check rows. Each row keeps its HTTP status and exact response hash.
The latest accepted candidate and the older control stay distinct.
All 697 rebased Rust results retain both complete file-map hashes.
The source index binds 13 receipts and 97 source files.
Four directory digests remain unchecked. Canary payload hashes remain observations.
No compile response hash is projected or invented.
These historical hashes support a future byte gate. They do not pass it.

The experimental native compile checker uses those exact accepted response hashes.
It derives current metadata from complete committed source vectors and native pins.
It compares every other raw response byte, including spacing and key order.
It keeps the latest accepted candidate separate from the older control.
All results stay `experimental-unsealed` with `product_acceptance: false`.
The checker covers 1,342 Python compile rows and 699 Rust compile rows.
It does not accept TypeScript samples as a complete TypeScript regression.
Assembly, product behavior and the numbered compatibility policy stay separate.

`raw_digest_compat.py` compares raw JSON bytes outside exact digest leaves.
Its caller must approve each path and obtain both digest values independently.
The helper cannot approve a compatibility policy or validate payload hashes.
It preserves all bytes outside those leaves, including spacing and key order.
No product gate uses a new compatibility rule until its numbered policy is sealed.

Reports must be new files.
Inside the repository, outputs must be under `.local/hosted-readiness/`.
Ports 8765 and 18765 are refused.
The port argument is checked but never opened.

## Current gates and ownership

The manifest binds all eleven gate sections of the current Rust plan.
It retains the full phase 1, phase 2 and issue57 step order.
It maps each of the ten amendment006 checks to its public tool source.

Amendment005 records the earlier TypeScript acceptance and its baseline.
Its historical diagnostic texts must not replace the current texts.
Amendment006 requires regression checks on the same React candidate as M6 #130.
React owns that integration and the #137 independent audit.
Rust owns this readiness tool, portable tool provisioning and Rust test evidence.
A Rust unit test run does not close M6.

Historical receipts keep their original host and tool hashes.
They record what ran then.
They do not impose Apple Silicon on the current fixed algorithm.
Use reviewed native tool pins for each supported host.
Keep generated file bytes and behavior tests unchanged.
Record changed asset descriptors and backend hashes explicitly.

## Full product acceptance

The product suites remain disabled here.
Before enabling them, provision the required tools, offline dependencies,
baseline compiler and accepted response recordings.
Keep source, binary, formatter, lockfile and asset hashes.
Use one candidate for every applicable gate.
Keep every case result, argv, environment change, time, exit code and raw log.
A failure, timeout, skipped check or missing result blocks acceptance.
An independent audit must find zero failures and zero unrun checks.

Create each database and emulator in an isolated run directory.
Use owned QA baseline processes on loopback ports.
Do not send QA traffic to 8765 or 18765.
Stop only processes started by that run.
Never stop a listener merely because it uses a port.
Keep logs and mark remaining checks unrun after a timeout.

Keep #141 and M12 open until their applicable checks pass independent review.
Do not reopen the completed historical Rust release milestones.
Default changes, tags and releases retain their separate user approval gates.

The bf3a04a0ee GitHub run passed all 2,041 compile byte comparisons.
A separate controller audit recomputed every accepted raw response hash,
current and reconstructed old payload hash, and exact digest leaf scope.
It checked all raw request hashes, 146 source pins, child ownership and cleanup.
The same run passed 169 fixtures, both complete Cargo suites and 27 runtime fixtures.
The first runtime build retained 10,102 regular installed files and 11 aliases.
Its saved artifacts passed source, license, version, binary-byte and log checks.
Artifact upload follows aliases and changes modes. A reuse installer must restore
only the recorded internal links and modes, then check the tools again on GitHub.
Services, dependency closure and product behavior remain separate unrun gates.

The Python environment consumer checks all 671 product slots and both compile variants.
It requires each exact frozen raw assembly reply and complete saved file map.
It keeps observer and product requirements separate. It emits environment inputs.
The full transitive wheel source and offline environment tests are still required.

The Cargo closure job uses two real products from the complete assembly artifact.
It verifies their exact frozen manifests, locks, complete files and compile proofs.
Cargo supplies the full 352-package union from both unchanged locks.
The helper checks every publisher archive, vendor checksum and license file.
It preserves the complete failure tree. An unknown byte change fails the job.
This prepares dependency files. Full product builds and behavior remain unrun.

The separate TS125 historical export preserves all 125 cases and 1,375 product file hashes.
It keeps 246 compile forms, 791 historical target rows and exact response roles.
Missing directly recorded request hashes stay null. Canary operations stay separate.
A second source-bound artifact reconstructs 146 compile and 125 assembly request hashes.
It calibrates all 140 recorded compile hashes and both known canary assembly bodies.
The accepted header binds both HTTP writers. Its old product checker differs from the current one.
All 100 legacy CNL request member-order gaps remain null and unrun.
These artifacts provide historical evidence. They do not pass full TypeScript acceptance.

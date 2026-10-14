# Renderer behavior acceptance tools

This package checks the eleven admitted renderer cases.
It does not load or run a model.
The input prompts, CNL sources and origin records live in inputs/.
The helpers in the parent acceptance package supply repository and evidence paths.
The tools have no scratch-directory import.

From the repository root, prepare with:

```sh
som-rs/runtime/acceptance-m3-venv/bin/python som-rs/scripts/compiler_acceptance/renderer_behavior/prepare.py
```

Preparation validates the eleven independent sources and current frozen contract.
It checks checker imports, tool versions and source/tool hashes.
It reads code and inputs. It does not write them.
It starts no database, provider mock, fixture, service or candidate request.
It writes preparation.json and dependencies.json under:

`.local/rust-compiler-goal/acceptance/renderer_behavior/preparation/`

Preparation refuses an existing output directory.
Use --output with a new directory under the same renderer_behavior evidence root
to prepare again. SOM_REPO can select a repository when source discovery is needed.

run.py without --execute prints UNRUN and starts nothing.
Root must approve the candidate and execution before use.
The execution arguments are:

```text
--execute
--preparation <prepared-evidence-directory>
--http-base <approved-loopback-url>
--candidate-binary <approved-absolute-binary-path>
--candidate-sha256 <approved-file-sha256>
--candidate-pid <owned-listener-pid>
--approval-label <root-execution-receipt-label>
--output <new-run-directory>
```

The preparation argument defaults to the path above.
The run output must stay under:
`.local/rust-compiler-goal/acceptance/renderer_behavior/`
Both output tools preserve existing evidence.
Root must supply a freshly started immutable candidate.

The execution runner compares CNL and typed JSON whole compile bundles.
It checks that returned prompts match the independent original inputs.
It saves each complete bundle and sends it unchanged to assemble.
It compares complete file maps and keeps both assembled responses.
It then runs the unchanged complete strict quality checker.
A task-owned M3Fixture provides isolated local databases and provider mocks.
The unchanged full-M2 checker runs separately from focused observations.

The nine unsupported old-checker shapes remain explicit non-passes.
Focused checks never change an old full_m2_acceptance result into a pass.
run.py returns nonzero if any unchanged full-M2 outcome fails or is unavailable.
Each check records UNRUN, PASS or FAIL.
Root reviews focused results with the unchanged shared544 gates.

Focused observations use only original prompt semantics and native row history.
They check types, values, branch journals, row state, idempotency and recovery.
The six comparison predicates get equality and both-side probes.
Nullable fields get null, empty, nonempty and Unicode probes.
The legal empty field-path parts preserve identity semantics.
The constant false branch arm is unreachable and is not counted as covered.
Write-free cases get checkpoint failure and crash recovery on both decisions.
Their active lease-engine coverage stays with unchanged shared544 gates.
Two closed-expression probes add exact lazy reader observations.
They supplement complete product checks and do not replace them.

Every run keeps complete inputs, requests, raw responses, bundles, products,
quality results, behavior traces, exceptions and artifact hashes.
Fixture owners stop their own service/database/mock processes.
No product source, frozen case, checker or quality profile is changed.

references/ contains the earlier source review and coverage map.
These files are reference snapshots, not a current candidate acceptance receipt.
The prior preparation/source hashes in them bind that earlier review only.
Fresh preparation and execution produce the current receipts.

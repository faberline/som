# Dependency helper iteration

All builds, fixtures, tools and product checks run on GitHub runners.
This route reuses data from one independently reviewed compiler run.
It does not accept a new compiler or a product.

`--source-revision` names the current helper Git HEAD.
`--candidate-revision` names the compiler that made the assembly data.
Its default is the helper revision for a normal full run.
Receipts retain both `helper_revision` and `candidate_revision`.
The Cargo `tested_sha` remains the compiler candidate.
The Python `source_revision` remains the helper revision.

Only candidate `f59e28d910ff4adb16e3becfdc82bd2978ca84fa` may cross helper revisions.
The fixed committed recipe binds run 40805208879, artifact 12868189241,
the GitHub ZIP digest, both manifest digests and the full saved-file inventory.
It also binds the exact independent byte audit.
No caller supplies an expected SHA or a replacement candidate.

The source guard checks all 139 factory and contract source pins.
The 168 original compile/assembly source pins retain their f59 role.
Only the separately reviewed early comparison-helper source bootstrap may differ.
Its new helper SHA is recorded beside the old candidate SHA.
No old source record is rewritten.

The guard also compares complete protected compiler, target, core data,
block-library, contract and build-input Git trees and their working bytes.
The unchanged historical blocks/current alias keeps its exact recorded target.
Other aliases are refused.
The new, unreferenced `product-rust-tools-v1.json` preparation profile is the sole
named profile addition outside that compiler tree.
Changing code to reference it, changing a factory input, or changing an active
contract requires a full candidate run.

The guard checks the exact GitHub artifact origin and retention.
After download, it hashes all 37,535 saved files.
It also checks both manifest hashes and the original binary and asset identities.
The original ZIP digest remains a GitHub attestation because download-artifact
extracts the transport. No local ZIP hash is claimed.
The existing raw response, complete product and requirement checks still run.

For a helper-only PR update, the main workflow runs fixtures and this source guard.
If the guard proves reuse, it calls the focused dependency workflow.
It skips the unaffected Cargo compiler suites, input generation and 20-minute
compile/assembly job. This skip is not a new candidate pass.
A real candidate input change selects the normal complete workflow.
An unavailable origin fails closed and does not silently start a large rebuild.

Manual dispatch of the main workflow defaults to a full current candidate.
`dependency_preparation_only: true` explicitly selects the fixed reuse route.
It refuses changed candidate inputs.
The separate dependency workflow also supports workflow_call and workflow_dispatch.
The existing PR workflow can call it from the same commit before it is on main.
Do not dispatch a duplicate while that focused run is active.

The focused jobs run all dependency fixtures, Cargo's 352-package closure,
all 671 Python product-slot bindings and their named offline environments,
and the separate complete six-tool Rust qualification.
They preserve failed environments, command logs and receipts.
They keep all product/runtime acceptance flags false.
Full CNL, product build, quality, behavior, TypeScript, React, policy and release
acceptance remain separate.

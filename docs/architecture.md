# Current SOM architecture

The user confirmed this direction on 2026-10-28.

`CNL → DSL → Code`

CNL is controlled natural language. It uses fixed English clauses.
DSL is the typed plan that names approved code blocks and their bindings.
Code is the complete set of files produced by the selected output target.

## Fixed rules

The caller supplies the business choices.
The Rust parser checks the request against the grammar.
The planner uses fixed rules to select and order approved blocks.
The assembler checks the complete DSL bundle and renders the files.
The target formatter applies the declared code style.
Missing facts, invalid types and unsupported requests return diagnostics.
The compiler does not guess or repair missing business choices.

This path requires no model training, model weights or model inference.
It requires no Apple chip, Metal or MLX.
The caller can be a person or an agent. An agent is not a runtime dependency of SOM.
Python reference tools and behavior checks remain useful for acceptance.
Their use does not make Python a production parser or a training requirement.

The source records these boundaries in:

- [The compiler contract](../som-rs/contracts/compiler-v1/CONTRACT.md).
- [The default Cargo features](../som-rs/Cargo.toml): the feature list is empty.
  The optional `legacy-model` feature belongs to the retained model tools.
- [The compiler](../som-rs/src/compiler.rs), which reports `model_loaded: false`.
- [The parser](../som-rs/src/cnl.rs) and [planner](../som-rs/src/planner.rs).

## Platform support and remaining tool work

The algorithm is not tied to Apple hardware.
That does not prove that every existing binary or tool package runs on every host.
Older releases pinned Homebrew paths, Mach-O binaries and macOS library hashes.
Those pins describe the tools used for those releases.
They are implementation limits to remove, not a reason to train a model.

[M12](https://github.com/faberline/som/milestone/18) owns the Rust CI and platform work.
[Issue #141](https://github.com/faberline/som/issues/141) tracks hosted test readiness.
The remaining work includes native formatter checks, tool discovery and platform-specific packages.
The Python quality loader, compiler asset hash, Rust formatter and TypeScript formatter need compatible tool records.
Do not copy a macOS binary hash onto a Linux binary or report that it passed.
Record each supported host's tool identity and verify the resulting output.

Keep the original contracts and receipts as evidence of their recorded runs.
Use a numbered amendment when changing a frozen tool contract or asset descriptor.
Record old and new hashes and the required review.
Changing a tool pin must preserve the applicable output and behavior checks.

## Development and tests

Edit source in each chat's own local worktree.
Push the branch to GitHub.
Run builds and tests on GitHub Actions runners, including `cargo test`.
Do not run builds or tests on the shared development computer.
Loopback databases and emulators can run inside a runner.
They do not require paid product services or model training.

Each development chat owns one milestone through its active goal.
Rust owns M12. React owns [M6](https://github.com/faberline/som/milestone/12).
The coordinator resolves shared files and the order of dependent changes.

Record the tested commit, runner, tool versions, logs and result.
Unit tests and readiness checks do not replace full product acceptance.
Missing required assets or unrun behavior checks remain open work.
Apple hardware alone is not an acceptance condition for the current algorithm.

## How to read older documents

Model training plans and model budgets are historical research.
They do not direct current development or block this compiler path.
Old models and runtime packages remain available for recovery.
Their presence does not make them inputs to the current compiler.
Historical acceptance proves only the source, tools and scope recorded in that run.

Read [the scenario roadmap](roadmap.md) for product tracks.
Read [the compiler guide](../som-rs/RUST_GUIDE.md) for the interface.
Read [the documentation audit](documentation-audit-2026-10-28.md) for the file review.

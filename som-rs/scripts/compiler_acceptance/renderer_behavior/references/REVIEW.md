# Source boundary review

No blocking source-boundary defect was found in the current Compiler API path.
This is a source review. No candidate, build, model, fixture or service ran.
No production file or existing checker changed.

Default Cargo features are empty.
The candle, safetensors and tokenizer dependencies are optional.
The three model engine modules and their model binaries need legacy-model.
See som-rs/Cargo.toml:7 and som-rs/src/lib.rs:47.

Compiler.compile_prompt validates the typed prompt, selects approved blocks,
validates the typed DSL, then makes its bundle and hashes.
There is no model call or model token gate.
See som-rs/src/compiler.rs:311.

Compiler.assemble_bundle checks the complete caller bundle and hashes.
It validates the caller's original plan.
The v7 path calls flow_compact.resolve and render_resolved directly.
It does not call the old flow_compact.assemble wrapper.
That old wrapper still has codec feature/encode gates at flow_compact.rs:174.
The Compiler API does not reach those gates.
The v6 path uses the quality library. The v1-v5 paths use the legacy v5 library.
Native typed binding, reference, schema and structural limits remain.
Grammar lexer tokens and base64 scalar decoding are not model token gates.

The admitted caller DSL stays in the render path.
Resolve clones it and binds approved parameters and symbols.
The flow program keeps the supplied tree, arguments, result references and undo IDs.
Its flow hash includes the original prompt and DSL.
The legacy role checker calls the planner only to check required roles and bindings.
It does not replace or repair the submitted plan.
The diagnostic legacy plan is used only to locate an error.
Standard compile choice does not force assembler equality with the standard plan.
Legal checked flow providers remain subject to the original catalog/tree checks.

The reachable source closure has 14 Rust files.
All 14 appear in backend_manifest.
The manifest has 26 entries including Cargo.toml/Cargo.lock, semantic contracts,
literal encoding, quality profile, four catalogs and two Python source templates.
The five grammar/compiler/policy/serializer/literal files are hashed in descriptor roles.
The three complete approved block libraries are hashed as assets and descriptor roles.
The native formatter has the built-in approved SHA-256.
Compiler.load rejects a changed manifest or asset file before admission.
No reachable embedded source dependency was found outside those hashes.
boundary-receipt.json records all checked source identities.

The default library still compiles flow_codec and flow_constraints modules.
They are not reachable from the reviewed main/Compiler request path.
Their codec policies are not claimed as compiler backend dependencies.
Optional model modules and diagnostic/helper binaries are also outside that path.
The backend source manifest is not a binary/toolchain/feature-profile receipt.
Root must bind the final binary and asset receipt to the reviewed source snapshot.

Production child-process work is limited to the pinned native Ruff binary.
Quality.load_pinned checks its exact digest, native Mach-O bytes and version.
Quality.execute uses the admitted binary path and fixed format/lint arguments.
It checks loaded asset identity before and after the process.
No Python interpreter, training process or model executable is called.
Generated Python templates are output data. The compiler does not execute them.
The CLI HTTP client permits loopback HTTP and disables redirects.
See quality.rs:99, quality.rs:150, and main.rs:270.

Legacy generate keeps its CLI name, prompt-file input and --url argument.
It keeps the raw HTTP prompt request, status/files response, flat error shape,
256 KiB body limit, loopback rule and exit0/2/1 behavior.
Serve now uses approved compiler assets, as required by the new runtime contract.
The old model service remains a separate feature-gated legacy binary.
This compatibility result is static. Root still owns live protocol checks.

# Eleven-case requirement map

behavior-coverage.json maps every case to these eight facets:
authentication, values/types, branch decisions/scope, durable state/row history,
compensation, recovery, idempotency and lease.
All case execution states remain UNRUN.

Nine forms have an explicit old-checker limit.
Seven compare two inputs and have no resource operation for the old lease read gate.
The constant empty branch has no reachable false arm.
The empty field-segment result hits the old unfiltered reference walker.
The public nullable export and frozen optional-field branch have supported old-checker shapes.
Actual old-checker false/error/unavailable outcomes stay non-passes.
Focused checks never become an old full_m2_acceptance pass.

The focused independent checks cover the new predicate, export, nullable value,
field-path and empty-branch behavior from original prompt semantics.
They inspect complete live products, native row history and durable journals.
They also check idempotency, owner isolation, failed checkpoints and crash recovery.
The two closed-expression probes add exact lazy access observations.
They do not replace live product checks.

The seven write-free cases have only focused expired-lease recovery coverage.
Their active heartbeat, stale-executor and heartbeat-failure requirement stays shared.
This is not marked as a focused lease pass.
The shared execution_lease/run_record template, support template, PG/Mongo FlowStore
blocks, and all six original flow checker modules are byte-equal to82f3b87.
Nine complete shared files were checked; all nine match their original hashes.
The unchanged full-M2 lease gates must still pass in Root's fresh historical run.

The historical544 inventory has 138 full-M2 flow behavior jobs:
formal114, dev12 and supplement12.
It also has 214 composed CRUD jobs and100 legacy service jobs.
Do not call all544 jobs full-M2 flow checks.
shared-544-flow-map.json lists every one of the138 IDs and applicable original gates.
Useful anchors are m2-cap-000 for PostgreSQL writes and branches,
m2-smoke-00 for a read-only PostgreSQL flow, and m2-cap-005 for provider versions.

No additional compiler-specific behavior gap was found in the planned11-case scope.
This statement depends on actual focused execution plus unchanged shared source
identity and a fresh passing544 run. Those runs are still unverified here.
Keep all original gates and their exact results.

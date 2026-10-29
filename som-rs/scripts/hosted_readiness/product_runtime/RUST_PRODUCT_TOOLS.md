# Complete Rust product tool qualification

Run this stage only on a GitHub Ubuntu22.04 x86_64 runner.
Run all new tests there.
Start the standalone qualifier and fixture process with Python -I -S -B.
These flags exclude ambient site hooks and local module search paths.
Do not run the qualifier, Rust tools or product gates locally.

The fixed profile is contracts/native-tools-v1/product-rust-tools-v1.json.
It is additive. It does not change old native-tools artifacts or toolchain.json.
It binds the official Rust1.96.1 channel manifest and five whole archives.
The source metadata SHA is bd4429a05bbb869c08774b5de6c69f631526ef205c715b509e590c036417e132.
The channel manifest SHA is 87eb76c53073e72b766083bed5530820694253b832a762d8385bda5759f03975.
The profile records every regular member, directory, mode and required binary.
The current archives contain215 regular files and no aliases.
Unknown files, directories, aliases, links and special members fail admission.

The six tools are rustc, cargo, rustfmt, cargo-fmt, cargo-clippy and clippy-driver.
The product rustfmt SHA equals the already pinned native compiler rustfmt SHA.
The tool profile retains complete official standard libraries and runtime libraries.
The six executable hashes alone do not define the installed tree.

The qualifier binds its own file, this profile and qualify_runtime.py to regular Git blobs.
It does that before it uses archive pins or starts a Rust tool.
The shared support module first requires its fixed literal reviewed raw SHA.
It compiles those raw bytes in a fresh registered module.
It never loads a cached PYC file.
Dirty, copied, linked, oversized or wrong-commit authority fails.

The qualifier downloads only the fixed official static.rust-lang.org URLs.
It refuses redirects, wrong lengths and wrong whole-file hashes.
It checks the complete archive inventory before it copies a member.
It does not execute install.sh.
Each component manifest.in must name every installed component file exactly once.
The installed paths come only from that checked manifest.
A shared destination is allowed only for identical pinned bytes and modes with both owners recorded.
Existing conflicting data fails. It is not overwritten.

All installed files get full byte, length and mode checks.
The exact file and directory sets must match.
Single-link regular files are required.
License bytes and outer publisher records are retained separately.
The compressed archives and channel metadata are retained too.
Every preserved input is rechecked after the version probes.

The fixed output directory is:
RUNNER_TEMP/som-rust-product-tools-qualification.
The executable prefix is its install/bin directory.
The qualifier checks all six actual hashes and ELF headers before execution.
It runs each exact path with --version.
It waits for each child and checks its owned process group cleanup, even on success.
It records command arguments, environment, raw logs, exit status and cleanup.
It checks the complete installed tree and all three source bindings again.

Cargo-fmt forwards its version request to rustfmt.
The owned environment selects the admitted rustfmt path.
Primary source: https://github.com/rust-lang/rustfmt/blob/master/src/cargo-fmt/main.rs.
Rustc, Cargo and rustfmt version text is also tied to the same official executable bytes
already observed in GitHub compiler job123223240037.
Clippy versions still need fresh GitHub observation and independent review.

## Consumer API

Import only the Git-bound raw qualifier source in a fresh module.
Bind the fixed profile raw bytes to that same declared candidate source first.
Do not use a normal file loader that can read stale bytecode.

profile_data(raw) validates the fixed complete profile.
expected_install(profile) returns a map keyed by the path relative to install/.
Each value has sha256, bytes, mode and a sorted components owner list.
verify_install_tree(out,profile) checks the complete installed tree.
It returns version som.rust-product-tool-installed-files.v1 and the expected files map.
verify_qualification(out,profile,source_revision) checks the saved qualification too.
It verifies source bindings, all input evidence, all command logs, waits and cleanup.
It returns a map from the six tool names to their admitted absolute Paths.
verify_tools(out,profile) rechecks all six actual file identities before later launches.
Consumers must also check the complete installed tree before and after their whole run.

The receipt version is som.rust-product-tool-qualification.v1.
The successful status is qualification_commands_complete_awaiting_independent_review.
The receipt keeps product_acceptance_passed=false and product_runtime_ready=false.
All full product gates remain unrun in this stage.
An incomplete, failed or ACK-only receipt cannot pass the consumer check.

Use direct qualification in the owning product job when possible.
An uploaded evidence artifact does not itself admit moved executables.
GitHub transport can change file modes.
Any restored tree needs the exact pinned modes and full qualification checks again.

## Remaining gates

All35 synthetic fixtures remain unrun until GitHub executes them.
All six live version probes remain unrun for this qualifier source.
Independent source and actual raw-output review remain required.
The frozen product checker native adapters are a separate reviewed change.
Complete352-crate vendor closure, checked relocation and all applicable product
build, format, clippy, test, behavior, CLI, negative and same-candidate gates remain required.
This profile does not waive or count any of those gates as passed.

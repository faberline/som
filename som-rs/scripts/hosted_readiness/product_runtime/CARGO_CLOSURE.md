# Complete frozen Cargo registry closure

This helper runs on GitHub Linux x86_64 only.
Use Python 3.11 or later for the standard TOML reader.
The job fragment uses Ubuntu 24.04. This bootstrap Python is not the qualified product runtime.

The two frozen locks have 203 and 334 registry packages.
Their union has exactly 352 name, version, registry, and checksum tuples.
The helper binds its code, shared runtime helper, lock files, manifest files, toolchain record,
and public compile and file-map authorities to the declared Git HEAD.
It checks complete working bytes against regular Git blobs.
It also checks the frozen manifest and lock hashes. No expected hash comes from the environment.

The frozen reference directories have no Rust source targets.
The consumer therefore requires actual same-candidate generated product roots.
It selects phase1 example-v1 and phase2 services14-0000 from OUT/assembly/manifest.json.
It also reads OUT/manifest.json and the selected raw compile and assembly receipts.
It checks the complete ordered compile and Rust assembly inventories.
It checks the two complete text file maps against the frozen public golden hashes.
It checks all per-file hashes and the real src/main.rs.
Each Cargo.toml and Cargo.lock must equal the exact frozen reference bytes.
The normal Rust target plan is required. No invented product target or dependency stub is used.

The assembly manifest must use som.native-assembly-experiment.v1.
Each selected row must carry actual_files_map_sha256, expected_files_map_sha256,
product_dir, files, request_path/request_sha256, response_path/response_sha256,
and rust_assembly_mode normal-target-plan.
All locators are relative to OUT.
The complete compile proof must have 2041 passed rows at the same candidate.
The source compile checker record and raw reply are bound to the assembly request.
The fixed independent checker verifies both raw replies again against committed TS065 authority.
Mutable checker reports cannot grant raw-byte compatibility.
The input proof keeps product acceptance false.

The helper downloads every official public crate archive and checks its exact locked SHA256.
It checks bounded tar data, paths, member types, duplicates, manifest identity, and every file hash.
It preserves notice files, the declared license file, the publisher license expression,
and the original archives. This is an inventory, not a license approval.

Exact Cargo and rustc 1.96.1 run only on GitHub.
Their hashes are observed inputs. They are not independently admitted binary pins.
The helper checks the frozen upstream rustc commit and the GNU Linux host.
It runs Cargo's own vendor command with both exact manifests, --locked, --sync, and --versioned-dirs.
Cargo writes the directory source and every .cargo-checksum.json.
The helper does not write an index or replacement checksum metadata.
It verifies the exact 352 vendor directories, every checksum entry, every actual file,
and the package checksums against the locks and publisher archives.
Only Cargo's exact Git metadata and root .cargo-ok omissions are allowed.
Git metadata includes .git path components and .gitignore or .gitattributes files at any depth.
Cargo 1.96.1 retains registry .orig and .rej files. Their omission is refused.
Any changed publisher byte or other omission blocks the closure for independent review.
This can expose a version-specific Cargo normalization change on the first hosted run.

A fresh Cargo home uses Cargo's emitted source configuration with offline mode.
Both real projects must pass cargo metadata --locked --offline with that home.
This command checks offline resolution. It does not build or test a product.
Frozen Cargo bytes and all staged source hashes must remain unchanged.

The candidate tar archive preserves source file modes, complete vendor checksum files,
license notices, and all original crate archives.
The receipt binds the tar SHA256 and all source, input, tool, crate, and file identities.
The job retains the complete bounded closure tree on failure, including original archives,
all vendor bytes, and the Cargo cache for independent review.
The emitted Cargo configuration contains the current owned run path.
Use it in that run. A moved bundle needs an explicit checked configuration step before use.

The current consumer route is blocked until the same-run assembly artifact exists.
The fragment expects input/_temp/som-unsealed-native-compile/assembly/manifest.json.
The controller must confirm that route against the actual assembly upload and choose the PR trigger.
Missing roots, missing receipts, bad hashes, unknown Cargo changes, and missing packages fail.
No product build, generated test, clippy run, service, behavior gate, or product acceptance is claimed.

References:
- [Cargo vendor](https://doc.rust-lang.org/cargo/commands/cargo-vendor.html)
- [Directory source checksums](https://doc.rust-lang.org/cargo/reference/source-replacement.html)
- [Registry archive checksums](https://doc.rust-lang.org/cargo/reference/registry-index.html)
- [Cargo 1.96.1 vendor_this rules](https://github.com/rust-lang/cargo/blob/356927216a2d746168cf76e5e88cc3f4b58e029d/src/cargo/ops/vendor.rs)

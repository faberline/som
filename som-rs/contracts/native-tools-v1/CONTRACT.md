# Native tools v1 — M12 / #146

SOM runs fixed rules: CNL to DSL to Code.
It needs no model, weights, training or Apple Silicon.
This contract adds native Linux x86_64 tool artifacts.
It preserves the accepted macOS arm64 artifacts.
It adds no language, scenario, DSL feature or business rule.

## Trusted artifacts

`artifacts.json` is the versioned source of artifact provenance.
It records official source URLs, archive and executable hashes, and license hashes.
The Linux runtime uses the corresponding committed native executable pins.
The existing macOS executable and descriptor hashes stay unchanged.
Unsupported native targets fail explicitly until trusted artifacts are added.

Ruff stays at 0.16.6 with the existing quality profile.
Rustfmt stays at 1.9.0 from Rust 1.96.1, edition2024, with the existing options.
Node stays at 26.5.0. Prettier stays at 3.9.10 with the exact accepted tree.
Linux uses the official Node binary without a separate libnode file.
The existing macOS Node artifact still requires its pinned libnode file.

Environment variables can select absolute tool paths.
They cannot select expected digests, versions, formatter options or profiles.
Verify bytes before execution. Refuse symlinks and non-regular tool files.
Include the selected path in each verification cache identity.
Keep the existing file-change, profile, version, error and timeout checks.
Do not add a test-only bypass.

## Compatibility

The old host and Homebrew paths describe historical installed artifacts.
They are superseded only for native-tool selection by this extension.
The old contract files and receipts keep their exact bytes.
The Rust007 and TypeScript003 amendment receipts bind this extension by hash.

Binary hashes differ across platforms.
Python and Rust formatter descriptors name the actual native binary hash.
The TypeScript descriptor keeps its canonical object rule.
Its Linux object has the native Node hash, `libnode: null`, and the accepted
Prettier tree hash. Its macOS object and descriptor keep their accepted bytes.
Backend source hashes also change when the adapter source changes.
The target-contract descriptors name the new sealed receipt maps.
Old bundles must fail the normal asset check rather than use an unrecorded tool.

All generated product files must keep their accepted bytes and behavior.
Existing field types, refusal rules, case sets, product layouts and formatter
settings stay unchanged. This extension does not waive a product gate.
Record baseline comparisons and every descriptor change separately.

## Provisioning and acceptance

Build and test only on GitHub runners.
Download artifacts from their recorded public source.
Check the archive digest before extracting a member.
Check every executable and the Prettier tree before execution.
Keep applicable license notices with the installed tools.
Generate compiler assets from tracked blocks and the approved native Ruff.
Do not copy a model directory, local cache, secret or private corpus.

Run both complete default Cargo suites, including binary and documentation tests.
No ignored or filtered test can replace a required result.
Keep the retired legacy-model feature disabled.
Run the complete applicable product and unchanged-output gates on one candidate.
Keep missing data, failures, timeouts and unrun checks blocking.
An independent audit must report zero failures and zero unrun checks.
Compiler unit tests and provisioning success do not prove product acceptance.

The amendment receipts state the actual implementation and review order.
Do not label staged implementation as wholly before implementation.
Contract review, tool execution and product acceptance are separate records.

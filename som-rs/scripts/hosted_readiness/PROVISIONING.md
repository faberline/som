# Native tool provisioning plan

This is the M12 / #146 native implementation that unblocks #141.
The current candidate uses the versioned pins in `contracts/native-tools-v1`.
The initial public-artifact proposal remains in `native-tools.json` here.
GitHub execution receipts qualify the actual tools separately from source review.
The fixed compiler needs no model or training data.

## Public artifacts

[native-tools.json](native-tools.json) records versioned public URLs, archive
digests, executable digests and license file digests.
The inventory was prepared on 2026-10-28.
Each archive was checked against the publisher's release metadata.
Archive members were hashed in memory. No downloaded program was executed.

| Tool | Version | Public distribution |
|---|---|---|
| Ruff | 0.16.6 | PyPI manylinux x86_64 wheel |
| rustfmt | 1.9.0, Rust 1.96.1 | Official Rust x86_64 Linux component archive |
| Node | 26.5.0 | Official Linux x64 archive |
| Prettier | 3.9.10 | npm package archive |

The Prettier archive produces tree digest
`f61c8e4b88d59c4ed6688179213270127e34f90911987959ef21ee396f9b7299`.
This equals the current formatter's trusted tree digest.
No platform change to those JavaScript bytes is needed.

Keep all applicable license notices when installing an artifact.
An archive digest does not replace its license notices.
The proposal records the notices found in each archive.

## Runtime adaptation in the candidate

Rust M12 owns these changes. Coordinate any additional shared path first.

1. Select trusted formatter artifact pins by native OS and CPU.
   Keep logical formatter versions, profiles and options unchanged.
   Do not read an expected digest from caller input or an environment variable.
2. Remove the Mach-O-only check in the quality loader.
   Keep digest, version, profile, file-change and timeout checks.
   Native ELF tools on Linux must pass the same checks.
3. Allow explicit Rust and Node executable paths.
   Check them against committed native artifact pins before execution.
   Include the resolved path in the verification cache key.
   Require the macOS libnode file only for an artifact that uses it.
4. Generate compiler assets from tracked blocks and the verified native Ruff.
   Change quality tests to use these compiler assets.
   Do not upload a local models directory to satisfy an old test path.
5. Install the verified Prettier tree and set its explicit path on the runner.
6. Run both complete default Cargo suites on GitHub without filters.
   Keep each failure and every unrun binary or documentation test explicit.

Rust amendment007 and TypeScript amendment003 record these tool identity changes.
Preserve the old receipts as evidence of earlier runs.
The user's platform correction authorizes removing the Apple-only requirement.
It does not authorize removing integrity or product behavior checks.
The new receipts record source approval and the actual staged implementation order.
They do not claim that tool or product execution passed.

Binary digests differ by native platform.
Bundle formatter descriptors must report the actual selected artifact.
Backend source changes also change backend digests.
Do not label a new Linux executable with an old macOS binary digest.
Generated product files must keep their accepted bytes and behavior.

## Full product gates

Compiler unit tests are one gate. They do not replace the product suites.
The full run still needs PostgreSQL, MongoDB, Redis and public service emulators.
Use the recorded logical versions with reproducible native artifact pins.
Use the full named case sets and one candidate for all applicable checks.
Keep the failure, timeout and owned-process rules in the readiness README.

The accepted response recordings and baseline compiler need a separate route.
The current repository does not contain every raw recording that the old runners use.
Classify each missing file before distribution.
Never upload private prompts, corpora, models or secrets into a public artifact.
Public source fixtures can be rebuilt from a recorded source revision.
An unavailable accepted baseline remains blocked until it can be supplied or
reproduced with the same full scope and independent review.
Do not replace a missing baseline with a fixture response.

The current amendment006 regression run uses the React M6 candidate.
M6 #130 owns that run. Its audit remains with the React chat.
Rust M12 supplies native tools and Rust evidence through that agreed boundary.

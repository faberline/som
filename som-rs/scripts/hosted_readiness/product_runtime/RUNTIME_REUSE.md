# Audited runtime artifact reuse

This installer runs only on GitHub Ubuntu 22.04 x86_64.
It does not build a new native tool.
It restores the exact first-build bytes that the controller reviewed from run 40740849594.
The byte audit and the qualification manifest are new source inputs.
The installer binds both files, its own code, the shared helper, and the six source archive pins to Git HEAD.

The pinned download-artifact action supports artifact-ids, run-id, repository, and github-token.
The job needs Actions read permission to download the artifact from the earlier run.
The installer compares GitHub API metadata with the committed repository, artifact ID, run ID,
source revision, name, expiry state, and artifact digest.
API response values and environment variables never select expected file hashes.
The token is used only for the read request. It is not passed to native children or written to receipts.

The original upload contains four metadata files, install files, license notices, and command logs.
The installer requires that exact path set. It rejects extra files and directories, links in transport,
hard links, unsafe paths, unknown modes, and unrecorded aliases.
The local audit copy has a separately fetched job.log. That file is not part of the original artifact.

All 10102 regular installed files must match the pinned inventory.
The eleven transported alias files must match the bytes of their recorded regular targets.
The installer copies regular files, restores mode755 or644 from the recorded executable flag,
and creates only the eleven recorded internal links.
No admitted executable can be a link.
The 41 executable paths and their version outputs come from the pinned original receipt.
The source and license evidence must match all six committed archive pins and thirteen notices.
Every original command must have zero exit status and both log hashes must match.

The output must be a fresh RUNNER_TEMP/som-product-runtime-qualification directory.
Its absolute path must equal the original Python install prefix.
A different runner path fails. The installer does not patch binaries or Python files to relocate them.
Original metadata and logs go under qualification-evidence.
Original license notices remain under licenses.
The original receipts keep their exact bytes.

Before any restored tool runs, the installer checks the complete restored tree.
It then rechecks all41 executable hashes and exact version stdout on GitHub.
The fixed Python core-module probe checks ssl, zlib, bz2, lzma, sqlite3, ctypes, venv, and ensurepip.
It compares the original OpenSSL, zlib, and SQLite version output.
Explicit Python -B prevents bytecode changes even in isolated mode.
Native children use the shared fixed environment and owned process-group cleanup.
No database or emulator starts.

After the probes, all installed file hashes, modes, links, old receipts, logs, and notices must still match.
The new receipt has version som.product-runtime-reuse.v1.
Success status is audited_runtime_reinstalled_and_rechecked_for_hosted_tests.
Product readiness and product acceptance stay false.
Moto, wheels, database services, and full product gates remain unrun.

The installer writes only fixed GitHub environment keys for Python, uv, PostgreSQL, MongoDB, and Redis.
Later wheel steps can use the qualified Python3.12.11 interpreter in this same job.
They must bind the installer, qualification, and audit to Git before using its public verification API.

Public API:
- qualification_data(raw_qualification, raw_audit, raw_source_pins) returns the validated qualification dict.
- read_inventory(path, qualification) checks its raw hash and returns the path-to-record dict.
- verify_installed_tree(root, inventory, qualification) checks the complete install tree and returns counts and the bound inventory hash.
- Refusal is the shared ValueError subclass. Unknown modes and aliases fail.

The API root is the runtime directory, not its install child.
A later consumer must check the full tree before its first child and after its last child.
A success flag in a mutable receipt is not file hash authority.
The committed qualification and original inventory hash remain that authority.

References:
- [Pinned action inputs](https://github.com/actions/download-artifact/blob/d3f86a106a0bac45b974a628896c90dbdf5c8093/action.yml)
- [GitHub artifact metadata API](https://docs.github.com/en/rest/actions/artifacts#get-an-artifact)

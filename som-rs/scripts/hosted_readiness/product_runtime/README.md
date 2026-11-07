# Product runtime first-build qualification

Run this prototype on GitHub Ubuntu 22.04 x86_64 only.
It uses the runner's system Python, with Python 3.10-compatible standard library code.
The job fragment is a separate review input. It does not set a workflow trigger.

The committed source pins admit PostgreSQL 17.7, CPython 3.12.11, and Redis 8.4.0 source archives.
They also admit MongoDB 7.0.28 and uv 0.9.9 native archive members.
The uv source archive supplies its license notices only.
The tool does not install the SOM Python project, MLX, models, or training packages.

The running script must use the fixed qualify_runtime.py path.
The pin file must use the fixed source-pins.json path.
Both complete working files must match the regular Git blobs at the declared HEAD.
The tool checks HEAD, disables Git replacement objects, and records each binding.
It reads expected archive hashes from the bound Git blob bytes.
It refuses dirty files and copied files at other paths.
Only system Git metadata commands run before this binding.
The controller must review the declared commit. Byte identity does not grant approval.

All six archives must pass their hash and member checks before a build or an admitted native tool runs.
The tool reads no expected hash from the environment.
Archive names, member paths, license hashes, executable hashes, links, and size limits are checked.
A bounded plain tar file limits parser input, including extra tar header data (PAX).
Full source trees can contain safe internal source links.
Admitted native executables must be regular files.

Use a new output directory below RUNNER_TEMP and outside the checkout.
The tool refuses existing output directories and symlink paths.
Builds, child processes, logs, temporary files, and installed files use that owned directory.
Child commands get a small fixed environment.
A timeout stops the child process group that this tool started.

The receipt records the candidate commit, manifest, builder script, bootstrap Python,
runner image identifiers, observed compiler and linker files, package inventory, command logs,
source entrypoint hashes, native tool hashes, and version results.
It checks core Python modules and records dynamic library output.
It preserves the pinned license notices.
It inventories installed aliases, helper scripts, and build intermediates separately.
It does not select aliases or helper scripts for qualification probes.
A source build can use its own verified helper files.

Source-built executable hashes are first-build observations.
They still need an independent qualification decision.
The Ubuntu image and system build packages are observed inputs, not new trusted binary pins.
The download archives and prebuilt member hashes have separate authority in the receipt.
Python's publisher Sigstore metadata is provenance only; this prototype does not verify its signature.

The tool does not start a database or Moto.
Moto, the complete Python wheel closure, product behavior, and product acceptance remain unrun.
A completed qualification command does not mean that the product runtime is ready.
Missing build tools, headers, system libraries, wrong versions, or integrity failures must fail the job.
Do not disable a product feature or weaken a gate to turn such a failure into success.

Run the synthetic tests on GitHub with test_qualify_runtime.py.
They use tiny fake archives and mocked network and process calls.
No test executes a built or downloaded program.

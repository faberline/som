# GitHub wheel closure qualification

Run produce_wheels.py only on a GitHub Ubuntu 22.04 x86_64 runner.
Use the Python 3.12.11 path admitted by reuse_runtime.py.
Start the producer and consumer with -I -S -B.
These flags disable ambient package paths, site hooks, and bytecode writes.
The producer needs tomllib, which is in that Python standard library.
Do not run the producer, tests, native tools, packages, or products locally.

The fixed producer, policy, environment consumer, native reuse helper, qualification,
byte audit, source pins, and product sources must match regular Git blobs at the declared HEAD.
Only system Git metadata commands run before this source check.
Bound helper modules execute only their verified raw bytes in fresh registered modules.
No file loader, cached PYC, or project import path can supply their code.
The producer then runs the source-bound native tree verifier.
It checks the complete reuse receipt and all 41 admitted tool records.
Python and uv must have the exact reviewed file hashes and versions.
Every later child checks these two file hashes again.
The whole native tree gets a final check.
No expected native hash comes from an environment variable or an input receipt flag.

The saved native artifact is an intermediate route.
It expires after 30 days.
This stage does not establish permanent runtime readiness.
It always reports product_runtime_ready=false and product_acceptance_passed=false.
Independent wheel metadata and license review also remain unrun.

The input environment receipt must cover all 671 frozen Python products.
The producer reuses the bound consumer to check all raw assembly replies and actual file bytes again.
It checks both compile variants for every frozen product.
It re-reads each requirements.in and original pyproject.toml.
The observer keeps requests 2.34.2.
Products keep requests 2.32.5 where their original source declares it.
No product receives a union of other product requirements.
The som-core project and model packages are never installed.

A dependency set key includes the exact requirements.in bytes, native Python hash,
uv hash, complete native inventory hash, Python version, and target platform.
Only identical keys share resolution work and an owned dependency metadata cache.
The first actual product in that set supplies its unchanged pyproject.toml.
Only the observer receives a project file made from its declared observer requirements.
Every actual product retains its own name, dependency groups, extras, constraints, and project bytes.

The producer first makes a universal uv.lock from that real project.
It uses --no-build and the official PyPI index.
It exports the frozen lock with its hashes.
Then it freezes the Linux CPython 3.12.11 target into a hash-locked requirements file.
It selects one compatible publisher wheel per locked package.
The selected SHA must occur in both the uv lock and official PyPI JSON.
The download must come from files.pythonhosted.org over HTTPS.
Redirects, byte counts, archive hashes, member paths, member types, expansion bounds,
METADATA identity, WHEEL tags, and every RECORD member hash are checked.
The producer never selects, builds, or installs a source distribution.
Universal lock source-distribution records remain metadata only.

The producer preserves each complete wheel.
It also saves all member hashes, raw METADATA, WHEEL, RECORD, and license and notice bytes.
An absent publisher notice file is reported explicitly.
That observation is not a license approval.
The complete wheel artifact and metadata need an independent audit.

The producer resolves the original requirements.in again with network access disabled.
It uses only the selected wheel directory and additive exact locked pins.
An additive pin can restrict an original requirement.
It cannot replace that requirement or remove an extra.
The offline result must equal the full selected target lock.
A missing transitive wheel or an unmet original constraint fails the stage.

After every dependency set passes, the producer checks every named project environment.
Each project gets a new owned directory, its original pyproject.toml, its own uv.lock,
and its own virtual environment.
The lock uses that exact dependency set's previously recorded metadata cache with network access disabled.
Sync uses the frozen lock, offline mode, and the pinned wheel directory.
No model, compiler, product module, server, or product test is started.
The metadata probe uses the admitted base Python with -I -S -B and only standard library modules.
It reads distributions from the exact owned site-packages directory as data.
It never puts that directory on sys.path or starts its Python executable.
The producer checks the entire installed scope before that probe.
No uv command runs after sync because uv's own interpreter query uses -I -B without -S.
The dependency check is the offline replay of all original requirements before installation.
uv run remains unrun until the full product gates.
The producer checks all installed package versions, raw METADATA hashes, and the full source-derived file set.
Every installed file or link must have an exact source and expected bytes or link target.
No unexpected Python, PTH, script, metadata, RECORD, or empty directory is accepted.
Only the actual top-level distribution RECORD uses the exact uv rewrite rules.
Nested vendored metadata, RECORD, and data directory payloads stay immutable.
The source authority pins uv's seed assets, activation templates, wrapper generator,
data relocation, RECORD serializer, and exact virtual environment configuration.
All ten seed templates must also occur in the exact qualified uv binary.
The three BAT templates use the CRLF transformation declared by uv's pinned .gitattributes.
The other seed assets use their exact Git bytes.
It checks exact quality tool versions in each quality product's own environment.
The unchanged strict quality checker itself remains a later product gate.

Qualification child commands set UV_NO_INSTALLER_METADATA=1 and UV_COMPILE_BYTECODE=0.
These settings are recorded in each command and environment receipt.
They are never exported through GITHUB_ENV or used as global defaults.
The first setting suppresses new INSTALLER, REQUESTED, direct URL, cache, and build metadata.
It does not excuse any extra file that appears anyway.
The later unchanged product gates must use their actual declared installer mode.
This qualification mode is not evidence that those full gates already passed.

The job streams these checks to keep disk use bounded.
It preserves complete installed-file and metadata receipts before removing its own successful virtual environment.
It keeps the source project, frozen lock, command arguments, process identity, logs, times,
package versions, wheel bindings, cache identity, and cleanup receipt.
It never removes a failed environment.
A failed closure blocks all project installs.
A failed named environment stops later environment installs and leaves them marked unrun.
No failed, blocked, or unrun slot can make the complete flags true.

Older products with no pyproject.toml retain their accepted observer-runtime route.
All their frozen IDs and map hashes remain in observer_runtime_products.
No fake project is made for them.
The observer environment is qualified once as the exact accepted shared runtime route.
All 671 products still need their full behavior gates later.
Those later gates must create fresh owned environments from these saved locks and wheel bytes.

The CLI flags were checked against the primary
[uv 0.9.9 CLI reference](https://github.com/astral-sh/uv/blob/0.9.9/docs/reference/cli.md)
and [uv 0.9.9 CLI source](https://github.com/astral-sh/uv/blob/0.9.9/crates/uv-cli/src/lib.rs).
The policy records their exact text hashes.
No pip command or uv pip download command is used.

The job steps fragment follows successful native reuse in the same owning job.
That job must also depend on experimental-native-compile.
The complete assembly artifact must come from the same workflow run and source revision.
The steps use its existing _temp/som-unsealed-native-compile artifact root.
The artifact upload omits the mutable dependency caches.
It retains every selected wheel, lock, notice, metadata audit, command log, successful inventory,
and failed environment for review.

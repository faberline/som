# Python environment input qualification

This helper makes a source-bound plan for Python dependencies.
Run it on a GitHub Linux x86_64 runner with system Python 3.11 or later.
The helper does not import a product or a project module.
It uses only Git metadata commands and standard library reads.
It does not run a resolver, package, compiler, test server, or product.

The fixed helper and plan paths must match regular Git blobs at the declared HEAD.
The plan pins the owning templates, product host, fixture imports, and quality checker.
A source change stops the helper until the plan gets a new source review.
The templates contain 17 runtime requirement spellings and eight development spellings.
These are direct requirements. They are not a complete wheel lock.

The observer requirements include Moto 5.2.3 and requests 2.34.2.
Each product keeps its own requirements, including requests 2.32.5.
Each environment gets its own cache, virtual environment, and wheel directory.
A virtual environment is a separate Python package installation.
The helper never installs the som-core project or model dependencies.
It does not replace a constraint or install a union of all product requirements.

The first GitHub job can run without product files.
It emits the source inventory and explicit missing inputs.
It always reports product_runtime_ready=false and product_acceptance_passed=false.
The separate job fragment does not define a workflow trigger.

After the owned assembly job passes, use the root of its artifact as --assembly.
That root must contain assembly/manifest.json.
The consumer requires som.native-assembly-experiment.v1 at the same tested revision.
It requires assembly_experiment_complete=true and the exact input golden receipts.
It requires every frozen public127 and legacy544 product in its fixed order.
It reads both compile variants and their named assembly request and reply bytes.
It checks every product byte, file count, accepted per-file hash, and complete map hash.
An incomplete, changed, missing, extra, linked, or repeated observation fails the job.
No file is selected by a dependency guess or a sample.

For each actual product with a pyproject.toml, the output keeps its exact bytes.
The output also keeps the runtime and development requirement groups.
Older products with no project file keep the observer runtime route used by hosts.py.
The output lists every such product by its frozen ID and map hash.
No fake project file is made for them.

Strict quality checks use each actual quality product's own Python environment.
The command is that environment's venv/bin/python, followed by the unchanged
som-core/scripts/verify_python_quality.py path, the product path, and --evidence with a new evidence path.
The product must already declare all four exact checker tool pins in its development group.
The source-bound checker records the tool versions and refuses wrong versions.
The observer only starts this command and collects the result.
Do not add missing product tools to a global environment.
If a required tool is not declared, bind its owning requirement source before provisioning it.

The next stage must run on GitHub with independently qualified Python 3.12.11 and uv 0.9.9.
For each environment, it must preserve the exact emitted inputs and do all of these steps:

1. Resolve the full transitive dependency set into a uv.lock without overrides.
2. Export a hash-locked requirements file from that frozen lock.
3. Download only Linux CPython 3.12 compatible wheels with hash checks.
4. Keep every wheel and record its hash, size, name, version, METADATA, and license/notice hashes.
5. Install only those wheels in that environment with network use disabled.
6. Run uv sync and uv run with the frozen lock and offline settings.
7. Check the installed dependency closure and each required tool version.
8. Check that the product requirements and lock bytes did not change.

The closure stage must keep a separate receipt for every environment.
The wheel bytes preserve the complete license and notice files for independent audit.
A successful resolver command alone cannot establish a complete offline closure.
A complete environment input set cannot establish runtime or product acceptance.
The existing product and behavior gates still need full GitHub runs and independent acceptance.

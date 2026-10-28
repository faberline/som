# SOM compiler acceptance tools

## Development execution host — 2026-10-28

The current compiler uses fixed `CNL → DSL → Code` algorithms.
It needs no trained model or Apple chip.
Run the build, test and service commands below on a GitHub Actions runner.
Edit locally and push first. Do not run them on the shared development computer.
Old native tool pins need compatible runner tools and the required contract amendments.
Keep full case coverage and behavior checks.
Read [the current architecture](../../../docs/architecture.md).

This folder contains the reusable independent acceptance tools for the SOM Rust compiler. Install the whole folder at `som-rs/scripts/compiler_acceptance/`. The tools do not invoke a model. They use the frozen contracts, public prompts, public Python validators, the fixed selection policy, the existing independent behavior checks, and the archived products as regression inputs.

Set `SOM_REPO` to the checkout root when this folder is still outside the repository. After installation, the tools find the repository from their file path. Evidence defaults to `<SOM_REPO>/.local/rust-compiler-goal/acceptance/`. Every runner requires a new output path below that directory. The scripts and this folder do not write repository source or Git state.

The asset index and the 544-job inventory must be regenerated after the final compiler source and asset package are installed. Do not reuse an exploratory index or inventory.

```sh
export SOM_REPO=/absolute/path/to/som
python3 "$SOM_REPO/som-rs/scripts/compiler_acceptance/asset_index_generator.py" \
  --package-assets /absolute/path/to/final/compiler-assets \
  --formatter /absolute/path/to/final/compiler-assets/ruff
python3 "$SOM_REPO/som-rs/scripts/compiler_acceptance/legacy544/runner.py" \
  --inventory-only \
  --asset-index "$SOM_REPO/.local/rust-compiler-goal/acceptance/per-version-asset-index-final.json" \
  --asset-provenance "$SOM_REPO/.local/rust-compiler-goal/acceptance/per-version-asset-index-provenance-final.json"
```

The principal commands are:

```sh
python3 "$SOM_REPO/som-rs/scripts/compiler_acceptance/qa_harness.py" \
  --http-base http://127.0.0.1:PORT \
  --asset-index "$SOM_REPO/.local/rust-compiler-goal/acceptance/per-version-asset-index-final.json" \
  --seed-bundle /absolute/path/to/independently-reviewed-valid-bundle

python3 "$SOM_REPO/som-rs/scripts/compiler_acceptance/public_products.py" \
  --http-base http://127.0.0.1:PORT \
  --asset-index "$SOM_REPO/.local/rust-compiler-goal/acceptance/per-version-asset-index-final.json" \
  --formatter /absolute/path/to/final/compiler-assets/ruff

python3 "$SOM_REPO/som-rs/scripts/compiler_acceptance/runtime_acceptance.py" \
  --binary /absolute/path/to/final/compiler \
  --assets /absolute/path/to/final/compiler-assets \
  --asset-index "$SOM_REPO/.local/rust-compiler-goal/acceptance/per-version-asset-index-final.json"

python3 "$SOM_REPO/som-rs/scripts/compiler_acceptance/gap_acceptance.py" \
  --binary /absolute/path/to/final/compiler \
  --assets /absolute/path/to/final/compiler-assets \
  --asset-index "$SOM_REPO/.local/rust-compiler-goal/acceptance/per-version-asset-index-final.json"

python3 "$SOM_REPO/som-rs/scripts/compiler_acceptance/legacy544/runner.py" \
  --inventory "$SOM_REPO/.local/rust-compiler-goal/acceptance/legacy544/inventory.json" \
  --http-base http://127.0.0.1:PORT \
  --asset-index "$SOM_REPO/.local/rust-compiler-goal/acceptance/per-version-asset-index-final.json" \
  --asset-provenance "$SOM_REPO/.local/rust-compiler-goal/acceptance/per-version-asset-index-provenance-final.json" \
  --formatter /absolute/path/to/final/compiler-assets/ruff
```

Use a fresh, owned candidate process for runtime checks. The HTTP, probe, asset index, formatter and output inputs must all identify the same frozen build. The root controller owns that binding and final acceptance. Missing, failed, or unrun cases remain failures.

`public_reference.py` contains only the existing public constructor and renderer functions used by `public_products.py`. It has no command-line runner and does not choose answers from case IDs. The separately prepared 11-case live-behavior runner remains a distinct acceptance scope.

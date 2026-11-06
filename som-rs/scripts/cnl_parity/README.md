# CNL replacement comparison

For full local acceptance, supply the candidate source repository, the
frozen baseline, the fixed corpus, and the original 178-case report. Put
the pinned Rust and Node tools on `PATH`. Set `SOM_PARITY_RUFF` to the
pinned Ruff executable and `SOM_TS_PRETTIER` to the pinned Prettier package
directory.

```sh
python3 som-rs/scripts/cnl_parity/accept.py \
  --baseline "$SOM_BASELINE" --candidate "$SOM_CANDIDATE" \
  --corpus "$SOM_CORPUS" --original-report "$SOM_ORIGINAL_REPORT" \
  --ruff "$SOM_PARITY_RUFF" --prettier "$SOM_TS_PRETTIER" \
  --output "$(mktemp -d /private/tmp/som-cnl-accept.XXXXXX)/results"
```

Each run copies the candidate sources into its output directory. A Git
source uses its current tracked and untracked files, excluding ignored
files. A plain snapshot excludes build directories and local runtime
assets. The copy gets only the three required quality fixtures from the
frozen baseline. Its compiler assets are then rebuilt. The input candidate
can therefore be the managed worktree, and the same command can run again.
`source-snapshot.json` records the original paths, the actual build copy,
and source and fixture hashes.

The acceptance command uses a fresh Cargo cache and anonymous public
fetches. It tests the standalone CNL crate, both SOM crates, the differential
corpus, and the HTTP and CLI paths. The standalone CNL test result covers
that crate. The complete NLP workspace has its own test suite.
`acceptance.json` is written only after every gate passes. The optional
`--baseline-report` flag reuses a retained baseline report during development.
For a full run with this flag, also pass `--baseline-bundles <directory>`.
This directory must contain the old compiler's four `<target>.bundle.json`
files. The runner binds each old bundle to the same retained prompt and DSL.
Changed declared assets must cause `ASSET_HASH_MISMATCH`. Unchanged assets
must produce the exact retained files. HTTP and CLI must agree in both cases.
Without report reuse, the runner uses the bundles from its baseline run.

NLP owns the deterministic Rust CNL syntax scan, statement events and source
positions. SOM owns typed lowering, semantic rules, public diagnostics,
DSL planning and code generation. `Cargo.toml` pins the NLP crate to an
immutable public Git revision, and both SOM lockfiles bind that revision.
The adapter requires syntax report API version 2. It renders errors from
typed rule facts and scope segments. It does not use NLP's compatibility
messages or pointers. It does not parse CNL text again.
NLP exports its `SOURCE_FILES`. SOM hashes those bytes and its lowering
sources into the compiler fingerprint. Recompile saved CNL to make a bundle
for a new compiler fingerprint. Historical bundles retain their original
compiler identity and must pass the existing identity checks.

To update NLP, agree on the syntax API and fixed cases first. Change the full
Git revision in `som-rs/Cargo.toml` and resolve both Cargo lockfiles. Run this
acceptance command before using the new assets. Keep the old compiler and
its assets with the old baseline report.
Target backend descriptors follow their frozen renderer contracts. A parser
change does not invalidate a target bundle whose complete declared assets
remain unchanged.

For a comparison within an already prepared environment, use `run.py`.
Keep a source copy of the retained compiler before changing its parser. Run
the comparison against that copy and an isolated candidate copy. Both copies
must include the repository CNL fixtures. The runner installs a temporary,
identical test in each copy and removes it when that run ends.

Set `SOM_BASELINE` and `SOM_CANDIDATE` to the repository roots. Set
`SOM_PARITY_RUFF` to the pinned Ruff executable. Set `SOM_TS_PRETTIER` to
the directory of the pinned Prettier package.
Put the frozen Rust and Node tools on `PATH`. The existing `CARGO_HOME`
setting is used unless `--cargo-home` supplies a separate cache.

```sh
python3 som-rs/scripts/cnl_parity/run.py \
  --baseline "$SOM_BASELINE" --candidate "$SOM_CANDIDATE" \
  --ruff "$SOM_PARITY_RUFF" --prettier "$SOM_TS_PRETTIER" \
  --offline
```

The command prints its new output directory. It freezes the corpus from the
baseline, records tool identities, runs both compilers, exercises the local
HTTP and CLI paths, and writes `comparison.json`. A failed comparison exits
with status 1. It never updates expected results to accept a difference.

Use `--parse-only` for a fast comparison of prompts, source maps and complete
parse errors. Use `--corpus` to reuse the same frozen inputs. Use
`--baseline-report` to reuse a retained result during development. Full
acceptance runs both compilers and their HTTP and CLI checks.
Pass `--original-report` to bind the original 178 cases to the prior
890-record report as well as to the freshly run baseline.

The original 178 labels retain their four-target matrix and exact report
shape. They must produce 322 products. Extra deterministic cases cover
missing and duplicate clauses in v1–v7, block EOF, line endings, whitespace,
Unicode and JSON literals, version dispatch, mixed errors and deep blocks.
Most extra cases compare parsing. The mixed-error cases also compare all
four compile responses. Source hashes count distinct inputs while labels
retain the purpose of each case.

The ignored Rust test only runs when requested. Its normal environment is
set by the Python runner. For a focused investigation, the test also accepts
`SOM_PARITY_CASE_PREFIX`, which selects input labels by prefix. The full
runner clears that setting so a focused run cannot pass full acceptance.

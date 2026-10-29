# React replay tree ordering diagnostic

This draft is a diagnostic. It is not product acceptance. Have another reviewer
inspect the exact script and fixture bytes before running them on GitHub.
Bind the workflow to the reviewed script and fixture SHA values and the Git
head that contains them. Do not download and execute an unbound script.

On a GitHub-hosted Ubuntu 22.04 runner with `actions: read`, obtain the fixed
artifact and metadata. Run the synthetic tests first. Use the directory that
contains the reviewed scripts as the working directory:

```sh
python3 -m unittest -v test_tree_order
gh api repos/faberline/som/actions/runs/40818588943 > "$RUNNER_TEMP/react-order-run.json"
gh api repos/faberline/som/actions/artifacts/12873517359 > "$RUNNER_TEMP/react-order-artifact.json"
gh api repos/faberline/som/actions/artifacts/12873517359/zip > "$RUNNER_TEMP/react-order-replay.zip"
python3 tree_order.py \
  --zip "$RUNNER_TEMP/react-order-replay.zip" \
  --run-json "$RUNNER_TEMP/react-order-run.json" \
  --artifact-json "$RUNNER_TEMP/react-order-artifact.json" \
  --out "$RUNNER_TEMP/react-order-result"
```

Inputs must be regular files directly inside `RUNNER_TEMP`. The output must be
a new directory directly inside it. The script verifies the source run and head,
artifact metadata, ZIP SHA, streamed TAR SHA, frozen tree counts and lexical
hashes. It refuses duplicate tree paths, duplicate TAR members and invalid
snapshot records. It reads the TAR stream without extracting it. It never
changes the input evidence.

The result contains `report.json` and six files named `<tree>-<order>.rows`.
Each row is `relative_path`, NUL, lowercase file SHA-256, newline. Each of npm,
cache and Prettier has its own lexical and `PurePosixPath` ordered list. No
paths are combined between trees. The report gives the count, both hashes,
whether order is equal, and the zero-based first difference with both names.
A difference is a diagnostic observation, not acceptance or permission to relax
a check.

The npm and Prettier rows are rehashed from TAR file bytes. The cache rows are
from retained before/after snapshots, which must match each other. The original
cache bytes are absent from this replay TAR and are not re-read. The snapshots'
lexical tree hash is checked against the recorded 246-file source SHA, but this
does not independently establish the original cache bytes or observe changes
between snapshots. Before using any finding to change a gate, independently
review the frozen tree hash rule and bind all affected producer and auditor
source and evidence in a reviewed amendment.

# Draft independent Ubuntu 22.04 replay audit

This audit is unsealed and is not product acceptance. It independently parses
the retained public artifact from run `40818588943`, head
`448862a76656095696f2791d46428a0b87e063e1`, artifact `12873517359`.
It never imports or executes the original producer or replay scripts.

The caller must retrieve the ZIP, GitHub run JSON, job JSON, artifact JSON and
job log on a GitHub-hosted Linux runner. All inputs and the new output must be
directly under `RUNNER_TEMP`. The required CLI is:

```sh
python3 -m unittest -v test_audit
python3 audit.py \
  --zip "$RUNNER_TEMP/replay.zip" \
  --run-json "$RUNNER_TEMP/replay-run.json" \
  --jobs-json "$RUNNER_TEMP/replay-jobs.json" \
  --artifact-json "$RUNNER_TEMP/replay-artifact.json" \
  --job-log "$RUNNER_TEMP/replay-job.log" \
  --output "$RUNNER_TEMP/replay-independent-audit.json"
```

Retrieve metadata with `gh api repos/faberline/som/actions/runs/40818588943`,
`gh api repos/faberline/som/actions/runs/40818588943/jobs`, and
`gh api repos/faberline/som/actions/artifacts/12873517359`. Retrieve the ZIP
with `gh api repos/faberline/som/actions/artifacts/12873517359/zip` and the
log with `gh run view 40818588943 --repo faberline/som --job 123265438449
--log`. The job requires GitHub `actions: read` and `contents: read` only.
Capture each command's output into the path named in the CLI above.

The audit pins the ZIP SHA, all eight ZIP members, the replay TAR and sidecar,
the prior source TAR SHA, the producer and replay heads, and source audit
receipts. It compares both recorded cache snapshots by every path, mode and
file digest. It recomputes their tree SHA and file count. It checks the Node
bytes and complete npm tree retained under `probe/source-replay`, the complete
installed Prettier tree, the reference manifest and lockfile, both Linux
platform package metadata files, and seven commands' argv, exits and raw
stdout/stderr hashes. It compares the recorded Ubuntu image to the GitHub job
log. TAR entries are streamed and are not extracted to the filesystem.

The original `/etc/os-release` bytes and the live replay cache are not retained
in the replay artifact. The recorded before/after cache maps can be checked
against the prior source tree SHA, but their capture times and intermediate
mutations cannot be independently observed after the run. The original source
TAR is identified by its fixed SHA and previous independent audit receipts;
this audit does not re-read that source TAR. It checks platform package metadata,
not every installed package file. It does not independently observe the original
process environment or OS-level network isolation. Success means only that the
retained evidence is internally consistent within these explicit limits.

The tests are synthetic and have not yet run. Run them on GitHub, and require
an independent source review before using the results.

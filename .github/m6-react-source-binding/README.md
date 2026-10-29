# React historical source snapshot experiment

This scratch prototype binds a fixed source subset to commit
`f59e28d910ff4adb16e3becfdc82bd2978ca84fa`. It is not a production policy,
an acceptance gate, or evidence that a candidate binary or product works.
Every successful result states `HISTORICAL SOURCE SUBSET / NO ACCEPTANCE`.

On an actual GitHub-hosted Linux runner, it checks the checkout HEAD against
the event SHA and requires f59 to be an ancestor. It rejects changed protected
paths and compares each working file with its f59 Git blob. It parses the
source-pinned Rust `BACKEND_FILES` literal and resolves all seven `include_str!`
aliases. It recomputes the backend digest. It verifies the fixed f59 receipt
chain, all 54 React002 receipt members, exact receipt file set, and target
digest. It refuses additional or missing amendment receipt files.
It refuses `__pycache__` inside the frozen roots, even if Git ignores it. It
rechecks the full source subset, protected status and HEAD after derivation.
It derives the formatter descriptor from the frozen macOS Node, libnode and
Prettier tree formula. A Linux runner observing that file does not prove a Linux
Node, formatter, cache, native policy, or runtime identity.

This is a read-only isolated-runner observation, not an atomic filesystem
snapshot. Path type checks and reads can race with a malicious concurrent writer;
a writer that changes and restores data between checks can evade re-observation.
Production authority needs an independently reviewed race-safe source boundary.

The known f59 hashes are observations of historical source. They are not future
amendment after-values. A new receipt or source edit needs an independently
reviewed derivation from the new full source and files maps. Keep derived hashes
and runtime observations outside their own input vectors to prevent cycles.

The test source checks a positive fixed snapshot and rejects wrong host/head,
altered aliases, receipt/member/file-set changes, malformed JSON, bad paths,
formatter changes, ignored cache directories, dirty protected paths, and HEAD
changes during observation. Tests use mocking to force faults;
those tests do not create a policy-authority bypass in product code.

No local test, import of this module, build or service was run while authoring.
Run only on a disposable GitHub-hosted runner. The checked-out test commit must
have the fixed f59 commit in its available ancestry. A workflow that adds a new
commit on top of `77d5c0dd4f9815e0a7b1ec281477e74a407f9919` needs checkout
`fetch-depth: 4` or greater. Use the exact push SHA as the checkout ref. From
the directory with these files, run:

```
python3 -m unittest -v test_react_source_snapshot
```

Reviewers must not infer current f59 runtime behavior, final policy sealing,
full 129-case React validation, or M6/M12 acceptance from this result.

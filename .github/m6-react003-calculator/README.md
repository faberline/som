# DRAFT React003 files-map calculator

This scratch calculator is not approved, sealed, independently audited or product acceptance. Its output is candidate metadata only. No commit identity, chronology, review verdict, or receipt status is invented. The real React003 scope and source have not yet been finalized. `plan.example.json` illustrates only three pending proposed file changes. It is not a complete plan if any other mapped files change.

The draft profile and helper pins in this example use the Path-order variant checked by GH run 40823968948. The old lexical npm tree SHA `69965a8f2a75cad99018e333ff1819bdc8cbf4f17570e77f24ad5f95aa57e558` remains a historical observation of the earlier producer data. The Path-order SHA `303f008099574a6edbb698df7d748a897f48f8a0c69cb1f3ee699e2937f50ca9` identifies the same retained npm file map under the frozen path ordering. This does not seal the profile, prove the actual runner image, or establish product acceptance. Cache bytes remain subject to the documented retained-snapshot evidence limits.

Both calculator and synthetic fixtures require a GitHub-hosted runner. Source and test bytes must be independently reviewed and bound to exact SHA-256 and the Git tree before import or execution. The final receipt, profile, helper, product runner, collector and auditor need independent review. Do not run this on a developer host.

After a GitHub workflow has checked out the reviewed candidate, verified the source/blob bindings, and copied an independently reviewed complete plan to runner temp, run:

```sh
python3 -B "$GITHUB_WORKSPACE/.github/m6-react003-calculator/react003_map.py" \
  --repo "$GITHUB_WORKSPACE" \
  --plan "$RUNNER_TEMP/react003-plan.json" \
  --out "$RUNNER_TEMP/react003-candidate-metadata.json"
```

`--plan` and a new `--out` must be absolute physical paths under `RUNNER_TEMP`; `--out` must be outside the repo. The plan must name every actual changed and added mapped path in sorted arrays, supply an exact new SHA-256 for each, and map every changed old file to its repo-relative archive path. Nothing may be deleted. The script pins 002 receipt bytes, target and count; reads all three contract roots; hashes every actual regular file; rejects symlinks, scan failures, traversal, unexpected files, mismatched new pins and missing or altered old archive bytes. Like the existing contract checker, it excludes `__pycache__` and `.pyc` entries from the scan. It rejects edits to the exact draft profile and prevents future receipt references in changed/added mapped files or the computed target digest in any mapped file.

This is a partial cycle guard, not a proof that arbitrary source has no semantic hash cycle. Independently review the entire dependency graph, including the later authority file and source hashes, before using the output. The map includes the profile, helper and all other files placed under its roots. The numbered 003 receipt and archive are siblings outside the roots. No target or receipt hash may be written back into mapped source; any mapped change requires recalculation and fresh review.

The GH fixture command is:

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest -v test_react003_map
```

Fixtures patch the pinned baseline and profile for small synthetic inputs. Two additional tests check the actual Path-order draft profile and example source pins. Stage `qa-profile.path-order.draft.json` and `plan.example.json` beside the test, and bind their exact source SHA and Git blob before testing. These checks do not establish the real 002 receipt, real candidate, review or acceptance. Failed or missing GH test results must not be treated as a pass. Preserve the original source, logs and failures.

# M6 on GitHub runners

Edit code locally. Run tests, builds, and services on GitHub-hosted runners.
SOM uses fixed rules for CNL → DSL → Code. It needs no model training.

## Two separate results

The `M6 tooling` workflow runs fake fixtures. It proves tool behavior only.
The `M6 hosted acceptance` workflow first checks the frozen tool files.
Its provisioning result always says `product_suites: not_run`.

The full run builds one candidate. It runs all twelve acceptance steps.
It then runs the independent audit against the retained raw output.
It samples no cases. A missing result blocks acceptance.

The recorded baseline and frozen tool bottles require `macos-26`.
This preserves the existing baseline for this compatibility run.
It does not make Apple hardware a requirement of the compiler algorithm.
M12 owns native tool support on other hosts.

## Approve inputs before transfer

`hosted_inputs.json` pins an encrypted input commit and its index hash.
The controller prepares the encrypted files separately from the code branch.
The secret `SOM_M6_ACCEPTANCE_INPUTS_V1` holds the decryption keyring.
Keys and local selection files must never enter Git.

The human must approve the input payload and destination repository first.
A label does not grant that approval.
After approval, the controller can push the encrypted input commit.
It can then add `m6:run-full-acceptance` to the same-repository pull request.
The label schedules the full job. It must not be added before approval.
Manual runs can select `provision` or `acceptance` after the workflow is available.

`fetch_hosted_inputs.py` checks every manifest before restore.
It verifies hashes, paths, sizes, modes, counts, and the exact keyring.
It restores only files under `.local/` in a fresh staging tree.
Its success result is input provisioning, not product acceptance.

## Run and retain evidence

Public crates and Python packages are fetched before offline verification.
The frozen npm caches remain read-only during verification.
The baseline uses QA port `18917`. The candidate uses QA port `18919`.
No QA service binds to `8765` or `18765`.

The isolated runner expects no default service on `8765`.
Two read-only health requests must both get an OS connection-refused result.
A timeout, a live response, or a state change fails that check.
The usual healthy-default mode stays available for other environments.

After each complete product check, the run removes installed dependencies.
It removes PostgreSQL scratch data only after a verified process stop.
It keeps both static build output trees, source files, replies, reports, and logs.

The driver removes input keys and GitHub tokens from child environments.
The workflow publishes only safe summaries in plain text.
It encrypts raw evidence before uploading an Actions artifact.
Never upload the whole acceptance directory as a plain artifact.

`summary.json` reports acceptance only when the build, all twelve steps,
the independent audit, source identity, and owned-process cleanup pass.
The audit must have zero failures and zero unrun checks.
The candidate remains a QA candidate. Default switching and release need #131.

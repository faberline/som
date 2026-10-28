# M6 compiler release

This is the operator plan for [issue #131](https://github.com/faberline/som/issues/131).
The release is pending.
The production default, tag, and GitHub release each need separate user approval in the conversation.
An input-upload approval does not approve any of these actions.

## Accepted inputs

[Issue #130](https://github.com/faberline/som/issues/130) passed independent acceptance.
[PR #148](https://github.com/faberline/som/pull/148) merged it to `main` at `b0913e81bb5098f490aac70fb25b4611fd1d8499`.
See the [acceptance receipt](../som-rs/evidence/react-output-acceptance-2026-10-29-issue130.json) and [independent review](../som-rs/evidence/react-output-acceptance-independent-review-2026-10-29-issue130.json).

| Role | Source commit | Binary SHA-256 |
|---|---|---|
| Candidate | `40cffc13b3bff35e3ea5804b9f43a49cdef7b3c0` | `59dab78a0f401a86e028c186285a888cfdf304b2d077e167d46b20d1131a3308` |
| Previous default | `8c4a91fbb5e3b9ae2f96881cf2198b3d2ce321af` | `2bceb3a0a1de02b08c71eb557c0c3e32fe5ee7b646935e7821e9e07ab3d56e0c` |

SHA-256 checks that a file has the exact accepted bytes.
Reuse these binaries.
Do not rebuild a different release candidate from a later source commit.
The fixed compiler needs no model or training data.

The complete #130 run covered 127 public Python products and 544 legacy jobs.
It also covered all applicable Rust, TypeScript, and React cases.
The [original runtime](https://github.com/faberline/som/actions/runs/40743738705) passed all 12 runtime steps.
That workflow failed in its first auditor.
The [corrected independent audit](https://github.com/faberline/som/actions/runs/40755984489) passed on the same saved bytes.
The release rehearsal does not replace this full acceptance.

## Hosted rehearsal

Use [m6-release.yml](../.github/workflows/m6-release.yml) on a GitHub-hosted macOS 26 runner.
The macOS tools preserve the historical tool hashes.
Portable tool work belongs to [M12](https://github.com/faberline/som/milestone/18).
All project tests, builds, and QA services run on GitHub runners.

The workflow downloads two exact #130 artifacts.
It checks their repository, run, source commit, length, archive hash, index, and member set.
It restores the already-approved 16 input packages.
It installs the frozen tools before QA starts.
The QA phase uses only loopback service calls.
It does not use port `8765` or `18765`.
No npm install is needed for these compiler canaries.
Any later product install must use `npm ci --offline`.

`prepare_compiler_inputs.py` pins the saved successful requests and replies.
It does not call a service.
It uses the explicit `som.compiler-output-switch-prerequisites.v1` profile.
The earlier release profile keeps its existing M2 behavior.

| Target | Candidate cases | Previous-default cases |
|---|---|---|
| Python | `PAIR_V7_FLOW_CONTRACT`, `services14-0000` | Same cases |
| Rust | `example-v1`, `example-v5` | Same cases |
| TypeScript | `example-v1`, `example-v5` | Same cases |
| React | `example-v1`, `example-v4` | Not supported by the previous default |

Each canary checks compile and assemble over HTTP.
It also checks the CLI assemble output.
The CLI is the command-line client.
Uncaptured CLI expectations come from the accepted HTTP reply.
The producer first checks this format against nine saved successful CLI outputs.
Previous Rust assemble expectations use the accepted product bytes and the unchanged reply format.
The receipt marks these expectations as derived.
It does not call them historical captures.

The rehearsal runs five scenarios on QA ports `18919` through `18999`.
The success scenario switches to the candidate, checks it, restores the previous default, and checks it again.
Four failure scenarios stop after the old service stops, the new service starts, the pointer changes, or the receipt commits.
Each failure must restore and check the previous default.
Each scenario must stop its own final process.
Process checks use the binary hash, command, start time, user, working directory, and port owner.

The workflow saves `m6-release-summary` and encrypted `m6-release-evidence` artifacts.
The summary must show all five scenarios passed.
An independent reviewer must read the raw journals and default-check results before release approval is requested.
The rehearsal result is pending.

## Production change

First prepare a concrete deployment package and rollback command.
Bind the exact candidate binary, assets, formatter tree, accepted receipts, release scripts, and current default receipt.
Resolve write ownership of the production checkout before changing it.
The current production checkout is `/Users/chrischeng/faberlines/som`.
The managed development worktree does not hold the production default receipt.

Present the tested candidate, current default, QA evidence, deployment paths, and rollback command to the user.
Request approval only when these exact items are ready.
Save the user's exact approval with the bound file hashes.
Use `switch.py --execute --approval FILE` only after that approval.
Then use `check_default.py` with the candidate hash.
Keep the previous binary, assets, and switch evidence for recovery.
Do not change the 127 public or 544 legacy product files.
Do not change the frozen React contract.

The exact production command and approval receipt are pending the hosted rehearsal and deployment package review.
The approval check requires the exact prerequisites, script, current default, and candidate hashes.
A QA approval file cannot stand in for a production approval file.

## Tag and GitHub release

Prepare the exact tag target and release notes after the default switch passes its checks.
Ask for tag approval before creating the tag.
Ask separately for GitHub release approval before publishing the release.
Attach the candidate hashes, source identity, QA evidence, production check, and rollback details.
Mark issue #131 done only after its required release evidence is complete and independently accepted.
Then check the remaining M6 umbrella issue.

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
Run all project tests on GitHub runners.
Local development is limited to Cargo lint and formatting checks.

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
The corrected rehearsal passed independent raw-evidence review.
See [run 40766965664](https://github.com/faberline/som/actions/runs/40766965664), the [rehearsal receipt](../som-rs/evidence/react-output-release-rehearsal-2026-10-29-issue131.json), and the [independent review](../som-rs/evidence/react-output-release-independent-review-2026-10-29-issue131.json).
Its tested controller source is `f76d8af5f49c7848b9fe5609d4975c679640b553`.
All 441 [tooling checks](https://github.com/faberline/som/actions/runs/40766965597) passed.
Each of the five scenarios first resumed a stopped previous compiler.
The reviewer matched 476 HTTP request/reply pairs and 238 CLI outputs.
All final processes stopped.
The protected-port checks only read listener metadata.
The first [hosted run 40764095844](https://github.com/faberline/som/actions/runs/40764095844) passed its five switch and rollback scenarios.
Its protected-port guard called a function that briefly bound `8765` and `18765` on the runner.
The summary's `protected_ports_unused` claim was incorrect.
That run is not final release acceptance.
The corrected run above replaces that evidence.

PR [#151](https://github.com/faberline/som/pull/151) later changed the switch
recovery path. It can attempt recovery when the candidate has exited, after
the recorded owner, port, receipt, and pointers pass the required checks.
Recovery can still fail and need an operator.
The updated helpers passed the [GitHub rehearsal](https://github.com/faberline/som/actions/runs/40863011173).
An [independent raw audit](https://github.com/faberline/som/actions/runs/40878250023)
checked that rehearsal's saved evidence. The rehearsal did not retain each
HTTP status, route, CLI exit code, or an independent wait record. The recorded
process and protected-port observations remain producer attestations.

## Production change

**Do not run the commands below in their current form.**
The production service may run on the designated host after explicit approval.
All tests must run on GitHub runners, including the HTTP and CLI canaries.
The current tools run those canaries locally during the switch and checks.
First provide a reviewed way to run the tests on GitHub without removing the required checks.
A production approval does not authorize local tests.
The commands below are a historical deployment draft, not an executable plan.

First prepare a concrete deployment package and rollback command.
Bind the exact candidate binary, assets, formatter tree, accepted receipts, release scripts, and current default receipt.
Resolve write ownership of the production checkout before changing it.
The current production checkout is `/Users/chrischeng/faberlines/som`.
The managed development worktree does not hold the production default receipt.
An earlier read-only inspection found no listener on port `8765` and no process with the receipt's recorded PID.
Check the current state again before any authorized production action.
If the previous default is stopped, a future plan must verify the current state
before it starts the same accepted previous compiler. It must save and check
the fresh process identity before the normal switch can proceed.
Starting that production service is part of the pending explicit approval.

Present the tested candidate, current default, QA evidence, deployment paths, and rollback command to the user.
Request approval only when these exact items are ready.
Save the user's exact approval with the bound file hashes.
Use `switch.py --execute --approval FILE` only after that approval.
Then use `check_default.py` with the candidate hash.
Keep the previous binary, assets, and switch evidence for recovery.
Do not change the 127 public or 544 legacy product files.
Do not change the frozen React contract.

The earlier deployment package passed independent data review before PR #151.
At that review, its 163 file records and 97 active file bindings matched their hashes.
The prepared production prerequisites hash is `2ca65ac03f0aec41abc9be3e7264005e3b4555e8c297919e3ab6928afe0cda5c`.
This differs from the hosted prerequisites because the file paths name the production checkout.
That data review confirmed that only those paths changed.
PR #151 changed `switch.py`, so the earlier package and approval bindings are stale.
Rebind and independently review the package against the tested helper bytes
before requesting any production approval.

The historical draft targets `/Users/chrischeng/faberlines/som/.local/rust-compiler-goal/acceptance/react-131-release-001`.
Any replacement plan must use the tested `release_common.py`, `switch.py`, `check_default.py`, and `resume_default.py` helper bytes.
It must check each old file hash before installation and keep byte and mode backups.
It must check each new hash before any service action.
It must keep the frozen `compiler_default_switch.py` unchanged.

The historical operator commands are listed below for review only.
The approval files do not exist until the user explicitly approves the start, switch, and recovery plan.
After a verified resume, bind the switch approval to the fresh receipt that the resume wrote.

```sh
SOM_RELEASE_ROOT=/Users/chrischeng/faberlines/som
SOM_RELEASE_DATA="$SOM_RELEASE_ROOT/.local/rust-compiler-goal/acceptance/react-131-release-001"
SOM_RELEASE_TOOLS="$SOM_RELEASE_ROOT/som-rs/scripts/rust_output_release"
SOM_RELEASE_PYTHON=/opt/homebrew/opt/python@3.12/bin/python3.12

"$SOM_RELEASE_PYTHON" "$SOM_RELEASE_TOOLS/resume_default.py" --prerequisites "$SOM_RELEASE_DATA/prerequisites.json" --root "$SOM_RELEASE_ROOT" --port 8765 --execute --output "$SOM_RELEASE_DATA/resume-001" --approval "$SOM_RELEASE_DATA/resume-approval.json"
"$SOM_RELEASE_PYTHON" "$SOM_RELEASE_TOOLS/switch.py" --prerequisites "$SOM_RELEASE_DATA/prerequisites.json" --root "$SOM_RELEASE_ROOT" --port 8765 --execute --output "$SOM_RELEASE_DATA/switch-001" --approval "$SOM_RELEASE_DATA/switch-approval.json"
"$SOM_RELEASE_PYTHON" "$SOM_RELEASE_TOOLS/check_default.py" --prerequisites "$SOM_RELEASE_DATA/prerequisites.json" --root "$SOM_RELEASE_ROOT" --port 8765 --expect-binary-sha256 59dab78a0f401a86e028c186285a888cfdf304b2d077e167d46b20d1131a3308 --output "$SOM_RELEASE_DATA/check-default-001"
```

The recovery command uses the committed switch evidence.
Bind its approval to the actual current receipt before recovery.

```sh
"$SOM_RELEASE_PYTHON" "$SOM_RELEASE_TOOLS/switch.py" --prerequisites "$SOM_RELEASE_DATA/prerequisites.json" --root "$SOM_RELEASE_ROOT" --port 8765 --restore "$SOM_RELEASE_DATA/switch-001" --output "$SOM_RELEASE_DATA/restore-001" --approval "$SOM_RELEASE_DATA/restore-approval.json"
```

Primary ownership and explicit service approval remain pending.
The approval check requires the exact prerequisites, script, current default, and candidate hashes.
A QA approval file cannot stand in for a production approval file.

## Tag and GitHub release

Prepare the exact tag target and release notes after the default switch passes its checks.
Ask for tag approval before creating the tag.
Ask separately for GitHub release approval before publishing the release.
Attach the candidate hashes, source identity, QA evidence, production check, and rollback details.
Mark issue #131 done only after its required release evidence is complete and independently accepted.
Then check the remaining M6 umbrella issue.

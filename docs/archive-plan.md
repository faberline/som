# Plan: move the old areas to an archive repository

Status: plan only (som#262). No file moves in this issue. The owner decided on 2026-11-17 (faberline/aw#539): the main repository keeps the Rust main line (`som-service` and what it needs). `som-rs/` (46.7 MB, frozen), `som-core/` (49.6 MB) and `som-code-python/` (9.3 MB) move with their history to one new private repository. This plan calls it `faberline/som-archive`; the owner may choose another name before step 1 of section 5.

## 1. What still reads each area

Paths below were read on `main`. A line of the form "inside" means a file in another old area.

### `som-rs/`

- `.github/workflows/ci.yml`: job `test-som-rs` (runs `som-rs/Cargo.toml`, `som-rs/som-targets/Cargo.toml`, `som-rs/scripts/hosted_readiness/bound_source.py`, `som-rs/scripts/hosted_readiness/provision_native.py`, `som-rs/contracts/native-tools-v1/artifacts.json`), job `test-scripts` (runs `som-rs/scripts/react_quickstart`, `som-rs/scripts/react_output_acceptance`, `som-rs/scripts/rust_output_release`) and `gate.needs`. If som#249 has merged, both jobs are the one job `test-hosted`.
- `.github/actions/same-candidate-inputs`: `action.yml` calls `som-rs/scripts/hosted_readiness/native_source_bootstrap.py` and `bound_source.py`.
- `.github/actions/native-active-source-fixtures`: five fixture files whose text names `som-rs/` paths.
- `.github/ci/native-http-whole-v1.json` and `.github/ci/native-integrated-controls-v1.json`: lists of `som-rs/...` paths.
- `.github/m6-ts-role-transport`: ten files. Their SHA-256 values are pinned inside `som-rs/contracts/compiler-v1/native-tools.json`, `som-rs/contracts/rust-output-v1/native-tools-compatibility.json` and `som-rs/contracts/typescript-output-v1/native-tools-compatibility.json`. `frozen-wire-profile-v1.json` in it names `som-rs/` paths and is a size violation (RH5) in the hygiene baseline.
- `som-service/tests/it/train_trim.rs`: reads `../som-rs/evidence/rust-training-data-trim-amendment-2026-11-15.json` and `../som-rs/evidence/rust-training-data-archive-cleanup-2026-10-14.json`.
- `som-service/scripts/replay.py`: runs `som-rs/scripts/cnl_parity/accept.py` and the legacy `som-rs` assets.
- `som-service/tests/it/readme_links.rs` and `som-service/tests/it/readme_mainline.rs`: assert that `README.md` contains `som-rs/README.md`, `frozen` and `som-rs`.
- Text only (no run): `som-service/cnl/src/som_cnl.pest` (line 1 comment), `som-service/scripts/nlp-oracle/Cargo.toml` (line 7 comment), `som-service/CONTRACT.md`, `som-service/THREAT_MODEL.md`, `som-service/tests/it/entrypoints.rs` (comment).
- Docs: `README.md`, `CLAUDE.md` (section "Frozen paths"), `aw.toml` (reason of the CR5 exception), `docs/legacy-data-inventory.md`, `docs/m6-release.md`, `docs/python-be.md`, `docs/react-fe.md`, `docs/react-output-v1-draft.md`, `docs/roadmap.md`, `docs/rust-be.md`, `docs/typescript-be.md`, `docs/typescript-output-v1-draft.md`.
- Seals and hashes inside `som-rs/`: `contracts/`, `scripts/hosted_readiness/inputs/`, `scripts/compiler_acceptance/` and `evidence/` pin file hashes and `som-rs/...` paths (about 140 entries are in the hygiene baseline). They stay valid only if the archive keeps the same relative paths and the same bytes (section 3).

### `som-core/`

- `som-service/tests/it/train_trim.rs`: reads `../som-core/data/flow-m3-r9/train.jsonl` (13301 bytes, SHA-256 `6200f4164804eca205f72e99be95df711940fe0d6e74e60037754d5a7891f645`).
- `.gitignore`: line 48 comment about `som-core`.
- `docs/legacy-data-inventory.md`: the table of `som-core/data/*`.
- Inside `som-rs/`: `src/flow_codec.rs` (lines 328, 359), `src/flow_compact.rs` (lines 9, 11, templates in `som-core/block_templates`), `src/flow_model.rs` (lines 105, 212), `tests/model_selection.rs` (line 13), and `scripts/hosted_readiness/inputs/public-golden-hashes-v1.json` (names `som-core/data/composed`).
- Inside `som-core/`: `data/flow-v7-r*/acceptance-seal*/seal.json` name `som-rs/` and `som-code-python/` paths.

### `som-code-python/`

- `som-service/scripts/replay.py`: line 40 checks that the symlink `som-code-python/data/blocks/current` points to `flow-v7-compact`.
- `docs/legacy-data-inventory.md`: the table of `som-code-python/data/*`.
- Inside `som-rs/`: `src/compiler.rs` (lines 13, 14, 16), `src/composed.rs` (line 10), `src/flow.rs` (lines 12, 14), `src/quality.rs` (line 15), `src/bin/som-model-legacy.rs` (line 36), `tests/cnl_repo_examples.rs` (lines 22 to 30). These are `include_bytes!` and `include_str!` of `som-code-python/data/blocks` and `data/block-contracts`.
- Inside `som-core/`: many `scripts/build_*.py`, `scripts/check_*.py` and `scripts/eval_*.py` read `som-code-python/`.

`som-service/` has no Cargo dependency on any of the three areas. `som-service/Cargo.toml` takes `faberline-nlp-cnl` from `som-service/cnl`. The nlp pin of `som-rs/Cargo.toml` (`rev 8ddb1f6df55eec0764ca50772b2c038441361727`) moves with `som-rs/`.

## 2. What must happen first

`som-rs/` is frozen (CLAUDE.md). Its sealed evidence changes only with an amendment that the owner approves. Nobody edits `som-rs/`, runs `cargo fmt` there or re-pins anything before the owner writes the approval below on som#262.

### Amendment text for the owner

> Amendment: archive move of the old som areas (som#262).
>
> 1. `som-rs/`, `som-core/`, `som-code-python/` and the helper paths `.github/actions/native-active-source-fixtures/`, `.github/actions/same-candidate-inputs/`, `.github/ci/native-http-whole-v1.json`, `.github/ci/native-integrated-controls-v1.json`, `.github/m6-ts-role-transport/` move to the private repository `faberline/som-archive`, with history. The archive has the same relative paths and the same bytes. The move is a copy followed by a delete. No sealed file, hash, digest, pin or evidence file is edited, regenerated or re-pinned, so every pin stays valid in the archive.
> 2. The nlp pin of `som-rs/Cargo.toml` stays as it is and moves with the file. The re-pin approval of som#180 is not needed. som#180 closes as not planned with the reason "moved to the archive".
> 3. These checks leave `main` with their subject and are not run on `main` again: the CI jobs `test-som-rs` and `test-scripts`; the test `som-service/tests/it/train_trim.rs` (its subject, the trimmed `som-core/data/flow-m3-r9/train.jsonl` and the two evidence files, is in the archive, and `ARCHIVE.md` of the archive records size 13301 and SHA-256 `6200f4164804eca205f72e99be95df711940fe0d6e74e60037754d5a7891f645`); and the script `som-service/scripts/replay.py`. This amendment is the approval to delete them; no other test is deleted.
> 4. `som-service/tests/it/readme_links.rs` and `som-service/tests/it/readme_mainline.rs` change only their expected strings, from `som-rs/README.md` and `frozen` to the archive pointer text of `README.md`. No other assertion changes.
> 5. The section "Frozen paths" of `CLAUDE.md` and the CR5 exception text in `aw.toml` change to say that the old areas are in the archive.
> 6. After the push and the check in section 3 pass, the archive is set read-only (archived). Nothing in it is edited later.
>
> Approved by the owner: write the sentence "核准 #262 封存 amendment" in a comment on som#262.

The children in section 4 start only after that comment exists.

## 3. Move method (keeps history)

The archive keeps the old paths. Pins that name `som-rs/...` therefore still resolve there. `main` keeps its own history (no rewrite, no force-push); the move adds a delete on `main` only.

```bash
git clone --no-local https://github.com/faberline/som.git som-orig
SHA="$(git -C som-orig rev-parse HEAD)"
git clone --no-local som-orig som-split
cd som-split
git filter-repo \
  --path som-rs/ --path som-core/ --path som-code-python/ \
  --path .github/actions/native-active-source-fixtures/ \
  --path .github/actions/same-candidate-inputs/ \
  --path .github/ci/native-http-whole-v1.json \
  --path .github/ci/native-integrated-controls-v1.json \
  --path .github/m6-ts-role-transport/ \
  --path som-service/scripts/replay.py \
  --path som-service/tests/it/train_trim.rs
```

Then add one commit with the root file `ARCHIVE.md` (source repository, source commit `$SHA`, the list of paths above, the train.jsonl size and SHA-256, "read only"). Push: `git remote add origin https://github.com/faberline/som-archive.git`, `git push origin --all`, `git push origin --tags`.

Check (must print nothing and exit 0). It proves that each moved path has the same tree or blob id, so the bytes are identical:

```bash
for p in som-rs som-core som-code-python \
  .github/actions/native-active-source-fixtures .github/actions/same-candidate-inputs \
  .github/ci/native-http-whole-v1.json .github/ci/native-integrated-controls-v1.json \
  .github/m6-ts-role-transport som-service/scripts/replay.py som-service/tests/it/train_trim.rs; do
  test "$(git -C som-orig rev-parse "$SHA:$p")" = "$(git -C som-split rev-parse "HEAD:$p")" || echo "DIFF $p"
done
```

How `main` points to the archive: child C5 adds a section "Archive" to `README.md` with the URL, the source commit `$SHA` and the sentence that the old areas are read only there, and it adds `docs/archive.md` with the same facts and the check above.

## 4. Child issues, in order (one pull request each)

Each child has `Waits for:` lines for the children before it. None starts before the owner comment of section 2 and the desk steps 1 to 3 of section 5.

1. **C1: som-service stops reading the old areas.** Delete `som-service/tests/it/train_trim.rs` and `som-service/scripts/replay.py` (amendment point 3). Change the comment lines in `som-service/cnl/src/som_cnl.pest` line 1, `som-service/scripts/nlp-oracle/Cargo.toml` line 7 and the text in `som-service/CONTRACT.md` and `som-service/THREAT_MODEL.md` so that they point to the archive. Acceptance: `git grep -n -E "som-rs|som-core|som-code-python" -- som-service ':!som-service/Cargo.lock'` prints only lines that contain `som-archive`, plus the two README test files that C5 changes; the three commands `cargo fmt --all -- --check`, `cargo clippy --workspace --all-targets --locked -- -D warnings`, `cargo test --locked` pass in `som-service/`.
2. **C2: CI and sealed helpers.** `Waits for:` C1 and som#249. Remove the jobs `test-som-rs` and `test-scripts` (or `test-hosted` after som#249) from `.github/workflows/ci.yml`, remove them from `gate.needs`, and delete `.github/actions/native-active-source-fixtures/`, `.github/actions/same-candidate-inputs/`, `.github/ci/native-http-whole-v1.json`, `.github/ci/native-integrated-controls-v1.json` and `.github/m6-ts-role-transport/`. The required check `gate` keeps its name and its checks. Acceptance: `git grep -n -E "som-rs|m6-ts-role-transport" -- .github` prints nothing; `aw ci check --dir . --no-ruleset` has no `fail`; the CI run of the pull request is green.
3. **C3: delete `som-rs/`.** `Waits for:` C2. Delete the folder (a deletion pull request is not limited to six files). Close som#180 as not planned. Acceptance: `test ! -e som-rs`; `git grep -n "som-rs" -- som-service ':!som-service/Cargo.lock'` prints only archive pointers; the check of section 3 was run before the merge and its output is in the pull request body; the CI run is green.
4. **C4: delete `som-core/` and `som-code-python/`.** `Waits for:` C3. Delete both folders. Remove the line 48 comment of `.gitignore` about `som-core`. Acceptance: `test ! -e som-core && test ! -e som-code-python`; the CI run is green.
5. **C5: docs, guard files and the hygiene baseline.** `Waits for:` C4. Update `README.md` (section "Archive" and the sentences about `som-rs`), `CLAUDE.md` (section "Frozen paths" and the nlp-pin text, amendment point 5), `aw.toml` (CR5 exception reason), `docs/roadmap.md`, `docs/rust-be.md`, `docs/python-be.md`, `docs/typescript-be.md`, `docs/react-fe.md`, `docs/m6-release.md`, `docs/react-output-v1-draft.md`, `docs/typescript-output-v1-draft.md`; delete `docs/legacy-data-inventory.md`; add `docs/archive.md`; change the expected strings in `som-service/tests/it/readme_links.rs` and `som-service/tests/it/readme_mainline.rs` (amendment point 4). Empty the hygiene baseline: it has 146 entries on 0b43953. 144 name the three areas or `.github/m6-ts-role-transport`; they go away. The other two are `RH1:.claude` and `RH1:som-service`: give each a `[[hygiene.root]]` entry with a reason and remove it from the baseline. If `aw.toml` has no `[hygiene]` table yet, add one with an empty `baseline = []` and the root entries. Acceptance: `aw repo check` exits 0 with `baseline = []`; `git grep -n -E "som-rs|som-core|som-code-python"` prints only archive pointers and the text of `docs/archive.md`; the three cargo commands pass in `som-service/`.

## 5. What the desk does by hand

1. Create the private repository `faberline/som-archive` (empty, default branch `main`, no template, no README).
2. Settings of the new repository: turn GitHub Actions off; no branch protection; same team access as `faberline/som`; do not give it the topic `aw`; no secrets.
3. A person with `git-filter-repo` runs section 3 (clone, filter, add `ARCHIVE.md`, push) and posts the output of the check on som#262. aw agents cannot create repositories and do not run this step.
4. After C3 and C4 are merged and the check still passes against the archive, set the archive to "Archive this repository" (read only).
5. Create the children C1 to C5 from section 4 with the labels `agent:claude`, `stage:spec` and the milestone of som#262; write the `Waits for: #<n>` lines with the real numbers.

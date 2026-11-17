# Archive

## Where

The old areas `som-rs/`, `som-core/` and `som-code-python/` are in the private repository [faberline/som-archive](https://github.com/faberline/som-archive). The helper paths `.github/actions/native-active-source-fixtures/`, `.github/actions/same-candidate-inputs/`, `.github/ci/native-http-whole-v1.json`, `.github/ci/native-integrated-controls-v1.json` and `.github/m6-ts-role-transport/` are there too.

- Source commit: `47e8b3f573882c4726fdb26bca23d2bf138f7820`.
- Archive head: `d6867bc5facbcc9647f5392afe8cff3e92897ed0`.
- The archive is read only. Each path has the same relative path and the same bytes as in the source commit.
- The plan of the move is `docs/archive-plan.md`.
- `som-rs/` is never restored in this repository and never edited.

## Check

Run this check with `SHA` set to the source commit, a clone of this repository at that commit as `som-orig`, and a clone of the archive as `som-split`. It must print nothing.

```bash
for p in som-rs som-core som-code-python \
  .github/actions/native-active-source-fixtures .github/actions/same-candidate-inputs \
  .github/ci/native-http-whole-v1.json .github/ci/native-integrated-controls-v1.json \
  .github/m6-ts-role-transport som-service/scripts/replay.py som-service/tests/it/train_trim.rs; do
  test "$(git -C som-orig rev-parse "$SHA:$p")" = "$(git -C som-split rev-parse "HEAD:$p")" || echo "DIFF $p"
done
```

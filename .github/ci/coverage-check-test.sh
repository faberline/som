#!/usr/bin/env bash
# Tests the coverage gate of coverage-check.sh (COV3 and COV4), with its own
# fake summary and temporary aw.toml files. No network.
set -euo pipefail

here="$(cd "$(dirname "$0")" && pwd)"
script="$here/coverage-check.sh"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
failed=0

# A measured value of 96.0 (960 of 1000 lines).
cat > "$tmp/summary.json" <<'EOF'
{"data":[{"files":[{"filename":"/w/a.rs","summary":{"lines":{"count":1000,"covered":960,"percent":96}}}]}]}
EOF

# expect <name> <line> <exit code> <check>=<status> ...
expect() {
  local name=$1 line=$2 want_exit=$3
  shift 3
  local toml="$tmp/$name.toml" out code=0 pair check want got
  printf '[standards.coverage]\nline = %s\ntarget = 100\n' "$line" > "$toml"
  out="$(COVERAGE_ROOT=/w bash "$script" "$tmp/summary.json" "$toml")" || code=$?
  local ok=1
  if [ "$code" -ne "$want_exit" ]; then
    ok=0
  fi
  for pair in "$@"; do
    check=${pair%%=*}
    want=${pair#*=}
    got="$(jq -r --arg c "$check" '.findings[] | select(.check == $c) | .status' <<<"$out" || true)"
    if [ "$got" != "$want" ]; then
      ok=0
      echo "  $check: got '$got', want '$want'"
    fi
  done
  if [ "$ok" -eq 1 ]; then
    echo "ok $name"
  else
    echo "FAIL $name (exit $code, want $want_exit)"
    failed=1
  fi
}

# Measured 96.0 is above line 94.25 by more than the slack: COV4 warns, the gate passes.
expect above-line-warns 94.25 0 COV4=warn COV3=pass
# Measured 96.0 is within the slack of line 95.8: COV4 passes.
expect within-slack-passes 95.8 0 COV4=pass
# Measured 96.0 is below line 97 minus the tolerance: COV3 fails, the gate fails.
expect below-line-fails 97 1 COV3=fail

exit "$failed"

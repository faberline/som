#!/usr/bin/env python3
"""Build the frozen Rust output phase 2 case inventory (issue #52).

usage:
  build_phase2_inventory.py           write contracts/rust-output-v1/phase2/case-inventory.json
  build_phase2_inventory.py --check   fail unless the committed inventory is exactly this output

A phase 2 case is a case the phase 1 Rust profile refuses. The cases are the ones the phase 2
behavior suite runs (som-rs/behavior/phase2/check_phase2.py cases()):
- the 444 legacy v6/v7 jobs of legacy544 run-005 (legacy100 stays in phase 1);
- the 115 out-of-scope CNL fixtures and the 3 out-of-scope public examples, as assembled in
  public-products-003b;
- RS01 to RS22 of negative-cases.json, as assembled in rs-products-001.

The inventory names every case by id, group, position and hash. It never copies a legacy prompt or
gold answer. The products live in the local .local acceptance tree, so this script and --check
need that tree; each prompt and product hash is checked against its run record as it is read.
"""
from __future__ import annotations

import argparse
import collections
import hashlib
import json
import pathlib
import sys
from typing import Any

HERE = pathlib.Path(__file__).resolve().parent
SOM_RS = HERE.parent.parent
REPO = SOM_RS.parent
OUT = SOM_RS / "contracts" / "rust-output-v1" / "phase2" / "case-inventory.json"
NEGATIVE = SOM_RS / "contracts" / "rust-output-v1" / "negative-cases.json"
FIXTURES = SOM_RS / "tests" / "compiler-v1" / "cnl-positive-fixtures.json"
PHASE1 = SOM_RS / "contracts" / "rust-output-v1" / "case-inventory.json"
SCOPE = SOM_RS / "contracts" / "rust-output-v1" / "scope.json"
SUITE = SOM_RS / "behavior" / "phase2" / "check_phase2.py"
PROFILE2 = HERE / "rust_profile_phase2.py"
EXPECTED = {"legacy": 444, "cnl_fixture": 115, "public_example": 3, "rs": 22}

sys.path.insert(0, str(HERE))
sys.path.insert(0, str(SUITE.parent))
import rust_profile  # type: ignore[import-not-found]  # noqa: E402
import rust_profile_phase2  # type: ignore[import-not-found]  # noqa: E402
import check_phase2  # type: ignore[import-not-found]  # noqa: E402


def sha(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def rel(path: pathlib.Path) -> str:
    return str(path.relative_to(REPO))


def build() -> dict[str, Any]:
    scope = rust_profile.load_scope()
    rows = []
    for case in check_phase2.cases():
        prompt, _files = check_phase2.load(case)
        refusal = rust_profile.check(prompt, scope)
        if refusal is None:
            raise SystemExit(f"{case['id']}: the phase 1 profile accepts it, so it is not a phase 2 case")
        kept = rust_profile_phase2.check(prompt, scope)
        kind = ("legacy" if case["family"] not in ("public", "rs")
                else "rs" if case["family"] == "rs"
                else "public_example" if case["group"] == "public_example" else "cnl_fixture")
        rows.append({"id": case["id"], "kind": kind, "family": case["family"], "group": case["group"],
                     "source": case["source"], "position": case["position"],
                     "prompt_version": case["prompt_version"], "prompt_sha256": case["prompt_sha256"],
                     "product_sha256": case["product_sha256"],
                     "phase1_refusal": refusal["rule"],
                     "phase2_expected": {"result": "refusal", "rule": kept["rule"]} if kept else {"result": "product"},
                     "checker": case["checker"],
                     "standard": case["standard"]})
    counts = collections.Counter(row["kind"] for row in rows)
    if dict(counts) != EXPECTED:
        raise SystemExit(f"phase 2 case counts {dict(counts)}, expected {EXPECTED}")
    rs_ids = sorted(row["id"] for row in rows if row["kind"] == "rs")
    negative = sorted(c["id"] for c in json.loads(NEGATIVE.read_text())["cases"] if c["id"].startswith("RS"))
    if rs_ids != negative:
        raise SystemExit("the rs cases differ from RS01 to RS22 of negative-cases.json")

    def tally(field: str) -> dict[str, int]:
        return dict(sorted(collections.Counter(str(row[field]) for row in rows).items()))

    by_prompt: dict[str, list[str]] = collections.defaultdict(list)
    for row in rows:
        by_prompt[row["prompt_sha256"]].append(row["id"])
    shared = sorted(ids for ids in by_prompt.values() if len(ids) > 1)

    return {
        "version": "som.rust-output-phase2-case-inventory.v1",
        "issue": 52,
        "selection_rules": [
            "A phase 2 case is a case the phase 1 Rust profile (scope.json, rust_profile.py) refuses.",
            "legacy: every legacy544 run-005 job outside legacy100; legacy100 is phase 1.",
            "cnl_fixture: every CNL positive fixture the phase 1 profile refuses, as assembled in public-products-003b.",
            "public_example: every public example the phase 1 profile refuses (v6, v7, v7-branch-null), as assembled in public-products-003b.",
            "rs: RS01 to RS22 of negative-cases.json, as assembled in rs-products-001; phase 2 removes their refusals.",
            "phase2_expected is the phase 2 profile (rust_profile_phase2.py) result: a Rust product, or the kept query_parameter_collision refusal.",
            "Cases are counted per source. A prompt can be a case in more than one source (a legacy job and a public example, the CNL and JSON forms of one RS prompt, or repeated legacy jobs); counts.distinct_prompts and shared_prompts record it.",
            "Legacy jobs are named by id, position and hash only; no legacy prompt or gold answer is copied.",
        ],
        "sources": {rel(path): sha(path) for path in (FIXTURES, NEGATIVE, PHASE1, SCOPE, SUITE, PROFILE2)},
        "counts": {"total": len(rows), "distinct_prompts": len(by_prompt), "by_kind": tally("kind"), "by_group": tally("group"),
                   "by_prompt_version": tally("prompt_version"), "by_phase1_refusal": tally("phase1_refusal"),
                   "by_checker": tally("checker"),
                   "by_phase2_expected": dict(sorted(collections.Counter(
                       row["phase2_expected"].get("rule", "product") for row in rows).items()))},
        "shared_prompts": shared,
        "rows": rows,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    text = json.dumps(build(), indent=2, sort_keys=True) + "\n"
    if args.check:
        if not OUT.exists() or OUT.read_text() != text:
            print(f"{rel(OUT)} differs from build_phase2_inventory.py output", file=sys.stderr)
            return 1
        print(f"{rel(OUT)} is current")
        return 0
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(text)
    print(f"wrote {rel(OUT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

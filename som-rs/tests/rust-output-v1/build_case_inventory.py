#!/usr/bin/env python3
"""Build the frozen Rust output phase 1 case inventory.

The inventory names every acceptance case by id and hash. It never copies a
legacy prompt or gold answer. The legacy inputs live in the local .local
acceptance tree, so this script and --check need that tree.
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
OUT = SOM_RS / "contracts" / "rust-output-v1" / "case-inventory.json"
EXAMPLES = SOM_RS / "contracts" / "compiler-v1" / "examples"
FIXTURES = SOM_RS / "tests" / "compiler-v1" / "cnl-positive-fixtures.json"
COVERAGE = HERE / "cases" / "index.json"
LEGACY = REPO / ".local" / "rust-compiler-goal" / "acceptance" / "legacy544"
INVENTORY = LEGACY / "inventory-003.json"
RUN = LEGACY / "run-005"

sys.path.insert(0, str(HERE))
import rust_profile  # type: ignore[import-not-found]  # noqa: E402


def sha(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def rel(path: pathlib.Path) -> str:
    return str(path.relative_to(REPO))


def classify(prompt: dict[str, Any], scope: dict[str, Any]) -> str:
    found = rust_profile.check(prompt, scope)
    return "in_scope" if found is None else found["rule"]


def build() -> dict[str, Any]:
    scope = rust_profile.load_scope()
    public = []
    for name in ("v1", "v2", "v3", "v4", "v5", "v6", "v7", "v7-branch-null"):
        prompt_path = EXAMPLES / f"{name}.prompt.json"
        cnl_path = EXAMPLES / f"{name}.cnl"
        prompt = json.loads(prompt_path.read_text())
        public.append({"id": f"example-{name}", "prompt_version": prompt["version"], "rust": classify(prompt, scope),
                       "prompt": rel(prompt_path), "prompt_file_sha256": sha(prompt_path),
                       "cnl": rel(cnl_path) if cnl_path.exists() else None,
                       "cnl_sha256": sha(cnl_path) if cnl_path.exists() else None})

    fixtures = json.loads(FIXTURES.read_text())
    fixture_rows = []
    fixture_counts: collections.Counter[tuple[str, str]] = collections.Counter()
    for case in fixtures["cases"]:
        rule = classify(case["expected_prompt"], scope)
        fixture_counts[(case["category"], rule)] += 1
        if case["category"] == "version_positive":
            fixture_rows.append({"id": case["id"], "prompt_version": case["expected_prompt"]["version"], "rust": rule,
                                 "prompt_sha256": case["prompt_sha256"], "cnl_sha256": case["cnl_sha256"]})

    coverage = json.loads(COVERAGE.read_text())
    coverage_rows = []
    for case in coverage["cases"]:
        prompt = json.loads((COVERAGE.parent / case["prompt"]).read_text())
        coverage_rows.append({"id": case["id"], "prompt_version": prompt["version"], "rust": classify(prompt, scope),
                              "prompt_file_sha256": case["prompt_file_sha256"], "cnl_sha256": case["cnl_sha256"]})

    inventory = json.loads(INVENTORY.read_text())
    legacy_rows = []
    legacy_counts: collections.Counter[tuple[str, str, str]] = collections.Counter()
    for index, group in enumerate(inventory["groups"]):
        request_path = RUN / f"{index:04d}" / "assemble.request.json"
        prompt = json.loads(request_path.read_text())["bundle"]["prompt"]
        rule = classify(prompt, scope)
        legacy_counts[(group["group"], prompt["version"], rule)] += 1
        if rule == "in_scope":
            legacy_rows.append({"id": group["id"], "group": group["group"], "inventory_index": index,
                                "prompt_version": prompt["version"], "prompt_sha256": group["prompt_sha256"]})

    acceptance = ([r for r in public if r["rust"] == "in_scope"] + [r for r in fixture_rows if r["rust"] == "in_scope"]
                  + coverage_rows + legacy_rows)
    return {
        "version": "som.rust-output-case-inventory.v1",
        "selection_rules": [
            "A case is in the Rust acceptance set when its typed prompt passes existing compiler-v1 validation and the frozen Rust profile in scope.json admits it.",
            "The acceptance set is every in-scope public example, every in-scope version_positive CNL fixture, every coverage case and every in-scope legacy job. Nothing is sampled.",
            "Each acceptance case compiles from CNL where a CNL source exists and from typed JSON, and the two Rust bundles must be byte-identical.",
            "Each acceptance case must assemble, build, pass its generated tests and pass clippy with the frozen toolchain, and pass the behavior parity suite against the Python product for the same prompt.",
            "Every out-of-scope case must give exactly the frozen UNSUPPORTED_TARGET diagnostic with target rust, and an unchanged Python bundle without target.",
            "Legacy jobs are named by id, inventory position and prompt hash only. Their prompts and gold answers are not copied into this repository.",
        ],
        "sources": {
            "examples": {rel(p): sha(p) for p in sorted(EXAMPLES.glob("*")) if p.suffix in (".json", ".cnl")},
            "cnl_positive_fixtures": {rel(FIXTURES): sha(FIXTURES)},
            "coverage_index": {rel(COVERAGE): sha(COVERAGE)},
            "legacy_inventory": {rel(INVENTORY): sha(INVENTORY), "run": rel(RUN),
                                 "prompt_location": "run/<inventory_index as 4 digits>/assemble.request.json at /bundle/prompt"},
        },
        "counts": {
            "acceptance_total": len(acceptance),
            "public_examples_in_scope": sum(r["rust"] == "in_scope" for r in public),
            "version_positive_in_scope": sum(r["rust"] == "in_scope" for r in fixture_rows),
            "coverage_cases": len(coverage_rows),
            "legacy_in_scope": len(legacy_rows),
            "cnl_positive_fixtures": [{"category": c, "rust": r, "count": n} for (c, r), n in sorted(fixture_counts.items())],
            "legacy": [{"group": g, "prompt_version": v, "rust": r, "count": n}
                       for (g, v, r), n in sorted(legacy_counts.items())],
        },
        "public_examples": public,
        "version_positive_fixtures": fixture_rows,
        "coverage_cases": coverage_rows,
        "legacy_in_scope": legacy_rows,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    text = json.dumps(build(), indent=2, ensure_ascii=False) + "\n"
    if args.check:
        if not OUT.exists() or OUT.read_text() != text:
            print("stale: " + rel(OUT))
            return 1
        print("checked case-inventory.json")
        return 0
    OUT.write_text(text)
    print("wrote case-inventory.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())

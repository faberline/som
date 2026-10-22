#!/usr/bin/env python3
"""Build the frozen React output phase 1 case inventory.

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

from common import CONTRACT, COVERAGE_CASES, EXAMPLES, REPO, SOM_RS, react_profile

OUT = CONTRACT / "case-inventory.json"
FIXTURES = SOM_RS / "tests" / "compiler-v1" / "cnl-positive-fixtures.json"
COVERAGE = COVERAGE_CASES / "index.json"
LEGACY = REPO / ".local" / "rust-compiler-goal" / "acceptance" / "legacy544"
INVENTORY = LEGACY / "inventory-003.json"
RUN = LEGACY / "run-005"
PUBLIC_PRODUCTS = REPO / ".local" / "rust-compiler-goal" / "acceptance" / "public-products-003b"


def sha(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def rel(path: pathlib.Path) -> str:
    return str(path.relative_to(REPO))


def classify(prompt: dict[str, Any], scope: dict[str, Any]) -> str:
    found = react_profile.check(prompt, scope)
    return "in_scope" if found is None else found["rule"]


def build() -> dict[str, Any]:
    scope = react_profile.load_scope()
    public = []
    for name in ("v1", "v2", "v3", "v4", "v5", "v6", "v7", "v7-branch-null"):
        prompt_path = EXAMPLES / f"{name}.prompt.json"
        cnl_path = EXAMPLES / f"{name}.cnl"
        prompt = json.loads(prompt_path.read_text())
        public.append({"id": f"example-{name}", "prompt_version": prompt["version"], "react": classify(prompt, scope),
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
            fixture_rows.append({"id": case["id"], "prompt_version": case["expected_prompt"]["version"], "react": rule,
                                 "prompt_sha256": case["prompt_sha256"], "cnl_sha256": case["cnl_sha256"]})

    coverage = json.loads(COVERAGE.read_text())
    coverage_rows = []
    for case in coverage["cases"]:
        prompt = json.loads((COVERAGE.parent / case["prompt"]).read_text())
        coverage_rows.append({"id": case["id"], "prompt_version": prompt["version"], "react": classify(prompt, scope),
                              "prompt_file_sha256": case["prompt_file_sha256"], "cnl_sha256": case["cnl_sha256"]})

    product_rows = []
    product_counts: collections.Counter[tuple[str, str]] = collections.Counter()
    for prompt_path in sorted(PUBLIC_PRODUCTS.glob("[0-9][0-9][0-9]/prompt.json")):
        prompt = json.loads(prompt_path.read_text())
        rule = classify(prompt, scope)
        product_counts[(prompt["version"], rule)] += 1
        if rule == "in_scope":
            product_rows.append({"id": f"public-product-{prompt_path.parent.name}", "index": prompt_path.parent.name,
                                 "prompt_version": prompt["version"], "prompt_file_sha256": sha(prompt_path)})

    examples_file = CONTRACT / "target-examples.json"
    name_rows = [{"id": case["id"], "form": case["form"], "base_example": case["base_example"],
                  "body_sha256": hashlib.sha256(case["body"].encode()).hexdigest()}
                 for case in json.loads(examples_file.read_text())["name_cases"]]

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

    acceptance = ([r for r in public if r["react"] == "in_scope"] + [r for r in fixture_rows if r["react"] == "in_scope"]
                  + [r for r in coverage_rows if r["react"] == "in_scope"] + name_rows + product_rows + legacy_rows)
    return {
        "version": "som.react-output-case-inventory.v1",
        "selection_rules": [
            "A case is in the React acceptance set when its typed prompt passes existing compiler-v1 validation and the frozen React profile in scope.json admits it.",
            "The acceptance set is every in-scope public example, every in-scope version_positive CNL fixture, every in-scope coverage case, every name case in target-examples.json, every in-scope public product and every in-scope legacy job. Nothing is sampled.",
            "Each acceptance case compiles from CNL where a CNL source exists and from typed JSON, and the two React bundles must be byte-identical.",
            "Each acceptance case must assemble and pass the toolchain.json commands (install, typecheck, format_check, test and two identical builds) with the frozen toolchain, and pass the api-contract.json suite against the Python product of the same prompt.",
            "Every out-of-scope case must give exactly the frozen UNSUPPORTED_TARGET diagnostic with target react, and an unchanged Python bundle without target.",
            "Legacy jobs are named by id, inventory position and prompt hash only. Their prompts and gold answers are not copied into this repository.",
        ],
        "sources": {
            "examples": {rel(p): sha(p) for p in sorted(EXAMPLES.glob("*")) if p.suffix in (".json", ".cnl")},
            "cnl_positive_fixtures": {rel(FIXTURES): sha(FIXTURES)},
            "coverage_index": {rel(COVERAGE): sha(COVERAGE)},
            "name_cases": "target-examples.json name_cases; each row hashes the exact request body",
            "public_products": {"directory": rel(PUBLIC_PRODUCTS),
                                "prompt_location": "<index as 3 digits>/prompt.json"},
            "legacy_inventory": {rel(INVENTORY): sha(INVENTORY), "run": rel(RUN),
                                 "prompt_location": "run/<inventory_index as 4 digits>/assemble.request.json at /bundle/prompt"},
        },
        "counts": {
            "acceptance_total": len(acceptance),
            "public_examples_in_scope": sum(r["react"] == "in_scope" for r in public),
            "version_positive_in_scope": sum(r["react"] == "in_scope" for r in fixture_rows),
            "coverage_cases_in_scope": sum(r["react"] == "in_scope" for r in coverage_rows),
            "name_cases": len(name_rows),
            "public_products_in_scope": len(product_rows),
            "legacy_in_scope": len(legacy_rows),
            "public_products": [{"prompt_version": v, "react": r, "count": n}
                                for (v, r), n in sorted(product_counts.items())],
            "cnl_positive_fixtures": [{"category": c, "react": r, "count": n} for (c, r), n in sorted(fixture_counts.items())],
            "legacy": [{"group": g, "prompt_version": v, "react": r, "count": n}
                       for (g, v, r), n in sorted(legacy_counts.items())],
        },
        "public_examples": public,
        "version_positive_fixtures": fixture_rows,
        "coverage_cases": coverage_rows,
        "name_cases": name_rows,
        "public_products_in_scope": product_rows,
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

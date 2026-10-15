#!/usr/bin/env python3
"""Build the positive target selection examples and their phase 1 coverage.

Each example is a complete compile request body for an in-scope public example
or coverage case, in the CNL and JSON forms. The coverage section lists the
field types, field kinds and list query parameters the Rust examples reach for
each prompt version, and the build fails if any phase 1 type or v5 query
variant is missing. Run with --check to confirm the saved file is current.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys
from typing import Any

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent.parent
EXAMPLES = ROOT / "contracts" / "compiler-v1" / "examples"
CASES = HERE / "cases"
SCOPE = ROOT / "contracts" / "rust-output-v1" / "scope.json"
OUT = ROOT / "contracts" / "rust-output-v1" / "target-examples.json"

RUST_EXPECTED = ("HTTP 200 with a som.dsl-bundle.v2 bundle with target rust. The CNL and JSON forms of the same "
                 "source give byte-identical bundles.")
PYTHON_EXPECTED = "HTTP 200 with the same response bytes as the same request without target."


def compact(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))


def sources() -> list[tuple[str, str, pathlib.Path, pathlib.Path]]:
    rows = [(f"example-v{v}", f"som-rs/contracts/compiler-v1/examples/v{v}", EXAMPLES / f"v{v}.cnl",
             EXAMPLES / f"v{v}.prompt.json") for v in range(1, 6)]
    for case in json.loads((CASES / "index.json").read_text())["cases"]:
        rows.append((case["id"], f"som-rs/tests/rust-output-v1/cases/{case['id']}", CASES / case["cnl"],
                     CASES / case["prompt"]))
    return rows


def body(form: str, cnl: pathlib.Path, prompt: pathlib.Path, target: str | None) -> str:
    if form == "cnl":
        obj: dict[str, Any] = {"format": "som.cnl.v1", "source": cnl.read_text()}
    else:
        obj = {"format": "som.prompt-json.v1", "prompt": json.loads(prompt.read_text())}
    if target is not None:
        obj["target"] = target
    return compact(obj)


def resources(prompt: dict[str, Any]) -> list[tuple[str, dict[str, Any], dict[str, Any] | None]]:
    if prompt["version"] == "som.prompt.v5":
        return [(role, prompt["resources"][role], prompt["resources"][role].get("query"))
                for role in ("parent", "child")]
    return [("resource", prompt["resource"], prompt.get("query"))]


def coverage(prompts: list[dict[str, Any]]) -> dict[str, Any]:
    scope = json.loads(SCOPE.read_text())
    out: dict[str, Any] = {}
    for version in scope["prompt_versions"]["in_scope"]:
        types: set[str] = set()
        kinds: set[str] = set()
        query: set[str] = set()
        examples = [p for p in prompts if p["version"] == version]
        for prompt in examples:
            for role, resource, q in resources(prompt):
                for field in resource["fields"]:
                    types.add(field["type"])
                    if field["required"]:
                        kinds.add("required")
                    elif field.get("default") is not None:
                        kinds.add("optional_with_default")
                    else:
                        kinds.add("optional_without_default")
                    if field.get("unique"):
                        kinds.add("unique")
                if version == "som.prompt.v5":
                    query.add(f"{role} filter_field {'set' if q and q.get('filter_field') else 'unset'}")
                    query.add(f"{role} date_field {'set' if q and q.get('date_field') else 'unset'}")
                elif q:
                    query.update(sorted(q))
        out[version] = {"examples": len(examples), "field_types": sorted(types), "field_kinds": sorted(kinds),
                        "query": sorted(query), "routes": "every route in scope.json routes for this version"}
        missing = set(scope["field_types"][version]) - types
        if missing:
            raise SystemExit(f"{version} examples miss field types {sorted(missing)}")
        needed = {"required", "optional_without_default", "unique"}
        if version not in ("som.prompt.v1", "som.prompt.v2"):
            needed.add("optional_with_default")  # v1 and v2 fields have no default member
        if needed - kinds:
            raise SystemExit(f"{version} examples miss a field kind: {sorted(kinds)}")
        if version == "som.prompt.v5" and len(query) != 8:
            raise SystemExit(f"v5 examples miss a query variant: {sorted(query)}")
    return out


def build() -> dict[str, Any]:
    examples = []
    prompts = []
    for source_id, base, cnl, prompt in sources():
        prompts.append(json.loads(prompt.read_text()))
        for form in ("cnl", "json"):
            examples.append({"id": f"{source_id}-{form}-rust", "source": f"{base}.{'cnl' if form == 'cnl' else 'prompt.json'}",
                             "target": "rust", "body": body(form, cnl, prompt, "rust"), "expected": RUST_EXPECTED})
        if source_id == "example-v1":
            for form in ("cnl", "json"):
                examples.append({"id": f"{source_id}-{form}-python", "source": f"{base}.{'cnl' if form == 'cnl' else 'prompt.json'}",
                                 "target": "python", "body": body(form, cnl, prompt, "python"),
                                 "expected": PYTHON_EXPECTED,
                                 "same_as_body": body(form, cnl, prompt, None)})
    return {"version": "som.rust-output-target-examples.v1",
            "rule": "The target member is the last member of the compile request object. Absent and python both "
                    "select Python; rust selects Rust. The CNL text and the typed prompt do not change by target.",
            "coverage": coverage(prompts), "examples": examples}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    text = json.dumps(build(), ensure_ascii=False, indent=2) + "\n"
    if args.check:
        if OUT.read_text() != text:
            print(f"{OUT} is stale", file=sys.stderr)
            return 1
        return 0
    OUT.write_text(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

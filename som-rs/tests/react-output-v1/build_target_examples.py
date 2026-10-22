#!/usr/bin/env python3
"""Build the positive React target examples and their phase 1 coverage.

Each example is a complete compile request body with target react:
- the public examples v1 to v4 and the in-scope Rust coverage cases, in CNL and JSON;
- name cases: names other targets refuse or that are JavaScript words, which
  React admits, plus Object.prototype member names that are not reserved.
Every case is checked against react_profile.check, which must find no
refusal. Run with --check to confirm the saved file is current.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys
from typing import Any, Callable

from common import CONTRACT, COVERAGE_CASES, EXAMPLES, TARGET, react_profile, rust_cases

OUT = CONTRACT / "target-examples.json"

REACT_EXPECTED = ("HTTP 200 with a som.dsl-bundle.v2 bundle with target react. The CNL and JSON forms of the same "
                  "source give byte-identical bundles.")
NAME_EXPECTED = "HTTP 200 with a som.dsl-bundle.v2 bundle with target react."
PYTHON_EXPECTED = "HTTP 200 with the same response bytes as the same request without target."
PYTHON_CONTROL = "HTTP 200 with the unchanged som.dsl-bundle.v1 bundle"

compact = rust_cases.compact


def sources() -> list[tuple[str, str, pathlib.Path, pathlib.Path]]:
    rows = [(f"example-v{v}", f"som-rs/contracts/compiler-v1/examples/v{v}", EXAMPLES / f"v{v}.cnl",
             EXAMPLES / f"v{v}.prompt.json") for v in range(1, 5)]
    for case in json.loads((COVERAGE_CASES / "index.json").read_text())["cases"]:
        if react_profile.check(json.loads((COVERAGE_CASES / case["prompt"]).read_text())) is not None:
            continue  # the v5 coverage cases are outside React phase 1
        rows.append((case["id"], f"som-rs/tests/rust-output-v1/cases/{case['id']}", COVERAGE_CASES / case["cnl"],
                     COVERAGE_CASES / case["prompt"]))
    return rows


def body(form: str, cnl: pathlib.Path, prompt: pathlib.Path, target: str | None) -> str:
    if form == "cnl":
        obj: dict[str, Any] = {"format": "som.cnl.v1", "source": cnl.read_text()}
    else:
        obj = {"format": "som.prompt-json.v1", "prompt": json.loads(prompt.read_text())}
    if target is not None:
        obj["target"] = target
    return compact(obj)


def coverage(prompts: list[dict[str, Any]]) -> dict[str, Any]:
    scope = react_profile.load_scope()
    out: dict[str, Any] = {}
    for version in scope["prompt_versions"]["in_scope"]:
        types: set[str] = set()
        kinds: set[str] = set()
        query: set[str] = set()
        examples = [p for p in prompts if p["version"] == version]
        for prompt in examples:
            for field in prompt["resource"]["fields"]:
                types.add(field["type"])
                if field["required"]:
                    kinds.add("required")
                elif field.get("default") is not None:
                    kinds.add("optional_with_default")
                else:
                    kinds.add("optional_without_default")
                if field.get("unique"):
                    kinds.add("unique")
            if prompt.get("query"):
                query.update(sorted(prompt["query"]))
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
    return out


def cnl_name_case(case_id: str, purpose: str, base: str, edits: list[tuple[int, str]],
                  prompt_edit: Callable[[dict[str, Any]], None], rust_case: str | None) -> dict[str, Any]:
    """A CNL body with line edits. prompt_edit makes the matching typed prompt for the scope check."""
    source = rust_cases.example(base, "cnl")
    for number, new in edits:
        source = rust_cases.replace_line(source, number, new)
    prompt = json.loads(rust_cases.example(base, "prompt.json"))
    prompt_edit(prompt)
    found = react_profile.check(prompt)
    assert found is None, (case_id, found)
    return {"id": case_id, "purpose": purpose, "form": "cnl", "base_example": f"{base}.cnl",
            "line_edits": [{"line": n, "with": t} for n, t in edits], "rust_case": rust_case, "target": "react",
            "body": compact({"format": "som.cnl.v1", "source": source, "target": "react"}),
            "expected": NAME_EXPECTED,
            "python_control_body": compact({"format": "som.cnl.v1", "source": source}),
            "python_control_expected": PYTHON_CONTROL}


def json_name_case(case_id: str, purpose: str, base: str, prompt_edit: Callable[[dict[str, Any]], None],
                   rust_case: str | None) -> dict[str, Any]:
    prompt = json.loads(rust_cases.example(base, "prompt.json"))
    prompt_edit(prompt)
    found = react_profile.check(prompt)
    assert found is None, (case_id, found)
    head = compact({"format": "som.prompt-json.v1", "prompt": prompt})
    return {"id": case_id, "purpose": purpose, "form": "json", "base_example": f"{base}.prompt.json",
            "rust_case": rust_case, "target": "react", "body": head[:-1] + "," + TARGET + "}",
            "expected": NAME_EXPECTED, "python_control_body": head, "python_control_expected": PYTHON_CONTROL}


def field(index: int, name: str) -> Callable[[dict[str, Any]], None]:
    def edit(p: dict[str, Any]) -> None:
        p["resource"]["fields"][index]["name"] = name
    return edit


def path(value: str) -> Callable[[dict[str, Any]], None]:
    def edit(p: dict[str, Any]) -> None:
        p["resource"]["path"] = value
    return edit


def table(name: str) -> Callable[[dict[str, Any]], None]:
    def edit(p: dict[str, Any]) -> None:
        p["resource"]["table"] = name
    return edit


def name_cases() -> list[dict[str, Any]]:
    return [
        cnl_name_case("RN01_CNL_FIELD_UPPERCASE", "An uppercase field name; Rust phase 1 refused it.", "v1",
                      [(8, "Field Title is required str and is unique.")], field(0, "Title"), "RS05_CNL_FIELD_UPPERCASE"),
        cnl_name_case("RN02_CNL_FIELD_SELF", "A field named self; Rust phase 1 refused it.", "v3",
                      [(8, "Field self is required str and is not unique.")], field(0, "self"), "RS07_CNL_FIELD_SELF"),
        json_name_case("RN03_JSON_FILTER_DOUBLE_UNDERSCORE", "A v2 filter field with a double underscore; Rust "
                       "phase 1 refused such names.", "v2", rust_cases.rename_filter("a__b"), None),
        cnl_name_case("RN04_CNL_FIELD_TO_STRING", "An Object.prototype member name that is not reserved.", "v1",
                      [(8, "Field toString is required str and is unique.")], field(0, "toString"), None),
        json_name_case("RN05_JSON_FIELD_HAS_OWN_PROPERTY", "An Object.prototype member name that is not reserved, "
                       "also the v2 filter field.", "v2", rust_cases.rename_filter("hasOwnProperty"), None),
        cnl_name_case("RN06_CNL_V4_FIELD_FUNCTION", "A JavaScript reserved word that Python admits as a field name; it "
                      "appears only as a member and a string.", "v4", [(9, "Field function is required str and is not unique.")],
                      field(1, "function"), None),
        json_name_case("RN07_JSON_V3_FILTER_VALUE_OF", "A v3 filter field named valueOf, so the list query parameter "
                       "is valueOf.", "v3", rust_cases.rename_filter("valueOf"), None),
        cnl_name_case("RN08_CNL_TABLE_TS_MIGRATIONS", "A table named like the TypeScript migrator table; a React "
                      "product has no table.", "v1", [(6, "Use table _som_ts_migrations.")],
                      table("_som_ts_migrations"), None),
        cnl_name_case("RN09_CNL_PATH_TWO_SEGMENTS", "A path with two segments.", "v1",
                      [(7, 'Use path "/team/items".')], path("/team/items"), None),
    ]


def build() -> dict[str, Any]:
    examples = []
    prompts = []
    for source_id, base, cnl, prompt in sources():
        prompts.append(json.loads(prompt.read_text()))
        for form in ("cnl", "json"):
            examples.append({"id": f"{source_id}-{form}-react",
                             "source": f"{base}.{'cnl' if form == 'cnl' else 'prompt.json'}",
                             "target": "react", "body": body(form, cnl, prompt, "react"),
                             "expected": REACT_EXPECTED})
        if source_id == "example-v1":
            for form in ("cnl", "json"):
                examples.append({"id": f"{source_id}-{form}-python",
                                 "source": f"{base}.{'cnl' if form == 'cnl' else 'prompt.json'}",
                                 "target": "python", "body": body(form, cnl, prompt, "python"),
                                 "expected": PYTHON_EXPECTED, "same_as_body": body(form, cnl, prompt, None)})
    for prompt in prompts:
        assert react_profile.check(prompt) is None, prompt["version"]
    return {"version": "som.react-output-target-examples.v1",
            "status": "frozen expected examples; not executed against a React-target compiler",
            "rule": "The target member is the last member of the compile request object. Absent and python both "
                    "select Python; react selects React. The CNL text and the typed prompt do not change by "
                    "target.",
            "coverage": coverage(prompts), "examples": examples,
            "name_cases_rule": "Each name case compiles for target react and builds a product that passes the "
                               "phase 1 product gates. rust_case names the Rust negative case the edit comes from. "
                               "python_control_body must give python_control_expected before and after the "
                               "React target lands.",
            "name_cases": name_cases()}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    text = json.dumps(build(), ensure_ascii=False, indent=2) + "\n"
    if args.check:
        if not OUT.exists() or OUT.read_text() != text:
            print(f"stale: {OUT.name}", file=sys.stderr)
            return 1
        print("checked target-examples.json")
        return 0
    OUT.write_text(text)
    print("wrote target-examples.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

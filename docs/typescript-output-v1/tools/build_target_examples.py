#!/usr/bin/env python3
"""Build the positive TypeScript target examples and their phase 1 coverage.

Each example is a complete compile request body with target typescript:
- the public examples v1 to v5 and the Rust coverage cases, in CNL and JSON;
- name cases: names the Rust phase 1 refuses but TypeScript admits, plus
  Object.prototype member names that are not reserved.
Every name case is checked against ts_profile.check, which must find no
refusal. Run with --check to confirm the saved file is current.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys
from typing import Any, Callable

from common import CONTRACT, COVERAGE_CASES, EXAMPLES, TARGET, rust_cases, ts_profile

OUT = CONTRACT / "target-examples.json"

TS_EXPECTED = ("HTTP 200 with a som.dsl-bundle.v2 bundle with target typescript. The CNL and JSON forms of the same "
               "source give byte-identical bundles.")
NAME_EXPECTED = "HTTP 200 with a som.dsl-bundle.v2 bundle with target typescript."
PYTHON_EXPECTED = "HTTP 200 with the same response bytes as the same request without target."
PYTHON_CONTROL = "HTTP 200 with the unchanged som.dsl-bundle.v1 bundle"

compact = rust_cases.compact


def sources() -> list[tuple[str, str, pathlib.Path, pathlib.Path]]:
    rows = [(f"example-v{v}", f"som-rs/contracts/compiler-v1/examples/v{v}", EXAMPLES / f"v{v}.cnl",
             EXAMPLES / f"v{v}.prompt.json") for v in range(1, 6)]
    for case in json.loads((COVERAGE_CASES / "index.json").read_text())["cases"]:
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


def resources(prompt: dict[str, Any]) -> list[tuple[str, dict[str, Any], dict[str, Any] | None]]:
    if prompt["version"] == "som.prompt.v5":
        return [(role, prompt["resources"][role], prompt["resources"][role].get("query"))
                for role in ("parent", "child")]
    return [("resource", prompt["resource"], prompt.get("query"))]


def coverage(prompts: list[dict[str, Any]]) -> dict[str, Any]:
    scope = ts_profile.load_scope()
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


def cnl_name_case(case_id: str, purpose: str, base: str, edits: list[tuple[int, str]],
                  prompt_edit: Callable[[dict[str, Any]], None], rust_case: str | None) -> dict[str, Any]:
    """A CNL body with line edits. prompt_edit makes the matching typed prompt for the scope check."""
    source = rust_cases.example(base, "cnl")
    for number, new in edits:
        source = rust_cases.replace_line(source, number, new)
    prompt = json.loads(rust_cases.example(base, "prompt.json"))
    prompt_edit(prompt)
    found = ts_profile.check(prompt)
    assert found is None, (case_id, found)
    return {"id": case_id, "purpose": purpose, "form": "cnl", "base_example": f"{base}.cnl",
            "line_edits": [{"line": n, "with": t} for n, t in edits], "rust_case": rust_case, "target": "typescript",
            "body": compact({"format": "som.cnl.v1", "source": source, "target": "typescript"}),
            "expected": NAME_EXPECTED,
            "python_control_body": compact({"format": "som.cnl.v1", "source": source}),
            "python_control_expected": PYTHON_CONTROL}


def json_name_case(case_id: str, purpose: str, base: str, prompt_edit: Callable[[dict[str, Any]], None],
                   rust_case: str | None) -> dict[str, Any]:
    prompt = json.loads(rust_cases.example(base, "prompt.json"))
    prompt_edit(prompt)
    found = ts_profile.check(prompt)
    assert found is None, (case_id, found)
    head = compact({"format": "som.prompt-json.v1", "prompt": prompt})
    return {"id": case_id, "purpose": purpose, "form": "json", "base_example": f"{base}.prompt.json",
            "rust_case": rust_case, "target": "typescript", "body": head[:-1] + "," + TARGET + "}",
            "expected": NAME_EXPECTED, "python_control_body": head, "python_control_expected": PYTHON_CONTROL}


def field(index: int, name: str, role: str | None = None) -> Callable[[dict[str, Any]], None]:
    def edit(p: dict[str, Any]) -> None:
        resource = p["resources"][role] if role else p["resource"]
        resource["fields"][index]["name"] = name
    return edit


def foreign_key(name: str) -> Callable[[dict[str, Any]], None]:
    def edit(p: dict[str, Any]) -> None:
        p["relation"]["foreign_key"] = name
    return edit


def table(name: str) -> Callable[[dict[str, Any]], None]:
    def edit(p: dict[str, Any]) -> None:
        p["resource"]["table"] = name
    return edit


def both(*edits: Callable[[dict[str, Any]], None]) -> Callable[[dict[str, Any]], None]:
    def edit(p: dict[str, Any]) -> None:
        for e in edits:
            e(p)
    return edit


def name_cases() -> list[dict[str, Any]]:
    return [
        cnl_name_case("TN01_CNL_FIELD_UPPERCASE", "An uppercase field name; Rust phase 1 refused it.", "v1",
                      [(8, "Field Title is required str and is unique.")], field(0, "Title"), "RS05_CNL_FIELD_UPPERCASE"),
        json_name_case("TN02_JSON_FIELD_UPPERCASE", "An uppercase field name; Rust phase 1 refused it.", "v1",
                       field(0, "Title"), "RS06_JSON_FIELD_UPPERCASE"),
        cnl_name_case("TN03_CNL_FIELD_SELF", "A field named self; Rust phase 1 refused it.", "v3",
                      [(8, "Field self is required str and is not unique.")], field(0, "self"), "RS07_CNL_FIELD_SELF"),
        json_name_case("TN04_JSON_CHILD_FIELD_DOUBLE_UNDERSCORE", "A child field with a double underscore; "
                       "Rust phase 1 refused it.", "v5", field(0, "a__b", "child"),
                       "RS08_JSON_CHILD_FIELD_DOUBLE_UNDERSCORE"),
        cnl_name_case("TN05_CNL_V5_FOREIGN_KEY_SELF", "A foreign key named self; Rust phase 1 refused it.", "v5",
                      [(30, "Relate child to parent with foreign key self and restrict deletion.")],
                      foreign_key("self"), "RS14_CNL_V5_FOREIGN_KEY_SELF"),
        json_name_case("TN06_JSON_V5_FOREIGN_KEY_CRATE", "A foreign key named crate; Rust phase 1 refused it.", "v5",
                       foreign_key("crate"), "RS15_JSON_V5_FOREIGN_KEY_CRATE"),
        cnl_name_case("TN07_CNL_V5_CHILD_FIELD_AND_FOREIGN_KEY", "A double underscore child field and a foreign key "
                      "named self together; Rust phase 1 refused the field first.", "v5",
                      [(20, "Field a__b is required str and is not unique."),
                       (30, "Relate child to parent with foreign key self and restrict deletion.")],
                      both(field(0, "a__b", "child"), foreign_key("self")), "RS16_CNL_V5_FIRST_REFUSAL"),
        cnl_name_case("TN08_CNL_TABLE_SQLX_MIGRATIONS", "A table named like the sqlx migrator table; TypeScript "
                      "reserves only _som_ts_migrations.", "v1", [(6, "Use table _sqlx_migrations.")],
                      table("_sqlx_migrations"), "RS21_CNL_TABLE_SQLX_MIGRATIONS"),
        cnl_name_case("TN09_CNL_FIELD_TO_STRING", "An Object.prototype member name that is not reserved.", "v1",
                      [(8, "Field toString is required str and is unique.")], field(0, "toString"), None),
        json_name_case("TN10_JSON_FIELD_HAS_OWN_PROPERTY", "An Object.prototype member name that is not reserved.",
                       "v2", rust_cases.rename_filter("hasOwnProperty"), None),
    ]


def build() -> dict[str, Any]:
    examples = []
    prompts = []
    for source_id, base, cnl, prompt in sources():
        prompts.append(json.loads(prompt.read_text()))
        for form in ("cnl", "json"):
            examples.append({"id": f"{source_id}-{form}-typescript",
                             "source": f"{base}.{'cnl' if form == 'cnl' else 'prompt.json'}",
                             "target": "typescript", "body": body(form, cnl, prompt, "typescript"),
                             "expected": TS_EXPECTED})
        if source_id == "example-v1":
            for form in ("cnl", "json"):
                examples.append({"id": f"{source_id}-{form}-python",
                                 "source": f"{base}.{'cnl' if form == 'cnl' else 'prompt.json'}",
                                 "target": "python", "body": body(form, cnl, prompt, "python"),
                                 "expected": PYTHON_EXPECTED, "same_as_body": body(form, cnl, prompt, None)})
    for prompt in prompts:
        assert ts_profile.check(prompt) is None, prompt["version"]
    return {"version": "som.typescript-output-target-examples.v1",
            "status": "frozen expected examples; not executed against a TypeScript-target compiler",
            "rule": "The target member is the last member of the compile request object. Absent and python both "
                    "select Python; typescript selects TypeScript. The CNL text and the typed prompt do not change by "
                    "target.",
            "coverage": coverage(prompts), "examples": examples,
            "name_cases_rule": "Each name case compiles for target typescript and builds a product that passes the "
                               "phase 1 product gates. rust_case names the Rust negative case the edit comes from. "
                               "python_control_body must give python_control_expected before and after the "
                               "TypeScript target lands.",
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

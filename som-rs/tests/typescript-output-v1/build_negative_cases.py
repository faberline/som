#!/usr/bin/env python3
"""Build the frozen TypeScript target negative cases with exact request bodies and spans.

Each case is a complete compile request body with target typescript, or a bad
target member. The expected diagnostic is derived from the body text and the
frozen scope rules, not from a compiler run. The span helpers are the frozen
Rust tools' (som-rs/tests/rust-output-v1/build_negative_cases.py), so both
targets share one span model. Run with --check to confirm the saved file is
current.
"""
from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from common import CONTRACT, SOM_RS, TARGET, rust_cases, ts_profile

OUT = CONTRACT / "negative-cases.json"

# rust-output-v1 amendment 006 names four targets in the envelope texts (this contract's amendment 002).
TARGET_CAUSE = rust_cases.TARGET_CAUSE
TARGET_FORM = rust_cases.TARGET_FORM
COMPILER_V1_REFUSED = ("TS21_CNL_V3_FILTER_LIMIT", "TS22_JSON_V3_FILTER_OFFSET")

cnl_case = rust_cases.cnl_case
json_case = rust_cases.json_case
envelope_case = rust_cases.envelope_case


def refusal(rule: str, **fmt: str) -> tuple[str, int, str, str]:
    entry = ts_profile.load_scope()["refusals"][rule]
    return entry["code"], entry["http_status"], entry["cause"].format(**fmt), entry["allowed_form"]


def scope_family(version: str) -> str:
    return ts_profile.load_scope()["prompt_versions"]["out_of_scope"][f"som.prompt.{version}"]


def foreign_key(name: str):
    def edit(p):
        p["relation"]["foreign_key"] = name
    return edit


def build() -> dict[str, Any]:
    cases = []
    # Target selection envelope.
    for case_id, form, raw, purpose in [
        ("TT01_JSON_TARGET_ALIAS", "json", '"target":"ts"', "An abbreviation is not a target name."),
        ("TT02_JSON_TARGET_NULL", "json", '"target":null', "A null target is not an omitted target."),
        ("TT03_JSON_TARGET_CASE", "json", '"target":"TypeScript"', "Target names are case sensitive."),
        ("TT04_CNL_TARGET_NUMBER", "cnl", '"target":3', "A non-string target."),
        ("TT05_CNL_TARGET_SPACE", "cnl", '"target":" typescript"', "Target names are not trimmed."),
        ("TT06_CNL_TARGET_ARRAY", "cnl", '"target":["typescript"]', "A list is not a target."),
    ]:
        cases.append(envelope_case(case_id, purpose, form, "v1", raw, "INVALID_SCHEMA", TARGET_CAUSE, TARGET_FORM))
    cases.append(envelope_case("TT07_JSON_TARGET_DUPLICATE", "A duplicate target member is invalid JSON for the "
                               "envelope.", "json", "v1", '"target":"typescript","target":"typescript"',
                               "INVALID_JSON", "Duplicate JSON member.",
                               "One valid JSON value with unique object keys.", key_span=True))

    # Prompt versions outside phase 1.
    for version, case_id in (("v6", "TS01_CNL_V6"), ("v7", "TS02_CNL_V7")):
        code, status, cause, form = refusal("prompt_version", version=version[1:], family=scope_family(version))
        cases.append(cnl_case(case_id, f"Prompt {version} is Python-only in TypeScript phase 1.", version, [],
                              "typescript", 2, version[1:], "/version", code, status, cause, form))
    for version, case_id in (("v6", "TS03_JSON_V6"), ("v7", "TS04_JSON_V7")):
        code, status, cause, form = refusal("prompt_version", version=version[1:], family=scope_family(version))
        cases.append(json_case(case_id, f"Prompt {version} is Python-only in TypeScript phase 1.", version,
                               lambda _: None, TARGET, "/prompt/version", code, status, cause, form))

    # Paths.
    code, status, cause, form = refusal("path_empty_segment", path="/items/")
    cases.append(cnl_case("TS05_CNL_PATH_TRAILING_SLASH", "A path with a trailing slash.", "v1",
                          [(7, 'Use path "/items/".')], "typescript", 7, '"/items/"', "/resource/path",
                          code, status, cause, form))
    code, status, cause, form = refusal("path_empty_segment", path="/a//b")
    cases.append(json_case("TS06_JSON_PATH_DOUBLE_SLASH", "A path with an empty segment.", "v2",
                           rust_cases.set_path("/a//b"), TARGET, "/prompt/resource/path", code, status, cause, form))
    code, status, cause, form = refusal("path_reserved", path="/healthz")
    cases.append(cnl_case("TS07_CNL_PATH_HEALTHZ", "A resource path that is the health check.", "v4",
                          [(7, 'Use path "/healthz".')], "typescript", 7, '"/healthz"', "/resource/path",
                          code, status, cause, form))
    cases.append(json_case("TS08_JSON_PATH_HEALTHZ", "A v3 resource path that is the health check.", "v3",
                           rust_cases.set_path("/healthz"), TARGET, "/prompt/resource/path", code, status, cause, form))

    # Reserved table.
    code, status, cause, form = refusal("table_reserved", table="_som_ts_migrations")
    cases.append(cnl_case("TS09_CNL_TABLE_TS_MIGRATIONS", "A table named like the TypeScript migrator table.", "v1",
                          [(6, "Use table _som_ts_migrations.")], "typescript", 6, "_som_ts_migrations",
                          "/resource/table", code, status, cause, form))

    def ts_table(p):
        p["resource"]["table"] = "_som_ts_migrations"
        p["resource"]["fields"][0]["name"] = "constructor"
    cases.append(json_case("TS10_JSON_TABLE_BEFORE_FIELD", "A table refusal comes before a field refusal.", "v1",
                           ts_table, TARGET, "/prompt/resource/table", code, status, cause, form))

    # Reserved names.
    code, status, cause, form = refusal("identifier_reserved", name="__proto__")
    cases.append(cnl_case("TS11_CNL_FIELD_PROTO", "A field named __proto__; v1 to v4 names admit a leading "
                          "underscore.", "v1", [(8, "Field __proto__ is required str and is unique.")], "typescript",
                          8, "__proto__", "/resource/fields/0/name", code, status, cause, form))
    code, status, cause, form = refusal("identifier_reserved", name="constructor")
    cases.append(json_case("TS12_JSON_FIELD_CONSTRUCTOR", "A field named constructor, also the v2 filter field.", "v2",
                           rust_cases.rename_filter("constructor"), TARGET, "/prompt/resource/fields/0/name",
                           code, status, cause, form))
    code, status, cause, form = refusal("identifier_reserved", name="prototype")
    cases.append(cnl_case("TS13_CNL_V5_CHILD_FIELD_PROTOTYPE", "A v5 child field named prototype.", "v5",
                          [(20, "Field prototype is required str and is not unique.")], "typescript", 20,
                          "prototype", "/resources/child/fields/0/name", code, status, cause, form))
    code, status, cause, form = refusal("identifier_reserved", name="constructor")
    cases.append(json_case("TS14_JSON_V5_FOREIGN_KEY_CONSTRUCTOR", "A v5 foreign key named constructor.", "v5",
                           foreign_key("constructor"), TARGET, "/prompt/relation/foreign_key",
                           code, status, cause, form))

    # Order.
    code, status, cause, form = refusal("path_empty_segment", path="/items/")
    cases.append(cnl_case("TS15_CNL_PATH_BEFORE_FIELD", "A path refusal comes before a field refusal.", "v1",
                          [(7, 'Use path "/items/".'), (8, "Field constructor is required str and is unique.")],
                          "typescript", 7, '"/items/"', "/resource/path", code, status, cause, form))
    code, status, cause, form = refusal("identifier_reserved", name="prototype")
    cases.append(cnl_case("TS16_CNL_V5_CHILD_FIELD_BEFORE_FOREIGN_KEY", "A child field refusal comes before a "
                          "foreign key refusal.", "v5",
                          [(20, "Field prototype is required str and is not unique."),
                           (30, "Relate child to parent with foreign key constructor and restrict deletion.")],
                          "typescript", 20, "prototype", "/resources/child/fields/0/name", code, status, cause, form))
    code, status, cause, form = refusal("identifier_reserved", name="constructor")

    def parent_first(p):
        p["resources"]["parent"]["fields"][0]["name"] = "constructor"
        p["resources"]["child"]["fields"][0]["name"] = "prototype"
    cases.append(json_case("TS17_JSON_V5_PARENT_BEFORE_CHILD", "Every parent check comes before every child check.",
                           "v5", parent_first, TARGET, "/prompt/resources/parent/fields/0/name",
                           code, status, cause, form))

    # v5 capabilities.
    code, status, cause, form = refusal("v5_capabilities", auth="jwt")
    cases.append(cnl_case("TS18_CNL_V5_CAPABILITIES", "A v5 prompt with capabilities.", "v5",
                          [(33, rust_cases.CAPABILITIES_CNL)], "typescript", 35, "jwt", "/capabilities/auth",
                          code, status, cause, form))
    cases.append(json_case("TS19_JSON_V5_CAPABILITIES", "A v5 prompt with capabilities.", "v5",
                           rust_cases.add_capabilities, TARGET, "/prompt/capabilities/auth",
                           code, status, cause, form))
    cases.append(cnl_case("TS20_CNL_V5_CAPABILITIES_FIRST", "A capabilities refusal comes before a field refusal.",
                          "v5", [(20, "Field prototype is required str and is not unique."),
                                 (33, rust_cases.CAPABILITIES_CNL)], "typescript", 35, "jwt", "/capabilities/auth",
                          code, status, cause, form))

    # compiler-v1 amendment 003 refuses a v3 filter named limit or offset for every target.
    code, status, cause = rust_cases.collision("query.filter_field")
    cases.append(cnl_case("TS21_CNL_V3_FILTER_LIMIT", "A v3 bool filter named like the limit parameter; "
                          "compiler-v1 refuses it for every target.", "v3",
                          [(10, "Field limit is optional bool and is not unique with default false."),
                           (13, "Filter exactly on field limit.")], "typescript", 13, "limit", "/query/filter_field",
                          code, status, cause, rust_cases.COLLISION_FORMS["cnl"]))
    cases[-1]["python_control_expected"] = "the same diagnostic"
    cases.append(json_case("TS22_JSON_V3_FILTER_OFFSET", "A v3 bool filter named like the offset parameter; "
                           "compiler-v1 refuses it for every target.", "v3", rust_cases.rename_filter("offset"),
                           TARGET, "/prompt/query/filter_field", code, status, cause,
                           rust_cases.COLLISION_FORMS["json"]))
    cases[-1]["python_control_expected"] = "the same diagnostic, located in python_control_body"

    # Existing errors keep precedence over target scope.
    frozen = json.loads(rust_cases.V1_NEGATIVE.read_text())
    base = (SOM_RS.parent / frozen["base_example"]["path"]).read_text()
    for source_id, case_id in (("CNL03_WRONG_FIELD_TYPE", "TP01_CNL_SYNTAX_BEFORE_TARGET"),
                               ("CNL02_UNSUPPORTED_PROMPT_VERSION", "TP02_CNL_VERSION_BEFORE_TARGET")):
        old = next(c for c in frozen["cases"] if c["id"] == source_id)
        source = rust_cases.replace_line(base, old["replace_line"], old["with"])
        exp = {"code": old["code"], **{k: old["expected"][k] for k in
                                       ("line", "column", "end_line", "end_column", "byte_start", "byte_end")},
               "prompt_pointer": old["expected"]["prompt_pointer"], "cause": old["cause"],
               "allowed_form": old["allowed_form"]}
        cases.append({"id": case_id,
                      "purpose": f"The frozen compiler-v1 case {source_id} keeps its diagnostic with target typescript.",
                      "form": "cnl", "base_example": "v7.cnl",
                      "line_edits": [{"line": old["replace_line"], "with": old["with"]}],
                      "http_status": old["http_status"],
                      "body": rust_cases.compact({"format": "som.cnl.v1", "source": source, "target": "typescript"}),
                      "span_text": old["span_text"], "expected": exp,
                      "python_control_body": rust_cases.compact({"format": "som.cnl.v1", "source": source}),
                      "python_control_expected": "the same diagnostic"})
    case = json_case("TP03_JSON_V4_RESERVED_FILTER_BEFORE_TARGET",
                     "Existing validation refuses a v4 filter named before, so the TypeScript scope never sees it.",
                     "v4", rust_cases.rename_filter("before"), TARGET, "/prompt/query/filter_field", "INVALID_VALUE",
                     400, "prompt.query.filter_field: reserved query name",
                     "A prompt that meets the pinned compiler contract.")
    case["python_control_expected"] = "the same diagnostic, located in python_control_body"
    cases.append(case)

    def parent_healthz(p):
        p["resources"]["parent"]["path"] = "/healthz"
    case = json_case("TP04_JSON_V5_HEALTHZ_PATH_BEFORE_TARGET",
                     "compiler-v1 refuses a v5 path whose first segment is healthz, so path_reserved is reachable only "
                     "for v1 to v4.", "v5", parent_healthz, TARGET, "/prompt/resources/parent/path", "INVALID_VALUE",
                     400, "invalid resource path", "A prompt that meets the pinned compiler contract.")
    case["python_control_expected"] = "the same diagnostic, located in python_control_body"
    cases.append(case)

    for case in cases:
        case.setdefault("python_control_expected", "HTTP 200 with the unchanged som.dsl-bundle.v1 bundle")
        if case["form"] == "cnl" and case["expected"]["prompt_pointer"] != "/target" and case["id"].startswith("TS"):
            source = json.loads(case["body"])["source"]
            assert source[case["expected"]["byte_start"]:case["expected"]["byte_end"]] == case["span_text"]
        elif case["id"].startswith(("TT", "TS")):
            raw = case["body"].encode()
            assert raw[case["expected"]["byte_start"]:case["expected"]["byte_end"]].decode() == case["span_text"]
        if case["id"].startswith("TS") and case["id"] not in COMPILER_V1_REFUSED:
            prompt = json.loads(case["python_control_body"]).get("prompt")
            if prompt is not None:
                got = ts_profile.check(prompt)
                assert got and got["cause"] == case["expected"]["cause"], (case["id"], got)
                assert "/prompt" + got["pointer"] == case["expected"]["prompt_pointer"], (case["id"], got)

    return {
        "version": "som.typescript-output-negative-cases.v1",
        "status": "frozen expected cases; not executed against a TypeScript-target compiler",
        "span_model": "CNL cases locate the CNL source. JSON cases and envelope cases locate the raw request body. "
                      "Lines and columns are 1-based Unicode scalar positions. Byte spans are 0-based half-open UTF-8 "
                      "offsets. JSON spans cover the whole value token, including quotes. A duplicate member locates "
                      "the second key token. This is the rust-output-v1 span model.",
        "python_control": "python_control_body is the same request without target. It must give "
                          "python_control_expected before and after the TypeScript target lands.",
        "cases": cases,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    text = json.dumps(build(), indent=2, ensure_ascii=False) + "\n"
    if args.check:
        if not OUT.exists() or OUT.read_text() != text:
            print(f"stale: {OUT.name}", file=sys.stderr)
            return 1
        print("checked negative-cases.json")
        return 0
    OUT.write_text(text)
    print("wrote negative-cases.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())

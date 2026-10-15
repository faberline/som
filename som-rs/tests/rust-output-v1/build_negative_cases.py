#!/usr/bin/env python3
"""Build the frozen Rust target negative cases with exact request bodies and spans.

Each case is a complete compile request body. The expected diagnostic is
derived here from the body text and the frozen scope rules, not from a
compiler run. Run with --check to confirm the saved file is current.
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
V1_NEGATIVE = ROOT / "tests" / "compiler-v1" / "cnl-negative-cases.json"
OUT = ROOT / "contracts" / "rust-output-v1" / "negative-cases.json"

sys.path.insert(0, str(HERE))
import rust_profile  # type: ignore[import-not-found]  # noqa: E402

TARGET_CAUSE = "Target must be the string python or rust."
TARGET_FORM = "Omit target, or use target python or rust."


def compact(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))


class Spans:
    """Map JSON pointers to raw token spans in one JSON text."""

    def __init__(self, text: str):
        self.text = text
        self.values: dict[str, tuple[int, int]] = {}
        self.keys: dict[str, list[tuple[int, int]]] = {}
        end = self._value(self._ws(0), "")
        assert self._ws(end) == len(text)

    def _ws(self, i: int) -> int:
        while i < len(self.text) and self.text[i] in " \t\r\n":
            i += 1
        return i

    def _string(self, i: int) -> int:
        assert self.text[i] == '"'
        i += 1
        while self.text[i] != '"':
            i += 2 if self.text[i] == "\\" else 1
        return i + 1

    def _value(self, i: int, pointer: str) -> int:
        start = i
        c = self.text[i]
        if c == "{":
            i = self._ws(i + 1)
            while self.text[i] != "}":
                key_end = self._string(i)
                key = json.loads(self.text[i:key_end]).replace("~", "~0").replace("/", "~1")
                self.keys.setdefault(pointer + "/" + key, []).append((i, key_end))
                i = self._ws(key_end)
                assert self.text[i] == ":"
                i = self._ws(self._value(self._ws(i + 1), pointer + "/" + key))
                if self.text[i] == ",":
                    i = self._ws(i + 1)
            end = i + 1
        elif c == "[":
            i = self._ws(i + 1)
            index = 0
            while self.text[i] != "]":
                i = self._ws(self._value(i, f"{pointer}/{index}"))
                index += 1
                if self.text[i] == ",":
                    i = self._ws(i + 1)
            end = i + 1
        elif c == '"':
            end = self._string(i)
        else:
            end = i
            while end < len(self.text) and self.text[end] not in ",}] \t\r\n":
                end += 1
        self.values.setdefault(pointer, (start, end))
        return end


def location(text: str, start: int, end: int) -> dict[str, int]:
    """Line, 1-based scalar column and 0-based UTF-8 byte span of text[start:end]."""
    def point(offset: int) -> tuple[int, int]:
        before = text[:offset]
        line = before.count("\n") + 1
        column = offset - (before.rfind("\n") + 1) + 1
        return line, column
    line, column = point(start)
    end_line, end_column = point(end)
    return {"line": line, "column": column, "end_line": end_line, "end_column": end_column,
            "byte_start": len(text[:start].encode()), "byte_end": len(text[:end].encode())}


def example(name: str, suffix: str) -> str:
    return (EXAMPLES / f"{name}.{suffix}").read_text()


def replace_line(source: str, number: int, new: str) -> str:
    lines = source.split("\n")
    lines[number - 1] = new
    return "\n".join(lines)


def cnl_case(case_id, purpose, base, edits, target, span_line, span_text, pointer, code, status, cause, form):
    source = example(base, "cnl")
    for number, new in edits:
        source = replace_line(source, number, new)
    line_text = source.split("\n")[span_line - 1]
    column = line_text.index(span_text)
    start = sum(len(line) + 1 for line in source.split("\n")[:span_line - 1]) + column
    body_obj = {"format": "som.cnl.v1", "source": source}
    if target is not None:
        body_obj["target"] = target
    expected = {"code": code, **location(source, start, start + len(span_text)), "prompt_pointer": pointer,
                "cause": cause, "allowed_form": form}
    control = compact({"format": "som.cnl.v1", "source": source})
    return {"id": case_id, "purpose": purpose, "form": "cnl", "base_example": f"{base}.cnl",
            "line_edits": [{"line": n, "with": t} for n, t in edits], "http_status": status,
            "body": compact(body_obj), "span_text": span_text, "expected": expected,
            "python_control_body": control}


def json_case(case_id, purpose, base, mutate, target_raw, pointer, code, status, cause, form, key_span=False):
    prompt = json.loads(example(base, "prompt.json"))
    mutate(prompt)
    head = compact({"format": "som.prompt-json.v1", "prompt": prompt})
    body = head[:-1] + "," + target_raw + "}" if target_raw else head
    spans = Spans(body)
    start, end = (spans.keys[pointer][-1] if key_span else spans.values[pointer])
    expected = {"code": code, **location(body, start, end), "prompt_pointer": pointer, "cause": cause,
                "allowed_form": form}
    return {"id": case_id, "purpose": purpose, "form": "json", "base_example": f"{base}.prompt.json",
            "http_status": status, "body": body, "span_text": body[start:end], "expected": expected,
            "python_control_body": head}


def envelope_case(case_id, purpose, form, base, target_raw, code, cause, allowed_form, key_span=False):
    if form == "cnl":
        head = compact({"format": "som.cnl.v1", "source": example(base, "cnl")})
    else:
        head = compact({"format": "som.prompt-json.v1", "prompt": json.loads(example(base, "prompt.json"))})
    body = head[:-1] + "," + target_raw + "}"
    spans = Spans(body)
    start, end = spans.keys["/target"][-1] if key_span else spans.values["/target"]
    expected = {"code": code, **location(body, start, end), "prompt_pointer": "/target", "cause": cause,
                "allowed_form": allowed_form}
    return {"id": case_id, "purpose": purpose, "form": form, "base_example": f"{base}.{'cnl' if form == 'cnl' else 'prompt.json'}",
            "http_status": 400, "body": body, "span_text": body[start:end], "expected": expected,
            "python_control_body": head}


def refusal(rule: str, **fmt: str) -> tuple[str, int, str, str]:
    entry = rust_profile.load_scope()["refusals"][rule]
    return entry["code"], entry["http_status"], entry["cause"].format(**fmt), entry["allowed_form"]


def scope_family(version: str) -> str:
    return rust_profile.load_scope()["prompt_versions"]["out_of_scope"][f"som.prompt.{version}"]


def rename_filter(name):
    def edit(p):
        old = p["query"]["filter_field"]
        for field in p["resource"]["fields"]:
            if field["name"] == old:
                field["name"] = name
        p["query"]["filter_field"] = name
    return edit


CAPABILITIES_CNL = "\n".join([
    "Begin capabilities.",
    "Use database postgresql.",
    "Use authentication jwt.",
    "Use object storage none.",
    "Use workflow json_export.",
    "Use no workflow value field.",
    "Use no workflow group field.",
    "End capabilities.",
])


def add_capabilities(p):
    p["capabilities"] = {"database": "postgresql", "auth": "jwt", "storage": "none", "workflow": "json_export",
                         "value_field": None, "group_field": None}


def set_path(path):
    def edit(p):
        p["resource"]["path"] = path
    return edit


def build() -> dict[str, Any]:
    cases = []
    # Target selection envelope.
    for case_id, form, raw, purpose in [
        ("RT01_JSON_TARGET_UNKNOWN", "json", '"target":"go"', "An unknown target name."),
        ("RT02_JSON_TARGET_NULL", "json", '"target":null', "A null target is not an omitted target."),
        ("RT03_JSON_TARGET_CASE", "json", '"target":"Rust"', "Target names are case sensitive."),
        ("RT04_CNL_TARGET_NUMBER", "cnl", '"target":7', "A non-string target."),
        ("RT05_CNL_TARGET_EMPTY", "cnl", '"target":""', "An empty target name."),
        ("RT06_CNL_TARGET_ARRAY", "cnl", '"target":["rust"]', "A list is not a target."),
    ]:
        cases.append(envelope_case(case_id, purpose, form, "v1", raw, "INVALID_SCHEMA", TARGET_CAUSE, TARGET_FORM))
    cases.append(envelope_case("RT07_JSON_TARGET_DUPLICATE", "A duplicate target member is invalid JSON for the envelope.",
                               "json", "v1", '"target":"rust","target":"rust"', "INVALID_JSON",
                               "Duplicate JSON member.", "One valid JSON value with unique object keys.", key_span=True))

    # Prompt versions outside phase 1.
    for version, case_id in (("v6", "RS01_CNL_V6"), ("v7", "RS02_CNL_V7")):
        code, status, cause, form = refusal("prompt_version", version=version[1:], family=scope_family(version))
        cases.append(cnl_case(case_id, f"Prompt {version} is Python-only in phase 1.", version, [], "rust", 2,
                              version[1:], "/version", code, status, cause, form))
    for version, case_id in (("v6", "RS03_JSON_V6"), ("v7", "RS04_JSON_V7")):
        code, status, cause, form = refusal("prompt_version", version=version[1:], family=scope_family(version))
        cases.append(json_case(case_id, f"Prompt {version} is Python-only in phase 1.", version, lambda _: None,
                               '"target":"rust"', "/prompt/version", code, status, cause, form))

    # Identifiers.
    code, status, cause, form = refusal("identifier_not_snake_case", name="Title")
    cases.append(cnl_case("RS05_CNL_FIELD_UPPERCASE", "An uppercase field name.", "v1",
                          [(8, "Field Title is required str and is unique.")], "rust", 8, "Title",
                          "/resource/fields/0/name", code, status, cause, form))

    def upper(p):
        p["resource"]["fields"][0]["name"] = "Title"
    cases.append(json_case("RS06_JSON_FIELD_UPPERCASE", "An uppercase field name.", "v1", upper, '"target":"rust"',
                           "/prompt/resource/fields/0/name", code, status, cause, form))
    code, status, cause, form = refusal("identifier_not_raw", name="self")
    cases.append(cnl_case("RS07_CNL_FIELD_SELF", "A field name that cannot be a raw identifier.", "v3",
                          [(8, "Field self is required str and is not unique.")], "rust", 8, "self",
                          "/resource/fields/0/name", code, status, cause, form))
    code, status, cause, form = refusal("identifier_not_snake_case", name="a__b")

    def double(p):
        p["resources"]["child"]["fields"][0]["name"] = "a__b"
    cases.append(json_case("RS08_JSON_CHILD_FIELD_DOUBLE_UNDERSCORE", "A child field name with a double underscore.",
                           "v5", double, '"target":"rust"', "/prompt/resources/child/fields/0/name",
                           code, status, cause, form))

    # Paths.
    code, status, cause, form = refusal("path_empty_segment", path="/items/")
    cases.append(cnl_case("RS09_CNL_PATH_TRAILING_SLASH", "A path with a trailing slash.", "v1",
                          [(7, 'Use path "/items/".')], "rust", 7, '"/items/"', "/resource/path",
                          code, status, cause, form))
    code, status, cause, form = refusal("path_empty_segment", path="/a//b")
    cases.append(json_case("RS10_JSON_PATH_DOUBLE_SLASH", "A path with an empty segment.", "v2", set_path("/a//b"),
                           '"target":"rust"', "/prompt/resource/path", code, status, cause, form))
    code, status, cause, form = refusal("path_reserved", path="/healthz")
    cases.append(cnl_case("RS11_CNL_PATH_HEALTHZ", "A resource path that is the health check.", "v4",
                          [(7, 'Use path "/healthz".')], "rust", 7, '"/healthz"', "/resource/path",
                          code, status, cause, form))

    # Query parameter collisions.
    code, status, cause, form = refusal("query_parameter_collision", name="limit")
    cases.append(cnl_case("RS12_CNL_V3_FILTER_LIMIT", "A v3 bool filter named like the limit parameter.", "v3",
                          [(10, "Field limit is optional bool and is not unique with default false."),
                           (13, "Filter exactly on field limit.")], "rust", 13, "limit", "/query/filter_field",
                          code, status, cause, form))
    code, status, cause, form = refusal("query_parameter_collision", name="offset")
    cases.append(json_case("RS13_JSON_V3_FILTER_OFFSET", "A v3 bool filter named like the offset parameter.", "v3",
                           rename_filter("offset"), '"target":"rust"', "/prompt/query/filter_field",
                           code, status, cause, form))

    # Foreign keys and order.
    code, status, cause, form = refusal("identifier_not_raw", name="self")
    cases.append(cnl_case("RS14_CNL_V5_FOREIGN_KEY_SELF", "A foreign key that cannot be a raw identifier.", "v5",
                          [(30, "Relate child to parent with foreign key self and restrict deletion.")], "rust", 30,
                          "self", "/relation/foreign_key", code, status, cause, form))
    code, status, cause, form = refusal("identifier_not_raw", name="crate")

    def crate(p):
        p["relation"]["foreign_key"] = "crate"
    cases.append(json_case("RS15_JSON_V5_FOREIGN_KEY_CRATE", "A foreign key that cannot be a raw identifier.", "v5",
                           crate, '"target":"rust"', "/prompt/relation/foreign_key", code, status, cause, form))
    code, status, cause, form = refusal("identifier_not_snake_case", name="a__b")
    cases.append(cnl_case("RS16_CNL_V5_FIRST_REFUSAL", "A child field refusal comes before a foreign key refusal.", "v5",
                          [(20, "Field a__b is required str and is not unique."),
                           (30, "Relate child to parent with foreign key self and restrict deletion.")], "rust", 20,
                          "a__b", "/resources/child/fields/0/name", code, status, cause, form))
    code, status, cause, form = refusal("path_empty_segment", path="/items/")
    cases.append(cnl_case("RS17_CNL_PATH_BEFORE_FIELD", "A path refusal comes before a field refusal.", "v1",
                          [(7, 'Use path "/items/".'), (8, "Field Title is required str and is unique.")], "rust", 7,
                          '"/items/"', "/resource/path", code, status, cause, form))

    # v5 capabilities.
    code, status, cause, form = refusal("v5_capabilities", auth="jwt")
    cases.append(cnl_case("RS18_CNL_V5_CAPABILITIES", "A v5 prompt with capabilities.", "v5", [(33, CAPABILITIES_CNL)],
                          "rust", 35, "jwt", "/capabilities/auth", code, status, cause, form))
    cases.append(json_case("RS19_JSON_V5_CAPABILITIES", "A v5 prompt with capabilities.", "v5", add_capabilities,
                           '"target":"rust"', "/prompt/capabilities/auth", code, status, cause, form))
    cases.append(cnl_case("RS20_CNL_V5_CAPABILITIES_FIRST", "A capabilities refusal comes before a field refusal.", "v5",
                          [(20, "Field a__b is required str and is not unique."), (33, CAPABILITIES_CNL)], "rust", 35,
                          "jwt", "/capabilities/auth", code, status, cause, form))

    # Reserved tables.
    code, status, cause, form = refusal("table_reserved", table="_sqlx_migrations")
    cases.append(cnl_case("RS21_CNL_TABLE_SQLX_MIGRATIONS", "A table named like the sqlx migrator table.", "v1",
                          [(6, "Use table _sqlx_migrations.")], "rust", 6, "_sqlx_migrations", "/resource/table",
                          code, status, cause, form))

    def sqlx_table(p):
        p["resource"]["table"] = "_sqlx_migrations"
        p["resource"]["fields"][0]["name"] = "Title"
    cases.append(json_case("RS22_JSON_TABLE_BEFORE_FIELD", "A table refusal comes before a field refusal.", "v1",
                           sqlx_table, '"target":"rust"', "/prompt/resource/table", code, status, cause, form))

    # Existing errors keep precedence over target scope.
    frozen = json.loads(V1_NEGATIVE.read_text())
    base = (ROOT.parent / frozen["base_example"]["path"]).read_text()
    for source_id, case_id in (("CNL03_WRONG_FIELD_TYPE", "RP01_CNL_SYNTAX_BEFORE_TARGET"),
                               ("CNL02_UNSUPPORTED_PROMPT_VERSION", "RP02_CNL_VERSION_BEFORE_TARGET")):
        old = next(c for c in frozen["cases"] if c["id"] == source_id)
        source = replace_line(base, old["replace_line"], old["with"])
        exp = {"code": old["code"], **{k: old["expected"][k] for k in ("line", "column", "end_line", "end_column", "byte_start", "byte_end")},
               "prompt_pointer": old["expected"]["prompt_pointer"], "cause": old["cause"], "allowed_form": old["allowed_form"]}
        cases.append({"id": case_id, "purpose": f"The frozen compiler-v1 case {source_id} keeps its diagnostic with target rust.",
                      "form": "cnl", "base_example": "v7.cnl",
                      "line_edits": [{"line": old["replace_line"], "with": old["with"]}],
                      "http_status": old["http_status"],
                      "body": compact({"format": "som.cnl.v1", "source": source, "target": "rust"}),
                      "span_text": old["span_text"], "expected": exp,
                      "python_control_body": compact({"format": "som.cnl.v1", "source": source}),
                      "python_control_expected": "the same diagnostic"})
    # Existing validation already refuses a v4 filter named like a list parameter.
    case = json_case("RP03_JSON_V4_RESERVED_FILTER_BEFORE_TARGET",
                     "Existing validation refuses a v4 filter named before, so the Rust collision rule never sees it.",
                     "v4", rename_filter("before"), '"target":"rust"', "/prompt/query/filter_field", "INVALID_VALUE", 400,
                     "prompt.query.filter_field: reserved query name", "A prompt that meets the pinned compiler contract.")
    case["python_control_expected"] = "the same diagnostic, located in python_control_body"
    cases.append(case)

    for case in cases:
        case.setdefault("python_control_expected", "HTTP 200 with the unchanged som.dsl-bundle.v1 bundle")
        if case["form"] == "cnl" and case["expected"]["prompt_pointer"] != "/target" and case["id"].startswith("RS"):
            source = json.loads(case["body"])["source"]
            assert source[case["expected"]["byte_start"]:case["expected"]["byte_end"]] == case["span_text"]
        elif case["id"].startswith(("RT", "RS")):
            body = case["body"].encode()
            assert body[case["expected"]["byte_start"]:case["expected"]["byte_end"]].decode() == case["span_text"]
        if case["id"].startswith("RS"):
            prompt = json.loads(case["python_control_body"]).get("prompt")
            if prompt is not None:
                got = rust_profile.check(prompt)
                assert got and got["cause"] == case["expected"]["cause"], (case["id"], got)

    return {
        "version": "som.rust-output-negative-cases.v1",
        "status": "frozen expected cases; not executed against a Rust-target compiler",
        "span_model": "CNL cases locate the CNL source. JSON cases and envelope cases locate the raw request body. Lines and columns are 1-based Unicode scalar positions. Byte spans are 0-based half-open UTF-8 offsets. JSON spans cover the whole value token, including quotes. A duplicate member locates the second key token.",
        "python_control": "python_control_body is the same request without target. It must give python_control_expected before and after the Rust target lands.",
        "cases": cases,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    text = json.dumps(build(), indent=2, ensure_ascii=False) + "\n"
    if args.check:
        if not OUT.exists() or OUT.read_text() != text:
            print("stale: " + str(OUT.relative_to(ROOT.parent)))
            return 1
        print("checked negative-cases.json")
        return 0
    OUT.write_text(text)
    print("wrote negative-cases.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())

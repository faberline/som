#!/usr/bin/env python3
"""Reference check for the Rust output phase 1 scope (som.rust-output-scope.v1).

It runs on a typed prompt that already passed the existing compiler-v1
validation. It returns None when the prompt is in scope, or the first refusal
as {rule, pointer, cause, allowed_form}. The order of checks is frozen in
CONTRACT.md section 4. This is an independent check for the frozen cases. It
does not import the compiler.
"""
from __future__ import annotations

import json
import pathlib
from typing import Any

HERE = pathlib.Path(__file__).resolve().parent
SCOPE = HERE.parent.parent / "contracts" / "rust-output-v1" / "scope.json"


def load_scope() -> dict[str, Any]:
    return json.loads(SCOPE.read_text())


def is_rust_snake_case(name: str) -> bool:
    """Mirror rustc's non_snake_case rule, and refuse names made only of underscores."""
    core = name.strip("_")
    if not core:
        return False
    if any(c.isupper() for c in core):
        return False
    return "__" not in core


def _refusal(scope: dict[str, Any], rule: str, pointer: str, **fmt: str) -> dict[str, Any]:
    entry = scope["refusals"][rule]
    return {"rule": rule, "code": entry["code"], "pointer": pointer,
            "cause": entry["cause"].format(**fmt), "allowed_form": entry["allowed_form"]}


def _identifier(scope: dict[str, Any], name: str, pointer: str) -> dict[str, Any] | None:
    if name in scope["identifiers"]["not_raw_identifiers"]:
        return _refusal(scope, "identifier_not_raw", pointer, name=name)
    if not is_rust_snake_case(name):
        return _refusal(scope, "identifier_not_snake_case", pointer, name=name)
    return None


def _path(scope: dict[str, Any], path: str, pointer: str, top_level: bool) -> dict[str, Any] | None:
    segments = path.split("/")[1:]
    if any(segment == "" for segment in segments):
        return _refusal(scope, "path_empty_segment", pointer, path=path)
    if top_level and path in scope["routes"]["fixed_top_level_paths"]:
        return _refusal(scope, "path_reserved", pointer, path=path)
    return None


def _table(scope: dict[str, Any], table: str, pointer: str) -> dict[str, Any] | None:
    if table in scope["identifiers"]["reserved_tables"]:
        return _refusal(scope, "table_reserved", pointer, table=table)
    return None


def check(prompt: dict[str, Any], scope: dict[str, Any] | None = None) -> dict[str, Any] | None:
    scope = scope or load_scope()
    version = prompt["version"]
    if version not in scope["prompt_versions"]["in_scope"]:
        family = scope["prompt_versions"]["out_of_scope"][version]
        return _refusal(scope, "prompt_version", "/version", version=version[len("som.prompt.v"):], family=family)
    if version == "som.prompt.v5" and "capabilities" in prompt:
        return _refusal(scope, "v5_capabilities", "/capabilities/auth", auth=prompt["capabilities"]["auth"])
    if version == "som.prompt.v5":
        resources = [("parent", "/resources/parent", True), ("child", "/resources/child", False)]
        for role, base, top in resources:
            resource = prompt["resources"][role]
            found = _path(scope, resource["path"], base + "/path", top) or _table(scope, resource["table"], base + "/table")
            if found:
                return found
            for index, field in enumerate(resource["fields"]):
                found = _identifier(scope, field["name"], f"{base}/fields/{index}/name")
                if found:
                    return found
        return _identifier(scope, prompt["relation"]["foreign_key"], "/relation/foreign_key")
    resource = prompt["resource"]
    found = _path(scope, resource["path"], "/resource/path", True) or _table(scope, resource["table"], "/resource/table")
    if found:
        return found
    for index, field in enumerate(resource["fields"]):
        found = _identifier(scope, field["name"], f"/resource/fields/{index}/name")
        if found:
            return found
    query = prompt.get("query")
    if query is not None and version in scope["query_parameters"]:
        fixed = scope["query_parameters"][version]["fixed"]
        if scope["query_parameters"][version]["filter_parameter"] == "field_name" and query["filter_field"] in fixed:
            return _refusal(scope, "query_parameter_collision", "/query/filter_field", name=query["filter_field"])
    return None

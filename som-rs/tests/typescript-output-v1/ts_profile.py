#!/usr/bin/env python3
"""Reference check for the TypeScript output phase 1 scope (som.typescript-output-scope.v1).

It runs on a typed prompt that already passed the existing compiler-v1
validation. It returns None when the prompt is in scope, or the first refusal
as {rule, code, pointer, cause, allowed_form}. The order of checks is
scope.json refusal_order. This is an independent check for the frozen cases.
It does not import the compiler.
"""
from __future__ import annotations

import json
import pathlib
from typing import Any

HERE = pathlib.Path(__file__).resolve().parent
SCOPE = HERE.parents[1] / "contracts" / "typescript-output-v1" / "scope.json"


def load_scope() -> dict[str, Any]:
    return json.loads(SCOPE.read_text())


def _refusal(scope: dict[str, Any], rule: str, pointer: str, **fmt: str) -> dict[str, Any]:
    entry = scope["refusals"][rule]
    return {"rule": rule, "code": entry["code"], "pointer": pointer,
            "cause": entry["cause"].format(**fmt), "allowed_form": entry["allowed_form"]}


def _identifier(scope: dict[str, Any], name: str, pointer: str) -> dict[str, Any] | None:
    if name in scope["identifiers"]["reserved_names"]:
        return _refusal(scope, "identifier_reserved", pointer, name=name)
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


def _resource(scope: dict[str, Any], resource: dict[str, Any], base: str, top: bool) -> dict[str, Any] | None:
    found = _path(scope, resource["path"], base + "/path", top) or _table(scope, resource["table"], base + "/table")
    if found:
        return found
    for index, field in enumerate(resource["fields"]):
        found = _identifier(scope, field["name"], f"{base}/fields/{index}/name")
        if found:
            return found
    return None


def check(prompt: dict[str, Any], scope: dict[str, Any] | None = None) -> dict[str, Any] | None:
    scope = scope or load_scope()
    version = prompt["version"]
    if version not in scope["prompt_versions"]["in_scope"]:
        family = scope["prompt_versions"]["out_of_scope"][version]
        return _refusal(scope, "prompt_version", "/version", version=version[len("som.prompt.v"):], family=family)
    if version == "som.prompt.v5":
        if "capabilities" in prompt:
            return _refusal(scope, "v5_capabilities", "/capabilities/auth", auth=prompt["capabilities"]["auth"])
        for role, top in (("parent", True), ("child", False)):
            found = _resource(scope, prompt["resources"][role], f"/resources/{role}", top)
            if found:
                return found
        return _identifier(scope, prompt["relation"]["foreign_key"], "/relation/foreign_key")
    return _resource(scope, prompt["resource"], "/resource", True)

#!/usr/bin/env python3
"""Reference check for the React output phase 1 scope (som.react-output-scope.v1).

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
SCOPE = HERE.parents[1] / "contracts" / "react-output-v1" / "scope.json"


def load_scope() -> dict[str, Any]:
    return json.loads(SCOPE.read_text())


def _refusal(scope: dict[str, Any], rule: str, pointer: str, **fmt: str) -> dict[str, Any]:
    entry = scope["refusals"][rule]
    return {"rule": rule, "code": entry["code"], "pointer": pointer,
            "cause": entry["cause"].format(**fmt), "allowed_form": entry["allowed_form"]}


def check(prompt: dict[str, Any], scope: dict[str, Any] | None = None) -> dict[str, Any] | None:
    scope = scope or load_scope()
    version = prompt["version"]
    if version not in scope["prompt_versions"]["in_scope"]:
        family = scope["prompt_versions"]["out_of_scope"][version]
        return _refusal(scope, "prompt_version", "/version", version=version[len("som.prompt.v"):], family=family)
    resource = prompt["resource"]
    path = resource["path"]
    if any(segment == "" for segment in path.split("/")[1:]):
        return _refusal(scope, "path_empty_segment", "/resource/path", path=path)
    for index, field in enumerate(resource["fields"]):
        if field["name"] in scope["identifiers"]["reserved_names"]:
            return _refusal(scope, "identifier_reserved", f"/resource/fields/{index}/name", name=field["name"])
    return None

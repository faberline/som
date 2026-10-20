#!/usr/bin/env python3
"""Reference check for the Rust output phase 2 scope (CONTRACT.md section 10).

Phase 2 admits every prompt compiler-v1 admits, with one refusal kept from phase 1:
query_parameter_collision, a v3 filter field named like a fixed list parameter. Its query string
is ambiguous (?limit=5 would be both the filter and the page size), the Python product for such a
prompt does not start, and compiler-v1 already refuses the same names in v4 and v5.

It runs on a typed prompt that already passed compiler-v1 validation and returns None or the
refusal, in the phase 1 shape and with the phase 1 cause and allowed form. rust_profile.py stays
the phase 1 record.
"""
from __future__ import annotations

from typing import Any

import rust_profile  # type: ignore[import-not-found]

KEPT = ("query_parameter_collision",)


def check(prompt: dict[str, Any], scope: dict[str, Any] | None = None) -> dict[str, Any] | None:
    scope = scope or rust_profile.load_scope()
    query = prompt.get("query")
    version = prompt["version"]
    if query is None or version not in scope["query_parameters"]:
        return None
    parameters = scope["query_parameters"][version]
    if parameters["filter_parameter"] == "field_name" and query["filter_field"] in parameters["fixed"]:
        return rust_profile._refusal(scope, "query_parameter_collision", "/query/filter_field",
                                     name=query["filter_field"])
    return None

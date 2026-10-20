#!/usr/bin/env python3
"""Reference check for the Rust output phase 2 scope (CONTRACT.md sections 10 and 12).

Phase 2 admits every prompt compiler-v1 admits. Amendment 002 kept one phase 1 refusal,
query_parameter_collision: a v3 filter field named like a fixed list parameter. Amendment 004
removed it, because compiler-v1 amendment 003 refuses those names (python_name_collisions) for
every target, so no prompt that reaches this profile has one.

It runs on a typed prompt that already passed compiler-v1 validation and returns None, the
phase 2 result for every such prompt. rust_profile.py stays the phase 1 record.
"""
from __future__ import annotations

from typing import Any

KEPT: tuple[str, ...] = ()


def check(prompt: dict[str, Any], scope: dict[str, Any] | None = None) -> dict[str, Any] | None:
    """None: phase 2 covers every prompt compiler-v1 admits."""
    return None

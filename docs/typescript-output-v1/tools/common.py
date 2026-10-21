"""Shared paths and helpers for the TypeScript output contract tools.

The tools live in <contract>/tools while the contract is a draft
(docs/typescript-output-v1/tools) and move to som-rs/tests/typescript-output-v1
when it is frozen. CONTRACT is the directory that holds scope.json in either
place. The JSON span helpers are reused from the frozen Rust tools, which are
read, never changed.
"""
from __future__ import annotations

import importlib.util
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
REPO = next(p for p in HERE.parents if (p / "som-rs" / "contracts" / "compiler-v1").is_dir())
SOM_RS = REPO / "som-rs"
RUST_TOOLS = SOM_RS / "tests" / "rust-output-v1"
EXAMPLES = SOM_RS / "contracts" / "compiler-v1" / "examples"
COVERAGE_CASES = RUST_TOOLS / "cases"


def contract_dir() -> pathlib.Path:
    draft = HERE.parent
    if (draft / "scope.json").is_file():
        return draft
    return SOM_RS / "contracts" / "typescript-output-v1"


CONTRACT = contract_dir()

sys.path.insert(0, str(HERE))
sys.path.append(str(RUST_TOOLS))  # rust_profile, imported by the Rust builder
import ts_profile  # type: ignore[import-not-found]  # noqa: E402

# The Rust builder shares its module name with build_negative_cases.py here, so load it by path.
_spec = importlib.util.spec_from_file_location("rust_build_negative_cases", RUST_TOOLS / "build_negative_cases.py")
assert _spec and _spec.loader
rust_cases = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rust_cases)

ts_profile.SCOPE = CONTRACT / "scope.json"

TARGET = '"target":"typescript"'

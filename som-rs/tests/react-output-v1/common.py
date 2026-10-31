"""Shared paths and helpers for the React output contract tools.

The tools live in som-rs/tests/react-output-v1 and the frozen contract in
som-rs/contracts/react-output-v1. The case helpers are reused from the frozen
Rust tools, which are read, never changed.
"""
from __future__ import annotations

import importlib.util
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
REPO = next(p for p in HERE.parents if (p / "som-rs" / "contracts" / "compiler-v1").is_dir())
SOM_RS = REPO / "som-rs"
RUST_TOOLS = SOM_RS / "tests" / "rust-output-v1"
TS_TOOLS = SOM_RS / "tests" / "typescript-output-v1"
EXAMPLES = SOM_RS / "contracts" / "compiler-v1" / "examples"
COVERAGE_CASES = RUST_TOOLS / "cases"

CONTRACT = SOM_RS / "contracts" / "react-output-v1"
EVIDENCE = SOM_RS / "evidence" / "react-output-v1"

sys.path.insert(0, str(HERE))
sys.path.append(str(RUST_TOOLS))  # rust_profile, imported by the Rust builder
import react_profile  # type: ignore[import-not-found]  # noqa: E402

# The Rust builder shares its module name with build_negative_cases.py here, so load it by path.
if '__som_native_active_controls__' in globals():
    from _som_native_active_controls_binding import controls as _native_controls
    _native_controls.verify_installed()
    if (_native_controls.role_sources.get('m6.api.host.rust-cases')
            != 'som-rs/tests/rust-output-v1/build_negative_cases.py'):
        raise ValueError('original Rust case source role required')
    rust_cases = _native_controls.load_module('m6.api.host.rust-cases')
else:
    _spec = importlib.util.spec_from_file_location("rust_build_negative_cases", RUST_TOOLS / "build_negative_cases.py")
    assert _spec and _spec.loader
    rust_cases = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(rust_cases)

react_profile.SCOPE = CONTRACT / "scope.json"

TARGET = '"target":"react"'
GENERATED = ("README.md", "src/schema.ts")


def fixed_files_sha256(files: dict[str, str]) -> str:
    """The SHA-256 of the canonical JSON {path: sha256} of the product files that are not generated."""
    import hashlib
    import json
    fixed = {path: digest for path, digest in files.items() if path not in GENERATED}
    return hashlib.sha256(json.dumps(fixed, sort_keys=True, separators=(",", ":")).encode()).hexdigest()

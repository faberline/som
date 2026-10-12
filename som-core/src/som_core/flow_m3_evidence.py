"""Shared M3 source and receipt guards. No model or planner is used here."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from . import flow_compact

ROOT = Path(__file__).resolve().parents[3]
SEAL_VERSION = "som.flow.m3-source-seal.v1"
RUNTIME_EXECUTABLES = (
    "som", "som-flow-codec", "som-flow-compact-verify", "som-flow-mask",
    "som-flow-model-trace", "som-flow-verify", "som-legacy-trace", "som-verify",
)


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def sources(dataset: Path) -> list[Path]:
    paths: set[Path] = set()
    for directory, pattern in (
        ("som-core/src/som_core", "*.py"),
        ("som-core/scripts", "*.py"),
        ("som-core/tests", "test_*flow*.py"),
        ("som-core/tests", "test_fixture*.py"),
        ("som-core/block_templates", "*.tpl"),
        ("som-rs/src", "*.rs"),
        ("som-rs/tests", "*.rs"),
        ("som-rs/contracts", "*"),
    ):
        paths.update(path for path in (ROOT / directory).rglob(pattern)
                     if path.is_file())
    paths.update(dataset / name for name in
                 ("train.jsonl", "dev.jsonl", "formal.jsonl", "manifest.json"))
    paths.update(ROOT / ('som-core/data/' + name) for name in (
        'composed/regression.jsonl', 'composed/heldout.jsonl', 'composed/blind.jsonl',
        'service-regression-v4/heldout.jsonl', 'flow-v7-r10/dev.jsonl', 'flow-v7-r10/formal.jsonl'))
    protected = ROOT / 'som-rs/evals/m2-flow-2026-10-04/r31'
    for directory in ('training-prerequisite-001', 'supplementary-acceptance-003'):
        for name in ('response.json', 'requirement.json', 'prompt.json', 'dsl.json'):
            paths.update((protected / directory).rglob(name))
    for group in ('services14', 'compositions120', 'blind80', 'legacy100'):
        paths.update((protected / ('legacy-generation-002/comparison/model/' + group)).glob('*.json'))
    paths.update({flow_compact.CATALOG,
                  ROOT / "som-code-python/data/blocks/flow-v7-compact/blocks.json",
                  ROOT / "som-rs/Cargo.toml", ROOT / "som-rs/Cargo.lock",
                  ROOT / "som-core/pyproject.toml", ROOT / "som-core/uv.lock",
                  ROOT / "som-rs/runtime/training-m3-requirements.txt",
                  ROOT / "som-rs/runtime/training-m3-test-requirements.txt",
                  ROOT / "som-rs/runtime/acceptance-m3-requirements.txt",
                  ROOT / "som-rs/runtime/acceptance-m3-lock.txt",
                  ROOT / "som-code-python/data/blocks/composed-quality/blocks.json",
                  ROOT / "som-code-python/data/blocks/v4/blocks.json"})
    model = ROOT / "som-rs/models/composed-quality-candidate"
    paths.update(model / name for name in
                 ("manifest.json", "ruff", "quality.json", "pyproject.toml",
                  "quality-contract.md", "quality-exceptions.json"))
    for saved in (model, ROOT / "som-rs/models/flow-m2-candidate/smoke-r31-001"):
        paths.update(path for path in saved.rglob("*") if path.is_file())
    return sorted(paths)


def hashes(dataset: Path) -> dict[str, str]:
    return {str(path.relative_to(ROOT)): digest(path)
            for path in sources(dataset)}


def case_evidence(directory: Path) -> list[Path]:
    """Bind products, observations and every checker log after cache cleanup."""
    excluded = {".venv", "__pycache__", "mypy-cache", "pylint-cache"}
    return sorted(path for path in directory.rglob("*") if path.is_file()
                  and not excluded.intersection(path.relative_to(directory).parts))


def verify_seal(path: Path, dataset: Path) -> dict:
    seal = json.loads(path.read_text())
    if (seal.get("version") != SEAL_VERSION
            or seal.get("source_unchanged") is not True
            or seal.get("dataset_sha256") != digest(dataset / "manifest.json")
            or seal.get("source_sha256") != hashes(dataset)):
        raise ValueError("M3 source or frozen data changed; use a new round")
    for name, wanted in {**seal.get("evidence_sha256", {}), **seal.get('runtime_sha256', {})}.items():
        target = ROOT / name
        if not target.resolve().is_relative_to(ROOT) or digest(target) != wanted:
            raise ValueError("M3 prerequisite evidence changed")
    return seal


def verify_runtime(seal: dict, selected: dict[str, Path]) -> None:
    """Reject a different executable, even when the sealed copy still exists."""
    approved = seal.get("runtime_sha256", {})
    for role, supplied in selected.items():
        target = supplied.resolve()
        if (role not in RUNTIME_EXECUTABLES or target.name != role
                or not target.is_relative_to(ROOT / "som-rs/runtime/versions")
                or str(target.relative_to(ROOT)) not in approved
                or digest(target) != approved[str(target.relative_to(ROOT))]):
            raise ValueError("M3 executable does not match its sealed runtime")


def verify_artifact_record(record: dict) -> None:
    """A generic pass flag cannot admit weights without actual tool results."""
    if (record.get("passed") is not True
            or record.get("source_unchanged") is not True
            or record.get("format_idempotent") is not True
            or record.get("byte_parity") is not True
            or record.get("mask_parity") is not True
            or record.get("normal_accepted") is not True):
        raise ValueError("M3 artifact integrity or complete parity failed")
    quality = record.get("quality", {})
    checks = quality.get("checks", {})
    if (quality.get("passed") is not True
            or quality.get("source_unchanged") is not True
            or quality.get("tool_versions") !=
            {"ruff": "0.16.6", "mypy": "2.3.1", "pylint": "4.0.9", "isort": "9.0.2"}
            or set(checks) != {"ruff", "format", "mypy", "pylint", "isort"}
            or any(row.get("exit_code") != 0 for row in checks.values())
            or quality.get("policy", {}).get("passed") is not True):
        raise ValueError("M3 strict five-tool product quality is incomplete")
    behavior = record.get("behavior", {})
    if (behavior.get("full_m2_acceptance") is not True
            or behavior.get("crud", {}).get("status") != "passed"
            or behavior.get("flow", {}).get("core_flow_passed") is not True
            or behavior.get("flow", {}).get("full_fault_matrix_passed") is not True):
        raise ValueError("M3 independent HTTP and fault matrix is incomplete")


def verify_receipt_hashes(record: dict, evidence: dict[str, str]) -> None:
    """Match the saved product and tool logs to their successful receipt."""
    directory = Path(record["evidence_directory"])
    if directory.is_absolute() or ".." in directory.parts:
        raise ValueError("M3 artifact receipt has an unsafe evidence path")
    products = record.get("source_sha256", {})
    if not products:
        raise ValueError("M3 artifact receipt is missing its product hashes")
    for name, wanted in products.items():
        relative = Path(name)
        if (relative.is_absolute() or ".." in relative.parts
                or evidence.get(str(directory / "product" / relative)) != wanted):
            raise ValueError("M3 saved product differs from its successful receipt")
    for tool, check in record["quality"]["checks"].items():
        for stream in ("stdout", "stderr"):
            wanted = check.get(f"{stream}_sha256")
            if not isinstance(wanted, str) or evidence.get(str(directory / "quality" / f"{tool}.{stream}")) != wanted:
                raise ValueError("M3 quality log differs from its successful receipt")

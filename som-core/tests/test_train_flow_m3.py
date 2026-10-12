"""M3 admission and interruption guards must work before weight allocation."""

import argparse
from copy import deepcopy
import json
from pathlib import Path
import sys

import pytest

from som_core import train_flow_m3
from som_core import flow_m3_evidence
from som_core import flow_codec
from som_core.flow_m3_evidence import digest, verify_artifact_record


def test_new_training_selects_semantic_input_without_changing_the_architecture() -> None:
    assert train_flow_m3.CONFIG["flow_input_policy"] == flow_codec.M3_BOUND_INPUT_VERSION
    assert {key: train_flow_m3.CONFIG[key] for key in
            ("n_layer", "n_embd", "n_head", "vocab_size", "n_ctx")} == {
        "n_layer": 2, "n_embd": 64, "n_head": 4, "vocab_size": 1024, "n_ctx": 4096,
    }


def test_runtime_arguments_cannot_replace_a_sealed_binary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(flow_m3_evidence, "ROOT", tmp_path)
    retained = tmp_path / "som-rs/runtime/versions/accepted"
    retained.mkdir(parents=True)
    accepted = retained / "som-flow-mask"
    accepted.write_bytes(b"accepted runtime")
    seal = {"runtime_sha256": {str(accepted.relative_to(tmp_path)): digest(accepted)}}
    flow_m3_evidence.verify_runtime(seal, {"som-flow-mask": accepted})
    another = tmp_path / "som-rs/runtime/versions/unsealed"
    another.mkdir()
    replacement = another / "som-flow-mask"
    replacement.write_bytes(accepted.read_bytes())
    with pytest.raises(ValueError, match="sealed runtime"):
        flow_m3_evidence.verify_runtime(seal, {"som-flow-mask": replacement})
    with pytest.raises(ValueError, match="sealed runtime"):
        flow_m3_evidence.verify_runtime(seal, {"som": accepted})
    accepted.write_bytes(b"changed executable")
    with pytest.raises(ValueError, match="sealed runtime"):
        flow_m3_evidence.verify_runtime(seal, {"som-flow-mask": accepted})


def test_saved_product_and_tool_logs_must_match_the_receipt() -> None:
    record = {"evidence_directory": "eval/case", "source_sha256": {"app.py": "source"},
              "quality": {"checks": {"mypy": {
                  "stdout_sha256": "output", "stderr_sha256": "errors"}}}}
    evidence = {"eval/case/product/app.py": "source",
                "eval/case/quality/mypy.stdout": "output",
                "eval/case/quality/mypy.stderr": "errors"}
    flow_m3_evidence.verify_receipt_hashes(record, evidence)
    for name in evidence:
        altered = dict(evidence, **{name: "changed"})
        with pytest.raises(ValueError, match="differs"):
            flow_m3_evidence.verify_receipt_hashes(record, altered)
    incomplete = deepcopy(record)
    del incomplete["quality"]["checks"]["mypy"]["stdout_sha256"]
    del evidence["eval/case/quality/mypy.stdout"]
    with pytest.raises(ValueError, match="quality log"):
        flow_m3_evidence.verify_receipt_hashes(incomplete, evidence)


def test_formal_cannot_enter_optimizer_or_checkpoint_selector() -> None:
    before = {name for name in sys.modules if name.startswith("mlx")}
    with pytest.raises(ValueError, match="cannot read formal"):
        train_flow_m3.examples(Path("/missing/formal.jsonl"), 300)
    assert {name for name in sys.modules if name.startswith("mlx")} == before


def test_missing_admission_does_not_import_mlx_or_make_output(
    tmp_path: Path,
) -> None:
    args = argparse.Namespace(
        data=tmp_path / "train.jsonl", dev=tmp_path / "dev.jsonl",
        admission=None, seal=None, legacy=None, blocks=None,
        output=tmp_path / "candidate",
    )
    before = {name for name in sys.modules if name.startswith("mlx")}
    with pytest.raises(ValueError, match="M3 needs"):
        train_flow_m3.run(args)
    assert not args.output.exists()
    assert {name for name in sys.modules if name.startswith("mlx")} == before


def test_only_owned_checkpoint_can_be_saved_after_initial_admission(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(train_flow_m3, "ROOT", tmp_path)
    root = tmp_path / "som-rs/models/flow-m3-candidate"
    root.mkdir(parents=True)
    data = tmp_path / "data"
    data.mkdir()
    (data / "manifest.json").write_text("sealed dataset")
    admission = tmp_path / "admission.json"
    admission.write_text("sealed admission")
    args = argparse.Namespace(output=root / "fresh", templates=None,
                              admission=admission, data=data / "train.jsonl")
    train_flow_m3.output_guard(args)
    args.output.mkdir()
    (args.output / "admission.json").write_bytes(admission.read_bytes())
    (args.output / "dataset.json").write_bytes((data / "manifest.json").read_bytes())
    with pytest.raises(ValueError, match="fresh"):
        train_flow_m3.output_guard(args)
    train_flow_m3.output_guard(args, saving=True)
    (args.output / "dataset.json").write_text("different admission")
    with pytest.raises(ValueError, match="belongs"):
        train_flow_m3.output_guard(args, saving=True)


def test_current_and_symlink_cannot_bypass_model_protection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(train_flow_m3, "ROOT", tmp_path)
    models = tmp_path / "som-rs/models"
    candidate = models / "flow-m3-candidate"
    candidate.mkdir(parents=True)
    (models / "current").symlink_to(candidate)
    outside = tmp_path / "outside"
    outside.mkdir()
    (candidate / "alias").symlink_to(outside)
    for target in (models / "current/new", candidate / "alias/new", outside / "new"):
        with pytest.raises(ValueError, match="fresh"):
            train_flow_m3.output_guard(argparse.Namespace(output=target, templates=None))
        assert not target.exists()


@pytest.mark.parametrize("duration", [-1, float("nan"), float("inf"), True, "zero"])
def test_malformed_budget_cannot_reserve_training(
    duration: object, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    ledger = tmp_path / "budget.json"
    monkeypatch.setattr(train_flow_m3, "BUDGET", ledger)
    initial = {"version": "som.flow.m3-training-time.v1", "limit_seconds": train_flow_m3.TIME_LIMIT,
               "attempts": [{"reserved_seconds": duration, "elapsed_seconds": None}]}
    ledger.write_text(json.dumps(initial))
    with pytest.raises(ValueError, match="duration"):
        with train_flow_m3.training_budget(1):
            pytest.fail("invalid budget allocated training")
    assert json.loads(ledger.read_text()) == initial or duration != duration


def test_budget_counts_unfinished_attempt_without_reset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    ledger = tmp_path / "budget.json"
    monkeypatch.setattr(train_flow_m3, "BUDGET", ledger)
    ledger.write_text(json.dumps({"version": "som.flow.m3-training-time.v1",
        "limit_seconds": train_flow_m3.TIME_LIMIT,
        "attempts": [{"reserved_seconds": train_flow_m3.TIME_LIMIT - 5, "elapsed_seconds": None}]}))
    with train_flow_m3.training_budget(10) as allowance:
        assert allowance == 5
    assert len(json.loads(ledger.read_text())["attempts"]) == 2


def test_generic_or_partial_pass_receipt_cannot_admit_weights() -> None:
    valid = {"passed": True, "source_unchanged": True, "format_idempotent": True,
        "byte_parity": True, "mask_parity": True, "normal_accepted": True,
        "quality": {"passed": True, "source_unchanged": True,
            "tool_versions": {"ruff": "0.16.6", "mypy": "2.3.1", "pylint": "4.0.9", "isort": "9.0.2"},
            "checks": {name: {"exit_code": 0} for name in ("ruff", "format", "mypy", "pylint", "isort")},
            "policy": {"passed": True}},
        "behavior": {"full_m2_acceptance": True, "crud": {"status": "passed"},
            "flow": {"core_flow_passed": True, "full_fault_matrix_passed": True}}}
    verify_artifact_record(valid)
    with pytest.raises(ValueError):
        verify_artifact_record({"passed": True})
    for missing in ("mypy", "pylint", "isort"):
        value = deepcopy(valid)
        del value["quality"]["checks"][missing]
        with pytest.raises(ValueError, match="five-tool"):
            verify_artifact_record(value)
    value = deepcopy(valid)
    value["behavior"]["flow"]["full_fault_matrix_passed"] = False
    with pytest.raises(ValueError, match="fault matrix"):
        verify_artifact_record(value)

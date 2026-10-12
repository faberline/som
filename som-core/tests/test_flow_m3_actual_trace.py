"""The acceptance trace must cover the emitted order and reject substitutions."""

from copy import deepcopy
import json
from pathlib import Path

import pytest

from m3_artifact_checks import selected_sequence
from som_core import flow_codec, flow_compact, flow_m3_target
from som_core.v1 import V1Error
from verify_flow_masks import tokens, trace
from verify_m3_selected_path import loader_refused, native_health_contract

ROOT = Path(__file__).resolve().parents[2]


def test_actual_flow_first_order_is_preserved_in_prefix_evidence():
    row = json.loads((ROOT / "som-core/data/flow-m3-r10/dev.jsonl").read_text().splitlines()[0])
    prompt, plan = row["prompt"], row["dsl"]
    actual = flow_m3_target.token_ids(flow_m3_target.encode(prompt, plan, flow_compact.metadata()))
    observed = selected_sequence(prompt, plan, actual)
    assert observed == actual and observed != tokens(prompt, plan)
    checked = trace(prompt, observed)
    assert checked["complete"] and checked["root"] == plan
    assert selected_sequence(prompt, plan, None) == tokens(prompt, plan)


def test_actual_choice_or_end_token_cannot_be_replaced_by_reference_dsl():
    row = json.loads((ROOT / "som-core/data/flow-m3-r10/dev.jsonl").read_text().splitlines()[0])
    prompt, plan = row["prompt"], row["dsl"]
    actual = flow_m3_target.token_ids(flow_m3_target.encode(prompt, plan, flow_compact.metadata()))
    different = deepcopy(plan)
    different["flow"]["result"] = {}
    with pytest.raises(ValueError, match="differ"):
        selected_sequence(prompt, different, actual)
    with pytest.raises(V1Error):
        selected_sequence(prompt, plan, actual[:-1])
    for invalid in ([], [True], [len(flow_codec.TOKENS)], [-1]):
        with pytest.raises(ValueError, match="invalid token ID"):
            selected_sequence(prompt, plan, invalid)


def test_native_loader_code_is_read_from_its_actual_error_format():
    assert loader_refused("", "MODEL_UNAVAILABLE: unknown flow training profile\n", 1)
    assert loader_refused('{"status":"error","code":"MODEL_UNAVAILABLE"}', "", 2)


def test_missing_loader_code_or_success_exit_is_not_a_refusal_pass():
    assert not loader_refused("", "MODEL_UNAVAILABLE: wrong bytes\n", 0)
    assert not loader_refused("", "panic: model path contains MODEL_UNAVAILABLE", 1)
    assert not loader_refused('{"status":"ok","code":"MODEL_UNAVAILABLE"}', "", 2)
    assert not loader_refused("", "MODEL_FAILED: wrong bytes\n", 1)


def test_native_health_uses_the_public_formatter_contract():
    expected = native_health_contract("model-hash", "blocks-hash", 1000)
    assert expected["formatter_version"] == "ruff 0.16.6"
    assert expected["flow_model_purpose"] == "m3-supervised-composition"
    assert expected["flow_decode_policy"] == "som.flow.constrained-decoding.v1"
    assert expected["flow_input_policy"] == "som.flow.m3-input.v3"
    assert expected["model_manifest_sha256"] == "model-hash"
    assert expected["blocks_sha256"] == "blocks-hash"
    assert expected["training_steps"] == 1000

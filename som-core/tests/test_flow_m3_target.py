"""Changing M3 target order must preserve all model choices and M2 bytes."""

from collections import Counter
from copy import deepcopy
import json
from pathlib import Path

import pytest

from som_core import flow_codec, flow_compact, flow_m3_target, train_flow_m3
from som_core.flow_constraints import State
from som_core.v1 import V1Error

ROOT = Path(__file__).resolve().parents[2]


def test_flow_first_preserves_complete_dsl_tokens_and_prefix_legality():
    metadata = flow_compact.metadata()
    rows = [json.loads(line) for line in
            (ROOT / "som-core/data/flow-m3-r10/dev.jsonl").read_text().splitlines()[:12]]
    for row in rows:
        prompt, plan = row["prompt"], row["dsl"]
        original = flow_codec.encode(prompt, plan, metadata)
        target = flow_m3_target.encode(prompt, plan, metadata)
        assert target != original
        assert Counter(flow_m3_target.token_ids(target)) == Counter(flow_m3_target.token_ids(original))
        assert flow_codec.decode(prompt, target, metadata) == plan
        assert list(flow_codec.decode(prompt, target, metadata)) == list(flow_m3_target.ROOT_KEYS)
        assert list(flow_codec.decode(prompt, original, metadata)) == ["files", "flow", "version"]
        state = State(prompt)
        for token in flow_m3_target.token_ids(target):
            assert token in state.allowed()
            state.advance(token)
        assert state.complete and state.root == plan


def test_flow_first_does_not_repair_an_invalid_reference():
    row = json.loads((ROOT / "som-core/data/flow-m3-r10/dev.jsonl").read_text().splitlines()[0])
    plan = deepcopy(row["dsl"])
    plan["flow"]["result"]["invalid"] = {"step": "missing", "field": "id"}
    with pytest.raises(V1Error):
        flow_m3_target.encode(row["prompt"], plan, flow_compact.metadata())


def test_target_contract_and_candidate_config_have_one_version():
    policy = flow_m3_target.policy()
    assert policy["version"] == flow_m3_target.VERSION == train_flow_m3.CONFIG["flow_output_policy"]
    assert policy["root_keys"] == list(flow_m3_target.ROOT_KEYS)
    assert policy["codec_version"] == flow_codec.COMPACT_VERSION
    assert policy["output_limit"] == flow_codec.LIMIT
    assert "target-policy.json" in train_flow_m3.SIDECARS

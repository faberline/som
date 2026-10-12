"""Model inputs must retain operation meanings without per-prompt legends."""

from copy import deepcopy
import json
from pathlib import Path
import re

import pytest

from som_core import flow, flow_codec, flow_compact
from som_core.flow_m3_data import fingerprints
from som_core.v1 import V1Error, load_blocks

ROOT = Path(__file__).resolve().parents[2]


def fixtures():
    return [json.loads(line)["prompt"] for line in
            (ROOT / "som-core/data/flow-v7-r10/dev.jsonl")
            .read_text().splitlines()]


def colliding_requirements():
    first = deepcopy(fixtures()[0])
    first["capabilities"].update(
        workflow="json_export", value_field=None, group_field=None,
    )
    first["flow"] = {
        "name": "inspect",
        "inputs": [{"name": "identifier", "type": "integer"}],
        "steps": [
            {"name": "access", "op": "authorize", "args": {}, "undo": None},
            {"name": "read", "op": "child.list", "undo": None,
             "args": {"parent_id": {"input": "identifier"}}},
        ],
        "result": {"value": {"input": "identifier"}},
    }
    second = deepcopy(first)
    second["flow"]["steps"][1]["op"] = "workflow.run"
    return first, second


def test_distinct_required_operations_have_distinct_model_inputs():
    first, second = colliding_requirements()
    metadata = flow_compact.metadata()
    library = load_blocks(ROOT / "som-code-python/data/blocks/flow-v7-compact")
    for prompt in (first, second):
        flow.validate(prompt)
    assert flow_codec.features_m3(first, metadata) == flow_codec.features_m3(second, metadata)
    assert flow_compact.reference(first, library) != flow_compact.reference(second, library)
    assert flow_codec.features_m3_semantic(first, metadata) != flow_codec.features_m3_semantic(second, metadata)
    assert fingerprints(first)["input"] != fingerprints(second)["input"]
    assert (fingerprints(first, input_policy=flow_codec.M3_INPUT_VERSION)["input"]
            == fingerprints(second, input_policy=flow_codec.M3_INPUT_VERSION)["input"])


def test_every_model_visible_semantic_fact_round_trips_without_the_prompt():
    metadata = flow_compact.metadata()
    for prompt in [*fixtures(), *colliding_requirements()]:
        text = flow_codec.features_m3_semantic(prompt, metadata)
        assert flow_codec.decode_m3_semantic_view(text, metadata) == flow.semantic_view(prompt)
        assert text.count("<") <= 2048
        assert text.endswith("<plan>")


def test_unknown_scalars_keep_spelling_type_and_unicode():
    metadata = flow_compact.metadata()
    values = flow_codec.semantic_pool(metadata)
    value = {"custom": "字串\n\"", "number": 1, "boolean": True,
             "nested": [0.1, None, "parent.f4", "parent.f3"]}
    text = "<input>" + "".join(flow_codec.serialize_semantic(value, values)) + "<list></list><plan>"
    restored = flow_codec.decode_m3_semantic_view(text, metadata)
    assert restored == value
    assert type(restored["number"]) is int
    assert type(restored["boolean"]) is bool
    assert all(token in flow_codec.TOKENS for token in
               re.findall(r"<[^<>\s]+>", text))


def test_unknown_policy_does_not_silently_select_an_archived_encoder():
    with pytest.raises(V1Error, match="unknown M3 input policy"):
        flow_codec.m3_features(fixtures()[0], input_policy="unknown")


def test_compound_destinations_cannot_alias_a_string_literal():
    with (ROOT / "som-core/data/flow-m3-r9/train.jsonl").open() as source:
        first = deepcopy(json.loads(source.readline())["prompt"])
    first["flow"] = {
        "name": "inspect",
        "inputs": [{"name": "parent_data", "type": "parent_input"},
                   {"name": "children", "type": "children_input"}],
        "steps": [
            {"name": "n0", "op": "authorize", "args": {}, "undo": None},
            {"name": "n1", "op": "transaction", "steps": [
                {"name": "n2", "op": "batch.create", "undo": "delete_created",
                 "args": {"parent": {"input": "parent_data"},
                          "children": {"input": "children"}}}]}],
        "result": {"body": {"literal": "parent.title", "type": "string"},
                   "data": {"step": "n2", "field": "parent.title"},
                   "size": {"step": "n2", "field": "parent.memo"}},
    }
    second = deepcopy(first)
    second["flow"]["result"]["body"]["literal"] = "parent.memo"
    metadata = flow_compact.metadata()
    library = load_blocks(ROOT / "som-code-python/data/blocks/flow-v7-compact")
    targets = [flow_codec.encode(prompt, flow_compact.reference(prompt, library), metadata)
               for prompt in (first, second)]
    assert targets[0] != targets[1]
    assert flow_codec.features_m3_semantic(first, metadata) == flow_codec.features_m3_semantic(second, metadata)
    assert flow_codec.features_m3_bound(first, metadata) != flow_codec.features_m3_bound(second, metadata)
    for prompt in (first, second):
        assert flow_codec.decode_m3_semantic_view(flow_codec.features_m3_bound(prompt, metadata), metadata) == flow.semantic_view(prompt)


@pytest.mark.parametrize("body", [
    "<v:1016>",
    "<list><v:759><v:759></list>",
    "<list><v:759><v:1015></list>",
    "<list><v:759><v:808></list>",
])
def test_invalid_or_noncanonical_semantic_scalars_are_rejected(body):
    text = "<input><map>" + body + "<v:0></map><list></list><plan>"
    with pytest.raises(V1Error):
        flow_codec.decode_m3_semantic_view(text, flow_compact.metadata())

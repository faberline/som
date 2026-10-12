"""Contract masks admit alternatives and cannot repair wrong choices."""

from copy import deepcopy
import json
from pathlib import Path

import pytest

from som_core import composed, flow, flow_codec, flow_compact
from som_core.flow_constraints import State
from som_core.v1 import V1Error, load_blocks

ROOT = Path(__file__).resolve().parents[2]
ROWS = [json.loads(line) for name in ("dev", "formal")
        for line in (ROOT / "som-core/data/flow-v7-r10" / (name + ".jsonl")).read_text().splitlines()]
LIBRARY = load_blocks(ROOT / "som-code-python/data/blocks/flow-v7-compact")
IDS = {token: index for index, token in enumerate(flow_codec.TOKENS)}


def accept(prompt, plan, reverse=False):
    state = State(prompt)
    def emit(value):
        if isinstance(value, dict):
            state.advance(2)
            items = list(value.items())
            for key, entry in reversed(items) if reverse else sorted(items):
                emit(key)
                emit(entry)
            state.advance(3)
        elif isinstance(value, list):
            state.advance(4)
            for item in value: emit(item)
            state.advance(5)
        else:
            state.advance(state.ids[composed.canonical(value)])
    emit(plan)
    state.advance(1)
    assert state.complete and state.root == plan
    return state


@pytest.mark.parametrize("row", ROWS, ids=[row["id"] for row in ROWS])
def test_all_frozen_m2_sequences_remain_legal(row):
    accept(row["prompt"], flow_compact.reference(row["prompt"], LIBRARY))


def test_map_order_is_not_a_canonical_answer_constraint():
    row = ROWS[0]
    accept(row["prompt"], flow_compact.reference(row["prompt"], LIBRARY), reverse=True)


def test_checked_block_is_a_legal_model_alternative():
    row = ROWS[0]
    standard = flow_compact.reference(row["prompt"], LIBRARY)
    checked = flow_compact.reference(row["prompt"], LIBRARY, "checked")
    assert standard != checked
    accept(row["prompt"], standard)
    accept(row["prompt"], checked)


def test_grammar_rejects_unknown_keys_without_repair():
    state = State(ROWS[0]["prompt"])
    state.advance(2)
    with pytest.raises(V1Error):
        state.advance(state.ids[composed.canonical("database")])
    assert state.tokens == 1


def test_invalid_complete_semantics_are_not_filled_in():
    row = ROWS[0]
    plan = flow_compact.reference(row["prompt"], LIBRARY)
    wrong = deepcopy(plan)
    wrong["flow"]["steps"] = wrong["flow"]["steps"][:1]
    with pytest.raises(V1Error):
        accept(row["prompt"], wrong)


def test_type_valid_business_error_can_end_and_then_fails_final_validation():
    row = ROWS[0]
    plan = flow_compact.reference(row["prompt"], LIBRARY)
    wrong = deepcopy(plan)
    wrong["flow"]["steps"] = wrong["flow"]["steps"][:1]
    wrong["flow"]["result"] = {}
    state = accept(row["prompt"], wrong)
    assert state.complete and state.root == wrong
    with pytest.raises(V1Error, match="semantic obligations"):
        flow.validate_plan(row["prompt"], state.root, flow_compact.metadata())

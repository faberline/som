"""Unused inputs and declaration order cannot prove new compositions."""

from copy import deepcopy
import json
from pathlib import Path

from som_core.flow_m3_data import composition_view, fingerprints

ROOT = Path(__file__).resolve().parents[2]


def prompt() -> dict:
    return json.loads((ROOT / "som-core/data/flow-v7-r10/dev.jsonl")
                      .read_text().splitlines()[0])["prompt"]


def test_unused_input_does_not_create_a_new_operation_graph() -> None:
    original = prompt()
    altered = deepcopy(original)
    altered["flow"]["inputs"].append({"name": "unused", "type": "integer"})
    before, after = fingerprints(original), fingerprints(altered)
    assert before["graph"] == after["graph"]
    assert before["input"] != after["input"]
    assert composition_view(original) == composition_view(altered)


def test_reordered_input_declarations_do_not_change_the_operation_graph() -> None:
    original = prompt()
    altered = deepcopy(original)
    altered["flow"]["inputs"].reverse()
    assert fingerprints(original)["graph"] == fingerprints(altered)["graph"]


def test_canonical_json_key_order_preserves_the_graph() -> None:
    original = prompt()
    saved = json.loads(json.dumps(original, sort_keys=True))
    assert fingerprints(original)["graph"] == fingerprints(saved)["graph"]

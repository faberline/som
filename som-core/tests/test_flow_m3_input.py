"""The M3 input view changes positions, never facts or selected plans."""

from collections import Counter
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import re

from som_core import flow_codec, flow_compact
from som_core.v1 import load_blocks

ROOT = Path(__file__).resolve().parents[2]
TOKENS = re.compile(r"<[^<>\s]+>")


def fixtures() -> list[dict]:
    return [json.loads(line) for line in
            (ROOT / "som-core/data/flow-v7-r10/dev.jsonl")
            .read_text().splitlines()]


def test_all_old_input_bytes_and_output_tokens_are_preserved() -> None:
    source = ROOT / (
        "som-rs/evals/m3-flow-2026-10-05/r4/predecessor-source-001/"
        "som-core/src/som_core/flow_codec.py"
    )
    spec = importlib.util.spec_from_file_location("som_core._archived_codec", source)
    assert spec is not None and spec.loader is not None
    archived = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(archived)
    metadata = flow_compact.metadata()
    library = load_blocks(ROOT / "som-code-python/data/blocks/flow-v7-compact")
    for row in fixtures():
        prompt = row["prompt"]
        dsl = flow_compact.reference(prompt, library)
        original = flow_codec.features(prompt, metadata)
        assert original == archived.features(prompt, metadata)
        assert flow_codec.encode(prompt, dsl, metadata) == archived.encode(prompt, dsl, metadata)
        new = flow_codec.features_m3(prompt, metadata)
        assert Counter(TOKENS.findall(new)) == Counter(TOKENS.findall(original))
        assert new != original
        assert new.endswith("<plan>")


def test_operation_order_is_visible_at_a_stable_input_position() -> None:
    metadata = flow_compact.metadata()
    for row in fixtures():
        prompt = row["prompt"]
        values = flow_codec.pool(prompt, normalized=True, metadata=metadata)
        expected = flow_codec.serialize(prompt["operations"][0]["action"], values)[0]
        original = TOKENS.findall(flow_codec.features_m3(prompt, metadata))
        assert original[21] == expected
        changed = deepcopy(prompt)
        changed["operations"][0:2] = reversed(changed["operations"][0:2])
        other = TOKENS.findall(flow_codec.features_m3(changed, metadata))
        assert other != original
        assert Counter(other) == Counter(original)

"""M2 fixture preparation and failed-admission checks allocate no weights."""

import argparse
import json
import sys
from pathlib import Path
from uuid import uuid4

import pytest
from som_core import flow_codec, train_flow


def options() -> argparse.Namespace:
    return argparse.Namespace(
        seed=17,
        context=4096,
        width=64,
        heads=4,
        layers=2,
        vocab_size=1024,
        max_new_tokens=2048,
        save_every=1000,
        templates=None,
        steps=1000,
        hours=1.0,
        admission=Path("/missing-admission"),
        seal=Path("/missing-seal"),
        legacy=train_flow.LEGACY,
        data=train_flow.DEVELOPMENT,
        dev=None,
        blocks=train_flow.BLOCKS,
        output=train_flow.ROOT
        / "som-rs/models/flow-m2-candidate"
        / str(uuid4()),
    )


def test_frozen_development_round_trips_with_exact_rust_vocabulary() -> None:
    rows = train_flow.examples(train_flow.DEVELOPMENT, train_flow.BLOCKS)
    codec = train_flow.tokenizer()
    pairs = train_flow.token_pairs(rows, codec)
    assert len(rows) == len(pairs) == 12
    assert len({row.source for row in rows}) == 12
    assert codec.get_vocab_size() == 1024
    assert codec.get_vocab() == {
        token: index for index, token in enumerate(flow_codec.TOKENS)
    }
    assert all(max(len(left), len(right)) <= 2048 for left, right in pairs)
    assert all(
        codec.decode(right, skip_special_tokens=False) == row.target
        for row, (_, right) in zip(rows, pairs, strict=True)
    )


def test_formal_fixture_bytes_cannot_enter_the_training_pairs() -> None:
    with pytest.raises(ValueError, match="frozen development"):
        train_flow.examples(
            train_flow.DEVELOPMENT.parent / "formal.jsonl", train_flow.BLOCKS
        )


@pytest.mark.parametrize(
    "source,target",
    [
        ("<unknown>", "<eos>"),
        ("<input>", "<pad><eos>"),
        ("<input>", "<map>"),
        ("<input>" * 2049, "<eos>"),
        ("<input>", "<map>" * 2048 + "<eos>"),
    ],
)
def test_unknown_incomplete_and_excess_sequences_fail(
    source: str, target: str
) -> None:
    with pytest.raises(ValueError, match="invalid smoke sequence"):
        train_flow.token_pairs(
            [train_flow.Example("invalid", source, target)],
            train_flow.tokenizer(),
        )


@pytest.mark.parametrize(
    "key,value",
    [
        ("seed", 42),
        ("context", 256),
        ("width", 128),
        ("heads", 2),
        ("layers", 3),
        ("vocab_size", 256),
        ("max_new_tokens", 4096),
        ("save_every", 2000),
        ("hours", 169),
        ("hours", float("nan")),
        ("steps", -1),
        ("output", train_flow.ROOT / "som-rs/models/current/new-model"),
        ("output", train_flow.LEGACY),
        ("legacy", train_flow.ROOT / "som-rs/models/current"),
        ("dev", train_flow.DEVELOPMENT.parent / "formal.jsonl"),
    ],
)
def test_architecture_inputs_and_protected_outputs_are_fixed(
    key: str, value: object
) -> None:
    args = options()
    setattr(args, key, value)
    with pytest.raises(ValueError):
        train_flow.validate_options(args)


def test_failed_admission_stops_before_sdk_import_or_output(
    tmp_path: Path,
) -> None:
    args = options()
    args.admission = tmp_path / "admission.json"
    args.seal = tmp_path / "seal.json"
    args.admission.write_text(json.dumps({"size_quality_passed": False}))
    args.seal.write_text("{}")
    before = {name for name in sys.modules if name.startswith("mlx")}
    with pytest.raises(ValueError, match="size and quality admission"):
        train_flow.run(args)
    assert not args.output.exists()
    assert {name for name in sys.modules if name.startswith("mlx")} == before


def test_cumulative_budget_counts_prior_and_unfinished_attempts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ledger = tmp_path / "budget.json"
    monkeypatch.setattr(train_flow, "BUDGET", ledger)
    ledger.write_text(
        json.dumps(
            {
                "version": "som.flow.training-time.v1",
                "limit_seconds": train_flow.TIME_LIMIT,
                "attempts": [
                    {"reserved_seconds": 5, "elapsed_seconds": 3},
                    {
                        "reserved_seconds": train_flow.TIME_LIMIT - 8,
                        "elapsed_seconds": None,
                    },
                ],
            }
        )
    )
    with train_flow.training_budget(10) as allowance:
        assert allowance == 5
    saved = json.loads(ledger.read_text())
    assert len(saved["attempts"]) == 3
    assert saved["attempts"][-1]["elapsed_seconds"] >= 0
    assert saved["attempts"][-1]["reserved_seconds"] == 5


def test_exhausted_budget_cannot_start_an_attempt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ledger = tmp_path / "budget.json"
    monkeypatch.setattr(train_flow, "BUDGET", ledger)
    ledger.write_text(
        json.dumps(
            {
                "version": "som.flow.training-time.v1",
                "limit_seconds": train_flow.TIME_LIMIT,
                "attempts": [
                    {
                        "reserved_seconds": train_flow.TIME_LIMIT,
                        "elapsed_seconds": None,
                    }
                ],
            }
        )
    )
    with pytest.raises(ValueError, match="exhausted"):
        with train_flow.training_budget(1):
            pytest.fail("exhausted budget entered the training body")

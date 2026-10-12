"""Train M2 seen-input weights only after source, size and quality admission.

The twelve frozen development fixtures are both the training and smoke set.
Formal fixtures never enter the optimizer or checkpoint selection.
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import math
import random
import shutil
import signal
import time
from collections import defaultdict
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from itertools import product
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import uuid4

from . import composed, flow, flow_codec, flow_compact, quality
from .v1 import load_blocks

if TYPE_CHECKING:
    from tokenizers import Tokenizer

ROOT = Path(__file__).resolve().parents[3]
DEVELOPMENT = ROOT / "som-core/data/flow-v7-r10/dev.jsonl"
BLOCKS = ROOT / "som-code-python/data/blocks/flow-v7-compact/blocks.json"
LEGACY = ROOT / "som-rs/models/composed-quality-candidate"
PURPOSE = "m2-development-fixture-smoke"
ADMISSION_VERSION = "som.flow.m2-training-admission.v2"
BUDGET_POLICY = ROOT / "som-rs/contracts/m2-output-budget.v2.json"
LIMITS = {
    "files": 12,
    "blocks": 64,
    "bytes": "observed",
    "input_tokens": 2048,
    "output_tokens": 2048,
}
TIME_LIMIT = 168 * 3600
BUDGET = ROOT / "som-rs/models/flow-m2-candidate/training-budget.json"
CONFIG = {
    "model_type": "gpt2",
    "output_mode": "flow_plan",
    "n_ctx": 4096,
    "n_positions": 4096,
    "n_embd": 64,
    "n_head": 4,
    "n_layer": 2,
    "vocab_size": 1024,
    "max_new_tokens": 2048,
    "layer_norm_epsilon": 1e-5,
}
SIDECARS = (
    "contracts.json",
    "contract.md",
    "codec.json",
    "admission.json",
    "development.jsonl",
    "quality.json",
    "ruff",
    "pyproject.toml",
    "quality-contract.md",
    "quality-exceptions.json",
    "output-budget.json",
)


@dataclass(frozen=True)
class Example:
    """One complete public input and reference structural output."""

    identity: str
    source: str
    target: str


def digest(path: Path) -> str:
    """Hash file contents, never metadata or a directory name."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def json_object(path: Path) -> dict[str, object]:
    """Reject non-object JSON at the tool boundary."""
    value: object = json.loads(path.read_text())
    if not isinstance(value, dict) or not all(
        isinstance(k, str) for k in value
    ):
        raise ValueError(f"expected JSON object: {path}")
    return value


def verify_files(value: object, label: str) -> None:
    """Verify a nonempty receipt against files inside this checkout."""
    if not isinstance(value, dict) or not value:
        raise ValueError(f"missing {label} file hashes")
    for name, expected in value.items():
        if not isinstance(name, str) or not isinstance(expected, str):
            raise ValueError(f"invalid {label} file hash")
        path = ROOT / name
        if (
            Path(name).is_absolute()
            or ".." in Path(name).parts
            or not path.resolve().is_relative_to(ROOT)
            or not path.is_file()
            or digest(path) != expected
        ):
            raise ValueError(f"stale or unsafe {label} file: {name}")


def verify_admission(admission_path: Path, seal_path: Path) -> None:
    """Fail before importing training SDKs or creating an output directory."""
    admission = json_object(admission_path)
    required = {
        "version": ADMISSION_VERSION,
        "size_quality_passed": True,
        "formal_cases": 114,
        "development_cases": 12,
        "limits": LIMITS,
        "development_sha256": digest(DEVELOPMENT),
        "blocks_sha256": digest(BLOCKS),
        "catalog_sha256": digest(flow_compact.CATALOG),
        "contract_sha256": flow.contract_hash(),
        "codec_version": flow_codec.COMPACT_VERSION,
        "source_seal_sha256": digest(seal_path),
        "output_budget_sha256": digest(BUDGET_POLICY),
    }
    if any(admission.get(key) != value for key, value in required.items()):
        raise ValueError("M2 size and quality admission is missing or stale")
    seal = json_object(seal_path)
    if (
        seal.get("version") != "som.flow.compact-source-seal.v1"
        or seal.get("counts") != {"dev": 12, "formal": 114}
        or seal.get("source_unchanged") is not True
        or seal.get("actual_input_overlap") != 0
        or seal.get("structural_overlap") != 0
        or seal.get("limits") != LIMITS
        or seal.get("codec_version") != flow_codec.COMPACT_VERSION
    ):
        raise ValueError("M2 source and input seal is incomplete")
    verify_files(seal.get("source_sha256"), "source")
    verify_files(admission.get("evidence_sha256"), "size and quality evidence")
    report_name = admission.get("size_quality_report")
    evidence = admission.get("evidence_sha256")
    if not isinstance(report_name, str) or not isinstance(evidence, dict):
        raise ValueError(
            "M2 admission lacks the complete size and quality report"
        )
    if report_name not in evidence:
        raise ValueError("M2 size and quality report is not hash-bound")
    report = json_object(ROOT / report_name)
    if (
        report.get("stage") != "m2_size_quality_training_prerequisite"
        or report.get("passed") is not True
        or report.get("source_unchanged") is not True
        or report.get("required") != report.get("accepted")
        or report.get("required") != 218
        or report.get("formal_cases") != 114
        or report.get("development_cases") != 12
        or report.get("maximum_cases") != 90
        or report.get("alternative_cases") != 2
    ):
        raise ValueError("M2 training requires every size and quality case")
    records = report.get("records")
    if (
        not isinstance(records, list)
        or len(records) != 218
        or any(
            not isinstance(row, dict)
            or row.get("normal_accepted") is not True
            or row.get("passed") is not True
            or row.get("format_idempotent") is not True
            for row in records
        )
    ):
        raise ValueError("M2 admission lacks complete normal artifact results")
    expected_ids = {
        json.loads(line)["id"]
        for split in ("dev", "formal")
        for line in (DEVELOPMENT.parent / (split + ".jsonl"))
        .read_text()
        .splitlines()
    }
    expected_ids.update(
        "maximum-" + "-".join(items)
        for items in product(
            ("postgresql", "mongodb"),
            ("jwt", "firebase", "google_oauth"),
            ("none", "s3", "gcs"),
            (
                "json_export",
                "gzip_export",
                "csv",
                "decimal_summary",
                "statistics",
            ),
        )
    )
    expected_ids.update({"alternative-standard", "alternative-checked"})
    if {row.get("id") for row in records} != expected_ids:
        raise ValueError("M2 admission repeats or omits a required artifact")
    for row in records:
        for key, limit in (
            ("files", 12),
            ("blocks", 64),
            ("input_tokens", 2048),
            ("output_tokens", 2048),
        ):
            value = row.get(key)
            if type(value) is not int or not 0 < value <= limit:
                raise ValueError("M2 artifact budget is missing or exceeded")
        if type(row.get("formatted_bytes")) is not int or row["formatted_bytes"] <= 0:
            raise ValueError("M2 artifact lacks a final UTF-8 size observation")
        quality_name = row.get("quality_report")
        if not isinstance(quality_name, str) or quality_name not in evidence:
            raise ValueError("M2 artifact lacks hash-bound quality evidence")
        check = json_object(ROOT / quality_name)
        checks = check.get("checks")
        if (
            check.get("passed") is not True
            or check.get("source_unchanged") is not True
            or check.get("tool_versions")
            != {
                "ruff": "0.16.6",
                "mypy": "2.3.1",
                "pylint": "4.0.9",
                "isort": "9.0.2",
            }
            or not isinstance(checks, dict)
            or set(checks) != {"ruff", "format", "mypy", "pylint", "isort"}
            or any(
                not isinstance(value, dict) or value.get("exit_code") != 0
                for value in checks.values()
            )
        ):
            raise ValueError(
                "M2 artifact did not pass every fixed quality tool"
            )


@contextmanager
def training_budget(requested: float) -> Iterator[float]:
    """Account all attempts and reserve interrupted attempts conservatively."""
    if not math.isfinite(requested) or not 0 < requested <= TIME_LIMIT:
        raise ValueError("training time must be within the one-week budget")
    BUDGET.parent.mkdir(parents=True, exist_ok=True)
    with BUDGET.with_suffix(".lock").open("a", encoding="utf-8") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise ValueError(
                "another M2 training attempt owns the budget"
            ) from error
        ledger = (
            json_object(BUDGET)
            if BUDGET.exists()
            else {
                "version": "som.flow.training-time.v1",
                "limit_seconds": TIME_LIMIT,
                "attempts": [],
            }
        )
        attempts = ledger.get("attempts")
        if (
            ledger.get("version") != "som.flow.training-time.v1"
            or ledger.get("limit_seconds") != TIME_LIMIT
            or not isinstance(attempts, list)
        ):
            raise ValueError("invalid cumulative M2 training ledger")
        spent = 0.0
        for attempt in attempts:
            if not isinstance(attempt, dict):
                raise ValueError("invalid M2 training attempt")
            reserved = attempt.get("reserved_seconds")
            elapsed = attempt.get("elapsed_seconds")
            value = reserved if elapsed is None else elapsed
            if (
                not isinstance(value, (int, float))
                or not math.isfinite(value)
                or value < 0
            ):
                raise ValueError("invalid M2 training duration")
            spent += value
        allowance = min(requested, TIME_LIMIT - spent)
        if allowance <= 0:
            raise ValueError("cumulative M2 training budget is exhausted")
        current = {
            "id": str(uuid4()),
            "reserved_seconds": allowance,
            "elapsed_seconds": None,
            "started_unix": time.time(),
        }
        attempts.append(current)

        def persist() -> None:
            temporary = BUDGET.with_suffix(".pending")
            temporary.write_text(json.dumps(ledger, indent=2) + "\n")
            temporary.replace(BUDGET)

        persist()
        started = time.monotonic()

        def timeout(_number: int, _frame: object) -> None:
            raise TimeoutError("M2 training time allowance expired")

        previous = signal.signal(signal.SIGALRM, timeout)
        signal.setitimer(signal.ITIMER_REAL, allowance)
        try:
            yield allowance
        finally:
            signal.setitimer(signal.ITIMER_REAL, 0)
            signal.signal(signal.SIGALRM, previous)
            current["elapsed_seconds"] = time.monotonic() - started
            persist()


def examples(data: Path, blocks: Path) -> list[Example]:
    """Read exactly the frozen development bytes; no formal data argument."""
    if (
        data.read_bytes() != DEVELOPMENT.read_bytes()
        or blocks.resolve() != BLOCKS
    ):
        raise ValueError(
            "flow smoke training requires frozen development "
            "and compact blocks"
        )
    library = load_blocks(blocks.parent)
    catalog = flow_compact.metadata()
    result: list[Example] = []
    for line in data.read_text().splitlines():
        row = json.loads(line)
        plan = flow_compact.reference(row["prompt"], library)
        source = flow_codec.features(row["prompt"], catalog)
        target = flow_codec.encode(row["prompt"], plan, catalog)
        if flow_codec.decode(row["prompt"], target, catalog) != plan:
            raise ValueError("reference codec changes a selected choice")
        result.append(Example(row["id"], source, target))
    if len(result) != 12 or len({row.source for row in result}) != 12:
        raise ValueError(
            "flow smoke training needs twelve distinct seen inputs"
        )
    return result


def tokenizer() -> Tokenizer:
    """Export the fixed, dense structural vocabulary expected by Rust."""
    from tokenizers import Regex, Tokenizer, decoders, models, pre_tokenizers

    result = Tokenizer(
        models.WordLevel(
            vocab={
                token: index for index, token in enumerate(flow_codec.TOKENS)
            },
            unk_token="<pad>",
        )
    )
    result.pre_tokenizer = pre_tokenizers.Split(
        Regex(r"<[^<>\s]+>"),
        behavior="isolated",
    )
    result.decoder = decoders.Fuse()
    return result


def token_pairs(
    rows: list[Example], codec: Tokenizer
) -> list[tuple[list[int], list[int]]]:
    """Require complete lossless sequences and the fixed per-side bounds."""
    pairs: list[tuple[list[int], list[int]]] = []
    for row in rows:
        left, right = (
            codec.encode(row.source).ids,
            codec.encode(row.target).ids,
        )
        if (
            not left
            or not right
            or 0 in left + right
            or max(len(left), len(right)) > 2048
            or len(left) + len(right) > 4096
            or right[-1] != 1
            or codec.decode(right, skip_special_tokens=False) != row.target
        ):
            raise ValueError(f"invalid smoke sequence: {row.identity}")
        pairs.append((left, right))
    return pairs


def validate_options(args: argparse.Namespace) -> None:
    """Keep the fixture experiment fixed and protect earlier model files."""
    expected = {
        "seed": 17,
        "context": 4096,
        "width": 64,
        "heads": 4,
        "layers": 2,
        "vocab_size": 1024,
        "max_new_tokens": 2048,
        "save_every": 1000,
    }
    if any(getattr(args, key) != value for key, value in expected.items()):
        raise ValueError(
            "flow smoke architecture must match the fixed M2 experiment"
        )
    if (
        args.steps < 0
        or not math.isfinite(args.hours)
        or not 0 < args.hours <= 168
    ):
        raise ValueError("invalid M2 training steps or time allowance")
    if args.admission is None or args.seal is None or args.legacy is None:
        raise ValueError("flow_plan requires --admission, --seal and --legacy")
    if args.templates is not None:
        raise ValueError("flow smoke does not accept operation templates")
    if args.legacy.resolve() != LEGACY:
        raise ValueError(
            "flow smoke must retain the frozen quality legacy decoder"
        )
    if (
        args.dev is not None
        and args.dev.read_bytes() != DEVELOPMENT.read_bytes()
    ):
        raise ValueError(
            "M2 smoke cannot select checkpoints using formal or new inputs"
        )
    if args.output.exists():
        raise ValueError(
            "use a new smoke output directory; preserve prior artifacts"
        )
    candidate_root = ROOT / "som-rs/models/flow-m2-candidate"
    if (args.output.absolute().parent != candidate_root
            or args.output.resolve().parent != candidate_root):
        raise ValueError("flow smoke needs a new direct child of flow-m2-candidate")


def write_sidecars(output: Path, admission: Path, codec: Tokenizer) -> None:
    """Copy frozen dependencies. Preserve the legacy and current models."""
    output.mkdir(parents=True, exist_ok=False)
    codec.save(str(output / "tokenizer.json"))
    (output / "config.json").write_text(composed.canonical(CONFIG) + "\n")
    shutil.copy2(flow_compact.CATALOG, output / "contracts.json")
    shutil.copy2(flow.CONTRACT_PATH, output / "contract.md")
    shutil.copy2(BUDGET_POLICY, output / "output-budget.json")
    shutil.copy2(DEVELOPMENT, output / "development.jsonl")
    shutil.copy2(admission, output / "admission.json")
    (output / "codec.json").write_text(
        composed.canonical(
            {
                "version": flow_codec.COMPACT_VERSION,
                "input_limit": 2048,
                "output_limit": 2048,
                "token_ids": codec.get_vocab(),
                "decode": "lossless; no repair; explicit end required",
            }
        )
        + "\n"
    )
    for name in SIDECARS[5:]:
        if name != "output-budget.json":
            shutil.copy2(LEGACY / name, output / name)
    shutil.copytree(LEGACY, output / "legacy")
    shutil.copy2(
        ROOT / "som-code-python/data/blocks/composed-quality/blocks.json",
        output / "legacy/blocks.json",
    )


def run(args: argparse.Namespace) -> None:
    """Train from scratch. A failed prerequisite creates no model artifact."""
    validate_options(args)
    verify_admission(args.admission, args.seal)
    rows = examples(args.data, args.blocks)
    codec = tokenizer()
    pairs = token_pairs(rows, codec)
    with training_budget(args.hours * 3600) as allowance:
        fit(args, rows, codec, pairs, allowance)


def fit(
    args: argparse.Namespace,
    rows: list[Example],
    codec: Tokenizer,
    pairs: list[tuple[list[int], list[int]]],
    allowance: float,
) -> None:
    """The only entry that imports MLX or allocates new model parameters."""

    import mlx.core as mx
    import mlx.optimizers as optim
    from mlx import nn
    from mlx.utils import tree_flatten
    from mlx_lm.models.cache import make_prompt_cache
    from mlx_lm.models.gpt2 import Model, ModelArgs

    random.seed(17)
    mx.random.seed(17)
    rng = random.Random(17)
    model = Model(
        ModelArgs(
            **{
                key: value
                for key, value in CONFIG.items()
                if key not in {"output_mode", "max_new_tokens"}
            }
        )
    )
    optimizer = optim.Adam(learning_rate=3e-4)
    write_sidecars(args.output, args.admission, codec)

    def loss(network: Model, ids: mx.array, mask: mx.array) -> mx.array:
        logits = network(ids[:, :-1])
        values = nn.losses.cross_entropy(logits, ids[:, 1:], reduction="none")
        return (values * mask).sum() / mask.sum()

    gradient = nn.value_and_grad(model, loss)
    history: list[dict[str, object]] = []
    started = time.monotonic()
    completed = 0
    perfect = False

    def evaluate() -> bool:
        model.eval()
        groups: dict[int, list[tuple[int, list[int], list[int]]]] = (
            defaultdict(list)
        )
        results: list[dict[str, object]] = [{} for _ in pairs]
        for index, (left, right) in enumerate(pairs):
            groups[len(left)].append((index, left, right))
        for group in groups.values():
            cache = make_prompt_cache(model)
            logits = model(
                mx.array([row[1] for row in group], dtype=mx.int32),
                cache=cache,
            )[:, -1]
            active = set(range(len(group)))
            for position in range(max(len(row[2]) for row in group)):
                selected = mx.argmax(logits, axis=-1)
                mx.eval(selected)
                values = selected.tolist()
                for index in list(active):
                    expected = group[index][2][position]
                    destination = group[index][0]
                    if values[index] != expected:
                        results[destination] = {
                            "id": rows[destination].identity,
                            "correct": False,
                            "first_error": position,
                            "expected": expected,
                            "selected": values[index],
                        }
                        active.remove(index)
                    elif position + 1 == len(group[index][2]):
                        results[destination] = {
                            "id": rows[destination].identity,
                            "correct": True,
                            "tokens": position + 1,
                        }
                        active.remove(index)
                if not active:
                    break
                logits = model(selected[:, None], cache=cache)[:, -1]
        model.train()
        correct = sum(result.get("correct") is True for result in results)
        history.append(
            {
                "step": completed,
                "correct": correct,
                "total": 12,
                "results": results,
            }
        )
        print(
            composed.canonical(
                {
                    "stage": "seen_input_smoke",
                    "step": completed,
                    "correct": correct,
                    "total": 12,
                }
            ),
            flush=True,
        )
        return correct == 12

    def save() -> None:
        verify_admission(args.admission, args.seal)
        weights = dict(tree_flatten(model.parameters()))
        mx.eval(weights)
        mx.save_safetensors(str(args.output / "model.safetensors"), weights)
        names = (
            "tokenizer.json",
            "config.json",
            "model.safetensors",
            *SIDECARS,
        )
        manifest = {
            "purpose": PURPOSE,
            "prompt_version": flow.VERSION,
            "dsl_version": flow.DSL_VERSION,
            "output_mode": "flow_plan",
            "quality_contract": quality.VERSION,
            "training_seed": 17,
            "training_examples": 12,
            "training_steps": completed,
            "elapsed_seconds": time.monotonic() - started,
            "blocks_sha256": digest(BLOCKS),
            "files": {name: digest(args.output / name) for name in names},
            "legacy_manifest_sha256": digest(
                args.output / "legacy/manifest.json"
            ),
            "development_sha256": digest(DEVELOPMENT),
            "selection": (
                "earliest perfect full-sequence seen-input checkpoint"
            ),
            "development_perfect": perfect,
            "development_checks": history,
            "unique_inputs": 12,
            "formal_training_examples": 0,
            "max_input_tokens": max(len(left) for left, _ in pairs),
            "max_output_tokens": max(len(right) for _, right in pairs),
            "parameter_count": sum(value.size for value in weights.values()),
        }
        (args.output / "manifest.json").write_text(
            json.dumps(manifest, indent=2) + "\n"
        )

    model.train()
    save()
    try:
        while completed < (args.steps or 2**63 - 1):
            if time.monotonic() - started >= allowance:
                break
            batch = [pairs[rng.randrange(12)] for _ in range(8)]
            size = max(len(left) + len(right) for left, right in batch)
            ids, masks = [], []
            for left, right in batch:
                padding = size - len(left) - len(right)
                ids.append(left + right + [0] * padding)
                masks.append(
                    [0] * (len(left) - 1) + [1] * len(right) + [0] * padding
                )
            value, gradients = gradient(
                model,
                mx.array(ids, dtype=mx.int32),
                mx.array(masks, dtype=mx.float32),
            )
            optimizer.update(model, gradients)
            mx.eval(model.parameters(), optimizer.state, value)
            completed += 1
            if completed % 100 == 0:
                print(f"step={completed} loss={float(value):.6f}", flush=True)
            if completed % 1000 == 0:
                perfect = evaluate()
                save()
                if perfect:
                    break
    finally:
        save()
    if not perfect:
        raise ValueError(
            "no admitted perfect seen-input checkpoint; artifacts retained"
        )

"""Independent M3 supervised training, with no formal-set reader.

M2 fixture training retains its own admission, paths and time ledger. M3
requires complete train/development artifact receipts before allocating weights.
"""

from __future__ import annotations

import argparse
from collections import defaultdict
from contextlib import contextmanager
import fcntl
import json
import math
from pathlib import Path
import random
import shutil
import signal
import time
from uuid import uuid4

from . import composed, flow, flow_codec, flow_compact, flow_m3_target, quality
from .flow_constraints import VERSION as POLICY_VERSION, policy
from .flow_m3_data import COUNTS, fingerprints
from .flow_m3_evidence import verify_artifact_record, verify_receipt_hashes, verify_seal
from .train_flow import BLOCKS, BUDGET_POLICY, CONFIG as SMOKE_CONFIG, LEGACY, LIMITS, Example, digest, json_object, token_pairs, tokenizer, verify_files

ROOT = Path(__file__).resolve().parents[3]
PURPOSE = "m3-supervised-composition"
ADMISSION_VERSION = "som.flow.m3-training-admission.v1"
CONFIG = dict(SMOKE_CONFIG, training_profile=PURPOSE,
              flow_decode_policy=POLICY_VERSION,
              flow_input_policy=flow_codec.M3_BOUND_INPUT_VERSION,
              flow_output_policy=flow_m3_target.VERSION)
BUDGET = ROOT / "som-rs/models/flow-m3-candidate/training-budget.json"
TIME_LIMIT = 168 * 3600
SIDECARS = ("contracts.json", "contract.md", "codec.json", "admission.json",
            "development.jsonl", "training.jsonl", "dataset.json", "decode-policy.json",
            "quality.json", "ruff", "pyproject.toml", "quality-contract.md",
            "quality-exceptions.json", "output-budget.json", "input-policy.json",
            "target-policy.json")


def output_guard(args: argparse.Namespace, *, saving: bool = False) -> None:
    candidate_root = ROOT / "som-rs/models/flow-m3-candidate"
    if (args.templates is not None or args.output.exists() and not saving
            or args.output.absolute().parent != candidate_root
            or args.output.resolve().parent != candidate_root):
        raise ValueError("M3 uses a fresh candidate directory and no templates")
    if saving and (not args.output.is_dir()
                   or digest(args.output / "admission.json") != digest(args.admission)
                   or digest(args.output / "dataset.json") != digest(args.data.parent / "manifest.json")):
        raise ValueError("M3 checkpoint directory no longer belongs to this admission")


def verify_admission(args: argparse.Namespace, *, saving: bool = False) -> dict:
    if args.admission is None or args.seal is None or args.dev is None or args.legacy is None:
        raise ValueError("M3 needs train, dev, admission, source seal and frozen legacy")
    if args.data.name != "train.jsonl" or args.dev.name != "dev.jsonl" or args.data.parent != args.dev.parent:
        raise ValueError("M3 reads only separate train.jsonl and dev.jsonl")
    manifest_path = args.data.parent / "manifest.json"
    manifest = json_object(manifest_path)
    admission = json_object(args.admission)
    seal = json_object(args.seal)
    if (manifest.get("version") != "som.flow.m3-dataset.v1" or manifest.get("counts") != COUNTS
            or manifest.get("input_policy_version") != flow_codec.M3_BOUND_INPUT_VERSION
            or manifest.get("cross_split_overlap") != dict.fromkeys(("input", "structure", "graph"), 0)
            or admission.get("version") != ADMISSION_VERSION
            or admission.get("counts") != COUNTS or admission.get("limits") != LIMITS
            or admission.get("dataset_sha256") != digest(manifest_path)
            or admission.get("source_seal_sha256") != digest(args.seal)
            or admission.get("blocks_sha256") != digest(BLOCKS)
            or admission.get("quality_behavior_passed") is not True
            or seal.get("version") != "som.flow.m3-source-seal.v1"
            or seal.get("decode_policy") != policy()
            or seal.get("target_policy") != flow_m3_target.policy()
            or admission.get("target_policy") != flow_m3_target.policy()):
        raise ValueError("M3 data, source or complete artifact admission is missing or stale")
    verify_seal(args.seal, args.data.parent)
    verify_files(admission.get("evidence_sha256"), "M3 admission evidence")
    for path in (args.data, args.dev):
        if manifest["files"].get(path.name) != digest(path):
            raise ValueError("M3 train/development bytes differ from their seal")
    report = json_object(ROOT / admission["quality_behavior_report"])
    rows = report.get("records", [])
    if (report.get("passed") is not True or len(rows) != 2700
            or report.get("source_unchanged") is not True
            or any(row.get("passed") is not True for row in rows)
            or {row.get("id") for row in rows} !=
               {f"m3-{split}-{index:04d}" for split in ("train", "dev") for index in range(COUNTS[split])}):
        raise ValueError("M3 requires every train/development quality and behavior receipt")
    for row in rows:
        if row.get("target_policy") != flow_m3_target.VERSION:
            raise ValueError("M3 artifact target differs from the admitted output policy")
        verify_artifact_record(row)
        verify_receipt_hashes(row, admission["evidence_sha256"])
    if args.blocks.resolve() != BLOCKS or args.legacy.resolve() != LEGACY:
        raise ValueError("M3 must retain the admitted compact blocks and frozen v6 legacy")
    expected = {"seed": 17, "width": 64, "layers": 2, "heads": 4,
                "vocab_size": 1024, "context": 4096, "max_new_tokens": 2048, "save_every": 1000}
    if any(getattr(args, key) != value for key, value in expected.items()):
        raise ValueError("M3 architecture differs from the fixed first-round plan")
    output_guard(args, saving=saving)
    if not math.isfinite(args.hours) or not 0 < args.hours <= 168 or args.steps < 0:
        raise ValueError("invalid M3 time or step allowance")
    return admission


def examples(path: Path, expected: int) -> list[Example]:
    """Read admitted labels. Formal paths and held-out Apps are prohibited."""
    if path.name not in {"train.jsonl", "dev.jsonl"}:
        raise ValueError("M3 optimizer and selector cannot read formal data")
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    if len(rows) != expected:
        raise ValueError("M3 split count differs")
    result = []
    for row in rows:
        if row["app"] in {"experiment", "documents"}:
            raise ValueError("held-out App entered training or development")
        prompt, plan = row["prompt"], row["dsl"]
        if fingerprints(prompt) != row["fingerprints"]:
            raise ValueError("M3 actual input or graph fingerprint is stale")
        target = flow_m3_target.encode(prompt, plan, flow_compact.metadata())
        result.append(Example(row["id"], flow_codec.features_m3_bound(prompt, flow_compact.metadata()), target))
    if len({row.source for row in result}) != expected:
        raise ValueError("M3 repeats an actual input")
    return result


@contextmanager
def training_budget(requested: float):
    """Reserve all M3 attempts in one separate, locked week-long ledger."""
    if not math.isfinite(requested) or not 0 < requested <= TIME_LIMIT:
        raise ValueError("M3 allowance must fit the cumulative one-week limit")
    BUDGET.parent.mkdir(parents=True, exist_ok=True)
    with BUDGET.with_suffix(".lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        ledger = json_object(BUDGET) if BUDGET.exists() else {"version": "som.flow.m3-training-time.v1", "limit_seconds": TIME_LIMIT, "attempts": []}
        if (ledger.get("version") != "som.flow.m3-training-time.v1"
                or ledger.get("limit_seconds") != TIME_LIMIT
                or not isinstance(ledger.get("attempts"), list)):
            raise ValueError("invalid M3 time ledger")
        spent = 0.0
        for row in ledger["attempts"]:
            if not isinstance(row, dict):
                raise ValueError("invalid M3 time ledger attempt")
            value = row.get("reserved_seconds") if row.get("elapsed_seconds") is None else row["elapsed_seconds"]
            if (not isinstance(value, (int, float)) or isinstance(value, bool)
                    or not math.isfinite(value) or value < 0):
                raise ValueError("invalid M3 time ledger duration")
            spent += value
        allowance = min(requested, TIME_LIMIT - spent)
        if not math.isfinite(allowance) or allowance <= 0:
            raise ValueError("M3 cumulative training allowance exhausted")
        attempt = {"id": str(uuid4()), "started_unix": time.time(), "reserved_seconds": allowance, "elapsed_seconds": None}
        ledger["attempts"].append(attempt)
        def persist():
            temporary = BUDGET.with_suffix(".pending")
            temporary.write_text(json.dumps(ledger, indent=2) + "\n")
            temporary.replace(BUDGET)
        persist()
        started = time.monotonic()
        def expired(_number, _frame): raise TimeoutError("M3 training allowance expired")
        previous = signal.signal(signal.SIGALRM, expired)
        signal.setitimer(signal.ITIMER_REAL, allowance)
        try:
            yield allowance
        finally:
            signal.setitimer(signal.ITIMER_REAL, 0)
            signal.signal(signal.SIGALRM, previous)
            attempt["elapsed_seconds"] = time.monotonic() - started
            persist()


def write_sidecars(args, codec):
    out = args.output
    out.mkdir(parents=True)
    codec.save(str(out / "tokenizer.json"))
    (out / "config.json").write_text(composed.canonical(CONFIG) + "\n")
    shutil.copy2(flow_compact.CATALOG, out / "contracts.json")
    shutil.copy2(flow.CONTRACT_PATH, out / "contract.md")
    shutil.copy2(BUDGET_POLICY, out / "output-budget.json")
    shutil.copy2(ROOT / "som-rs/contracts/m3-input-bindings.v3.json", out / "input-policy.json")
    shutil.copy2(flow_m3_target.POLICY, out / "target-policy.json")
    shutil.copy2(args.data, out / "training.jsonl")
    shutil.copy2(args.dev, out / "development.jsonl")
    shutil.copy2(args.data.parent / "manifest.json", out / "dataset.json")
    shutil.copy2(args.admission, out / "admission.json")
    (out / "decode-policy.json").write_text(composed.canonical(policy()) + "\n")
    (out / "codec.json").write_text(composed.canonical({"version": flow_codec.COMPACT_VERSION,
        "input_limit": 2048, "output_limit": 2048, "token_ids": codec.get_vocab(),
        "decode": "lossless; no repair; explicit end required"}) + "\n")
    for name in ("quality.json", "ruff", "pyproject.toml", "quality-contract.md", "quality-exceptions.json"):
        shutil.copy2(LEGACY / name, out / name)
    shutil.copytree(LEGACY, out / "legacy")
    shutil.copy2(ROOT / "som-code-python/data/blocks/composed-quality/blocks.json", out / "legacy/blocks.json")


def run(args: argparse.Namespace) -> None:
    for name in ("data", "dev", "admission", "seal", "legacy", "blocks"):
        if getattr(args, name, None) is not None:
            setattr(args, name, getattr(args, name).resolve())
    # Keep the output spelling, so a `current` alias cannot bypass protection.
    args.output = args.output.absolute()
    verify_admission(args)
    train, dev = examples(args.data, 2400), examples(args.dev, 300)
    if {row.source for row in train} & {row.source for row in dev}:
        raise ValueError("M3 train/development overlap")
    codec = tokenizer()
    train_pairs, dev_pairs = token_pairs(train, codec), token_pairs(dev, codec)
    with training_budget(args.hours * 3600) as allowance:
        fit(args, train, dev, codec, train_pairs, dev_pairs, allowance)


def fit(args, train, dev, codec, train_pairs, dev_pairs, allowance):
    # No MLX import or new weight allocation occurs before complete admission.
    import mlx.core as mx
    import mlx.optimizers as optim
    from mlx import nn
    from mlx.utils import tree_flatten
    from mlx_lm.models.cache import make_prompt_cache
    from mlx_lm.models.gpt2 import Model, ModelArgs
    random.seed(17)
    mx.random.seed(17)
    rng = random.Random(17)
    model = Model(ModelArgs(**{key: value for key, value in CONFIG.items()
                             if key not in {"output_mode", "max_new_tokens", "training_profile", "flow_decode_policy", "flow_input_policy", "flow_output_policy"}}))
    optimizer = optim.Adam(learning_rate=3e-4)
    write_sidecars(args, codec)
    def loss(network, ids, mask):
        values = nn.losses.cross_entropy(network(ids[:, :-1]), ids[:, 1:], reduction="none")
        return (values * mask).sum() / mask.sum()
    gradient = nn.value_and_grad(model, loss)
    completed, perfect, history = 0, False, []
    started = time.monotonic()
    buckets = defaultdict(list)
    for index, (left, right) in enumerate(train_pairs):
        buckets[(len(left) + len(right)) // 128].append(index)

    def evaluate():
        model.eval()
        groups = defaultdict(list)
        results = [{} for _ in dev_pairs]
        for index, (left, right) in enumerate(dev_pairs):
            groups[len(left)].append((index, left, right))
        for whole in groups.values():
            for begin in range(0, len(whole), 8):
                group = whole[begin:begin + 8]
                cache = make_prompt_cache(model)
                logits = model(mx.array([row[1] for row in group], dtype=mx.int32), cache=cache)[:, -1]
                active = set(range(len(group)))
                for position in range(max(len(row[2]) for row in group)):
                    selected = mx.argmax(logits, axis=-1)
                    mx.eval(selected)
                    values = selected.tolist()
                    for index in list(active):
                        destination = group[index][0]
                        expected = group[index][2][position]
                        if values[index] != expected:
                            results[destination] = {"id": dev[destination].identity, "correct": False,
                                "first_error": position, "expected": expected, "selected": values[index]}
                            active.remove(index)
                        elif position + 1 == len(group[index][2]):
                            results[destination] = {"id": dev[destination].identity, "correct": True, "tokens": position + 1}
                            active.remove(index)
                    if not active: break
                    logits = model(selected[:, None], cache=cache)[:, -1]
        model.train()
        correct = sum(row.get("correct") is True for row in results)
        history.append({"step": completed, "correct": correct, "total": 300, "results": results})
        print(composed.canonical({"stage": "complete-unconstrained-development", "step": completed,
                                "correct": correct, "total": 300}), flush=True)
        return correct == 300

    def save():
        verify_admission(args, saving=True)
        weights = dict(tree_flatten(model.parameters()))
        mx.eval(weights)
        temporary = args.output / "model.pending.safetensors"
        mx.save_safetensors(str(temporary), weights)
        temporary.replace(args.output / "model.safetensors")
        names = ("tokenizer.json", "config.json", "model.safetensors", *SIDECARS)
        manifest = {"purpose": PURPOSE, "prompt_version": flow.VERSION, "dsl_version": flow.DSL_VERSION,
            "output_mode": "flow_plan", "quality_contract": quality.VERSION, "decode_policy": POLICY_VERSION,
            "target_policy": flow_m3_target.VERSION,
            "training_seed": 17, "training_examples": 2400, "development_examples": 300,
            "formal_training_examples": 0, "training_steps": completed,
            "elapsed_seconds": time.monotonic() - started, "blocks_sha256": digest(BLOCKS),
            "files": {name: digest(args.output / name) for name in names},
            "legacy_manifest_sha256": digest(args.output / "legacy/manifest.json"),
            "dataset_sha256": digest(args.output / "dataset.json"),
            "development_perfect": perfect, "development_checks": history,
            "selection": "earliest 1000-step checkpoint with all 300 raw development sequences exact",
            "unique_inputs": 2700, "parameter_count": sum(value.size for value in weights.values()),
            "max_input_tokens": max(len(left) for left, _ in train_pairs + dev_pairs),
            "max_output_tokens": max(len(right) for _, right in train_pairs + dev_pairs)}
        (args.output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
        if completed > 0 and completed % 1000 == 0:
            checkpoint = args.output / "checkpoints" / f"{completed:06d}"
            if not checkpoint.exists():
                checkpoint.mkdir(parents=True)
                shutil.copy2(args.output / "model.safetensors", checkpoint / "model.safetensors")
                shutil.copy2(args.output / "manifest.json", checkpoint / "manifest.json")

    model.train()
    save()
    try:
        while completed < (args.steps or 2**63 - 1) and time.monotonic() - started < allowance:
            batches = []
            for indices in buckets.values():
                shuffled = list(indices)
                rng.shuffle(shuffled)
                batches.extend(shuffled[index:index + 8] for index in range(0, len(shuffled), 8))
            rng.shuffle(batches)
            for indices in batches:
                if completed >= (args.steps or 2**63 - 1) or time.monotonic() - started >= allowance: break
                batch = [train_pairs[index] for index in indices]
                size = max(len(left) + len(right) for left, right in batch)
                ids, masks = [], []
                for left, right in batch:
                    padding = size - len(left) - len(right)
                    ids.append(left + right + [0] * padding)
                    masks.append([0] * (len(left) - 1) + [1] * len(right) + [0] * padding)
                value, gradients = gradient(model, mx.array(ids, dtype=mx.int32), mx.array(masks, dtype=mx.float32))
                optimizer.update(model, gradients)
                mx.eval(model.parameters(), optimizer.state, value)
                completed += 1
                if completed % 100 == 0:
                    print(composed.canonical({"stage": "training", "step": completed, "loss": float(value),
                                            "seconds": time.monotonic() - started}), flush=True)
                if completed % 1000 == 0:
                    perfect = evaluate()
                    save()
                    if perfect: break
            if perfect: break
    finally:
        save()
    if not perfect:
        raise ValueError("M3 has no admitted perfect development checkpoint; all results retained")

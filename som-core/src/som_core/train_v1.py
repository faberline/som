"""Train a SOM v1 decoder from scratch and export files for the Rust service.

Input JSONL rows have exactly two keys: ``prompt`` and ``dsl``.  The trainer
validates both before tokenizing.  A smoke run uses the same export path with
zero optimizer steps, so it tests the model format without claiming accuracy.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import time
from pathlib import Path

from .v1 import assemble, load_blocks, resolve_prompt_refs, validate_prompt


def canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


OPERATIONS = (
    "api.create",
    "api.get",
    "api.list",
    "api.list.query",
    "api.list.boolean",
    "api.list.boolean.date",
    "api.patch",
    "api.delete",
) + tuple(
    f"{prefix}.{role}.{op}{suffix}"
    for prefix in ("api.related", "api.mongo")
    for role in ("parent", "child")
    for op in ("create", "get", "list", "patch", "delete")
    for suffix in ((".plain", ".boolean", ".date") if op == "list" else ("",))
) + ("api.related.batch", "api.mongo.batch")


def read_pairs(
    path: Path, blocks: Path, mode: str
) -> tuple[list[tuple[str, str]], dict | None]:
    library = load_blocks(blocks.parent)
    pairs = []
    templates = {}
    for line_number, line in enumerate(
        path.read_text(encoding="utf-8").splitlines(), 1
    ):
        row = json.loads(line)
        if set(row) != {"prompt", "dsl"}:
            raise ValueError(f"row {line_number}: expected prompt and dsl")
        validate_prompt(row["prompt"])
        assemble(resolve_prompt_refs(row["prompt"], row["dsl"]), library)
        if mode == "operation_blocks":
            version = row["prompt"]["version"]
            api_file = next((f for f in row["dsl"]["files"] if any(b["id"] in {"api.head", "api.related.head"} or b["id"].startswith("api.business.head.") for b in f["blocks"])), None)
            if api_file is None:
                raise ValueError(f"row {line_number}: missing named API slots")
            api = [b for b in api_file["blocks"] if b["id"] in OPERATIONS]
            if version == "som.prompt.v5":
                from .related import model_input, operation, template_key
                expected_ids = [operation(step, row["prompt"]) for step in row["prompt"]["logic"]]
                if [entry["id"] for entry in api] != expected_ids:
                    raise ValueError(f"row {line_number}: wrong scoped operation block")
                pairs.extend((model_input(step, row["prompt"]), f"<{entry['id']}>") for step, entry in zip(row["prompt"]["logic"], api, strict=True))
                templates.setdefault(template_key(row["prompt"]), row["dsl"])
                continue
            list_id = {
                "som.prompt.v1": "api.list",
                "som.prompt.v2": "api.list.query",
                "som.prompt.v3": "api.list.boolean",
                "som.prompt.v4": "api.list.boolean.date",
            }[version]
            expected = {"api.create", "api.get", list_id, "api.patch", "api.delete"}
            if (
                len(api) != len(row["prompt"]["logic"])
                or {entry["id"] for entry in api} != expected
            ):
                raise ValueError(
                    f"row {line_number}: expected all five CRUD operations"
                )
            pairs.extend(
                (f"<step>{step}</step><block>", f"<{entry['id']}>")
                for step, entry in zip(row["prompt"]["logic"], api, strict=True)
            )
            templates.setdefault(version, row["dsl"])
        else:
            pairs.append(
                (
                    f"<prompt>{canonical(row['prompt'])}</prompt><dsl>",
                    canonical(row["dsl"]) + "<eos>",
                )
            )
    if not pairs:
        raise ValueError("training set is empty")
    return pairs, templates if mode == "operation_blocks" else None


def train() -> None:
    parser = argparse.ArgumentParser(description="Train SOM v1 from scratch")
    parser.add_argument(
        "--data", type=Path, required=True, help="validated prompt/DSL JSONL"
    )
    parser.add_argument(
        "--blocks", type=Path, required=True, help="approved blocks.json"
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--steps", type=int, default=0)
    parser.add_argument("--dev", type=Path, help="separate development set for checkpoint choice")
    parser.add_argument("--stop-at-perfect", action="store_true")
    parser.add_argument("--templates", type=Path, help="non-learned typed template skeletons")
    parser.add_argument("--hours", type=float, default=168.0)
    parser.add_argument("--seed", type=int, default=17)
    parser.add_argument("--vocab-size", type=int, default=2048)
    parser.add_argument("--context", type=int, default=4096)
    parser.add_argument("--width", type=int, default=128)
    parser.add_argument("--heads", type=int, default=4)
    parser.add_argument("--layers", type=int, default=2)
    parser.add_argument("--max-new-tokens", type=int, default=2048)
    parser.add_argument(
        "--mode", choices=["full_dsl", "operation_blocks", "block_plan", "flow_plan", "flow_plan_m3"], default="full_dsl"
    )
    parser.add_argument("--legacy", type=Path, help="frozen legacy decoder for a block_plan bundle")
    parser.add_argument("--admission", type=Path, help="complete admission for the selected flow training profile")
    parser.add_argument("--seal", type=Path, help="frozen source and input ledger for the selected profile")
    parser.add_argument("--save-every", type=int, default=1000)
    args = parser.parse_args()
    if args.steps < 0 or not 0 < args.hours <= 168 or args.save_every <= 0:
        parser.error("steps must be nonnegative and hours must be in (0, 168]")
    if args.width % args.heads:
        parser.error("width must divide evenly by heads")
    if args.mode == "flow_plan":
        from .train_flow import run
        return run(args)
    if args.mode == "flow_plan_m3":
        from .train_flow_m3 import run
        return run(args)
    if args.mode == "block_plan":
        from .train_composed import run
        return run(args)
    if args.stop_at_perfect and (args.dev is None or args.mode != "operation_blocks"):
        parser.error("stop-at-perfect requires a development set and operation_blocks mode")

    from tokenizers import (
        Tokenizer,
        decoders,
        models,
        pre_tokenizers,
        trainers,
    )

    pairs, template = read_pairs(args.data, args.blocks, args.mode)
    if args.templates:
        if template is None:
            parser.error("templates require operation_blocks mode")
        extras = json.loads(args.templates.read_text())
        template.update(extras)
    dev_pairs = read_pairs(args.dev, args.blocks, args.mode)[0] if args.dev else []
    tokenizer = Tokenizer(models.BPE(unk_token="<unk>"))
    tokenizer.pre_tokenizer = pre_tokenizers.ByteLevel(add_prefix_space=False)
    tokenizer.decoder = decoders.ByteLevel()
    tokenizer.train_from_iterator(
        (left + right for left, right in pairs),
        trainer=trainers.BpeTrainer(
            vocab_size=args.vocab_size,
            special_tokens=["<unk>", "<eos>", *(f"<{op}>" for op in OPERATIONS)],
        ),
    )
    examples = []
    for left, right in pairs:
        prefix = tokenizer.encode(left, add_special_tokens=False).ids
        answer = tokenizer.encode(right, add_special_tokens=False).ids
        tokens = prefix + answer
        if len(tokens) > args.context:
            raise ValueError(
                f"example has {len(tokens)} tokens; context is {args.context}"
            )
        examples.append((tokens, len(prefix)))

    import mlx.core as mx
    import mlx.optimizers as optim
    from mlx import nn
    from mlx.utils import tree_flatten
    from mlx_lm.models.gpt2 import Model, ModelArgs

    random.seed(args.seed)
    mx.random.seed(args.seed)
    config = {
        "model_type": "gpt2",
        "n_ctx": args.context,
        "n_embd": args.width,
        "n_head": args.heads,
        "n_layer": args.layers,
        "n_positions": args.context,
        "layer_norm_epsilon": 1e-5,
        "vocab_size": tokenizer.get_vocab_size(with_added_tokens=True),
        "max_new_tokens": args.max_new_tokens,
        "output_mode": args.mode,
    }
    model = Model(
        ModelArgs(
            **{
                k: v
                for k, v in config.items()
                if k not in {"max_new_tokens", "output_mode"}
            }
        )
    )
    optimizer = optim.Adam(learning_rate=3e-4)

    def loss(model: Model, tokens: mx.array, prefix: int) -> mx.array:
        logits = model(tokens[None, :-1])[0]
        targets = tokens[1:]
        return nn.losses.cross_entropy(
            logits[prefix - 1 :], targets[prefix - 1 :]
        ).mean()

    loss_and_grad = nn.value_and_grad(model, loss)
    start = time.monotonic()
    model.train()
    args.output.mkdir(parents=True, exist_ok=True)
    tokenizer.save(str(args.output / "tokenizer.json"))
    (args.output / "config.json").write_text(canonical(config) + "\n", encoding="utf-8")
    if template is not None:
        (args.output / "dsl_template.json").write_text(
            canonical(template) + "\n", encoding="utf-8"
        )
    order = list(range(len(examples)))
    rng = random.Random(args.seed)
    completed = 0
    dev_history = []

    def evaluate_dev():
        # Repeated inputs are evaluated once, with their multiplicities retained.
        correct = 0
        cache = {}
        operation_ids = [(op, tokenizer.token_to_id(f"<{op}>")) for op in OPERATIONS]
        model.eval()
        for left, right in dev_pairs:
            if left not in cache:
                ids = tokenizer.encode(left, add_special_tokens=False).ids
                scores = model(mx.array([ids], dtype=mx.int32))[0, -1]
                mx.eval(scores)
                cache[left] = max(operation_ids, key=lambda item: float(scores[item[1]].item()))[0]
            correct += f"<{cache[left]}>" == right
        model.train()
        result = {"step": completed, "correct": correct, "total": len(dev_pairs), "unique_inputs": len(cache)}
        dev_history.append(result)
        print("development=" + canonical(result), flush=True)
        return correct == len(dev_pairs)

    def checkpoint() -> None:
        weights = dict(tree_flatten(model.parameters()))
        mx.eval(weights)
        pending = args.output / "pending.safetensors"
        mx.save_safetensors(str(pending), weights)
        os.replace(pending, args.output / "model.safetensors")
        manifest = {
            "dsl_version": "som.dsl.v2"
            if any(
                value["version"] == "som.dsl.v2" for value in (template or {}).values()
            )
            else "som.dsl.v1",
            "blocks_sha256": sha256(args.blocks),
            "files": {
                name: sha256(args.output / name)
                for name in (
                    "tokenizer.json",
                    "config.json",
                    "model.safetensors",
                    *(["dsl_template.json"] if template is not None else []),
                )
            },
            "training_steps": completed,
            "elapsed_seconds": round(time.monotonic() - start, 3),
            "data_sha256": sha256(args.data),
            "development_sha256": sha256(args.dev) if args.dev else None,
            "templates_sha256": sha256(args.templates) if args.templates else None,
            "development_checks": dev_history,
            "selection": "earliest perfect development checkpoint" if args.stop_at_perfect else "fixed step budget",
        }
        pending_manifest = args.output / "pending.manifest.json"
        pending_manifest.write_text(canonical(manifest) + "\n", encoding="utf-8")
        os.replace(pending_manifest, args.output / "manifest.json")

    for step in range(args.steps):
        if time.monotonic() - start >= args.hours * 3600:
            break
        if step % len(order) == 0:
            rng.shuffle(order)
        tokens, prefix = examples[order[step % len(order)]]
        value, grads = loss_and_grad(model, mx.array(tokens, dtype=mx.int32), prefix)
        optimizer.update(model, grads)
        mx.eval(model.parameters(), optimizer.state, value)
        completed += 1
        if (step + 1) % 100 == 0:
            print(f"step={step + 1} loss={value.item():.4f}", flush=True)
        if completed % args.save_every == 0:
            perfect = evaluate_dev() if dev_pairs else False
            checkpoint()
            if perfect and args.stop_at_perfect:
                break
    checkpoint()
    print(
        f"exported {args.output}; examples={len(examples)} "
        f"steps={completed} elapsed_s={time.monotonic() - start:.1f}",
        flush=True,
    )


if __name__ == "__main__":
    train()

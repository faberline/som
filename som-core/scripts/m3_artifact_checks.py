"""Check one actual DSL and product. Behavior answers come from requirements."""

from __future__ import annotations

from copy import deepcopy
import gzip
import hashlib
import json
from pathlib import Path

from audit_business_transactions import source_hashes
from som_core import composed, flow_codec, flow_compact, quality
from som_core.flow_m3_evidence import verify_artifact_record
from som_core.train_flow import LEGACY
from verify_flow_masks import tokens, trace
from verify_flow_service import verify as verify_behavior
from verify_python_quality import verify as verify_quality


def exchange(process, value: dict) -> dict:
    process.stdin.write(composed.canonical(value) + "\n")
    process.stdin.flush()
    line = process.stdout.readline()
    if not line:
        raise RuntimeError("native verification process stopped before a complete response")
    return json.loads(line)


def selected_sequence(prompt: dict, plan: dict, output_ids: list[int] | None) -> list[int]:
    """Retain the emitted token order. Never reconstruct an actual model choice."""
    if output_ids is None:
        return tokens(prompt, plan)
    if not output_ids or any(type(token) is not int or not 0 <= token < len(flow_codec.TOKENS) for token in output_ids):
        raise ValueError("actual model output contains an invalid token ID")
    raw = "".join(flow_codec.TOKENS[token] for token in output_ids)
    if flow_codec.decode(prompt, raw, flow_compact.metadata()) != plan:
        raise ValueError("actual model tokens differ from its selected DSL")
    return list(output_ids)


def check(prompt: dict, plan: dict, files: dict[str, str], directory: Path,
          fixture: dict, library: dict, assembler, mask, *,
          actual_output_ids: list[int] | None = None, actual_input: str | None = None) -> dict:
    """Use actual selected choices for parity, quality and independent HTTP."""
    record = {"passed": False, "prompt_sha256": hashlib.sha256(composed.canonical(prompt).encode()).hexdigest()}
    (directory / "prompt.json").write_text(composed.canonical(prompt) + "\n")
    (directory / "dsl.json").write_text(composed.canonical(plan) + "\n")
    if actual_output_ids is not None:
        features = flow_codec.features_m3_bound(prompt, flow_compact.metadata())
        if actual_input != features:
            raise ValueError("actual M3 model input differs from its bound prompt")
    else:
        features = flow_codec.features(prompt, flow_compact.metadata())
    encoded = flow_codec.encode(prompt, plan, flow_compact.metadata())
    (directory / "model-input.txt").write_text(features)
    reference = flow_compact.assemble(prompt, plan, library, LEGACY / "ruff")
    native = exchange(assembler, {"prompt": prompt, "dsl": plan, "formatter_model": str(LEGACY)})
    if native.get("status") != "ok" or native.get("files") != files or reference != files:
        raise ValueError("same actual DSL has different Python/Rust final files")
    (directory / "response.json").write_text(composed.canonical(native) + "\n")
    record.update(byte_parity=True, normal_accepted=True,
                  formatted_bytes=sum(len(value.encode()) for value in files.values()),
                  files=len(files), blocks=sum(len(file["blocks"]) for file in plan["files"]),
                  input_tokens=features.count("<"), output_tokens=encoded.count("<"))
    if record["files"] > 12 or record["blocks"] > 64 or max(record["input_tokens"], record["output_tokens"]) > 2048:
        raise ValueError("actual generated product exceeds a fixed budget")
    selected = selected_sequence(prompt, plan, actual_output_ids)
    (directory / "selected-output-ids.json").write_text(composed.canonical(selected) + "\n")
    rust = exchange(mask, {"prompt": prompt, "tokens": selected})
    compared = deepcopy(rust)
    for choice in compared.get("trace", []):
        choice.pop("context", None)
    python = trace(prompt, selected)
    record["mask_parity"] = compared == python and rust.get("complete") is True
    with gzip.open(directory / "allowed-sets.json.gz", "wt") as evidence:
        evidence.write(composed.canonical({"rust": rust, "python": python}))
    if not record["mask_parity"]:
        raise ValueError("actual DSL has different native/Python legal token sets")
    product = directory / "product"
    product.mkdir()
    for name, source in files.items():
        destination = product / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(source)
    record["source_sha256"] = source_hashes(product)
    record["format_idempotent"] = all(not name.endswith(".py") or
        quality.format_source(name, source, LEGACY / "ruff", package=prompt["package"]) == source
        for name, source in files.items())
    record["quality"] = verify_quality(product, directory / "quality")
    if not record["quality"]["passed"] or not record["format_idempotent"]:
        raise ValueError("actual generated product fails strict Python quality")
    record["behavior"] = verify_behavior(prompt, product, fixture)
    record["source_unchanged"] = record["source_sha256"] == source_hashes(product)
    record["passed"] = True
    verify_artifact_record(record)
    return record

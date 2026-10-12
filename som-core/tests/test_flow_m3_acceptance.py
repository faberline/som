"""A typed alternative and a frozen checkpoint are separate evidence gates."""

from copy import deepcopy
import json
from pathlib import Path
import sys

import pytest

from som_core import flow, flow_compact
from som_core.flow_m3_evidence import digest
from som_core.train_flow import BLOCKS
from som_core.train_flow_m3 import SIDECARS
from som_core.v1 import V1Error, load_blocks

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "som-core/scripts"))
from run_m3_flow_acceptance import candidate_hashes, dsl_valid


def test_valid_dsl_can_still_omit_a_requested_api_operation() -> None:
    row = json.loads((ROOT / "som-core/data/flow-v7-r10/dev.jsonl")
                     .read_text().splitlines()[0])
    prompt = row["prompt"]
    plan = deepcopy(flow_compact.reference(prompt, load_blocks(BLOCKS.parent)))
    metadata = flow_compact.metadata()["blocks"]
    app = next(file for file in plan["files"] if file["path"] == "{package}/app.py")
    removed = next(block for block in app["blocks"]
                   if metadata[block["id"]].get("operation") == ["parent", "patch"])
    app["blocks"].remove(removed)
    for block in app["blocks"]:
        block["refs"] = [ref for ref in block["refs"]
                         if ref["symbol"] != removed["name"]]
    assert dsl_valid(prompt, plan)
    with pytest.raises(V1Error, match="missing, extra or out of order"):
        flow.validate_plan(prompt, plan, flow_compact.metadata())


def test_selected_dsl_without_write_authorization_is_invalid() -> None:
    row = json.loads((ROOT / "som-core/data/flow-v7-r10/dev.jsonl")
                     .read_text().splitlines()[0])
    prompt = row["prompt"]
    plan = flow_compact.reference(prompt, load_blocks(BLOCKS.parent))
    plan["flow"]["steps"].pop(0)
    assert not dsl_valid(prompt, plan)


def test_changed_checkpoint_bytes_cannot_reuse_the_manifest(tmp_path: Path) -> None:
    # These are marker files, not executable model weights.
    names = {"tokenizer.json", "config.json", "model.safetensors", *SIDECARS}
    for name in names:
        (tmp_path / name).write_bytes(name.encode())
    legacy = tmp_path / "legacy"
    legacy.mkdir()
    (legacy / "manifest.json").write_text("{}")
    manifest = {"files": {name: digest(tmp_path / name) for name in names},
                "legacy_manifest_sha256": digest(legacy / "manifest.json")}
    (tmp_path / "manifest.json").write_text(json.dumps(manifest))
    initial = candidate_hashes(tmp_path)
    assert "legacy/manifest.json" in initial
    (tmp_path / "config.json").write_text("changed configuration")
    with pytest.raises(ValueError, match="checkpoint bytes"):
        candidate_hashes(tmp_path)
    (tmp_path / "config.json").write_bytes(b"config.json")
    (legacy / "manifest.json").write_text('{"changed":true}')
    with pytest.raises(ValueError, match="frozen legacy"):
        candidate_hashes(tmp_path)

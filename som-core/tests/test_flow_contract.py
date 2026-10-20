"""Security and semantic obligations for the unreleased bounded-flow format."""

from copy import deepcopy
import hashlib
import json
from pathlib import Path

import pytest

from som_core import composed, flow, flow_codec
from som_core.v1 import V1Error, load_blocks

ROOT = Path(__file__).resolve().parents[2]
CORPUS = ROOT / "som-core/data/flow-v7-r10"


def row(split="formal", index=0):
    return json.loads((CORPUS/(split+".jsonl")).read_text().splitlines()[index])["prompt"]


def prompt_failure(prompt, change):
    p = deepcopy(prompt)
    steps = p["flow"]["steps"]
    tx = steps[1]
    child = tx["steps"][-1]
    branch = steps[2]
    if change == "auth": steps.pop(0)
    elif change == "write_outside_tx": steps[1:2] = tx["steps"]
    elif change == "nested_tx":
        inner=deepcopy(tx); inner["name"]="inner_tx"; tx["steps"]=[inner]
    elif change == "missing_inverse": child["undo"] = None
    elif change == "forward_ref": tx["steps"][0]["args"]["data"] = {"step": child["name"], "field": "id"}
    elif change == "wrong_type": child["args"]["parent_id"] = {"input": "choose"}
    elif change == "cross_arm": branch["else"][0]["args"]["parent_id"] = {"step": "yes_export", "field": "size"}
    elif change == "branch_leak": p["flow"]["result"]["leak"] = {"step": "yes_export", "field": "size"}
    elif change == "join_type": branch["exports"]["exported"]["else"]["field"] = "size"
    elif change == "self_ref": child["args"]["parent_id"] = {"step": child["name"], "field": "id"}
    elif change == "delete": child["op"] = "child.delete"
    elif change == "sql": child["args"]["sql"] = "DELETE FROM parents"
    elif change == "raw_code": child["args"]["parent_id"] = "eval('1')"
    elif change == "provider_in_tx":
        child.update(op="object.put", args={"key": {"literal": "x", "type": "string"}, "body": {"literal": "eA==", "type": "bytes"}})
    elif change == "provider_absent":
        steps.append({"name": "object", "op": "object.get", "args": {"key": {"literal": "x", "type": "string"}}, "undo": None})
    elif change == "nonfinite": branch["condition"]["right"] = {"literal": float("inf"), "type": "float"}
    elif change == "bad_literal": branch["condition"]["right"] = {"literal": 1, "type": "boolean"}
    elif change == "bad_date": branch["condition"]["right"] = {"literal": "2023-02-29", "type": "date"}
    elif change == "null_nonnullable": branch["condition"] = {"op": "is_null", "left": {"input": "choose"}}
    elif change == "ordered_boolean": branch["condition"]["op"] = "lt"
    elif change == "bad_name": p["flow"]["inputs"][0]["name"] = "model_dump"
    elif change == "malformed_ref": child["args"]["parent_id"] = {"step": [], "field": "id"}
    elif change == "malformed_type": p["flow"]["inputs"][0]["type"] = []
    elif change == "route_collision": p["resources"]["parent"]["path"] = "/flow-runs/x"
    elif change == "wrong_contract": p["contract"]["sha256"] = "0"*64
    else: raise ValueError(change)
    return p


REJECTIONS = {
    "auth": "INVALID_TRANSACTION", "write_outside_tx": "INVALID_TRANSACTION",
    "nested_tx": "INVALID_TRANSACTION", "missing_inverse": "INVALID_COMPENSATION",
    "forward_ref": "INVALID_REF", "wrong_type": "INVALID_REF",
    "cross_arm": "INVALID_REF", "branch_leak": "INVALID_REF", "join_type": "INVALID_REF",
    "self_ref": "INVALID_REF", "delete": "UNSUPPORTED_LOGIC", "sql": "INVALID_SCHEMA",
    "raw_code": "INVALID_REF", "provider_in_tx": "INVALID_TRANSACTION",
    "provider_absent": "INVALID_TRANSACTION", "nonfinite": "INVALID_REF",
    "bad_literal": "INVALID_REF", "bad_date": "INVALID_REF", "null_nonnullable": "INVALID_REF",
    "ordered_boolean": "INVALID_REF", "bad_name": "INVALID_VALUE",
    "malformed_ref": "INVALID_REF", "malformed_type": "INVALID_REF",
    "route_collision": "INVALID_VALUE", "wrong_contract": "UNSUPPORTED_CONTRACT",
}


@pytest.mark.parametrize("change,code", REJECTIONS.items())
def test_prompt_contract_refuses_unsafe_requests(change, code):
    with pytest.raises(V1Error) as error:
        flow.validate(prompt_failure(row(), change))
    assert error.value.code == code


def test_three_branch_paths_and_two_transactions_are_representable():
    p = row(index=110)
    flow.validate(p)
    assert sum(step["op"] == "transaction" for step in p["flow"]["steps"]) == 2
    branch = next(step for step in p["flow"]["steps"] if step["op"] == "if")
    assert branch["then"][0]["op"] == "if"
    nested = branch["then"][0]
    assert nested["exports"]["nested_exported"]["then"]["step"] == nested["then"][0]["name"]
    assert nested["exports"]["nested_exported"]["else"]["step"] == nested["else"][0]["name"]


def test_equivalent_blocks_do_not_require_the_reference_plan():
    p = row("dev")
    a = flow.plan_tree(p)
    b = flow.plan_tree(p, "checked")
    assert a != b
    for candidate in (a, b): flow.validate_tree(p, candidate, flow.catalog())
    b["steps"][1]["id"] = "flow.mongodb.parent.get"
    with pytest.raises(V1Error) as error: flow.validate_tree(p, b, flow.catalog())
    assert error.value.code == "INVALID_COMPOSITION"


def test_wrong_model_choices_are_not_replaced():
    p = row(); tree = flow.plan_tree(p)
    tree["steps"][1]["steps"][0]["id"] = "flow.postgresql.parent.get"
    before = deepcopy(tree)
    with pytest.raises(V1Error): flow.validate_tree(p, tree, flow.catalog())
    assert tree == before


@pytest.mark.parametrize("undo", [[], {}, 123, True])
def test_malformed_model_inverse_has_a_defined_rejection(undo):
    p = row(); tree = flow.plan_tree(p)
    tree["steps"][1]["steps"][0]["undo"] = undo
    before = deepcopy(tree)
    with pytest.raises(V1Error) as error:
        flow.validate_tree(p, tree, flow.catalog())
    assert error.value.code == "INVALID_COMPENSATION"
    assert tree == before


def test_actual_model_input_preserves_decisions_and_removes_names():
    p = row(); q = deepcopy(p)
    q["package"] = "renamed_app"
    q["resources"]["parent"]["name"] = "RenamedParent"
    q["resources"]["parent"]["table"] = "renamed_parents"
    q["resources"]["parent"]["path"] = "/renamed_parents"
    q["resources"]["parent"]["fields"][0]["name"] = "renamed_title"
    q["flow"]["name"] = "renamed_flow"
    assert flow.semantic_view(p) == flow.semantic_view(q)
    # Semantic tokens exclude names. The following binding tokens expose only
    # the safe destination indices, which may change when strings collide.
    semantic = "<input>"+"".join(flow_codec.serialize(flow.semantic_view(p),flow_codec.pool(p,normalized=True)))
    assert flow_codec.features(p).startswith(semantic)
    assert flow_codec.features(q).startswith(semantic)
    assert "renamed_title" not in flow_codec.features(q)
    assert "renamed_flow" not in flow_codec.features(q)
    for section, key, value in [("capabilities", "auth", "firebase"), ("capabilities", "database", "mongodb")]:
        different = deepcopy(p); different[section][key] = value
        assert flow_codec.features(p) != flow_codec.features(different)
    different = deepcopy(p)
    different["flow"]["steps"][2]["condition"]["op"] = "ne"
    assert flow_codec.features(p) != flow_codec.features(different)


def test_all_frozen_inputs_and_model_output_tokens_fit_and_roundtrip():
    blocks = load_blocks(ROOT / "som-code-python/data/blocks/flow-v7")
    manifest = json.loads((CORPUS/"manifest.json").read_text())
    assert manifest["block_catalog_sha256"] == hashlib.sha256(flow.CATALOG.read_bytes()).hexdigest()
    assert manifest["block_library_sha256"] == hashlib.sha256((ROOT / "som-code-python/data/blocks/flow-v7/blocks.json").read_bytes()).hexdigest()
    seen = {}
    for split in ("dev", "formal"):
        hashes=[]; structures=[]
        for line in (CORPUS/(split+".jsonl")).read_text().splitlines():
            p=json.loads(line)["prompt"]
            features=flow_codec.features(p)
            dsl=flow.reference(p, blocks)
            assert flow_codec.decode(p, flow_codec.encode(p, dsl)) == dsl
            hashes.append(hashlib.sha256(features.encode()).hexdigest())
            structures.append(hashlib.sha256(composed.canonical(flow.semantic_view(p)).encode()).hexdigest())
        assert hashes == manifest["actual_model_input_sha256"][split]
        assert structures == manifest["normalized_input_sha256"][split]
        assert len(set(hashes)) == len(hashes)
        seen[split] = set(hashes)
    assert not seen["dev"] & seen["formal"]


def test_codec_rejects_truncation_unknown_and_duplicate_keys():
    p=row("dev"); values=flow_codec.pool(p)
    key=values.index("version")
    bad=["<map><eos>", "<v:999999><eos>", "<map><v:%d><v:0><v:%d><v:0></map><eos>"%(key,key), "<list></list><eos>extra", "<list>"*34+"<eos>"]
    for text in bad:
        with pytest.raises(V1Error): flow_codec.decode(p,text)


def test_old_block_objects_are_unchanged():
    old=load_blocks(ROOT/"som-code-python/data/blocks/composed-quality")
    candidate=load_blocks(ROOT/"som-code-python/data/blocks/flow-v7")
    # The #57 `.self` variants were appended to composed-quality after flow-v7 was frozen.
    variants={key for key in old if key.endswith(".self")}
    assert variants=={"orm.base.self","orm.composed.mongo.base.self"}
    assert all(key.removesuffix(".self") in candidate for key in variants)
    assert all(candidate[key] == value for key,value in old.items() if key not in variants)


@pytest.mark.parametrize("operations", [
    [{"scope": "parent", "action": "get"}],
    [{"scope": "child", "action": "list"}, {"scope": "parent", "action": "create"}],
    [{"scope": "batch", "action": "batch_create"}, {"scope": "child", "action": "patch"}],
])
def test_v7_operation_subsets_keep_only_the_declared_routes(operations):
    p = row("dev")
    p["operations"] = operations
    blocks = load_blocks(ROOT / "som-code-python/data/blocks/flow-v7")
    dsl = flow.reference(p, blocks)
    catalog = json.loads(flow.CATALOG.read_text())
    selected = [catalog["blocks"][block["id"]]["operation"]
                for file in dsl["files"] for block in file["blocks"]
                if catalog["blocks"][block["id"]]["operation"] is not None]
    assert selected == [[item["scope"], item["action"]] for item in operations]
    assert flow_codec.decode(p, flow_codec.encode(p, dsl)) == dsl
    flow.resolve(p, dsl)

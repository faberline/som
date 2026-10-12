"""Seal name-independent v7 tasks before any test-weight training."""

from copy import deepcopy
import argparse
import hashlib
import itertools
import json
from pathlib import Path

from som_core import composed, flow, flow_codec

ROOT = Path(__file__).resolve().parents[2]
DEST = ROOT / "som-core/data/flow-v7"


def action(name, op, args=None):
    return {"name": name, "op": op, "args": args or {},
            "undo": "restore_patch" if op.endswith(".patch") else "delete_created" if op in flow.MUTATIONS else None}


def input_ref(name): return {"input": name}
def result_ref(name, field=""): return {"step": name, "field": field}


def requirement(database, auth, storage, workflow, index, style=0):
    initial = json.loads((ROOT/"som-core/data/composed/regression.jsonl").read_text().splitlines()[0])["prompt"]
    p = deepcopy(initial)
    p.update(version=flow.VERSION, contract={"id": flow.CONTRACT, "sha256": flow.contract_hash()}, package=f"flow_app_{index}")
    p["capabilities"] = {"database": database, "auth": auth, "storage": storage, "workflow": workflow,
                         "value_field": "amount" if workflow in {"statistics", "decimal_summary"} else None,
                         "group_field": "unit" if workflow == "decimal_summary" else None}
    def field(name, kind, required=True, **extra):
        return {"name": name, "type": {"string": "str", "integer": "int", "boolean": "bool"}.get(kind, kind), "required": required, "unique": False, **extra}
    p["resources"]["parent"] = {"name": "Parent", "table": "parents", "path": "/parents",
        "fields": [field("title", "string"), field("servings", "integer"), field("active", "boolean", False, default=False), field("due", "date", False, default=None), field("memo", "string", False, default=None)],
        "query": {"filter_field": "active", "date_field": "due", "max_limit": 100}}
    p["resources"]["child"] = {"name": "Child", "table": "children", "path": "/children",
        "fields": [field("title", "string"), field("code", "string", unique=True), field("amount", "float"), field("unit", "string"), field("active", "boolean", False, default=False), field("due", "date", False, default=None), field("memo", "string", False, default=None), field("count", "integer")],
        "query": {"filter_field": "active", "date_field": "due", "max_limit": 100}}
    p["relation"] = {"foreign_key": "owner_id", "on_delete": "restrict"}
    p["migration"] = {"revision": f"f{index:04d}", "down_revision": None}
    args = [{"name": "parent", "type": "parent_input"}, {"name": "child", "type": "child_input"}, {"name": "choose", "type": "boolean"}]
    nodes = [action("access", "authorize")]
    create_parent = action("created_parent", "parent.create", {"data": input_ref("parent")})
    create_child = action("created_child", "child.create", {"parent_id": result_ref("created_parent", "id"), "data": input_ref("child")})
    if style % 4 == 1:
        nodes += [{"name": "first_tx", "op": "transaction", "steps": [create_parent]}, {"name": "second_tx", "op": "transaction", "steps": [create_child]}]
    elif style % 4 == 2:
        args.append({"name": "children", "type": "children_input"})
        nodes.append({"name": "write_tx", "op": "transaction", "steps": [action("batch_rows", "batch.create", {"parent": input_ref("parent"), "children": input_ref("children")})]})
        nodes.append(action("created_parent", "parent.get", {"parent_id": result_ref("batch_rows", "parent.id")}))
    else:
        nodes.append({"name": "write_tx", "op": "transaction", "steps": [create_parent, create_child]})
    if style % 4 == 3:
        args.append({"name": "patch", "type": "child_patch"})
        nodes[-1]["steps"].append(action("changed_child", "child.patch", {"parent_id": result_ref("created_parent", "id"), "child_id": result_ref("created_child", "id"), "data": input_ref("patch")}))
    if style >= 12:
        nodes.append(action("read_parent", "parent.get", {"parent_id": result_ref("created_parent", "id")}))
    export = action("yes_export", "workflow.run", {"parent_id": result_ref("created_parent", "id")})
    otherwise = action("no_export", "workflow.run", {"parent_id": result_ref("created_parent", "id")})
    if style >= 20 and style % 4 != 3:
        nested = {"name": "nested_choice", "op": "if", "condition": {"op": "gt", "left": result_ref("created_parent", "servings"), "right": {"literal": 0, "type": "integer"}},
                  "then": [export], "else": [action("nested_no_export", "workflow.run", {"parent_id": result_ref("created_parent", "id")})],
                  "exports": {"nested_exported": {"then": result_ref("yes_export"), "else": result_ref("nested_no_export")}}}
        then_nodes = [nested]
        then_result = result_ref("nested_exported")
    else:
        then_nodes = [export]
        then_result = result_ref("yes_export")
    condition = {"op": "eq" if style % 2 == 0 else "ne", "left": input_ref("choose"), "right": {"literal": True, "type": "boolean"}}
    nodes.append({"name": "choice", "op": "if", "condition": condition, "then": then_nodes, "else": [otherwise],
                  "exports": {"exported": {"then": then_result, "else": result_ref("no_export")}}})
    if storage != "none":
        args.append({"name": "key", "type": "string"})
        nodes.append(action("stored", "object.put", {"key": input_ref("key"), "body": result_ref("exported", "body")}))
    result_fields = ["id", "title", "servings", "active", "due", "memo"]
    result = {"parent": result_ref("created_parent", result_fields[(style//4) % len(result_fields)])}
    if storage != "none": result["object"] = result_ref("stored", "sha256")
    p["flow"] = {"name": "publish", "inputs": args, "steps": nodes, "result": result}
    flow.validate(p)
    return p


def smoke(index):
    p = requirement("postgresql" if index % 2 == 0 else "mongodb", ("jwt", "firebase", "google_oauth")[index % 3], "none", "json_export", 1000+index)
    p["flow"]["inputs"] = [{"name": "parent_id", "type": "integer"}]
    steps = [action("access", "authorize"), action("found", "parent.get", {"parent_id": input_ref("parent_id")})]
    if index >= 6: steps.append(action("rows", "child.list", {"parent_id": result_ref("found", "id")}))
    p["flow"]["steps"] = steps
    p["flow"]["result"] = {"value": result_ref("found", ("id", "title", "servings", "active", "due", "memo")[index % 6])}
    return flow.validate(p)


def semantic_case(index):
    p = requirement("postgresql", "jwt", "s3", "decimal_summary", 200+index, index+1)
    steps = p["flow"]["steps"]
    inputs = p["flow"]["inputs"]
    extra = None
    if index == 0:
        inputs.append({"name": "parent_patch", "type": "parent_patch"})
        steps[2]["steps"].append(action("changed_parent", "parent.patch", {"parent_id": result_ref("created_parent", "id"), "data": input_ref("parent_patch")}))
    elif index == 1:
        # A batch result has no indexed element references. Use its scoped list.
        extra = action("listed_children", "child.list", {"parent_id": result_ref("created_parent", "id")})
    elif index == 2:
        extra = action("read_child", "child.get", {"parent_id": result_ref("created_parent", "id"), "child_id": result_ref("created_child", "id")})
    elif index == 3:
        extra = action("listed_parents", "parent.list")
    elif index == 4:
        p["capabilities"]["workflow"] = "csv"
        p["capabilities"]["value_field"] = None
        p["capabilities"]["group_field"] = None
        inputs.append({"name": "csv", "type": "string"})
        steps[2]["steps"] = [action("imported", "workflow.import", {"parent_id": result_ref("created_parent", "id"), "csv": input_ref("csv")})]
    elif index == 5:
        steps.append(action("read_object", "object.get", {"key": input_ref("key")}))
    elif index == 8:
        choice = next(step for step in steps if step["op"] == "if")
        choice["condition"] = {"op": "is_null", "left": result_ref("created_parent", "due")}
    elif index == 9:
        inputs.append({"name": "cutoff", "type": "date"})
        choice = next(step for step in steps if step["op"] == "if")
        choice["condition"] = {"op": "lt", "left": input_ref("cutoff"), "right": {"literal": "2030-01-01", "type": "date"}}
    if extra is not None: steps.insert(-2, extra)
    return flow.validate(p)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=DEST)
    args = parser.parse_args()
    destination = args.output
    if destination.exists() and any(destination.iterdir()): raise ValueError("preserve existing corpus")
    destination.mkdir(parents=True, exist_ok=True)
    formal = []
    for index, caps in enumerate(itertools.product(("postgresql", "mongodb"), ("jwt", "firebase", "google_oauth"), ("none", "s3", "gcs"), ("json_export", "gzip_export", "csv", "decimal_summary", "statistics"))):
        formal.append({"id": f"cap-{index:03d}", "prompt": requirement(*caps, index), "checks": ["both_branch_arms", "typed_dataflow", "transaction", "idempotency", "ownership", "migration_restart", "compensation"]})
    for index in range(24):
        formal.append({"id": f"semantic-{index:02d}", "prompt": semantic_case(index),
                       "checks": ["late_failure", "reverse_revision", "restart_recovery", "uncertain_provider", "concurrent_claim", "alternate_legal_dsl"]})
    dev = [{"id": f"smoke-{index:02d}", "prompt": smoke(index), "checks": ["metal_full_decode", "typed_result"]} for index in range(12)]
    signatures = {}
    actual_signatures = {}
    contents = {}
    for label, rows in [("dev", dev), ("formal", formal)]:
        values = [hashlib.sha256(composed.canonical(flow.semantic_view(row["prompt"])).encode()).hexdigest() for row in rows]
        if len(set(values)) != len(values): raise ValueError("duplicate normalized input in " + label)
        signatures[label] = values
        actual = [hashlib.sha256(flow_codec.features(row["prompt"]).encode()).hexdigest() for row in rows]
        if len(set(actual)) != len(actual): raise ValueError("duplicate actual model input in " + label)
        actual_signatures[label] = actual
        contents[label+".jsonl"] = "".join(composed.canonical(row)+"\n" for row in rows)
    if set(signatures["dev"]) & set(signatures["formal"]): raise ValueError("development/formal overlap")
    if set(actual_signatures["dev"]) & set(actual_signatures["formal"]): raise ValueError("actual model input overlap")
    for name, content in contents.items(): (destination/name).write_text(content)
    manifest = {"version": "som.flow.corpus.v1", "seed": 17, "frozen_before_training": True,
                "counts": {"dev": 12, "formal": 114, "capability_tuples": 90, "semantic_cases": 24},
                "unique_normalized_inputs": {key: len(set(value)) for key,value in signatures.items()},
                "normalized_input_sha256": signatures, "split_overlap": 0,
                "actual_model_input_sha256": actual_signatures, "actual_input_overlap": 0,
                "codec_version": flow_codec.VERSION, "codec_source_sha256": hashlib.sha256((ROOT/"som-core/src/som_core/flow_codec.py").read_bytes()).hexdigest(),
                "block_catalog_sha256": hashlib.sha256(flow.CATALOG.read_bytes()).hexdigest(),
                "block_library_sha256": hashlib.sha256((ROOT/"som-code-python/data/blocks/flow-v7/blocks.json").read_bytes()).hexdigest(),
                "projection_source_sha256": hashlib.sha256((ROOT/"som-core/src/som_core/flow.py").read_bytes()).hexdigest(),
                "files": {name: hashlib.sha256((destination/name).read_bytes()).hexdigest() for name in ("dev.jsonl", "formal.jsonl")},
                "contract_sha256": flow.contract_hash(), "checks": ["refs", "order", "branch_scope", "auth", "transaction", "compensation", "provider_versions", "leases", "migration", "strict_quality", "limits"],
                "training_exclusions": ["Experiment", "Observation", "Document", "Review"], "paid_agent_work": False}
    (destination/"manifest.json").write_text(json.dumps(manifest, indent=2)+"\n")
    print(json.dumps(manifest["counts"]))


if __name__ == "__main__": main()

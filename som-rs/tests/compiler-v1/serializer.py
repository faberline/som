#!/usr/bin/env python3
"""Independent SOM typed-prompt to CNL serializer and fixture generator.

This file uses only public prompt fixtures and the saved grammar field map.
It does not import a compiler, parser, planner, model, answer set, or corpus.
"""
from __future__ import annotations

import copy
import hashlib
import itertools
import json
import pathlib
import sys
from typing import Any

HERE = pathlib.Path(__file__).resolve().parent
FIXTURES_PATH = HERE / "typed-json-fixtures.json"
MANIFEST_PATH = HERE / "cnl-positive-fixtures.json"

UNDO = {None: "On failure, use no undo.", "delete_created": "On failure, undo with delete_created.", "restore_patch": "On failure, undo with restore_patch."}
PREDICATE_TEXT = {
    "eq": " is equal to ", "ne": " is not equal to ",
    "lt": " is less than ", "le": " is at most ",
    "gt": " is greater than ", "ge": " is at least ",
}
SCALAR_TEXT = {"string": "string", "integer": "integer", "float": "float", "boolean": "boolean", "date": "date", "bytes": "bytes"}


def _json_scalar(value: Any) -> str:
    if value is None or type(value) in (bool, int, float, str):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    raise TypeError("CNL scalar must be null, boolean, integer, finite number, or string")


def _value(ref: dict[str, Any]) -> str:
    if set(ref) == {"input"} and isinstance(ref["input"], str):
        return "input " + ref["input"]
    if set(ref) == {"step", "field"} and isinstance(ref["step"], str) and isinstance(ref["field"], str):
        return "step " + ref["step"] + " field " + _json_scalar(ref["field"])
    if set(ref) == {"literal", "type"} and isinstance(ref["type"], str):
        return "literal " + SCALAR_TEXT[ref["type"]] + " " + _json_scalar(ref["literal"])
    raise ValueError("unknown runtime reference form")


def _field(field: dict[str, Any]) -> str:
    line = "Field {name} is {required} {type} and is {unique}".format(
        name=field["name"], required="required" if field["required"] else "optional",
        type=field["type"], unique="unique" if field["unique"] else "not unique")
    if "default" in field:
        line += " with default " + _json_scalar(field["default"])
    return line + "."


def _query(query: dict[str, Any]) -> list[str]:
    lines = ["Begin query."]
    selector = query["filter_field"]
    lines.append("Filter exactly on field " + selector + "." if selector is not None else "Use no exact filter.")
    if "date_field" in query:
        date = query["date_field"]
        lines.append("Filter before date on field " + date + "." if date is not None else "Use no date filter.")
    lines.extend(["Limit query results to " + str(query["max_limit"]) + ".", "End query."])
    return lines


def _resource(role: str, resource: dict[str, Any], related: bool) -> list[str]:
    start = "Begin " + role + " resource." if related else "Begin resource."
    end = "End " + role + " resource." if related else "End resource."
    lines = [start, "Use resource name " + resource["name"] + ".", "Use table " + resource["table"] + ".", "Use path " + _json_scalar(resource["path"]) + "."]
    lines.extend(_field(f) for f in resource["fields"])
    if related:
        lines.extend(_query(resource["query"]))
    lines.append(end)
    return lines


def _flow_node(node: dict[str, Any]) -> list[str]:
    name, op = node["name"], node["op"]
    if op == "transaction":
        lines = ["Begin transaction " + name + "."]
        for step in node["steps"]:
            lines.extend(_flow_node(step))
        lines.append("End transaction " + name + ".")
        return lines
    if op == "if":
        condition = node["condition"]
        left = _value(condition["left"])
        if condition["op"] == "is_null":
            predicate = left + " is null"
        else:
            predicate = left + PREDICATE_TEXT[condition["op"]] + _value(condition["right"])
        lines = ["Begin branch " + name + " when " + predicate + ".", "Begin then."]
        for step in node["then"]:
            lines.extend(_flow_node(step))
        lines.append("End then.")
        lines.append("Begin else.")
        for step in node["else"]:
            lines.extend(_flow_node(step))
        lines.append("End else.")
        if node["exports"]:
            for export in sorted(node["exports"]):
                refs = node["exports"][export]
                lines.append("Export " + export + " from then " + _value(refs["then"]) + " and from else " + _value(refs["else"]) + ".")
        else:
            lines.append("Export no values.")
        lines.append("End branch " + name + ".")
        return lines
    lines = ["Begin step " + name + " with operation " + op + "."]
    for arg in sorted(node["args"]):
        lines.append("Set argument " + arg + " to " + _value(node["args"][arg]) + ".")
    lines.append(UNDO[node["undo"]])
    lines.append("End step " + name + ".")
    return lines


def serialize_prompt(prompt: dict[str, Any], statement_map: dict[str, Any] | None = None, *, contract_dir: pathlib.Path | None = None) -> str:
    """Return LF-terminated CNL v1 text. Arrays retain order; map keys sort where grammar treats them as maps."""
    if statement_map is None:
        if contract_dir is None:
            raise ValueError("pass statement_map or contract_dir")
        statement_map = json.loads((contract_dir / "field-statement-map.json").read_text())
    smap = statement_map
    version = int(prompt["version"].rsplit("v", 1)[1])
    lines = ["Use SOM CNL version 1.", f"Use prompt version {version}.", "Use package " + prompt["package"] + "."]
    if version in (6, 7):
        contract = prompt["contract"]
        lines.append("Bind contract " + contract["id"] + " with SHA-256 " + _json_scalar(contract["sha256"]) + ".")
    if version <= 4:
        lines.extend(_resource("", prompt["resource"], False))
        if "query" in prompt:
            lines.extend(_query(prompt["query"]))
    else:
        for role in ("parent", "child"):
            lines.extend(_resource(role, prompt["resources"][role], True))
        relation = prompt["relation"]
        if relation.get("on_delete") != "restrict":
            raise ValueError("related prompts require on_delete=restrict")
        lines.append("Relate child to parent with foreign key " + relation["foreign_key"] + " and restrict deletion.")
    migration = prompt["migration"]
    lines.append("Use migration revision " + migration["revision"] + ".")
    down = migration["down_revision"]
    lines.append("Use previous migration revision " + down + "." if down is not None else "Use no previous migration revision.")
    if version >= 5:
        if "capabilities" not in prompt:
            lines.append("Use no capabilities.")
        else:
            caps = prompt["capabilities"]
            lines.extend(["Begin capabilities.", "Use database " + caps["database"] + ".", "Use authentication " + caps["auth"] + ".", "Use object storage " + caps["storage"] + ".", "Use workflow " + caps["workflow"] + "."])
            lines.append("Use workflow value field " + caps["value_field"] + "." if caps["value_field"] is not None else "Use no workflow value field.")
            lines.append("Use workflow group field " + caps["group_field"] + "." if caps["group_field"] is not None else "Use no workflow group field.")
            lines.append("End capabilities.")
    lines.append("Begin routes.")
    if version <= 4:
        lines.extend(prompt["logic"])
    elif version == 5:
        for entry in prompt["logic"]:
            lines.append("For " + entry["scope"] + ": " + entry["step"])
    else:
        for entry in prompt["operations"]:
            if entry["scope"] == "batch":
                lines.append("Allow batch creation.")
            else:
                lines.append("Allow " + entry["action"] + " on " + entry["scope"] + ".")
    lines.append("End routes.")
    if version <= 5:
        expected_check = smap["check_sentences"][str(version)]
        if prompt.get("tests") != [expected_check]:
            raise ValueError(f"tests must exactly match the fixed public check sentence for v{version}")
        lines.append("Begin checks.")
        lines.append(prompt["tests"][0])
        lines.append("End checks.")
    if version == 7:
        flow = prompt["flow"]
        lines.append("Begin flow " + flow["name"] + ".")
        if flow["inputs"]:
            for item in flow["inputs"]:
                lines.append("Input " + item["name"] + " has type " + item["type"] + ".")
        else:
            lines.append("Use no inputs.")
        lines.append("Begin steps.")
        for node in flow["steps"]:
            lines.extend(_flow_node(node))
        lines.append("End steps.")
        if flow["result"]:
            for key in sorted(flow["result"]):
                lines.append("Return " + key + " from " + _value(flow["result"][key]) + ".")
        else:
            lines.append("Return no values.")
        lines.append("End flow " + flow["name"] + ".")
    lines.append("End request.")
    return "\n".join(lines) + "\n"


def _v7_base(fixtures: dict[str, Any]) -> dict[str, Any]:
    p = copy.deepcopy(fixtures["v7_flow_contract"])
    child = p["resources"]["child"]
    if "score" not in {f["name"] for f in child["fields"]}:
        child["fields"].append({"name": "score", "type": "int", "required": True, "unique": False})
    if "description" not in {f["name"] for f in child["fields"]}:
        child["fields"].append({"name": "description", "type": "str", "required": False, "unique": False, "default": None})
    p["flow"] = {"name": "flowCase", "inputs": [], "steps": [{"name": "auth", "op": "authorize", "args": {}, "undo": None}], "result": {}}
    p["operations"] = ([{"scope": "parent", "action": a} for a in ("create", "get", "list", "patch", "delete")] +
                        [{"scope": "child", "action": a} for a in ("create", "get", "list", "patch", "delete")] +
                        [{"scope": "batch", "action": "batch_create"}])
    return p


def _set_caps(prompt: dict[str, Any], database: str, auth: str, storage: str, workflow: str) -> None:
    caps = prompt["capabilities"]
    caps.update(database=database, auth=auth, storage=storage, workflow=workflow)
    if workflow in ("decimal_summary", "statistics"):
        caps["value_field"] = "score"
        caps["group_field"] = "label" if workflow == "decimal_summary" else None
    else:
        caps["value_field"] = None
        caps["group_field"] = None


def _flow_for_operation(op: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any], str, str]:
    """Return (inputs, steps, capabilities, case label, expected note)."""
    caps = {"database": "postgresql", "auth": "jwt", "storage": "none", "workflow": "json_export", "value_field": None, "group_field": None}
    auth_step = {"name": "auth", "op": "authorize", "args": {}, "undo": None}
    if op == "authorize":
        return [], [auth_step], caps, "authorize_first_once", "Accept one first authorize with no arguments."
    op_args: dict[str, Any] = {}
    inputs: list[dict[str, str]] = []
    txn = op in {"parent.create", "parent.patch", "child.create", "child.patch", "batch.create", "workflow.import"}
    if op == "parent.create":
        inputs = [{"name":"parent_data","type":"parent_input"}]; op_args = {"data":{"input":"parent_data"}}; undo="delete_created"
    elif op == "parent.get":
        inputs = [{"name":"parent_id","type":"integer"}]; op_args={"parent_id":{"input":"parent_id"}}; undo=None
    elif op == "parent.list": op_args={}; undo=None
    elif op == "parent.patch":
        inputs=[{"name":"parent_id","type":"integer"},{"name":"patch","type":"parent_patch"}]; op_args={"parent_id":{"input":"parent_id"},"data":{"input":"patch"}}; undo="restore_patch"
    elif op == "child.create":
        inputs=[{"name":"parent_id","type":"integer"},{"name":"child_data","type":"child_input"}]; op_args={"parent_id":{"input":"parent_id"},"data":{"input":"child_data"}}; undo="delete_created"
    elif op == "child.get":
        inputs=[{"name":"parent_id","type":"integer"},{"name":"child_id","type":"integer"}]; op_args={"parent_id":{"input":"parent_id"},"child_id":{"input":"child_id"}}; undo=None
    elif op == "child.list":
        inputs=[{"name":"parent_id","type":"integer"}]; op_args={"parent_id":{"input":"parent_id"}}; undo=None
    elif op == "child.patch":
        inputs=[{"name":"parent_id","type":"integer"},{"name":"child_id","type":"integer"},{"name":"patch","type":"child_patch"}]; op_args={"parent_id":{"input":"parent_id"},"child_id":{"input":"child_id"},"data":{"input":"patch"}}; undo="restore_patch"
    elif op == "batch.create":
        inputs=[{"name":"parent_data","type":"parent_input"},{"name":"children_data","type":"children_input"}]; op_args={"parent":{"input":"parent_data"},"children":{"input":"children_data"}}; undo="delete_created"
    elif op == "workflow.run":
        inputs=[{"name":"parent_id","type":"integer"}]; op_args={"parent_id":{"input":"parent_id"}}; undo=None
    elif op == "workflow.import":
        caps["workflow"]="csv"; inputs=[{"name":"parent_id","type":"integer"},{"name":"csv_text","type":"string"}]; op_args={"parent_id":{"input":"parent_id"},"csv":{"input":"csv_text"}}; undo="delete_created"
    elif op == "object.put":
        caps["storage"]="s3"; op_args={"key":{"literal":"case-object","type":"string"},"body":{"literal":"YQ==","type":"bytes"}}; undo="delete_created"
    elif op == "object.get":
        caps["storage"]="s3"; inputs=[{"name":"object_key","type":"string"}]; op_args={"key":{"input":"object_key"}}; undo=None
    else: raise ValueError(op)
    action={"name":"call","op":op,"args":op_args,"undo":undo}
    if txn:
        steps=[auth_step,{"name":"writeTxn","op":"transaction","steps":[action]}]
    else:
        steps=[auth_step,action]
    return inputs,steps,caps,"valid_action", "Accept exact public argument types and required undo/transaction/provider boundary."


def _case(case_id: str, category: str, prompt: dict[str, Any], note: str, statement_map: dict[str, Any]) -> dict[str, Any]:
    cnl = serialize_prompt(prompt, statement_map)
    return {"id": case_id, "category": category, "expected": {"status":"accept"}, "expected_prompt": prompt, "cnl": cnl, "prompt_sha256": hashlib.sha256(json.dumps(prompt,sort_keys=True,separators=(",",":"),ensure_ascii=False,allow_nan=False).encode()).hexdigest(), "cnl_sha256": hashlib.sha256(cnl.encode()).hexdigest(), "semantic_expectation": note}


def _normalize_map_statement_order(text: str) -> list[str]:
    """Sort only contiguous clauses whose statements populate JSON maps."""
    lines=text.splitlines()
    out=[]; run=[]
    def flush():
        nonlocal run
        out.extend(sorted(run)); run=[]
    for line in lines:
        if line.startswith(("Set argument ", "Export ")) or line.startswith("Return "):
            run.append(line)
        else:
            flush(); out.append(line)
    flush()
    return out


def build_manifest(*, contract_dir: pathlib.Path, fixtures_path: pathlib.Path | None = None) -> dict[str, Any]:
    fixtures_path = fixtures_path or FIXTURES_PATH
    fixture_data=json.loads(fixtures_path.read_text())["fixtures"]
    statement_map=json.loads((contract_dir / "field-statement-map.json").read_text())
    cases=[]
    for key in ("v1_minimal_crud","v2_string_filter_max20","v3_bool_default","v4_date_before","v5_related_business","v6_composed_operations","v7_flow_contract"):
        cases.append(_case("PAIR_"+key.upper(),"version_positive",fixture_data[key],"Serialize hand-authored public typed prompt without changing array order or typed values.",statement_map))
    cap_base=copy.deepcopy(fixture_data["v6_composed_operations"])
    child=cap_base["resources"]["child"]
    if "score" not in {f["name"] for f in child["fields"]}:
        child["fields"].append({"name":"score","type":"int","required":True,"unique":False})
    cap_values={"database":["postgresql","mongodb"],"auth":["jwt","firebase","google_oauth"],"storage":["none","s3","gcs"],"workflow":["json_export","csv","gzip_export","decimal_summary","statistics"]}
    for database,auth,storage,workflow in itertools.product(cap_values["database"],cap_values["auth"],cap_values["storage"],cap_values["workflow"]):
        p=copy.deepcopy(cap_base); _set_caps(p,database,auth,storage,workflow)
        cid="CAP_"+"_".join((database,auth,storage,workflow)).upper()
        cases.append(_case(cid,"capability_tuple",p,"One of all 90 public enum tuples; numeric field preconditions are met with child.score and, for decimal_summary, required string child.label.",statement_map))
    v7base=_v7_base(fixture_data)
    for op in ("authorize","parent.create","parent.get","parent.list","parent.patch","child.create","child.get","child.list","child.patch","batch.create","workflow.run","workflow.import","object.put","object.get"):
        p=copy.deepcopy(v7base); inputs,steps,caps,_,note=_flow_for_operation(op); p["flow"]={"name":"flowCase","inputs":inputs,"steps":steps,"result":{}}; p["capabilities"].update(caps)
        cases.append(_case("OP_"+op.upper().replace(".","_"),"flow_operation",p,note,statement_map))
    # All six ordered/equality comparators use equal integer input types.
    for pred in ("eq","ne","lt","le","gt","ge"):
        p=copy.deepcopy(v7base); p["flow"]={"name":"flowCase","inputs":[{"name":"left_value","type":"integer"},{"name":"right_value","type":"integer"}],"steps":[{"name":"auth","op":"authorize","args":{},"undo":None},{"name":"compare","op":"if","condition":{"op":pred,"left":{"input":"left_value"},"right":{"input":"right_value"}},"then":[],"else":[],"exports":{}}],"result":{}}
        cases.append(_case("REF_COMPARE_"+pred.upper(),"flow_reference_predicate",p,"Accept the comparator for matching integer scalar references.",statement_map))
    # Nullable branch refinement and a same-typed branch export.
    p=copy.deepcopy(v7base); p["flow"]={"name":"flowCase","inputs":[{"name":"parent_data","type":"parent_input"},{"name":"child_data","type":"child_input"}],"steps":[{"name":"auth","op":"authorize","args":{},"undo":None},{"name":"writeTxn","op":"transaction","steps":[{"name":"created","op":"parent.create","args":{"data":{"input":"parent_data"}},"undo":"delete_created"},{"name":"childMade","op":"child.create","args":{"parent_id":{"step":"created","field":"id"},"data":{"input":"child_data"}},"undo":"delete_created"}]},{"name":"descBranch","op":"if","condition":{"op":"is_null","left":{"step":"childMade","field":"description"}},"then":[],"else":[],"exports":{}}],"result":{}}
    cases.append(_case("REF_NULLABLE_OPTIONAL_FIELD","flow_reference_predicate",p,"Accept is_null for nullable child.description; branch refinement is local.",statement_map))
    p=copy.deepcopy(v7base); p["flow"]={"name":"flowCase","inputs":[{"name":"left_value","type":"integer"},{"name":"right_value","type":"integer"}],"steps":[{"name":"auth","op":"authorize","args":{},"undo":None},{"name":"choose","op":"if","condition":{"op":"eq","left":{"input":"left_value"},"right":{"input":"right_value"}},"then":[],"else":[],"exports":{"choice":{"then":{"input":"left_value"},"else":{"input":"right_value"}}}}],"result":{"chosen":{"step":"choice","field":""}}}
    cases.append(_case("REF_BRANCH_EXPORT_SAME_TYPE","flow_branch_export",p,"Accept matching integer exports from both branches and expose only the named export after the branch.",statement_map))
    example_dir=contract_dir/"examples"
    example_names=("v1","v2","v3","v4","v5","v6","v7","v7-branch-null")
    source_paths=(contract_dir/"field-statement-map.json",contract_dir/"grammar.ebnf",contract_dir/"CONTRACT.md",contract_dir/"serializer-policy.json",contract_dir/"canonical-vectors.json",contract_dir/"canonical-vector-qualification.json",fixtures_path,HERE/"flow-operation-capability-assertions.json")+tuple(p for name in example_names for p in (example_dir/(name+".prompt.json"),example_dir/(name+".cnl")))
    source_files={str(path):hashlib.sha256(path.read_bytes()).hexdigest() for path in source_paths}
    serializer_hash=hashlib.sha256(pathlib.Path(__file__).read_bytes()).hexdigest()
    example_comparison=[]
    for name in example_names:
        prompt=json.loads((example_dir/(name+".prompt.json")).read_text())
        authored=(example_dir/(name+".cnl")).read_text()
        generated=serialize_prompt(prompt,statement_map)
        authored_lines=authored.splitlines(); generated_lines=generated.splitlines()
        first=None
        for i,(left,right) in enumerate(itertools.zip_longest(authored_lines,generated_lines),1):
            if left!=right:
                first={"line":i,"authored":left,"serialized":right}; break
        map_order_equal=_normalize_map_statement_order(authored)==_normalize_map_statement_order(generated)
        example_comparison.append({"id":name,"prompt_sha256":source_files[str(example_dir/(name+".prompt.json"))],"cnl_sha256":source_files[str(example_dir/(name+".cnl"))],"byte_equal":authored==generated,"map_order_variation_allowed":not authored==generated and map_order_equal,"normalized_map_statement_order_equal":map_order_equal,"first_line_difference":first})
    return {"format":"som.cnl-positive-fixtures.v1","status":"hand-authored JSON plus explicit fixture generation rules; no compiler/parser used","serializer":str(pathlib.Path(__file__).resolve()),"serializer_sha256":serializer_hash,"source_hashes":source_files,"serializer_policy":"Arrays retain order. Map-shaped flow args, exports, and results serialize in sorted key order for deterministic generated CNL. Equivalent source clauses may appear in another order and map to the same JSON object; do not require text equality. JSON scalar spelling retains integer/float category and negative zero; strings use UTF-8 Unicode with JSON escapes.","public_example_comparison_policy":"Byte equality is recorded only as a drift signal. Different order among map-shaped argument/result/export clauses is accepted and must compare by resulting typed JSON. Field, operation, input, and node arrays remain order-sensitive.","public_example_comparison":example_comparison,"case_count":len(cases),"counts":{"version_positive":7,"capability_tuple":90,"flow_operation":14,"flow_reference_predicate":7,"flow_branch_export":1},"capability_dimensions":cap_values,"operation_ids":["authorize","parent.create","parent.get","parent.list","parent.patch","child.create","child.get","child.list","child.patch","batch.create","workflow.run","workflow.import","object.put","object.get"],"cases":cases}


if __name__ == "__main__":
    import argparse
    parser=argparse.ArgumentParser()
    parser.add_argument("--contract-dir",type=pathlib.Path,required=True)
    parser.add_argument("--fixtures",type=pathlib.Path,default=FIXTURES_PATH)
    parser.add_argument("--output",type=pathlib.Path,default=MANIFEST_PATH)
    args=parser.parse_args()
    manifest=build_manifest(contract_dir=args.contract_dir,fixtures_path=args.fixtures)
    args.output.write_text(json.dumps(manifest,indent=2,ensure_ascii=False,allow_nan=False)+"\n")
    print(f"wrote {args.output} ({manifest['case_count']} cases)")

"""Bounded flow semantics and typed runtime references for prompt v7 / DSL v3."""

from copy import deepcopy
import hashlib
import json
import keyword
import math

from . import composed, related
from .v1 import V1Error, _object

VERSION = "som.prompt.v7"
DSL_VERSION = "som.dsl.v3"
CONTRACT = "related-flow.v1"
CONTRACT_PATH = composed.ROOT / "som-rs/contracts/related-flow.v1.md"
CATALOG = composed.ROOT / "som-code-python/data/block-contracts/flow-v7.json"
SCALARS = {"string", "integer", "float", "boolean", "date", "bytes"}
INPUT_TYPES = SCALARS - {"bytes"} | {"parent_input", "child_input", "parent_patch", "child_patch", "children_input"}
ARGUMENTS = {
    "authorize": {}, "parent.create": {"data": "parent_input"},
    "child.create": {"parent_id": "integer", "data": "child_input"},
    "parent.get": {"parent_id": "integer"},
    "child.get": {"parent_id": "integer", "child_id": "integer"},
    "parent.list": {}, "child.list": {"parent_id": "integer"},
    "parent.patch": {"parent_id": "integer", "data": "parent_patch"},
    "child.patch": {"parent_id": "integer", "child_id": "integer", "data": "child_patch"},
    "batch.create": {"parent": "parent_input", "children": "children_input"},
    "workflow.run": {"parent_id": "integer"},
    "workflow.import": {"parent_id": "integer", "csv": "string"},
    "object.put": {"key": "string", "body": "bytes"}, "object.get": {"key": "string"},
}
MUTATIONS = {"parent.create", "child.create", "batch.create", "workflow.import", "parent.patch", "child.patch", "object.put"}


def contract_hash():
    return hashlib.sha256(CONTRACT_PATH.read_bytes()).hexdigest()


def input_fields(inputs, parent, child):
    """Render a closed set of types; callers cannot pass Python type text."""
    from .v1 import _identifier
    parent = _identifier(parent, "parent name")
    child = _identifier(child, "child name")
    types = {"string": "Annotated[str, Field(strict=True, max_length=1048576)]", "integer": "Annotated[int, Field(strict=True, ge=-(2**31), lt=2**31)]", "float": "Annotated[float, Field(strict=True, allow_inf_nan=False)]", "boolean": "Annotated[bool, Field(strict=True)]", "date": "date",
             "parent_input": parent+"Create", "child_input": child+"Create", "parent_patch": parent+"Patch", "child_patch": child+"Patch", "children_input": "Annotated[list["+child+"Create], Field(min_length=1, max_length=20)]"}
    if not isinstance(inputs, list) or len(inputs) > 8: raise V1Error("INVALID_VALUE", "invalid flow inputs")
    result = []
    names = set()
    for item in inputs:
        _object(item, {"name", "type"}, "input field")
        name = _name(item["name"])
        if not isinstance(item["type"], str) or item["type"] not in types or name in names: raise V1Error("INVALID_VALUE", "invalid flow input type or duplicate name")
        names.add(name); result.append({"name": name, "input_type": types[item["type"]]})
    return result


def base(prompt):
    result = deepcopy(prompt)
    result.pop("flow", None)
    result["version"] = composed.VERSION
    result["contract"] = {"id": composed.CONTRACT, "sha256": composed.contract_hash()}
    return result


def _name(value):
    if not isinstance(value, str) or not value.isascii() or not value.isidentifier() or keyword.iskeyword(value) or len(value) > 64 or value.startswith("_") or value.startswith("model_") or value in {"model_config", "model_fields"}:
        raise V1Error("INVALID_VALUE", "expected a safe flow name")
    return value


def _literal(value, kind):
    valid = (kind == "string" and isinstance(value, str) or
             kind == "integer" and type(value) is int and -2147483648 <= value <= 2147483647 or
             kind == "float" and type(value) in (int, float) and math.isfinite(value) or
             kind == "boolean" and type(value) is bool)
    if kind == "date" and isinstance(value, str):
        from datetime import date
        try:
            valid = date.fromisoformat(value).isoformat() == value
        except ValueError:
            valid = False
    if kind == "bytes" and isinstance(value, str):
        import base64
        try:
            valid = 0 < len(base64.b64decode(value, validate=True)) <= 1048576
        except ValueError:
            valid = False
    if not valid:
        raise V1Error("INVALID_REF", "literal does not match its declared type")


def output_type(prompt, operation):
    if operation in {"parent.create", "parent.get", "parent.patch"}:
        return "parent_row"
    if operation in {"child.create", "child.get", "child.patch"}:
        return "child_row"
    return {"authorize": "identity", "parent.list": "parent_rows", "child.list": "child_rows",
            "batch.create": "batch", "workflow.run": "workflow", "workflow.import": "child_rows",
            "object.put": "object", "object.get": "object"}[operation]


def _member(prompt, kind, field):
    if not field:
        return kind
    if "." in field:
        first, rest = field.split(".", 1)
        return _member(prompt, _member(prompt, kind, first), rest)
    fixed = {"identity": {"sub": "string", "role": "string"}, "object": {"key": "string", "size": "integer", "sha256": "string", "body": "bytes"},
             "workflow": {"body": "bytes", "size": "integer", "sha256": "string", "data": "json"},
             "batch": {"parent": "parent_row", "children": "child_rows"}}
    if kind in fixed and field in fixed[kind]:
        return fixed[kind][field]
    if kind in {"parent_row", "child_row"}:
        role = kind.split("_", 1)[0]
        if field == "id": return "integer"
        if field in {"created_at", "updated_at"}: return "string"
        if role == "child" and field == prompt["relation"]["foreign_key"]: return "integer"
        for definition in prompt["resources"][role]["fields"]:
            if definition["name"] == field:
                kind = {"str": "string", "int": "integer", "bool": "boolean"}.get(definition["type"], definition["type"])
                return kind + ("?" if not definition["required"] and definition.get("default") is None else "")
    raise V1Error("INVALID_REF", "unknown result field or incompatible reference")


def ref_type(prompt, ref, inputs, visible):
    if not isinstance(ref, dict):
        raise V1Error("INVALID_REF", "expected a typed reference")
    refined = visible.get("@" + composed.canonical(ref))
    if refined is not None:
        return refined
    if set(ref) == {"input"}:
        if not isinstance(ref["input"], str) or ref["input"] not in inputs: raise V1Error("INVALID_REF", "unknown input")
        return inputs[ref["input"]]
    if set(ref) == {"step", "field"}:
        if not isinstance(ref["step"], str) or ref["step"] not in visible or not isinstance(ref["field"], str): raise V1Error("INVALID_REF", "step does not dominate this reference")
        return _member(prompt, visible[ref["step"]], ref["field"])
    if set(ref) == {"literal", "type"} and isinstance(ref["type"], str) and ref["type"] in SCALARS:
        _literal(ref["literal"], ref["type"])
        return ref["type"]
    raise V1Error("INVALID_REF", "unknown reference kind")


def validate(prompt, _result_types=False):
    _object(prompt, {"version", "contract", "package", "resources", "relation", "operations", "capabilities", "migration", "flow"}, "prompt")
    if prompt["version"] != VERSION: raise V1Error("UNSUPPORTED_VERSION", "expected prompt v7")
    if prompt["contract"] != {"id": CONTRACT, "sha256": contract_hash()}:
        raise V1Error("UNSUPPORTED_CONTRACT", "flow contract hash differs")
    composed.validate(base(prompt))
    if any(prompt["resources"]["parent"]["path"] == route or prompt["resources"]["parent"]["path"].startswith(route+"/") for route in ("/flows", "/flow-runs")):
        raise V1Error("INVALID_VALUE", "resource route collides with flow routes")
    flow = _object(prompt["flow"], {"name", "inputs", "steps", "result"}, "flow")
    _name(flow["name"])
    if not isinstance(flow["inputs"], list) or len(flow["inputs"]) > 8:
        raise V1Error("SCOPE_TOO_LARGE", "at most eight typed flow inputs")
    inputs = {}
    for item in flow["inputs"]:
        _object(item, {"name", "type"}, "flow input")
        name = _name(item["name"])
        if name in inputs or not isinstance(item["type"], str) or item["type"] not in INPUT_TYPES:
            raise V1Error("INVALID_REF", "duplicate input or unsupported input type")
        inputs[name] = item["type"]
    counts = {"action": 0, "if": 0, "transaction": 0}
    names = set(inputs)
    authorization = False

    def walk(steps, visible, depth=0, transaction=False):
        nonlocal authorization
        if not isinstance(steps, list): raise V1Error("INVALID_SCHEMA", "steps must be a list")
        local = dict(visible)
        for step in steps:
            if not isinstance(step, dict) or "name" not in step or "op" not in step:
                raise V1Error("INVALID_SCHEMA", "invalid flow node")
            name = _name(step["name"])
            if name in names: raise V1Error("INVALID_REF", "duplicate step name")
            names.add(name)
            op = step["op"]
            if not isinstance(op, str): raise V1Error("UNSUPPORTED_LOGIC", "unsupported flow action")
            if op == "transaction":
                _object(step, {"name", "op", "steps"}, "transaction")
                if not authorization or transaction: raise V1Error("INVALID_TRANSACTION", "unauthorized or nested transaction")
                counts["transaction"] += 1
                if counts["transaction"] > 2: raise V1Error("SCOPE_TOO_LARGE", "flow node budget exceeded")
                local = walk(step["steps"], local, depth, True)
                continue
            if op == "if":
                _object(step, {"name", "op", "condition", "then", "else", "exports"}, "if")
                if not authorization: raise V1Error("INVALID_AUTHORIZATION", "authorization must precede branching")
                counts["if"] += 1
                if counts["if"] > 2: raise V1Error("SCOPE_TOO_LARGE", "flow node budget exceeded")
                if depth >= 2: raise V1Error("SCOPE_TOO_LARGE", "branch depth exceeds two")
                condition = step["condition"]
                operator = condition.get("op") if isinstance(condition, dict) else None
                _object(condition, {"op", "left"} if operator == "is_null" else {"op", "left", "right"}, "condition")
                left = ref_type(prompt, condition["left"], inputs, local)
                if operator == "is_null" and not left.endswith("?"):
                    raise V1Error("INVALID_REF", "null check requires a nullable scalar")
                if operator != "is_null":
                    right = ref_type(prompt, condition["right"], inputs, local)
                    if operator not in {"eq", "ne", "lt", "le", "gt", "ge"} or left != right or left.rstrip("?") not in SCALARS - {"bytes"}:
                        raise V1Error("INVALID_REF", "incompatible comparison")
                    if operator not in {"eq", "ne"} and (left.endswith("?") or left == "boolean"):
                        raise V1Error("INVALID_REF", "unsafe ordered comparison")
                non_null = dict(local)
                if operator == "is_null":
                    non_null["@" + composed.canonical(condition["left"])] = left[:-1]
                then = walk(step["then"], local, depth+1, transaction)
                otherwise = walk(step["else"], non_null, depth+1, transaction)
                if not isinstance(step["exports"], dict): raise V1Error("INVALID_REF", "exports must be a map")
                for export, binding in step["exports"].items():
                    _name(export)
                    _object(binding, {"then", "else"}, "branch export")
                    a = ref_type(prompt, binding["then"], inputs, then)
                    b = ref_type(prompt, binding["else"], inputs, otherwise)
                    if a != b or export in names: raise V1Error("INVALID_REF", "branch export mismatch")
                    names.add(export); local[export] = a
                continue
            if op not in ARGUMENTS: raise V1Error("UNSUPPORTED_LOGIC", "unsupported flow action")
            _object(step, {"name", "op", "args", "undo"}, "action")
            counts["action"] += 1
            if counts["action"] > 8: raise V1Error("SCOPE_TOO_LARGE", "flow node budget exceeded")
            if op == "authorize":
                if authorization or local or transaction: raise V1Error("INVALID_AUTHORIZATION", "authorization must be first")
                authorization = True
            elif not authorization:
                raise V1Error("INVALID_AUTHORIZATION", "missing authorization")
            if op in MUTATIONS - {"object.put"} and not transaction:
                raise V1Error("INVALID_TRANSACTION", "database mutation lacks transaction")
            if op.startswith("object."):
                if transaction or prompt["capabilities"]["storage"] == "none":
                    raise V1Error("INVALID_TRANSACTION", "incompatible provider or provider inside transaction")
            if op == "workflow.import" and prompt["capabilities"]["workflow"] != "csv":
                raise V1Error("INVALID_COMPOSITION", "CSV import requires CSV capability")
            _object(step["args"], set(ARGUMENTS[op]), "action arguments")
            for argument, kind in ARGUMENTS[op].items():
                if ref_type(prompt, step["args"][argument], inputs, local) != kind:
                    raise V1Error("INVALID_REF", "wrong argument type")
            wanted = "restore_patch" if op.endswith(".patch") else "delete_created" if op in MUTATIONS else None
            if step["undo"] != wanted:
                raise V1Error("INVALID_COMPENSATION", "missing or incompatible compensation")
            local[name] = output_type(prompt, op)
        return local

    visible = walk(flow["steps"], {})
    if not authorization: raise V1Error("INVALID_AUTHORIZATION", "flow requires authorization")
    if counts["action"] > 8 or counts["if"] > 2 or counts["transaction"] > 2:
        raise V1Error("SCOPE_TOO_LARGE", "flow node budget exceeded")
    if not isinstance(flow["result"], dict): raise V1Error("INVALID_REF", "result must be a map")
    for name, ref in flow["result"].items():
        _name(name)
        if ref_type(prompt, ref, inputs, visible) == "bytes":
            raise V1Error("INVALID_REF", "binary results must use object or workflow metadata")
    if _result_types:
        return {name: ref_type(prompt, ref, inputs, visible)
                for name, ref in flow["result"].items()}
    return prompt


def result_types(prompt):
    """Expose only the types proven by the same scope checker."""
    return validate(prompt, _result_types=True)


def block_id(prompt, operation, variant="standard"):
    backend = prompt["capabilities"]["database"]
    provider = prompt["capabilities"]["storage"] if operation.startswith("object.") else backend
    return "flow." + provider + "." + operation + (".checked" if variant == "checked" and operation in {"parent.get", "child.get"} else "")


def plan_tree(prompt, variant="standard"):
    """Reference only. Production must preserve the model's explicit nodes."""
    validate(prompt)
    def convert(nodes):
        result = []
        for node in nodes:
            item = deepcopy(node)
            op = item.pop("op")
            item["id"] = "flow.if" if op == "if" else block_id(prompt, op, variant)
            if op == "transaction": item["steps"] = convert(item["steps"])
            elif op == "if":
                item["then"] = convert(item["then"]); item["else"] = convert(item["else"])
            elif item["undo"] is not None:
                provider = prompt["capabilities"]["storage"] if op == "object.put" else prompt["capabilities"]["database"]
                item["undo"] = "flow." + provider + ".undo." + item["undo"]
            result.append(item)
        return result
    return {"name": prompt["flow"]["name"], "steps": convert(prompt["flow"]["steps"]), "result": deepcopy(prompt["flow"]["result"])}


def validate_tree(prompt, tree, catalog):
    """Check semantic obligations rather than requiring one block sequence."""
    validate(prompt)
    _object(tree, {"name", "steps", "result"}, "flow plan")
    reconstructed = deepcopy(tree)
    def convert(nodes):
        if not isinstance(nodes, list): raise V1Error("INVALID_DSL", "flow plan nodes must be a list")
        result = []
        for node in nodes:
            if not isinstance(node, dict) or not isinstance(node.get("id"), str):
                raise V1Error("INVALID_DSL", "flow block missing")
            meta = catalog.get(node["id"])
            if meta is None: raise V1Error("UNSUPPORTED_BLOCK", "unapproved flow block")
            if any(prompt["capabilities"][key] != value for key, value in meta["capabilities"].items()):
                raise V1Error("INVALID_COMPOSITION", "incompatible flow capability")
            item = deepcopy(node); item.pop("id"); item["op"] = meta["operation"]
            if item["op"] == "transaction": item["steps"] = convert(item.get("steps"))
            elif item["op"] == "if":
                item["then"] = convert(item.get("then")); item["else"] = convert(item.get("else"))
            elif item.get("undo") is not None:
                inverse = catalog.get(item["undo"]) if isinstance(item["undo"], str) else None
                if inverse is None or inverse.get("inverse") is None:
                    raise V1Error("INVALID_COMPENSATION", "unapproved inverse")
                if any(prompt["capabilities"][key] != value for key, value in inverse["capabilities"].items()):
                    raise V1Error("INVALID_COMPENSATION", "incompatible inverse provider")
                item["undo"] = inverse["inverse"]
            result.append(item)
        return result
    reconstructed["steps"] = convert(tree["steps"])
    expected = {key: value for key, value in prompt["flow"].items() if key != "inputs"}
    # Python considers True == 1 and 1 == 1.0. A typed DSL must not.
    if composed.canonical(reconstructed) != composed.canonical(expected):
        raise V1Error("INVALID_COMPOSITION", "flow does not fulfill the declared semantic obligations")
    return tree


def catalog():
    """Public machine contracts, including typed arguments and reverse effects."""
    result = {"flow.if": {"operation": "if", "capabilities": {}, "params": {"condition": "predicate"}, "requires": ["identity"], "inverse": None}}
    for database in ("postgresql", "mongodb"):
        for operation, params in ARGUMENTS.items():
            if operation.startswith("object."): continue
            identity = "flow." + database + "." + operation
            result[identity] = {"operation": operation, "capabilities": {"database": database}, "params": params,
                                "requires": [] if operation == "authorize" else ["identity"], "inverse": None}
            if operation in {"parent.get", "child.get"}: result[identity+".checked"] = deepcopy(result[identity])
        result["flow."+database+".transaction"] = {"operation": "transaction", "capabilities": {"database": database}, "params": {}, "requires": ["identity"], "inverse": None}
        for inverse in ("delete_created", "restore_patch"):
            result["flow."+database+".undo."+inverse] = {"operation": "undo", "capabilities": {"database": database}, "params": {}, "requires": ["committed_effect"], "inverse": inverse}
    for storage in ("s3", "gcs"):
        for operation in ("object.put", "object.get"):
            result["flow."+storage+"."+operation] = {"operation": operation, "capabilities": {"storage": storage}, "params": ARGUMENTS[operation], "requires": ["identity"], "inverse": None}
        result["flow."+storage+".undo.delete_created"] = {"operation": "undo", "capabilities": {"storage": storage}, "params": {}, "requires": ["owned_object_version"], "inverse": "delete_created"}
    return result


def selected_ids(tree):
    """Return only the model's selected forward, control and inverse IDs."""
    result = []
    def walk(nodes):
        for node in nodes:
            result.append(node["id"])
            if "steps" in node: walk(node["steps"])
            elif "then" in node:
                walk(node["then"]); walk(node["else"])
            elif node["undo"] is not None: result.append(node["undo"])
    walk(tree["steps"])
    return list(dict.fromkeys(result))


def reference(prompt, blocks, variant="standard"):
    """Reference code composition; never called by production inference."""
    validate(prompt)
    metadata = json.loads(CATALOG.read_text())
    dsl = composed.reference(base(prompt), blocks)
    dsl["version"] = DSL_VERSION
    dsl["flow"] = plan_tree(prompt, variant)
    dsl["files"] = [file for file in dsl["files"] if file["path"] != composed.SLOTS["alembic_ini"]]
    for file in dsl["files"]:
        for block in file["blocks"]:
            revised = "v7."+block["id"]
            if revised in metadata["blocks"]:
                block["id"] = revised
                block["params"] = deepcopy(metadata["blocks"][revised]["bindings"])
        slot = next(key for key,path in composed.SLOTS.items() if file["path"] == path)
        additions = {"schemas": ["schema.flow"], "database": ["service.flow.store."+prompt["capabilities"]["database"]],
                     "integrations": ["storage.flow."+prompt["capabilities"]["storage"]],
                     "models": ["orm.flow.journal"] if prompt["capabilities"]["database"] == "postgresql" else [],
                     "migration": ["migration.flow.journal"]}
        for identity in additions.get(slot, []):
            entry = metadata["blocks"][identity]
            name = entry["provides"].split(":",1)[1]
            refs = [value.split(":",1)[1] for value in entry["requires"] if value.startswith(slot+":")]
            node = {"id": identity, "name": name, "params": deepcopy(entry["bindings"]), "refs": refs}
            if slot == "migration":
                file["blocks"].insert(-1, node)
                file["blocks"][-1]["refs"].append(name)
            else: file["blocks"].append(node)
        if slot == "app":
            next(block for block in file["blocks"] if block["name"] == "tail")["refs"] = [block["name"] for block in file["blocks"] if block["name"] not in {"tail", "app"}]
    helper_ids = ["workflow.flow."+prompt["capabilities"]["workflow"], "api.flow.engine."+prompt["capabilities"]["database"], *selected_ids(dsl["flow"])]
    helpers = []
    for identity in helper_ids:
        entry = metadata["blocks"][identity]
        helpers.append({"id":identity, "name":entry["provides"].split(":",1)[1], "params":deepcopy(entry["bindings"]),
                        "refs":[value.split(":",1)[1] for value in entry["requires"] if value.startswith("flow_helpers:")]})
    dsl["files"].append({"path":metadata["slots"]["flow_helpers"], "blocks":helpers})
    for file in dsl["files"]:
        for block in file["blocks"]:
            block["refs"] = [{"symbol": name} for name in block["refs"]]
    validate_plan(prompt, dsl, metadata)
    return dsl


def validate_plan(prompt, dsl, metadata=None):
    """File obligations and runtime obligations are both mandatory."""
    validate(prompt)
    metadata = metadata or json.loads(CATALOG.read_text())
    _object(dsl, {"version", "files", "flow"}, "DSL v3")
    if dsl["version"] != DSL_VERSION: raise V1Error("UNSUPPORTED_VERSION", "expected DSL v3")
    validate_tree(prompt, dsl["flow"], metadata["flow_blocks"])
    composed.validate_plan(base(prompt), file_symbols(dsl), metadata)
    emitted = {block["id"] for file in dsl["files"] for block in file["blocks"] if block["id"].startswith("flow.")}
    if emitted != set(selected_ids(dsl["flow"])):
        raise V1Error("INVALID_REF", "selected flow helpers are missing or extra")
    return dsl


def file_symbols(dsl):
    """Lower explicit program-symbol references, without changing any choice."""
    result = {"version": "som.dsl.v2", "files": deepcopy(dsl.get("files"))}
    if not isinstance(result["files"], list): raise V1Error("INVALID_DSL", "files must be a list")
    for file in result["files"]:
        if not isinstance(file, dict) or not isinstance(file.get("blocks"), list): raise V1Error("INVALID_DSL", "file blocks must be a list")
        for block in file["blocks"]:
            if not isinstance(block, dict) or not isinstance(block.get("refs"), list): raise V1Error("INVALID_REF", "program symbols must be a list")
            symbols = []
            for ref in block["refs"]:
                if not isinstance(ref, dict) or set(ref) != {"symbol"} or not isinstance(ref["symbol"], str): raise V1Error("INVALID_REF", "explicit program symbol required")
                symbols.append(ref["symbol"])
            block["refs"] = symbols
    return result


def resolve(prompt, dsl):
    """Typed lowering preserves chosen files, blocks, order and runtime refs."""
    import re
    validate_plan(prompt, dsl)
    values = composed.values(base(prompt))
    values.update({"dsl.flow": composed.canonical(dsl["flow"]), "flow.inputs": prompt["flow"]["inputs"],
                   "flow.custom_fields": json.dumps({item["name"]: item["type"] for item in prompt["resources"]["child"]["fields"]}, ensure_ascii=False, separators=(",", ":")),
                   "flow.versioned": any(identity.endswith(".object.put") for identity in selected_ids(dsl["flow"]))})
    result = file_symbols(dsl)
    for file in result["files"]:
        file["path"] = re.sub(r"\{([A-Za-z_.]+)\}", lambda match: str(values[match[1]]), file["path"])
        for block in file["blocks"]:
            for key, ref in block["params"].items():
                source = ref["$ref"]
                if source == "block.id": block["params"][key] = block["id"]
                elif source in values: block["params"][key] = deepcopy(values[source])
                else: raise V1Error("INVALID_REF", "unknown prompt binding")
    return result


def assemble(prompt, dsl, blocks, binary):
    """Use the same native formatter and final byte limits as existing output."""
    from . import quality
    from .v1 import assemble as render
    return quality.finalize(render(resolve(prompt, dsl), blocks), binary)


def semantic_view(prompt):
    """Remove names/literal values, retaining every fact affecting plan choice."""
    validate(prompt)
    result = deepcopy(prompt)
    inputs = {item["name"]: "i"+str(index) for index, item in enumerate(result["flow"]["inputs"])}
    names = {}
    def collect(nodes):
        for node in nodes:
            names[node["name"]] = "n"+str(len(names))
            if node["op"] == "transaction": collect(node["steps"])
            if node["op"] == "if":
                collect(node["then"]); collect(node["else"])
                for name in node["exports"]: names[name] = "n"+str(len(names))
    collect(result["flow"]["steps"])
    fields = {role: {field["name"]: "f"+str(index) for index, field in enumerate(result["resources"][role]["fields"])}
              for role in ("parent", "child")}
    kinds = {}
    def result_kinds(nodes):
        for node in nodes:
            if node["op"] == "transaction": result_kinds(node["steps"])
            elif node["op"] == "if":
                result_kinds(node["then"]); result_kinds(node["else"])
                for export, binding in node["exports"].items():
                    kinds[export] = ref_type(prompt, binding["then"], {item["name"]: item["type"] for item in prompt["flow"]["inputs"]}, kinds)
            else: kinds[node["name"]] = output_type(prompt, node["op"])
    result_kinds(prompt["flow"]["steps"])
    def normalize(value):
        if isinstance(value, list): return [normalize(item) for item in value]
        if not isinstance(value, dict): return value
        if set(value) == {"step", "field"}:
            kind = kinds[value["step"]]
            selected = value["field"]
            parts = selected.split(".") if selected else []
            role = kind.split("_", 1)[0]
            if kind == "batch" and parts and parts[0] == "parent":
                role = "parent"
                parts = ["parent", *[fields[role].get(part, part) for part in parts[1:]]]
            elif role in fields:
                parts = ["parent_fk" if role == "child" and part == prompt["relation"]["foreign_key"] else fields[role].get(part, part) for part in parts]
            return {"step": names[value["step"]], "field": ".".join(parts)}
        transformed = {}
        for key, item in value.items():
            if key == "literal": transformed[key] = "typed_literal"
            elif key == "input": transformed[key] = inputs.get(item, item)
            elif key == "step": transformed[key] = names.get(item, item)
            elif key == "name": transformed[key] = item if set(value) >= {"type", "required", "unique"} else names.get(item, inputs.get(item, "resource"))
            elif key in {"package", "table", "path", "foreign_key", "revision", "down_revision", "flow_name"}: transformed[key] = key
            elif key == "exports": transformed[key] = {names[name]: normalize(binding) for name,binding in item.items()}
            elif key == "result": transformed[key] = {"r"+str(index): binding for index, binding in enumerate(sorted((normalize(binding) for binding in item.values()), key=composed.canonical))}
            else: transformed[key] = normalize(item)
        return transformed
    result["flow"]["name"] = "flow_name"
    # Field selectors carry their normalized position, not their spelling.
    for role, resource in result["resources"].items():
        for index, definition in enumerate(resource["fields"]): definition["name"] = "f"+str(index)
        for key in ("filter_field", "date_field"):
            resource["query"][key] = fields[role].get(resource["query"][key], resource["query"][key])
    for key in ("value_field", "group_field"):
        result["capabilities"][key] = fields["child"].get(result["capabilities"][key], result["capabilities"][key])
    return normalize(result)

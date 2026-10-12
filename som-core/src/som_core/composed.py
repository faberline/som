"""Composable prompt, typed block contracts, and lossless structural token codec.

The reference planner is for training and tests only. Runtime decoding must never
call it. Validation checks independent block obligations, not a reference plan.
"""
from copy import deepcopy
import hashlib
import json
from pathlib import Path

from . import related
from .v1 import V1Error, _object

VERSION = "som.prompt.v6"
CONTRACT = "related-business.v1"
ROOT = Path(__file__).resolve().parents[3]
CATALOG = ROOT / "som-code-python/data/block-contracts/composed.json"
CONTRACT_PATH = ROOT / "som-rs/contracts/related-business.v1.md"
SLOTS = {
    "models": "{package}/models.py", "schemas": "{package}/schemas.py",
    "app": "{package}/app.py", "tests": "tests/test_{package}.py",
    "migration": "alembic/versions/{migration.revision}_{package}.py",
    "database": "{package}/database.py", "init": "{package}/__init__.py",
    "alembic_env": "alembic/env.py", "alembic_ini": "alembic.ini",
    "project": "pyproject.toml", "readme": "README.md",
    "integrations": "{package}/integrations.py", "mongo_migration": "{package}/migrate.py",
}
ACTION = dict(zip(related.OPS, related.CRUD)) | {"batch_create": related.BATCH}


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def validate_library(blocks, catalog=None):
    catalog = catalog or json.loads(CATALOG.read_text())
    for identity, meta in catalog['blocks'].items():
        block = blocks.get(identity)
        if block is None or hashlib.sha256(canonical(block).encode()).hexdigest() != meta['source_sha256'] or block['params'] != meta['params']:
            raise V1Error('INVALID_BLOCK', 'block bytes or parameter types differ from their contract')


def contract_hash():
    return hashlib.sha256(CONTRACT_PATH.read_bytes()).hexdigest()


def from_legacy(prompt):
    result = deepcopy(prompt)
    result["version"] = VERSION
    result["contract"] = {"id": CONTRACT, "sha256": contract_hash()}
    result["operations"] = [{"scope": s["scope"], "action": next(k for k,v in ACTION.items() if v == s["step"])} for s in result.pop("logic")]
    result.pop("tests")
    return result


def legacy(prompt):
    result = deepcopy(prompt)
    result["version"] = related.VERSION
    result.pop("contract", None)
    result.pop("operations", None)
    result["logic"] = [{"scope": r, "step": s} for r in ("parent", "child") for s in related.CRUD] + [{"scope": "batch", "step": related.BATCH}]
    result["tests"] = [related.TEST]
    return result


def validate(prompt):
    p = _object(prompt, {"version", "contract", "package", "resources", "relation", "operations", "capabilities", "migration"}, "prompt")
    if p["version"] != VERSION:
        raise V1Error("UNSUPPORTED_VERSION", "unsupported composed prompt")
    if p["contract"] != {"id": CONTRACT, "sha256": contract_hash()}:
        raise V1Error("UNSUPPORTED_CONTRACT", "contract id or hash does not match")
    ops = p["operations"]
    if not isinstance(ops, list) or not 1 <= len(ops) <= 11:
        raise V1Error("UNSUPPORTED_LOGIC", "expected 1..11 unique scoped operations")
    seen = set()
    for op in ops:
        s = _object(op, {"scope", "action"}, "operation")
        scope, action = s["scope"], s["action"]
        if not isinstance(scope, str) or not isinstance(action, str):
            raise V1Error("UNSUPPORTED_LOGIC", "invalid operation")
        pair = (scope, action)
        if pair in seen or not (scope in {"parent", "child"} and action in related.OPS or pair == ("batch", "batch_create")):
            raise V1Error("UNSUPPORTED_LOGIC", "unsupported or duplicate scoped operation")
        seen.add(pair)
    related.validate(legacy(p))
    return p


def values(prompt):
    p = validate(prompt)
    result = related.values(legacy(p))
    routes = []
    for op in p["operations"]:
        scope, action = op["scope"], op["action"]
        path = result["batch.path"] if scope == "batch" else result[scope + (".item_path" if action in {"get", "patch", "delete"} else ".path")]
        routes.append({"method": {"create":"post", "get":"get", "list":"get", "patch":"patch", "delete":"delete", "batch_create":"post"}[action], "path": path})
    result["contract.routes"] = canonical(routes)
    return result


def reference(prompt, blocks):
    p = validate(prompt)
    q = legacy(p)
    q["logic"] = [{"scope": op["scope"], "step": ACTION[op["action"]]} for op in p["operations"]]
    # related.document validates the old complete contract; compose a complete
    # reference first, then retain and order the requested route blocks.
    dsl = related.document(legacy(p), blocks)
    api = next(f for f in dsl["files"] if f["path"] == SLOTS["app"])
    by_name = {b["name"]: b for b in api["blocks"]}
    route_names = ["batch" if op["scope"] == "batch" else op["scope"] + "_" + op["action"] for op in p["operations"]]
    api["blocks"] = [by_name["head"], *[by_name[n] for n in route_names], by_name["workflow"], by_name["tail"], by_name["app"]]
    for f in dsl["files"]:
        slot = next(k for k,v in SLOTS.items() if v == f["path"])
        for b in f["blocks"]:
            if b['id'] in {'orm.mongo.base', 'orm.mongo.parent', 'orm.mongo.child'}:
                b['id'] = b['id'].replace('orm.mongo.', 'orm.composed.mongo.')
            if slot in {"models", "migration"} and b["name"] == "child":
                b["refs"].append("parent")
            if slot == "app" and b["name"] == "tail":
                b["refs"] = ["head", *route_names, "workflow"]
            if slot == "app" and b["name"] == "app":
                b["refs"] = ["tail"]
            if slot == "tests" and b["name"] in {"parent", "child"}:
                b["refs"] = ["head"]
            if slot == "tests" and b["name"] == "test":
                b["id"] = "test.composed.behavior"
                b["params"] = {"routes": {"$ref": "contract.routes"}}
    return dsl


def features(prompt):
    p = validate(prompt)
    result = [f"<cap:{k}:{p['capabilities'][k]}>" for k in ("database", "auth", "storage", "workflow")]
    for role in ("parent", "child"):
        r = p["resources"][role]
        result.append(f"<resource:{role}>")
        result.append(f"<query:{related.query_kind(r)}>")
        # Fields are validated and bound through typed references. They do not
        # change block selection, so they must not become memorization cues.
    result.append("<operations>")
    result.extend(f"<op:{op['scope']}:{op['action']}>" for op in p["operations"])
    result.extend(["<op:none>"] * (11 - len(p["operations"])))
    return "".join([*result, "</operations>", "<plan>"])


def signature(prompt):
    p = validate(prompt)
    fields = []
    for role in ("parent", "child"):
        r = p["resources"][role]
        fields.append(sorted((f["type"], f["required"], f["unique"], str(f.get("default")), f["name"] == r["query"]["filter_field"], f["name"] == r["query"]["date_field"], f["name"] == p["capabilities"]["value_field"], f["name"] == p["capabilities"]["group_field"]) for f in r["fields"]))
    return canonical({"fields": fields, "capabilities": {k:v for k,v in p["capabilities"].items() if k not in {"value_field", "group_field"}}, "operations": sorted((o["scope"],o["action"]) for o in p["operations"])})


def encode(dsl):
    tokens = []
    for f in dsl["files"]:
        slot = next((k for k,v in SLOTS.items() if v == f["path"]), None)
        if slot is None:
            raise V1Error("INVALID_PATH", "unknown file slot")
        tokens.append(f"<file:{slot}>")
        for b in f["blocks"]:
            tokens.extend([f"<block:{b['id']}>", f"<name:{b['name']}>"])
            for key, ref in sorted(b["params"].items()):
                if not isinstance(ref, dict) or set(ref) != {"$ref"}:
                    raise V1Error("INVALID_REF", "block_plan parameters must use typed prompt references")
                tokens.append(f"<bind:{key}:{ref['$ref']}>")
            tokens.append("<deps>")
            tokens.extend(f"<name:{r}>" for r in b["refs"])
            tokens.append("<end>")
        tokens.append("</file>")
    return "".join([*tokens, "<eos>"])


def decode(text):
    import re
    tokens = re.findall(r"<[^<>\s]+>", text)
    if "".join(tokens) != text or not tokens or tokens[-1] != "<eos>":
        raise V1Error("INVALID_DSL", "malformed or incomplete block plan")
    files, current, block = [], None, None
    deps = False
    for token in tokens[:-1]:
        if token.startswith("<file:") and current is None and block is None:
            slot = token[6:-1]
            if slot not in SLOTS:
                raise V1Error("INVALID_PATH", "unknown file slot")
            current = {"path": SLOTS[slot], "blocks": []}; files.append(current)
        elif token.startswith("<block:") and current is not None and block is None:
            block = {"id":token[7:-1], "name":None, "params":{}, "refs":[]}; deps = False
        elif token.startswith("<name:") and block is not None:
            name = token[6:-1]
            if deps:
                block["refs"].append(name)
            elif block["name"] is None:
                block["name"] = name
            else:
                raise V1Error("INVALID_DSL", "unexpected block name")
        elif token.startswith("<bind:") and block is not None and block["name"] is not None and not deps:
            parts = token[6:-1].split(":")
            if len(parts) != 2 or parts[0] in block["params"]:
                raise V1Error("INVALID_DSL", "duplicate or malformed binding")
            block["params"][parts[0]] = {"$ref":parts[1]}
        elif token == "<deps>" and block is not None and block["name"] is not None and not deps:
            deps = True
        elif token == "<end>" and block is not None and deps:
            current["blocks"].append(block); block = None
        elif token == "</file>" and current is not None and block is None and current["blocks"]:
            current = None
        else:
            raise V1Error("INVALID_DSL", "unexpected structural token: " + token)
    if current is not None or block is not None or not files:
        raise V1Error("INVALID_DSL", "incomplete block plan")
    return {"version":"som.dsl.v2", "files":files}


def validate_plan(prompt, dsl, catalog=None):
    p = validate(prompt)
    catalog = catalog or json.loads(CATALOG.read_text())
    if not isinstance(dsl, dict) or set(dsl) != {"version", "files"} or dsl["version"] != "som.dsl.v2" or not isinstance(dsl["files"], list):
        raise V1Error("INVALID_DSL", "expected DSL v2")
    if not 1 <= len(dsl["files"]) <= 12 or sum(len(f['blocks']) for f in dsl["files"] if isinstance(f, dict) and isinstance(f.get('blocks'), list)) > 64:
        raise V1Error("SCOPE_TOO_LARGE", "plan exceeds file/block limit")
    provided, requirements, routes, files_seen = set(), [], [], set()
    ordered_operations = []
    caps = p["capabilities"]
    for f in dsl["files"]:
        _object(f, {"path", "blocks"}, "file")
        slot = next((k for k,v in catalog["slots"].items() if v == f["path"]), None)
        if slot is None:
            raise V1Error("INVALID_PATH", "unknown file slot")
        if slot in files_seen or not isinstance(f["blocks"], list) or not f["blocks"]:
            raise V1Error("INVALID_DSL", "duplicate or empty file")
        files_seen.add(slot)
        names = {}
        closed = False
        for b in f["blocks"]:
            _object(b, {"id", "name", "params", "refs"}, "block")
            if not isinstance(b["id"], str) or b["id"] not in catalog["blocks"]:
                raise V1Error("UNSUPPORTED_BLOCK", "unapproved composed block")
            meta = catalog["blocks"][b["id"]]
            if slot != meta["slot"] or any(caps[k] != v for k,v in meta["capabilities"].items()):
                raise V1Error("INVALID_COMPOSITION", "incompatible file or capability")
            if meta.get("query") and related.query_kind(p["resources"][meta["query"][0]]) != meta["query"][1]:
                raise V1Error("INVALID_COMPOSITION", "incompatible query block")
            if (meta.get('operation') or meta['provides'] == 'app:workflow') and meta.get('authorization') != 'require_access':
                raise V1Error('INVALID_COMPOSITION', 'route lacks an authorization contract')
            if (meta.get('operation') == ['batch', 'batch_create'] or meta['provides'] == 'app:workflow' and caps['workflow'] == 'csv') and meta.get('transaction') != 'atomic':
                raise V1Error('INVALID_COMPOSITION', 'operation lacks an atomic transaction contract')
            if b["params"] != meta["bindings"]:
                raise V1Error("INVALID_REF", "parameter bindings do not satisfy block types and roles")
            if not isinstance(b["name"], str) or not b["name"].isascii() or not b["name"].isidentifier() or b["name"] in names or not isinstance(b["refs"], list) or any(not isinstance(r,str) or r not in names for r in b["refs"]) or len(b["refs"]) != len(set(b["refs"])):
                raise V1Error("INVALID_REF", "invalid dependency reference")
            node = meta["provides"]
            if node in provided:
                raise V1Error("INVALID_COMPOSITION", "duplicate symbol or route")
            local_needs = {n for n in meta["requires"] if n.split(":")[0] == slot}
            if not local_needs <= {names[r] for r in b["refs"]}:
                raise V1Error("INVALID_REF", "missing typed dependency")
            if slot == "app":
                if node == "app:tail":
                    if not {n for n in provided if n.startswith("app:") and n not in {"app:head"}} <= {names[r] for r in b["refs"]}:
                        raise V1Error("INVALID_REF", "router close must reference all routes")
                    closed = True
                elif closed and node != "app:app":
                    raise V1Error("INVALID_COMPOSITION", "route after router close")
            names[b["name"]] = node
            provided.add(node); requirements.extend(meta["requires"])
            if meta.get("operation"):
                ordered_operations.append(tuple(meta["operation"]))
            routes.extend(meta["routes"])
    wanted = [(op["scope"],op["action"]) for op in p["operations"]]
    if ordered_operations != wanted:
        raise V1Error("INVALID_COMPOSITION", "requested operations are missing, extra or out of order")
    needed = set(catalog["required_common"])
    needed.update(catalog["required_database"][caps["database"]])
    if caps["storage"] != "none":
        needed.add("integrations:storage_common")
    if not needed <= provided or not set(requirements) <= provided:
        raise V1Error("INVALID_COMPOSITION", "missing service capability or dependency")
    if len(routes) != len(set(routes)):
        raise V1Error("INVALID_COMPOSITION", "duplicate route")
    return dsl

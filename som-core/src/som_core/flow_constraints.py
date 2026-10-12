"""Prefix grammar and contract masks. No reference planner or label is read.

The mask can rule out an illegal token. It cannot supply a missing node or
select the requested business operation. Complete semantic fulfillment remains
the final validator's job.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
import keyword

from . import composed, flow, flow_codec, flow_compact
from .v1 import V1Error

VERSION = "som.flow.constrained-decoding.v1"
KEYS = {
    "root": {"files", "flow", "version"}, "file": {"blocks", "path"},
    "block": {"id", "name", "params", "refs"},
    "binding": {"$ref"}, "symbol": {"symbol"}, "flow": {"name", "result", "steps"},
    "node": {"args", "condition", "else", "exports", "id", "name", "steps", "then", "undo"},
    "args": {"parent_id", "child_id", "data", "parent", "children", "csv", "key", "body"},
    "condition": {"op", "left", "right"}, "export": {"then", "else"},
    "reference": {"input", "step", "field", "literal", "type"},
}


def policy() -> dict:
    return {"version": VERSION, "codec": flow_codec.COMPACT_VERSION,
            "map_keys": {kind: sorted(keys) for kind, keys in KEYS.items()},
            "capabilities": "public block contracts", "scope": "dominating selected nodes",
            "alternatives": "all legal block IDs and map key orders",
            "fulfillment": "final validator only; never force missing operations",
            "repair": False, "label_access": False, "max_tokens": 2048}


def safe_name(value) -> bool:
    return (isinstance(value, str) and value.isascii() and value.isidentifier()
            and not keyword.iskeyword(value) and not value.startswith(("_", "model_"))
            and len(value) <= 64)


@dataclass
class Frame:
    kind: str
    path: tuple[str, ...]
    value: dict | list
    key: str | None = None


@dataclass
class State:
    prompt: dict
    metadata: dict = field(default_factory=flow_compact.metadata)
    stack: list[Frame] = field(default_factory=list)
    root: dict | None = None
    complete: bool = False
    tokens: int = 0

    def __post_init__(self):
        flow.validate(self.prompt)
        self.values = flow_codec.pool(self.prompt, metadata=self.metadata)
        self.ids = {composed.canonical(value): index + 8 for index, value in enumerate(self.values)}
        self.inputs = {item["name"]: item["type"] for item in self.prompt["flow"]["inputs"]}
        self.flow_catalog = flow.catalog()
        self.public_visible = self.walk_types(self.prompt["flow"]["steps"], {}, public=True)
        self.public_all = dict(self.public_visible)
        pending = {}
        def collect(nodes):
            for node in nodes:
                if node["op"] in flow.ARGUMENTS:
                    self.public_all[node["name"]] = flow.output_type(self.prompt, node["op"])
                pending.update(node.get("exports", {}))
                for child in ("steps", "then", "else"):
                    collect(node.get(child, []))
        collect(self.prompt["flow"]["steps"])
        for _ in range(32):
            if not pending:
                break
            for name, binding in list(pending.items()):
                try:
                    self.public_all[name] = flow.ref_type(self.prompt, binding["then"], self.inputs, self.public_all)
                    del pending[name]
                except V1Error:
                    continue

    def walk_types(self, nodes, visible, *, public=False):
        result = dict(visible)
        for node in nodes:
            op = node.get("op") if public else self.flow_catalog.get(node.get("id"), {}).get("operation")
            if op == "transaction":
                result = self.walk_types(node.get("steps", []), result, public=public)
            elif op == "if":
                arms = {}
                for arm in ("then", "else"):
                    scoped = dict(result)
                    condition = node.get("condition", {})
                    if arm == "else" and condition.get("op") == "is_null":
                        kind = flow.ref_type(self.prompt, condition["left"], self.inputs, scoped)
                        scoped["@" + composed.canonical(condition["left"])] = kind[:-1]
                    arms[arm] = self.walk_types(node.get(arm, []), scoped, public=public)
                for name, refs in node.get("exports", {}).items():
                    kinds = [flow.ref_type(self.prompt, refs[arm], self.inputs, arms[arm]) for arm in ("then", "else")]
                    if kinds[0] != kinds[1]:
                        raise V1Error("INVALID_REF", "branch export types differ")
                    result[name] = kinds[0]
            elif op in flow.ARGUMENTS and isinstance(node.get("name"), str):
                result[node["name"]] = flow.output_type(self.prompt, op)
        return result

    def visible(self, path):
        # Results can precede steps in a legal map order. The public requirement
        # supplies their declared types, but the final selected plan must still
        # provide those results. Runtime arguments use selected predecessors.
        if len(path) >= 2 and path[:2] == ("flow", "result"):
            return dict(self.public_visible)
        result = {}
        for frame in self.stack:
            if frame.kind == "nodes":
                if frame.path and frame.path[-1] in {"then", "else"}:
                    parent = next((item for item in reversed(self.stack[:self.stack.index(frame)]) if item.kind == "node"), None)
                    condition = parent.value.get("condition", {}) if parent else {}
                    if frame.path[-1] == "else" and condition.get("op") == "is_null":
                        kind = flow.ref_type(self.prompt, condition["left"], self.inputs, result)
                        result["@" + composed.canonical(condition["left"])] = kind[:-1]
                result = self.walk_types(frame.value, result)
        branch = next((frame for frame in reversed(self.stack) if frame.kind == "node" and "exports" in path and frame.path == path[:len(frame.path)]), None)
        if branch:
            arm = path[-1] if path[-1] in {"then", "else"} else None
            if arm:
                condition = branch.value.get("condition", {})
                if arm == "else" and condition.get("op") == "is_null":
                    kind = flow.ref_type(self.prompt, condition["left"], self.inputs, result)
                    result["@" + composed.canonical(condition["left"])] = kind[:-1]
                if arm in branch.value:
                    result = self.walk_types(branch.value[arm], result)
                else:
                    # Map keys have no ordering requirement. Validate the
                    # selected branch's exact scope when that branch arrives.
                    result.update(self.public_all)
        return result

    def transaction(self) -> bool:
        current = next((frame for frame in reversed(self.stack) if frame.kind == "node"), None)
        return any(frame is not current and frame.kind == "node" and self.flow_catalog.get(frame.value.get("id"), {}).get("operation") == "transaction"
                   or frame.kind == "nodes" and frame.path[-1:] == ("steps",) and len(frame.path) > 2
                   for frame in self.stack)

    def operation_ids(self, node=None):
        visible = self.visible(("flow", "steps"))
        authorized = "identity" in visible.values()
        allowed = []
        for identity, meta in self.flow_catalog.items():
            op = meta["operation"]
            if op == "undo" or any(self.prompt["capabilities"][key] != value for key, value in meta["capabilities"].items()):
                continue
            if op == "authorize" and (authorized or self.transaction()):
                continue
            if op != "authorize" and not authorized:
                continue
            if op == "transaction" and self.transaction():
                continue
            if op in flow.MUTATIONS - {"object.put"} and not self.transaction():
                continue
            if op.startswith("object.") and self.transaction():
                continue
            if op == "workflow.import" and self.prompt["capabilities"]["workflow"] != "csv":
                continue
            if node is not None and "args" in node and op in flow.ARGUMENTS:
                args = node["args"]
                if set(args) != set(flow.ARGUMENTS[op]):
                    continue
                try:
                    if any(flow.ref_type(self.prompt, args[key], self.inputs, visible) != kind for key, kind in flow.ARGUMENTS[op].items()):
                        continue
                except V1Error:
                    continue
            allowed.append(identity)
        return allowed

    def child_kind(self, frame, key):
        kind = frame.kind
        if kind == "root": return {"files": "files", "flow": "flow"}.get(key, "scalar")
        if kind == "file": return "blocks" if key == "blocks" else "scalar"
        if kind == "block": return {"params": "params", "refs": "refs"}.get(key, "scalar")
        if kind == "params": return "binding"
        if kind == "flow": return {"steps": "nodes", "result": "result"}.get(key, "scalar")
        if kind in {"args", "result", "export"}: return "reference"
        if kind == "exports": return "export"
        if kind == "node": return {"args": "args", "condition": "condition", "then": "nodes", "else": "nodes", "steps": "nodes", "exports": "exports"}.get(key, "scalar")
        if kind == "condition" and key in {"left", "right"}: return "reference"
        return "scalar"

    def expected_kind(self):
        if not self.stack: return "root"
        frame = self.stack[-1]
        if isinstance(frame.value, list): return {"files": "file", "blocks": "block", "nodes": "node", "refs": "symbol"}[frame.kind]
        return self.child_kind(frame, frame.key)

    def next_path(self):
        if not self.stack: return ()
        frame = self.stack[-1]
        return frame.path + ((str(len(frame.value)),) if isinstance(frame.value, list) else (frame.key,))

    def scalar_domain(self, frame):
        key, kind, value = frame.key, frame.kind, frame.value
        if isinstance(value, list):
            return sorted({meta["provides"].split(":", 1)[1] for meta in self.metadata["blocks"].values()})
        if kind == "root" and key == "version": return [flow.DSL_VERSION]
        if kind == "file" and key == "path": return list(self.metadata["slots"].values())
        if kind == "block":
            if key == "id": return [identity for identity, meta in self.metadata["blocks"].items() if all(self.prompt["capabilities"][cap] == wanted for cap, wanted in meta.get("capabilities", {}).items())]
            if key == "name":
                if value.get("id") in self.metadata["blocks"]: return [self.metadata["blocks"][value["id"]]["provides"].split(":", 1)[1]]
                return sorted({meta["provides"].split(":", 1)[1] for meta in self.metadata["blocks"].values()})
        if kind == "binding": return sorted({ref["$ref"] for meta in self.metadata["blocks"].values() for ref in meta["bindings"].values()})
        if kind == "symbol":
            return [node["name"] for owner in self.stack if owner.kind == "blocks"
                    for node in owner.value if "name" in node]
        if kind == "flow": return [self.prompt["flow"]["name"]]
        if kind == "node":
            if key == "id": return self.operation_ids(value)
            if key == "undo":
                return [None, *[identity for identity, meta in self.flow_catalog.items() if meta["operation"] == "undo" and all(self.prompt["capabilities"][cap] == wanted for cap, wanted in meta["capabilities"].items())]]
            if key == "name": return [item for item in self.values if safe_name(item) and item not in self.inputs]
        if kind == "condition": return ["eq", "ne", "lt", "le", "gt", "ge", "is_null"]
        if kind == "reference":
            visible = self.visible(frame.path)
            if key == "input": return list(self.inputs)
            if key == "step": return [name for name in visible if not name.startswith("@")]
            if key == "type": return sorted(flow.SCALARS)
            if key == "field":
                kinds = [visible[value["step"]]] if value.get("step") in visible else list(visible.values())
                fields = {"", "id", "created_at", "updated_at", "sub", "role", "key", "size", "sha256", "body", "data", "parent", "children", "parent.id", self.prompt["relation"]["foreign_key"]}
                fields.update(item["name"] for role in ("parent", "child") for item in self.prompt["resources"][role]["fields"])
                fields.update("parent." + item["name"] for item in self.prompt["resources"]["parent"]["fields"])
                return sorted(field for field in fields if any(self.member_exists(kind, field) for kind in kinds))
            if key == "literal": return self.values
        return self.values

    def member_exists(self, kind, field):
        try:
            flow._member(self.prompt, kind, field)
            return True
        except V1Error:
            return False

    def keys(self, frame):
        if frame.kind in KEYS:
            result = set(KEYS[frame.kind])
        elif frame.kind == "params":
            block = next((item for item in reversed(self.stack[:-1]) if item.kind == "block"), None)
            if block and block.value.get("id") in self.metadata["blocks"]:
                result = set(self.metadata["blocks"][block.value["id"]]["bindings"])
            else:
                result = {key for meta in self.metadata["blocks"].values() for key in meta["bindings"]}
        elif frame.kind == "result":
            result = {item for item in self.values if safe_name(item)}
        else:
            result = {item for item in self.values if safe_name(item) and item not in self.inputs}
        if frame.kind == "reference":
            if "input" in frame.value: result &= {"input"}
            elif {"step", "field"} & set(frame.value): result &= {"step", "field"}
            elif {"literal", "type"} & set(frame.value): result &= {"literal", "type"}
        return result - set(frame.value)

    def can_close(self, frame):
        value, kind = frame.value, frame.kind
        try:
            if isinstance(value, list):
                return len(value) <= {"files": 12, "blocks": 64, "nodes": 12, "refs": 64}[kind] and (bool(value) if kind in {"files", "blocks"} else True)
            if frame.key is not None: return False
            if kind in {"root", "file", "block", "binding", "symbol", "flow", "export"} and set(value) != KEYS[kind]: return False
            if kind == "root":
                visible = self.walk_types(value["flow"]["steps"], {})
                for reference in value["flow"]["result"].values():
                    flow.ref_type(self.prompt, reference, self.inputs, visible)
            if kind == "block":
                meta = self.metadata["blocks"].get(value.get("id"))
                if meta is None or value["name"] != meta["provides"].split(":", 1)[1] or value["params"] != meta["bindings"]: return False
            if kind == "reference": flow.ref_type(self.prompt, value, self.inputs, self.visible(frame.path))
            if kind == "condition":
                op = value.get("op")
                if set(value) != ({"op", "left"} if op == "is_null" else {"op", "left", "right"}): return False
                visible = self.visible(frame.path)
                left = flow.ref_type(self.prompt, value["left"], self.inputs, visible)
                if op == "is_null": return left.endswith("?")
                right = flow.ref_type(self.prompt, value["right"], self.inputs, visible)
                return left == right and left.rstrip("?") in flow.SCALARS - {"bytes"} and (op in {"eq", "ne"} or not left.endswith("?") and left != "boolean")
            if kind == "node":
                op = self.flow_catalog.get(value.get("id"), {}).get("operation")
                required = {"id", "name", "steps"} if op == "transaction" else {"id", "name", "condition", "then", "else", "exports"} if op == "if" else {"id", "name", "args", "undo"}
                if set(value) != required or not safe_name(value["name"]): return False
                if value["id"] not in self.operation_ids(value): return False
                if op in flow.ARGUMENTS:
                    inverse = "restore_patch" if op.endswith(".patch") else "delete_created" if op in flow.MUTATIONS else None
                    selected = self.flow_catalog.get(value["undo"], {}).get("inverse") if value["undo"] else None
                    if selected != inverse: return False
                if op == "if": self.walk_types([value], self.visible(frame.path))
            return True
        except (V1Error, KeyError, TypeError, ValueError):
            return False

    def allowed(self) -> list[int]:
        if self.tokens >= 2048 or self.complete: return []
        if self.root is not None: return [1]
        if not self.stack: return [2]
        frame = self.stack[-1]
        result = []
        if self.can_close(frame): result.append(5 if isinstance(frame.value, list) else 3)
        if isinstance(frame.value, dict) and frame.key is None:
            result.extend(self.ids[composed.canonical(key)] for key in self.keys(frame) if composed.canonical(key) in self.ids)
        else:
            kind = self.expected_kind()
            if kind == "scalar":
                result.extend(self.ids[composed.canonical(value)] for value in self.scalar_domain(frame) if composed.canonical(value) in self.ids)
            elif len(self.stack) < 32:
                result.append(4 if kind in {"files", "blocks", "nodes", "refs"} else 2)
        return sorted(set(result))

    def advance(self, token: int) -> None:
        if token not in self.allowed():
            raise V1Error("INVALID_DSL", "token violates the flow prefix contract")
        self.tokens += 1
        if token == 1:
            self.complete = True
            return
        if token in {2, 4}:
            self.stack.append(Frame(self.expected_kind(), self.next_path(), {} if token == 2 else []))
            return
        if token in {3, 5}:
            value = self.stack.pop().value
        else:
            value = deepcopy(self.values[token - 8])
        if not self.stack:
            self.root = value
            return
        frame = self.stack[-1]
        if isinstance(frame.value, list): frame.value.append(value)
        elif frame.key is None: frame.key = value
        else:
            frame.value[frame.key] = value
            frame.key = None

"""M3 split fingerprints and bounded, typed composition generation.

Names, literal values, unused inputs and input declaration order cannot create
a new operation graph. The graph fingerprint also drops unused resource fields.
It retains control flow, typed references, capabilities and API operation order.
"""

from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import random

from . import composed, flow, flow_codec, flow_compact
from .v1 import V1Error, load_blocks

ROOT = Path(__file__).resolve().parents[3]
COUNTS = {"train": 2400, "dev": 300, "formal": 300}
APPS = (("project", "Project", "Todo"), ("catalog", "Catalog", "Product"),
        ("bookmarks", "Collection", "Bookmark"), ("recipe", "Recipe", "Ingredient"),
        ("customers", "Customer", "Contact"), ("devices", "Device", "Inspection"),
        ("survey", "Survey", "Question"), ("courses", "Course", "Lesson"),
        ("experiment", "Experiment", "Observation"), ("documents", "Document", "Review"))


def sha(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def composition_view(prompt: dict) -> dict:
    """Normalize input vertices by first use, ignoring unreferenced inputs."""
    used: list[str] = []
    def visit(value):
        if isinstance(value, dict):
            if set(value) == {"input"} and value["input"] not in used:
                used.append(value["input"])
            else:
                for key in sorted(value):
                    visit(value[key])
        elif isinstance(value, list):
            for item in value:
                visit(item)
    visit(prompt["flow"]["steps"])
    visit(prompt["flow"]["result"])
    cleaned = deepcopy(prompt)
    definitions = {item["name"]: item for item in cleaned["flow"]["inputs"]}
    cleaned["flow"]["inputs"] = [definitions[name] for name in used]
    return flow.semantic_view(cleaned)


def fingerprints(prompt: dict, *, input_policy: str = flow_codec.M3_BOUND_INPUT_VERSION) -> dict[str, str]:
    view = flow.semantic_view(prompt)
    # A historical contract checksum is not a distinct flow composition.
    view.pop("contract", None)
    active = composition_view(prompt)
    graph = {key: active[key] for key in ("capabilities", "operations", "flow")}
    graph["query"] = {role: active["resources"][role]["query"] for role in ("parent", "child")}
    graph["result_types"] = sorted(flow.result_types(prompt).values())
    return {"input": sha(flow_codec.m3_features(prompt, flow_compact.metadata(), input_policy=input_policy)),
            "structure": sha(composed.canonical(view)),
            "graph": sha(composed.canonical(graph))}


def action(name: str, op: str, args: dict | None = None) -> dict:
    return {"name": name, "op": op, "args": args or {},
            "undo": "restore_patch" if op.endswith(".patch") else
                    "delete_created" if op in flow.MUTATIONS else None}


def inp(name: str) -> dict:
    return {"input": name}


def ref(name: str, field: str = "") -> dict:
    return {"step": name, "field": field}


def candidate(base: dict, caps: tuple[str, ...], app: int, serial: int,
              *, hard: bool = False, supplementary: bool = False) -> dict:
    """Compose public semantic operations; no production planner is invoked."""
    rng = random.Random(17 + serial * 7919)
    p = deepcopy(base)
    family, parent, child = APPS[app]
    p["package"] = f"m3_{family}_{serial}"
    for role, name in (("parent", parent), ("child", child)):
        resource = p["resources"][role]
        resource.update(name=name, table=name.lower() + "s", path="/" + name.lower() + "s")
    p["migration"]["revision"] = f"m3_{serial}"
    for role in ("parent", "child"):
        query_kind = rng.randrange(3)
        p["resources"][role]["query"].update(
            filter_field="active" if query_kind else None,
            date_field="due" if query_kind == 2 else None)
    database, auth, storage, workflow = caps
    p["capabilities"] = {"database": database, "auth": auth, "storage": storage,
        "workflow": workflow, "value_field": "amount" if workflow in {"statistics", "decimal_summary"} else None,
        "group_field": "unit" if workflow == "decimal_summary" else None}
    inputs: list[dict] = []

    def input_value(name: str, kind: str) -> dict:
        if not any(item["name"] == name for item in inputs):
            inputs.append({"name": name, "type": kind})
        return inp(name)

    steps = [action("access", "authorize")]
    write_branch = supplementary and rng.randrange(3) == 0 or hard and serial % 2 == 0
    mode = rng.randrange(4)
    if write_branch:
        mode = 0
    if hard:
        mode = 0
    writes = []
    child_row = None
    if mode == 1:
        writes.append(action("batch_rows", "batch.create", {
            "parent": input_value("parent", "parent_input"),
            "children": input_value("children", "children_input")}))
        parent_id = ref("batch_rows", "parent.id")
        parent_row = "found_parent"
        after_write = [action(parent_row, "parent.get", {"parent_id": parent_id})]
    elif mode == 2:
        parent_row = "found_parent"
        steps.append(action(parent_row, "parent.get", {
            "parent_id": input_value("parent_id", "integer")}))
        parent_id = ref(parent_row, "id")
        child_row = "created_child"
        writes.append(action(child_row, "child.create", {
            "parent_id": parent_id, "data": input_value("child", "child_input")}))
        after_write = []
    else:
        parent_row = "created_parent"
        writes.append(action(parent_row, "parent.create", {
            "data": input_value("parent", "parent_input")}))
        parent_id = ref(parent_row, "id")
        child_row = "created_child"
        if mode == 3 and workflow == "csv":
            child_row = None
            writes.append(action("imported", "workflow.import", {
                "parent_id": parent_id, "csv": input_value("csv", "string")}))
        else:
            writes.append(action(child_row, "child.create", {
                "parent_id": parent_id, "data": input_value("child", "child_input")}))
        after_write = []
    if write_branch:
        writes = writes[:1]
        child_row = None
        inputs[:] = [entry for entry in inputs if entry["name"] != "child"]
    patch = rng.randrange(2 if write_branch else 4) if not hard else 0
    if patch & 1:
        writes.append(action("changed_parent", "parent.patch", {
            "parent_id": parent_id, "data": input_value("parent_patch", "parent_patch")}))
    if patch & 2 and child_row:
        writes.append(action("changed_child", "child.patch", {
            "parent_id": parent_id, "child_id": ref(child_row, "id"),
            "data": input_value("child_patch", "child_patch")}))
    split = not write_branch and (hard or rng.choice((False, True))) and len(writes) >= 2
    if split:
        cut = rng.randrange(1, len(writes))
        steps.extend([{"name": "first_tx", "op": "transaction", "steps": writes[:cut]},
                      {"name": "second_tx", "op": "transaction", "steps": writes[cut:]}])
    else:
        steps.append({"name": "write_tx", "op": "transaction", "steps": writes})
    steps.extend(after_write)
    reader = rng.randrange(6) if not hard else 0
    if reader == 1:
        steps.append(action("reread_parent", "parent.get", {"parent_id": parent_id}))
        if rng.choice((False, True)):
            parent_id = ref("reread_parent", "id")
            parent_row = "reread_parent"
    elif reader == 2 and child_row:
        steps.append(action("reread_child", "child.get", {
            "parent_id": parent_id, "child_id": ref(child_row, "id")}))
        if rng.choice((False, True)):
            child_row = "reread_child"
    elif reader == 3:
        steps.append(action("listed", "child.list", {"parent_id": parent_id}))
    elif reader == 4:
        steps.append(action("listed", "parent.list"))

    # Each branch must be reachable through its HTTP inputs. An existing or
    # patched row does not give the branch driver control of the original data.
    predicate_kinds = [0, 3] if (mode == 2 or
            patch & 1 and (mode == 1 or parent_row == "reread_parent")) else list(range(4))

    def predicate(kind: int) -> dict:
        if kind == 0:
            return {"op": rng.choice(("eq", "ne")), "left": input_value("choose", "boolean"),
                    "right": {"literal": True, "type": "boolean"}}
        if kind == 1:
            return {"op": rng.choice(("lt", "le", "gt", "ge")), "left": ref(parent_row, "servings"),
                    "right": {"literal": 0, "type": "integer"}}
        if kind == 2:
            return {"op": "is_null", "left": ref(parent_row, rng.choice(("memo", "due")))}
        return {"op": rng.choice(("lt", "le", "gt", "ge", "eq", "ne")),
                "left": input_value("cutoff", "date"),
                "right": {"literal": "2030-01-01", "type": "date"}}

    def export(name: str) -> dict:
        return action(name, "workflow.run", {"parent_id": parent_id})

    if write_branch:
        def make_child(name: str) -> dict:
            return action(name, "child.create", {"parent_id": parent_id,
                "data": input_value(name + "_data", "child_input")})
        child_choice = {"name": "child_choice", "op": "if", "condition": predicate(0),
            "then": [make_child("yes_child")], "else": [make_child("no_child")],
            "exports": {"selected_child": {"then": ref("yes_child"), "else": ref("no_child")}}}
        if hard:
            child_choice["then"] = [{"name": "nested_child_choice", "op": "if",
                "condition": predicate(3), "then": [make_child("inner_yes_child")],
                "else": [make_child("inner_no_child")], "exports": {
                    "inner_child": {"then": ref("inner_yes_child"), "else": ref("inner_no_child")}}}]
            child_choice["exports"]["selected_child"]["then"] = ref("inner_child")
        steps.append({"name": "children_tx", "op": "transaction", "steps": [child_choice]})
        child_row = "selected_child"

    branch = (0 if hard else rng.randrange(3)) if write_branch else rng.randrange(5) if not hard else 3
    if branch == 0:
        steps.append(export("exported"))
        exported = "exported"
    else:
        outer_kind = rng.choice([kind for kind in predicate_kinds if not write_branch or kind != 0])
        choice = {"name": "choice", "op": "if", "condition": predicate(outer_kind),
                  "then": [export("yes_export")], "else": [export("no_export")],
                  "exports": {"exported": {"then": ref("yes_export"), "else": ref("no_export")}}}
        if branch == 3:
            inner_kind = rng.choice([kind for kind in predicate_kinds if kind != outer_kind])
            nested = {"name": "nested_choice", "op": "if", "condition": predicate(inner_kind),
                      "then": [export("inner_yes")], "else": [export("inner_no")],
                      "exports": {"nested_exported": {"then": ref("inner_yes"), "else": ref("inner_no")}}}
            arm = rng.choice(("then", "else"))
            choice[arm] = [nested]
            choice["exports"]["exported"][arm] = ref("nested_exported")
        steps.append(choice)
        exported = "exported"
        if branch == 4:
            second_kind = rng.choice([kind for kind in predicate_kinds if kind != outer_kind])
            steps.append({"name": "second_choice", "op": "if", "condition": predicate(second_kind),
                          "then": [export("second_yes")], "else": [export("second_no")],
                          "exports": {"second_exported": {"then": ref("second_yes"), "else": ref("second_no")}}})
            exported = "second_exported"
    if storage != "none":
        key = input_value("key", "string")
        steps.append(action("stored", "object.put", {"key": key, "body": ref(exported, "body")}))
        if not hard and rng.choice((False, True)):
            steps.append(action("read_object", "object.get", {"key": key}))
    choices = [ref(parent_row, key) for key in ("id", "title", "servings", "active", "due", "memo")]
    choices += [ref(exported, key) for key in ("size", "sha256", "data")]
    if child_row:
        choices += [ref(child_row, key) for key in ("id", "amount", "unit", "count", "memo")]
    if storage != "none":
        choices += [ref("stored", key) for key in ("key", "sha256", "size")]
    if any(node["name"] == "listed" for node in steps):
        choices.append(ref("listed"))
    rng.shuffle(choices)
    results = {"value_" + str(index): value for index, value in enumerate(choices[:rng.randrange(1, 4)])}
    p["flow"] = {"name": "publish", "inputs": inputs, "steps": steps, "result": results}
    # The route order is another explicit model choice. These remain full CRUD
    # services so the existing independent CRUD checker applies to every case.
    operations = p["operations"]
    shift = rng.randrange(len(operations))
    p["operations"] = operations[shift:] + operations[:shift]
    flow.validate(p)
    return p


def known_fingerprints() -> dict[str, set[str]]:
    """Exclude inspected v7 inputs, including M2 maximum and alternate cases."""
    result = {key: set() for key in ("input", "structure", "graph")}
    paths = [path for directory in (ROOT / "som-core/data").glob("flow-v7*")
             for path in directory.glob("*.jsonl")]
    paths.extend(path for directory in (ROOT / "som-core/data").glob("flow-m3-*")
                 for path in directory.glob("*.jsonl"))
    requirements = ROOT / "som-rs/evals/m2-flow-2026-10-04/r31/training-prerequisite-001"
    prompts = []
    for path in paths:
        prompts.extend(json.loads(line)["prompt"] for line in path.read_text().splitlines())
    prior = list(requirements.glob("*/prompt.json"))
    prior.extend((ROOT / "som-rs/evals/m2-flow-2026-10-04/r31/supplementary-acceptance-003").glob("*/prompt.json"))
    for path in prior:
        value = json.loads(path.read_text())
        prompts.append(value.get("prompt", value))
    for prompt in prompts:
        if not isinstance(prompt, dict) or prompt.get("version") != flow.VERSION:
            continue
        try:
            # Only the current validator can compute the common semantic view.
            # Keep the old files intact and rebind the checksum in this copy.
            checked = deepcopy(prompt)
            checked["contract"] = {"id": flow.CONTRACT, "sha256": flow.contract_hash()}
            for key, value in fingerprints(checked).items():
                result[key].add(value)
        except V1Error as error:
            raise ValueError("previously seen v7 input cannot be audited") from error
    return result


def make_rows(capabilities: list[tuple[str, ...]]) -> tuple[dict[str, list[dict]], dict]:
    base = json.loads((ROOT / "som-core/data/flow-v7-r10/formal.jsonl").read_text().splitlines()[0])["prompt"]
    library = load_blocks(ROOT / "som-code-python/data/blocks/flow-v7-compact")
    metadata = flow_compact.metadata()
    known = known_fingerprints()
    all_used = {key: set() for key in known}
    rows = {split: [] for split in COUNTS}
    serial = 0
    rejected: dict[str, int] = {}
    for split, total in COUNTS.items():
        slots = ([caps for caps in capabilities for _ in range(20)] +
                 [capabilities[index % 90] for index in range(600)] if split == "train" else
                 [caps for caps in capabilities for _ in range(3)] +
                 [capabilities[(index * 17) % 90] for index in range(30)])
        for position, caps in enumerate(slots):
            hard = split != "train" and position >= 270
            app = position % (10 if split == "formal" else 8)
            for _attempt in range(20000):
                serial += 1
                try:
                    prompt = candidate(base, caps, app, serial, hard=hard,
                                       supplementary=split == "train" and position >= 1800)
                    marks = fingerprints(prompt)
                    if any(value in all_used[key] for key, value in marks.items()):
                        rejected["duplicate_new"] = rejected.get("duplicate_new", 0) + 1
                        continue
                    if split == "formal" and any(value in known[key] for key, value in marks.items()):
                        rejected["previously_seen"] = rejected.get("previously_seen", 0) + 1
                        continue
                    plan = flow_compact.reference(prompt, library)
                    target = flow_codec.encode(prompt, plan, metadata)
                    source = flow_codec.features_m3_bound(prompt, metadata)
                    if flow_codec.decode_m3_semantic_view(source, metadata) != flow.semantic_view(prompt):
                        raise ValueError("model input loses a required semantic fact")
                    if flow_codec.decode(prompt, target, metadata) != plan:
                        raise ValueError("reference codec changes a choice")
                except V1Error as error:
                    rejected[error.code] = rejected.get(error.code, 0) + 1
                    continue
                break
            else:
                raise ValueError(f"cannot find a disjoint {split} composition for {caps}")
            for key, value in marks.items():
                all_used[key].add(value)
            rows[split].append({"id": f"m3-{split}-{position:04d}", "app": APPS[app][0],
                "origin": "typed-operation-grammar; seed=17", "prompt": prompt, "dsl": plan,
                "fingerprints": marks, "input_tokens": source.count("<"),
                "output_tokens": target.count("<"), "hard_semantics": hard})
            if (position + 1) % 100 == 0:
                print(composed.canonical({"stage": "disjoint-generation", "split": split,
                      "accepted": position + 1, "candidate_attempts": serial}), flush=True)
        assert len(rows[split]) == total
    return rows, {"rejected_candidates": rejected,
                  "previously_seen_counts": {key: len(value) for key, value in known.items()}}

"""Freeze ten apps and disjoint structural compositions before training."""

import ast
import hashlib
import json
import random
from copy import deepcopy
from pathlib import Path

from som_core.related import BATCH, CRUD, TEST, VERSION, document
from som_core.v1 import V1Error, assemble, load_blocks, resolve_prompt_refs, validate_prompt

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "som-core/data/related-service"
BLOCKS = ROOT / "som-code-python/data/blocks/v5/blocks.json"


def field(name, ty="str", required=True, unique=False, default=None):
    value = dict(name=name, type=ty, required=required, unique=unique)
    if not required:
        value["default"] = default
    return value


def flag(name):
    return field(name, "bool", False, default=False)


def date(name):
    return field(name, "date", False)


APPS = [
    ("projects", "Project", "Todo", "todos", [field("name"), field("description", required=False)], [field("title"), field("description", required=False), flag("completed"), date("due_on")]),
    ("catalogs", "Catalog", "Product", "products", [field("name"), field("code", unique=True)], [field("sku", unique=True), field("name"), field("weight", "float", False), flag("listed")]),
    ("collections", "Collection", "Bookmark", "bookmarks", [field("name")], [field("url"), field("title"), flag("starred"), field("notes", required=False)]),
    ("recipes", "Recipe", "Ingredient", "ingredients", [field("name"), field("servings", "int")], [field("name"), field("quantity", "float"), field("unit"), field("notes", required=False)]),
    ("customers", "Customer", "Contact", "contacts", [field("name"), field("reference", unique=True)], [field("name"), field("email", required=False), field("phone", required=False), flag("subscribed")]),
    ("devices", "Device", "Inspection", "inspections", [field("serial", unique=True), field("name")], [field("summary"), field("passed", "bool"), flag("follow_up"), date("next_check_on")]),
    ("surveys", "Survey", "Question", "questions", [field("title"), flag("closed")], [field("text"), flag("mandatory"), field("weight", "float", False)]),
    ("courses", "Course", "Lesson", "lessons", [field("code", unique=True), field("title")], [field("title"), field("minutes", "int"), flag("published"), date("release_on")]),
    ("experiments", "Experiment", "Observation", "observations", [field("code", unique=True), field("title")], [field("sample_count", "int"), field("value", "float"), field("reading", "float", False), flag("verified")]),
    ("documents", "Document", "Review", "reviews", [field("reference", unique=True), field("title")], [field("reviewer"), field("score", "float", False), field("notes", required=False), flag("approved"), date("review_by")]),
]


def resource(name, table, fields, limit=100, prefix=""):
    fields = deepcopy(fields)
    boolean = next((f["name"] for f in fields if f.get("default") is False and not f["unique"]), None)
    day = next((f["name"] for f in fields if f["type"] == "date"), None)
    return dict(name=name, table=table, path=prefix + "/" + table, fields=fields, query=dict(filter_field=boolean, date_field=day, max_limit=limit))


def case(app, variant, serial, training=False):
    tables, parent, child, child_table, pf, cf = APPS[app]
    rng = random.Random(serial + 50100)
    pf, cf = deepcopy(pf), deepcopy(cf)
    if variant == 1:
        pf.append(field("note", required=False))
        cf.append(field("counter", "int", False))
    elif variant == 2:
        cf.append(field("external_code", unique=True))
    elif variant == 3:
        pf.append(flag("enabled"))
        cf.append(field("rank", "int"))
    elif variant == 4:
        for role_fields in (pf, cf):
            for i in range(8 - len(role_fields)):
                role_fields.append(field(f"extra_{i}", ("int", "str", "float", "bool")[i % 4], i % 2 == 0))
            rng.shuffle(role_fields)
    if training:
        # New structures, not just new app names or new operation permutations.
        for role_fields in (pf, cf):
            while len(role_fields) < rng.randint(max(1, len(role_fields)), 8):
                i = len(role_fields)
                ty = rng.choice(["str", "int", "float", "bool"])
                role_fields.append(field(f"metric_{i}", ty, rng.choice([True, False]), ty in {"str", "int"} and rng.choice([True, False])))
            for f in role_fields:
                if f["type"] in {"int", "float", "str"} and rng.random() < 0.4:
                    f["required"] = not f["required"]
                    if f["required"]:
                        f.pop("default", None)
                    else:
                        f["default"] = None
            # Ensure training covers every query-template combination.
            kind = rng.randrange(3)
            role_fields[:] = [f for f in role_fields if f["type"] != "date"]
            if kind:
                if not any(f.get("default") is False for f in role_fields):
                    role_fields[:] = role_fields[:6]
                    role_fields.append(flag("selected"))
                if kind == 2:
                    role_fields[:] = role_fields[:7]
                    role_fields.append(date("deadline"))
            else:
                for f in role_fields:
                    if f.get("default") is False:
                        f["default"] = None
            rng.shuffle(role_fields)
    resources = dict(parent=resource(parent, tables, pf, [20, 25, 50, 75, 100][variant], "/api" if variant == 4 else ""), child=resource(child, child_table, cf, [100, 20, 25, 50, 75][variant]))
    steps = [{"scope": role, "step": step} for role in ("parent", "child") for step in CRUD] + [{"scope": "batch", "step": BATCH}]
    if variant or training:
        rng.shuffle(steps)
    return dict(version=VERSION, package=f"app_{tables}", resources=resources, relation=dict(foreign_key=parent.lower() + "_id", on_delete="restrict"), logic=steps, tests=[TEST], migration=dict(revision=f"related{serial}", down_revision=None))


def signature(prompt):
    # Exclude names, paths and operation order: these alone cannot create a split.
    value = []
    for role in ("parent", "child"):
        r = prompt["resources"][role]
        q = r["query"]
        value.append(sorted((f["type"], f["required"], f["unique"], str(f.get("default")), f["name"] == q["filter_field"], f["name"] == q["date_field"]) for f in r["fields"]))
    return json.dumps(value, sort_keys=True)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_rows(path, rows):
    path.write_text("".join(json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n" for row in rows))


def main():
    library = load_blocks(BLOCKS.parent)
    OUT.mkdir(parents=True, exist_ok=True)
    held = []
    info = []
    used = set()
    for app in range(10):
        for variant in range(5):
            prompt = case(app, variant, app * 5 + variant)
            sig = signature(prompt)
            # A deterministic extra nullable field distinguishes any semantic collision.
            if sig in used:
                fields = prompt["resources"]["parent"]["fields"]
                fields.append(field("annotation", "float", False))
                sig = signature(prompt)
            assert sig not in used
            used.add(sig)
            held.append(prompt)
            info.append(dict(index=len(held)-1, app=APPS[app][0], variant=variant, paired=variant == 0))
    train, dev = [], []
    serial = 1000
    for app in range(8):
        for target, count in ((train, 75), (dev, 13 if app < 4 else 12)):
            accepted = 0
            while accepted < count:
                prompt = case(app, serial % 5, serial, True)
                serial += 1
                try:
                    validate_prompt(prompt)
                except V1Error:
                    continue
                sig = signature(prompt)
                if sig in used:
                    continue
                used.add(sig)
                target.append(prompt)
                accepted += 1
    expected = []
    for name, prompts in (("train", train), ("dev", dev), ("heldout", held)):
        rows = []
        for prompt in prompts:
            validate_prompt(prompt)
            dsl = document(prompt, library)
            files = assemble(resolve_prompt_refs(prompt, dsl), library)
            assert len(files) == 12
            for path, source in files.items():
                if path.endswith(".py"):
                    ast.parse(source, filename=path)
            rows.append(dict(prompt=prompt, dsl=dsl))
            if name == "heldout":
                expected.append(files)
        write_rows(OUT / f"{name}.jsonl", rows)
    write_rows(OUT / "heldout.expected.jsonl", expected)
    bad = []
    changes = [lambda p: p["relation"].update(on_delete="cascade"), lambda p: p["relation"].update(foreign_key="id"), lambda p: p["resources"]["child"].update(table=p["resources"]["parent"]["table"]), lambda p: p["logic"].pop(), lambda p: p["logic"][0].update(step="Write arbitrary Python."), lambda p: p["resources"]["parent"]["query"].update(max_limit=101), lambda p: p["resources"]["child"]["fields"].extend([field(f"bad{i}") for i in range(9)]), lambda p: p.update(source="print(1)"), lambda p: p["resources"]["child"]["fields"][0].update(name=p["relation"]["foreign_key"]), lambda p: p["migration"].update(down_revision="previous"), lambda p: p["resources"]["parent"].update(path="/healthz"), lambda p: p["resources"]["child"]["query"].update(filter_field="missing")]
    for mutate in changes:
        prompt = deepcopy(held[0]); mutate(prompt)
        try:
            validate_prompt(prompt)
        except V1Error as error:
            bad.append(dict(prompt=prompt, code=error.code))
        else:
            raise AssertionError("invalid prompt accepted")
    write_rows(OUT / "rejections.jsonl", bad)
    old = ROOT / "som-core/data/crud-query-date-service-v4/train.jsonl"
    (OUT / "combined-train.jsonl").write_bytes(old.read_bytes() + (OUT / "train.jsonl").read_bytes())
    previous = ROOT / "som-core/data/service-regression-v4"
    for name in ("heldout.jsonl", "heldout.expected.jsonl"):
        (OUT / ("regression-" + name)).write_bytes((previous / name).read_bytes() + (OUT / name).read_bytes())
    manifest = dict(train_count=600, dev_count=100, heldout_count=50, regression_count=150, heldout_apps=["experiments", "documents"], cases=info, split="unique name-free, order-free typed structures across new splits", blocks_sha256=digest(BLOCKS), files={p.name: digest(p) for p in sorted(OUT.glob("*.jsonl"))})
    (OUT / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    example = ROOT / "som-rs/examples/related-service-prompt.json"
    example.write_text(json.dumps(held[0], indent=2) + "\n")
    print(json.dumps({"train":len(train), "dev":len(dev), "heldout":len(held), "unique_structures":len(used), "max_output_bytes":max(sum(len(x.encode()) for x in f.values()) for f in expected)}))


if __name__ == "__main__":
    main()

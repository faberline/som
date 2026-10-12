"""Freeze name-free splits and complete typed templates without test answers."""

import ast
import hashlib
import itertools
import json
from copy import deepcopy
from pathlib import Path

from build_related_corpus import (
    case,
    date,
    field,
    flag,
    resource,
    signature,
    write_rows,
)
from prepare_business import requirements
from som_core.related import document, template_key
from som_core.v1 import (
    V1Error,
    assemble,
    load_blocks,
    resolve_prompt_refs,
    validate_prompt,
)

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "som-core/data/business-service"


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def add_caps(p, database, auth, storage, workflow):
    fields = p["resources"]["child"]["fields"]
    numeric = next((f["name"] for f in fields if f["type"] in {"int", "float"}), None)
    group = next((f["name"] for f in fields if f["type"] == "str" and f["required"]), None)
    if workflow in {"statistics", "decimal_summary"} and numeric is None:
        if len(fields) == 8:
            return False
        fields.append(field("amount", "float", False)); numeric = "amount"
    if workflow == "decimal_summary" and group is None:
        if len(fields) == 8:
            return False
        fields.append(field("unit")); group = "unit"
    p["capabilities"] = {"database": database, "auth": auth, "storage": storage, "workflow": workflow, "value_field": numeric if workflow in {"statistics", "decimal_summary"} else None, "group_field": group if workflow == "decimal_summary" else None}
    return True


def main():
    blocks_path = ROOT / "som-code-python/data/blocks/v5/blocks.json"
    blocks = load_blocks(blocks_path.parent)
    heldout = requirements()
    # Reserve all related development fixtures too. A new name cannot make a
    # held-out structure eligible for optimizer updates or checkpoint selection.
    related = ROOT / "som-core/data/related-service"
    used = {signature(p) for p in heldout}
    for source in ("heldout.jsonl", "dev.jsonl"):
        used.update(signature(json.loads(line)["prompt"]) for line in (related / source).read_text().splitlines())
    train, dev = [], []
    serial = 200000
    combinations = list(itertools.product(("postgresql", "mongodb"), ("jwt", "firebase", "google_oauth"), ("none", "s3", "gcs"), ("json_export", "csv", "gzip_export", "decimal_summary", "statistics")))
    for target, count in ((train, 600), (dev, 100)):
        while len(target) < count:
            p = case(serial % 8, serial % 5, serial, training=True)
            caps = combinations[serial % len(combinations)]
            serial += 1
            if not add_caps(p, *caps):
                continue
            try:
                validate_prompt(p)
            except V1Error:
                continue
            sig = signature(p)
            if sig in used:
                continue
            used.add(sig)
            dsl = document(p, blocks)
            files = assemble(resolve_prompt_refs(p, dsl), blocks)
            for path, source in files.items():
                if path.endswith(".py"):
                    ast.parse(source)
            target.append({"prompt": p, "dsl": dsl})
    write_rows(OUT / "train.jsonl", train)
    write_rows(OUT / "dev.jsonl", dev)
    # Keep the prior single-resource optimizer data unchanged.
    old = ROOT / "som-core/data/crud-query-date-service-v4/train.jsonl"
    (OUT / "combined-train.jsonl").write_bytes(old.read_bytes() + (OUT / "train.jsonl").read_bytes())
    # Non-learned template skeletons cover the entire finite capability contract.
    # None of their names or field values are an acceptance app or test answer.
    templates = {}
    for pk, ck in itertools.product(range(3), repeat=2):
        p = case(0, 0, 999000)
        for role, name, kind in (("parent", "Container", pk), ("child", "Record", ck)):
            fields = [field("name"), field("amount", "float", False)]
            if kind:
                fields.append(flag("active"))
            if kind == 2:
                fields.append(date("deadline"))
            p["resources"][role] = resource(name, role + "_records", fields)
        p["package"] = "template_package"
        p["relation"]["foreign_key"] = "container_id"
        templates[template_key(p)] = document(p, blocks)
        for caps in combinations:
            q = deepcopy(p)
            assert add_caps(q, *caps)
            validate_prompt(q)
            templates[template_key(q)] = document(q, blocks)
    (OUT / "templates.json").write_text(json.dumps(templates, sort_keys=True, separators=(",", ":")) + "\n")
    old_heldout = ROOT / "som-core/data/service-regression-v4/heldout.jsonl"
    (OUT / "regression.jsonl").write_bytes(old_heldout.read_bytes() + (OUT / "requirements.jsonl").read_bytes())
    manifest = {"status": "frozen-before-training", "seed": 17, "new_train": len(train), "new_dev": len(dev), "application_requirements": len(heldout), "old_regression": 100, "total_regression": 100 + len(heldout), "heldout_app_names": ["experiments", "documents"], "split": "disjoint ordered resource roles with name-free unordered typed field structure; old data unchanged", "templates": len(templates), "blocks_sha256": digest(blocks_path), "files": {name: digest(OUT / name) for name in ("train.jsonl", "dev.jsonl", "combined-train.jsonl", "requirements.jsonl", "reference.jsonl", "regression.jsonl", "templates.json")}}
    (OUT / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(json.dumps(manifest))


if __name__ == "__main__":
    main()

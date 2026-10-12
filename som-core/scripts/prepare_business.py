"""Produce reviewable application requirements and reference candidates."""

import ast
import json
from copy import deepcopy
from pathlib import Path

from build_related_corpus import case, field, write_rows
from som_core.related import document
from som_core.v1 import assemble, load_blocks, resolve_prompt_refs, validate_prompt

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "som-core/data/business-service"


def requirements():
    scenarios = json.loads((ROOT / "som-rs/evals/related-service/business-scenarios.json").read_text())
    prompts = []
    for index, app in enumerate(scenarios["apps"]):
        prompt = case(index, 0, 80000 + index)
        caps = {key: app[key] for key in ("database", "auth", "storage", "workflow")}
        caps.update(value_field="quantity" if app["id"] == "recipes" else "reading" if app["id"] == "experiments" else None, group_field="unit" if app["id"] == "recipes" else None)
        prompt["capabilities"] = caps
        # Every complete app has a late-write conflict check.
        if not any(f["unique"] for f in prompt["resources"]["child"]["fields"]):
            prompt["resources"]["child"]["fields"].append(field("external_code", unique=True))
        if caps["storage"] != "none":
            prompt["resources"]["child"]["fields"].append(field("object_key", required=False))
        prompts.append(prompt)
    # Extra generated apps exercise structures that the ten standard schemas
    # cannot cover: all-optional inputs, eight fields, and reordered paths.
    for index in (0, 6):
        p = deepcopy(prompts[index])
        p["package"] += "_boundary"
        p["resources"]["parent"]["path"] = "/api/v2" + p["resources"]["parent"]["path"]
        p["logic"].reverse()
        for r in p["resources"].values():
            r["fields"] = [field("code", unique=True), field("quantity", "int", False), field("reading", "float", False), field("note", required=False), field("enabled", "bool", False, default=False), field("flag", "bool"), field("date_value", "date", False), field("extra", required=False)]
            r["query"] = {"filter_field": "enabled", "date_field": "date_value", "max_limit": 100}
        p["capabilities"].update(workflow="json_export", value_field=None, group_field=None)
        prompts.append(p)
        p = deepcopy(prompts[index])
        p["package"] += "_optional"
        for r in p["resources"].values():
            r["fields"] = [field("code", required=False, unique=True), field("quantity", "int", False), field("enabled", "bool", False, default=False)]
            r["query"] = {"filter_field": "enabled", "date_field": None, "max_limit": 20}
        p["logic"] = p["logic"][5:] + p["logic"][:5]
        prompts.append(p)
    return prompts


def main():
    blocks = load_blocks(ROOT / "som-code-python/data/blocks/v5")
    OUT.mkdir(parents=True, exist_ok=True)
    rows, expected, info = [], [], []
    for index, p in enumerate(requirements()):
        validate_prompt(p)
        dsl = document(p, blocks)
        files = assemble(resolve_prompt_refs(p, dsl), blocks)
        for path, source in files.items():
            if path.endswith(".py"):
                ast.parse(source, filename=path)
        rows.append({"prompt": p, "dsl": dsl})
        expected.append(files)
        info.append({"index": index, "app": p["package"], "files": len(files), "bytes": sum(len(s.encode()) for s in files.values()), "paired": index < 10})
    write_rows(OUT / "requirements.jsonl", rows)
    write_rows(OUT / "reference.jsonl", expected)
    (OUT / "coverage.json").write_text(json.dumps({"status": "reference-development-not-frozen", "application_count": len(rows), "cases": info}, indent=2) + "\n")
    print(json.dumps(info))


if __name__ == "__main__":
    main()

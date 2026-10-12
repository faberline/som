"""Check structural schemas independently of the semantic validator."""

import argparse
from copy import deepcopy
import hashlib
import importlib.metadata
import json
from pathlib import Path

from jsonschema import Draft202012Validator

from som_core import flow
from som_core.v1 import load_blocks

ROOT = Path(__file__).resolve().parents[2]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    paths = {name: ROOT / "som-rs/contracts" / (name + ".schema.json")
             for name in (flow.VERSION, flow.DSL_VERSION)}
    validators = {}
    for name, path in paths.items():
        schema = json.loads(path.read_text())
        Draft202012Validator.check_schema(schema)
        validators[name] = Draft202012Validator(schema)
    blocks = load_blocks(ROOT / "som-code-python/data/blocks/flow-v7")
    corpus = ROOT / "som-core/data/flow-v7-r10"
    records = []
    prompts = [json.loads(line)["prompt"] for split in ("dev", "formal")
               for line in (corpus / (split + ".jsonl")).read_text().splitlines()]
    for index, prompt in enumerate(prompts):
        for name, value in ((flow.VERSION, prompt), (flow.DSL_VERSION, flow.reference(prompt, blocks))):
            errors = [error.message for error in validators[name].iter_errors(value)]
            records.append({"id": str(index) + "-" + name, "valid": True,
                            "passed": not errors, "errors": errors})
    alternate = flow.reference(prompts[0], blocks, "checked")
    validators[flow.DSL_VERSION].validate(alternate)
    negative = []
    prompt = deepcopy(prompts[0]); prompt["source"] = "print('not allowed')"
    negative.append(("source", flow.VERSION, prompt))
    prompt = deepcopy(prompts[0]); prompt["flow"]["steps"][1]["op"] = "sql"
    negative.append(("sql-operation", flow.VERSION, prompt))
    prompt = deepcopy(prompts[0]); prompt["flow"]["steps"][1]["args"]["parent_id"] = "1"
    negative.append(("untyped-ref", flow.VERSION, prompt))
    prompt = deepcopy(prompts[0]); prompt["flow"]["steps"][1]["block_id"] = "private"
    negative.append(("prompt-block-id", flow.VERSION, prompt))
    for key, bad in (("path", "../../outside.py"), ("block", "unapproved"), ("symbol", "engine"), ("param", "import os")):
        dsl = deepcopy(alternate)
        if key == "path": dsl["files"][0]["path"] = bad
        if key == "block": dsl["files"][0]["blocks"][0]["id"] = bad
        if key == "symbol": dsl["files"][0]["blocks"][0]["refs"] = [bad]
        if key == "param": dsl["files"][0]["blocks"][0]["params"] = {"source": bad}
        negative.append((key, flow.DSL_VERSION, dsl))
    for name, version, value in negative:
        rejected = not validators[version].is_valid(value)
        records.append({"id": name, "valid": False, "passed": rejected})
    result = {"stage": "structural_schema_only", "product_acceptance": False,
              "jsonschema_version": importlib.metadata.version("jsonschema"),
              "schema_sha256": {name: hashlib.sha256(path.read_bytes()).hexdigest() for name, path in paths.items()},
              "cases": len(records) + 1, "legal_alternative_passed": True,
              "passed": all(row["passed"] for row in records), "records": records}
    (args.output / "result.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({key: result[key] for key in ("stage", "cases", "passed")}))
    raise SystemExit(not result["passed"])


if __name__ == "__main__":
    main()

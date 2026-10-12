"""Measure and lint compact artifacts. Never bypass product acceptance."""

import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import subprocess

from som_core import flow, flow_codec, flow_compact as compact, quality
from som_core.v1 import assemble, load_blocks
from freeze_flow_cases import requirement

ROOT = Path(__file__).resolve().parents[2]


def maximum_cases():
    """Exercise eight fields, eight actions, nested branches and two sessions."""
    cases = []
    for index, database in enumerate(('postgresql', 'mongodb')):
        prompt = requirement(database, 'google_oauth', 's3', 'csv', 3000 + index, 21)
        prompt["resources"]["parent"]["fields"] += [
            {"name": "rating", "type": "float", "required": True, "unique": False},
            {"name": "number", "type": "int", "required": True, "unique": False},
            {"name": "enabled", "type": "bool", "required": False, "default": False, "unique": False},
        ]
        cases.append(("max-" + database, prompt))
    return cases


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--all", action="store_true")
    args = parser.parse_args()
    args.output.mkdir(parents=True,exist_ok=False)
    library = load_blocks(ROOT/"som-code-python/data/blocks/flow-v7-compact")
    rows = [json.loads(line) for line in (ROOT/"som-core/data/flow-v7-r10/formal.jsonl").read_text().splitlines()]
    cases = maximum_cases()
    if args.all:
        cases += [(row["id"],row["prompt"]) for row in rows]
        cases += [(row["id"],row["prompt"]) for row in map(json.loads, (ROOT/'som-core/data/flow-v7-r10/dev.jsonl').read_text().splitlines())]
    binary=ROOT/"som-rs/models/composed-quality-candidate/ruff"
    records=[]
    for identity, prompt in cases:
        destination=args.output/identity; destination.mkdir()
        dsl=compact.reference(prompt,library)
        model_input = flow_codec.features(prompt, compact.metadata())
        model_output = flow_codec.encode(prompt, dsl, compact.metadata())
        if flow_codec.decode(prompt, model_output, compact.metadata()) != dsl:
            raise ValueError('compact codec changed a model choice')
        lowered=compact.resolve(prompt,dsl)
        files={}; violations={}
        for file in lowered["files"]:
            source=assemble({"version":"som.dsl.v2","files":[file]},library)[file["path"]]
            path=file["path"]
            if path.endswith(".py"):
                source=quality.assemble_imports(source)
                common=["--config",str(quality.PROFILE),"--config","lint.isort.known-first-party="+json.dumps([prompt["package"]]),"--no-cache","--stdin-filename",path]
                for command in (["check","--select","I","--fix"],["format"]):
                    result=subprocess.run([str(binary),*command,*common,"-"],input=source,text=True,capture_output=True,check=False)
                    if result.returncode:
                        (destination/"formatter-error.txt").write_text(result.stdout+result.stderr)
                        raise ValueError(f"formatter failed: {identity} {path}; {result.stderr}")
                    source=result.stdout
                check=subprocess.run([str(binary),"check",*common,"--output-format=json","-"],input=source,text=True,capture_output=True,check=False)
                violations[path]=json.loads(check.stdout)
            target=destination/"product"/path;target.parent.mkdir(parents=True,exist_ok=True);target.write_text(source)
            files[path]=source
        record={"id":identity,"stage":"diagnostic","product_acceptance":False,"formatted_bytes":sum(len(source.encode()) for source in files.values()),"files":len(files),"blocks":sum(len(file["blocks"]) for file in dsl["files"]),"ruff_violations":sum(len(value) for value in violations.values()),"file_bytes":{path:len(source.encode()) for path,source in files.items()}}
        record.update(input_tokens=model_input.count('<'), output_tokens=model_output.count('<'))
        (destination/'model-input.txt').write_text(model_input)
        (destination/'model-output.txt').write_text(model_output)
        (destination/"ruff.json").write_text(json.dumps(violations,indent=2)+"\n")
        (destination/"prompt.json").write_text(json.dumps(prompt,indent=2)+"\n")
        (destination/"dsl.json").write_text(json.dumps(dsl,indent=2)+"\n")
        (destination/"result.json").write_text(json.dumps(record,indent=2)+"\n")
        records.append(record)
        print(json.dumps(record),flush=True)
    report={"records":records,"blocks_sha256":hashlib.sha256((ROOT/"som-code-python/data/blocks/flow-v7-compact/blocks.json").read_bytes()).hexdigest(),"catalog_sha256":hashlib.sha256(compact.CATALOG.read_bytes()).hexdigest(),"passed":all(r["files"]<=12 and r["blocks"]<=64 and max(r["input_tokens"],r["output_tokens"])<=2048 and r["ruff_violations"]==0 for r in records),"output_budget":"som.m2.output-budget.v2","product_acceptance":False}
    (args.output/"result.json").write_text(json.dumps(report,indent=2)+"\n")
    raise SystemExit(not report["passed"])


if __name__=="__main__": main()

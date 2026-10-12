"""Measure draft helpers. Diagnostic files are never successful SOM output."""

import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re
import subprocess

from som_core import composed, flow, flow_codec, quality
from som_core.v1 import assemble, load_blocks

ROOT=Path(__file__).resolve().parents[2]


def measure(prompt, blocks, destination):
    dsl=flow.reference(prompt,blocks)
    resolved=flow.resolve(prompt,dsl)
    binary=ROOT/"som-rs/models/composed-quality-candidate/ruff"
    files={}; violations={}
    for file in resolved["files"]:
        path=file["path"]
        source=assemble({"version":"som.dsl.v2","files":[file]},blocks)[path]
        if path.endswith(".py"):
            source=quality.assemble_imports(source)
            common=["--config",str(quality.PROFILE),"--config","lint.isort.known-first-party="+json.dumps([prompt["package"]]),"--no-cache","--stdin-filename",path]
            for command in (["check","--select","I","--fix"],["format"]):
                result=subprocess.run([str(binary),*command,*common,"-"],input=source,text=True,capture_output=True,check=True)
                source=result.stdout
            checked=subprocess.run([str(binary),"check",*common,"--output-format=json","-"],input=source,text=True,capture_output=True,check=False)
            violations[path]=json.loads(checked.stdout)
        files[path]=source
        if destination is not None:
            target=destination/"product"/path; target.parent.mkdir(parents=True,exist_ok=True); target.write_text(source)
    report={"product_acceptance":False,"stage":"budget_diagnostic","files":len(files),"blocks":sum(len(f["blocks"]) for f in dsl["files"]),"formatted_bytes":sum(len(s.encode()) for s in files.values()),
            "input_tokens":len(re.findall(r"<[^<>]+>",flow_codec.features(prompt))),"output_tokens":len(re.findall(r"<[^<>]+>",flow_codec.encode(prompt,dsl))),
            "ruff_violations":sum(len(v) for v in violations.values()),"file_bytes":{name:len(source.encode()) for name,source in files.items()},"source_sha256":{name:hashlib.sha256(source.encode()).hexdigest() for name,source in files.items()}}
    report["budget_passed"]=report["files"]<=12 and report["blocks"]<=64 and report["formatted_bytes"]<=65536 and max(report["input_tokens"],report["output_tokens"])<=2048
    if destination is not None:
        (destination/"prompt.json").write_text(json.dumps(prompt,indent=2)+"\n")
        (destination/"dsl.json").write_text(json.dumps(dsl,indent=2)+"\n")
        (destination/"ruff.json").write_text(json.dumps(violations,indent=2)+"\n")
        (destination/"result.json").write_text(json.dumps(report,indent=2)+"\n")
    return report


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--output",type=Path,required=True)
    parser.add_argument("--all",action="store_true")
    args=parser.parse_args(); args.output.mkdir(parents=True,exist_ok=False)
    blocks=load_blocks(ROOT/"som-code-python/data/blocks/flow-v7")
    rows=[json.loads(line) for line in (ROOT/"som-core/data/flow-v7-r10/formal.jsonl").read_text().splitlines()]
    cases=[]
    for index in (37,82):
        p=deepcopy(rows[index]["prompt"])
        p["resources"]["parent"]["fields"] += [{"name":"rating","type":"float","required":True,"unique":False},{"name":"number","type":"int","required":True,"unique":False},{"name":"enabled","type":"bool","required":False,"default":False,"unique":False}]
        cases.append(("max-"+p["capabilities"]["database"],p))
    if args.all: cases += [(row["id"],row["prompt"]) for row in rows]
    records=[]
    for name,prompt in cases:
        record=measure(prompt,blocks,args.output/name)
        records.append({"id":name,**record})
        print(json.dumps({key:records[-1][key] for key in ("id","formatted_bytes","blocks","input_tokens","output_tokens","ruff_violations","budget_passed")}),flush=True)
    report={"limits":{"files":12,"blocks":64,"bytes":65536,"input_tokens":2048,"output_tokens":2048},"records":records,"passed":all(r["budget_passed"] for r in records),"product_acceptance":False,
            "contract_sha256":flow.contract_hash(),"blocks_sha256":quality.sha256(ROOT/"som-code-python/data/blocks/flow-v7/blocks.json"),"catalog_sha256":quality.sha256(flow.CATALOG),"formatter_sha256":quality.sha256(ROOT/"som-rs/models/composed-quality-candidate/ruff")}
    (args.output/"result.json").write_text(json.dumps(report,indent=2)+"\n")
    raise SystemExit(not report["passed"])


if __name__=="__main__": main()

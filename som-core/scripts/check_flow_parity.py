"""Compare typed v7 decisions and lowering; product gates stay separate."""

import argparse
from copy import deepcopy
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess

from som_core import composed, flow, flow_codec
from som_core.v1 import V1Error, load_blocks

ROOT=Path(__file__).resolve().parents[2]


def negative_inputs():
    specification=importlib.util.spec_from_file_location("flow_negative", ROOT/"som-core/tests/test_flow_contract.py")
    tests=importlib.util.module_from_spec(specification); specification.loader.exec_module(tests)
    for change,code in tests.REJECTIONS.items():
        # Wire JSON contains no non-finite numbers. Keep that source-level
        # mutation in the Python test and test its wire representation here.
        p=tests.prompt_failure(tests.row(),change)
        if change=="nonfinite": p["flow"]["steps"][2]["condition"]["right"]["literal"]="Infinity"
        yield {"id":"reject-"+change,"action":"prompt","prompt":p,"expected_code":code}


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--output",type=Path,required=True)
    parser.add_argument("--corpus",type=Path,default=ROOT/"som-core/data/flow-v7-r10")
    args=parser.parse_args(); args.output.mkdir(parents=True,exist_ok=False)
    blocks=load_blocks(ROOT/"som-code-python/data/blocks/flow-v7")
    binary=ROOT/"som-rs/target/flow-m2/debug/som-flow-verify"
    cases=[]
    for group in ("dev","formal"):
        for line in (args.corpus/(group+".jsonl")).read_text().splitlines():
            row=json.loads(line); p=row["prompt"]
            d=flow.reference(p,blocks)
            cases.append({"id":row["id"],"action":"resolve","prompt":p,"encoded":flow_codec.encode(p,d)})
    p=json.loads((args.corpus/"dev.jsonl").read_text().splitlines()[0])["prompt"]
    alternate=flow.reference(p,blocks,"checked")
    cases.append({"id":"alternate-checked","action":"resolve","prompt":p,"dsl":alternate})
    cases.extend(negative_inputs())
    standard=flow.reference(p,blocks)
    for label in ("unknown","truncated","untyped_symbol","wrong_inverse","missing_helper","wrong_block", "list_inverse", "object_inverse", "number_inverse", "boolean_inverse"):
        d=deepcopy(standard); case={"id":"dsl-"+label,"prompt":p,"action":"resolve","dsl":d}
        if label=="unknown": case["encoded"]="<wrong><eos>"
        if label=="truncated": case["encoded"]=flow_codec.encode(p,d).removesuffix("<eos>")
        if label=="untyped_symbol":
            b=next(b for f in d["files"] for b in f["blocks"] if b["refs"])
            b["refs"][0]=b["refs"][0]["symbol"]
        if label=="wrong_inverse": d["flow"]["steps"][1]["undo"]="flow.postgresql.undo.delete_created"
        if label.endswith("_inverse") and label != "wrong_inverse":
            invalid = {"list_inverse": [], "object_inverse": {}, "number_inverse": 123, "boolean_inverse": True}
            d["flow"]["steps"][1]["undo"] = invalid[label]
        if label=="wrong_block": d["flow"]["steps"][1]["id"]="flow.mongodb.parent.get"
        if label=="missing_helper":
            f=next(f for f in d["files"] if f["path"]==json.loads(flow.CATALOG.read_text())["slots"]["flow_helpers"])
            b=next(b for b in f["blocks"] if b["id"].startswith("flow.")); f["blocks"].remove(b)
        cases.append(case)
    sent="".join(composed.canonical(case)+"\n" for case in cases)
    (args.output/"inputs.jsonl").write_text(sent)
    result=subprocess.run([str(binary),str(ROOT/"som-code-python/data/blocks/flow-v7/blocks.json")],input=sent,text=True,capture_output=True,check=False)
    (args.output/"rust.stdout").write_text(result.stdout); (args.output/"rust.stderr").write_text(result.stderr)
    if result.returncode: raise RuntimeError("Rust parity process failed")
    output=[json.loads(line) for line in result.stdout.splitlines()]
    if len(cases)!=len(output): raise ValueError("truncated Rust output")
    records=[]
    for case,rust in zip(cases,output,strict=True):
        p=case["prompt"]; record={"id":case["id"]}
        try:
            flow.validate(p)
            expected={"status":"ok","features":flow_codec.features(p)}
            if case["action"]=="prompt": expected["semantic"]=flow.semantic_view(p)
            else:
                d=flow_codec.decode(p,case["encoded"]) if "encoded" in case else case["dsl"]
                expected.update(dsl=d,resolved=flow.resolve(p,d),encoded=flow_codec.encode(p,d))
            record.update(kind="valid",passed=rust==expected)
            if rust!=expected:
                record["different_keys"]=[key for key in set(expected)|set(rust) if expected.get(key)!=rust.get(key)]
        except V1Error as error:
            record.update(kind="rejected",python=error.code,rust=rust.get("code"),passed=rust.get("code")==error.code)
        if "expected_code" in case: record["passed"] &= record.get("python")==case["expected_code"]
        records.append(record)
    report={"stage":"semantic_and_lowering_only","product_acceptance":False,"cases":len(cases),"valid":sum(r["kind"]=="valid" for r in records),"rejected":sum(r["kind"]=="rejected" for r in records),"passed":all(r["passed"] for r in records),"records":records,
            "inputs_sha256":hashlib.sha256(sent.encode()).hexdigest(),"binary_sha256":hashlib.sha256(binary.read_bytes()).hexdigest(),"codec_version":flow_codec.VERSION}
    (args.output/"result.json").write_text(json.dumps(report,indent=2)+"\n")
    print(json.dumps({k:report[k] for k in ("stage","cases","valid","rejected","passed")}))
    raise SystemExit(not report["passed"])


if __name__=="__main__": main()

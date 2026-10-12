"""Prove compact token parity without claiming assembly or model acceptance."""

import argparse
from copy import deepcopy
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess

from som_core import composed, flow, flow_codec, flow_compact
from som_core.v1 import V1Error, load_blocks

from check_flow_parity import negative_inputs
from probe_flow_compact import maximum_cases

ROOT = Path(__file__).resolve().parents[2]
CORPUS = ROOT / "som-core/data/flow-v7-r10"
LIBRARY = ROOT / "som-code-python/data/blocks/flow-v7-compact"
BINARY = ROOT / "som-rs/target/flow-m2/debug/som-flow-codec"


def source_hashes() -> dict[str, str]:
    paths = [
        flow_compact.CATALOG,
        LIBRARY / "blocks.json",
        BINARY,
        CORPUS / "dev.jsonl",
        CORPUS / "formal.jsonl",
        ROOT / "som-rs/contracts/related-flow.v1.md",
        Path(__file__).resolve(),
        ROOT / "som-core/scripts/check_flow_parity.py",
        ROOT / "som-core/scripts/probe_flow_compact.py",
        ROOT / "som-core/tests/test_flow_compact_contract.py",
        ROOT / "som-core/tests/test_flow_contract.py",
    ]
    paths += [ROOT / ("som-core/src/som_core/" + name + ".py")
              for name in ("flow", "flow_codec", "flow_compact", "composed")]
    paths += [ROOT / ("som-rs/src/" + name)
              for name in ("flow.rs", "flow_codec.rs", "composed.rs",
                           "bin/som-flow-codec.rs")]
    return {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in paths}


def cases() -> list[dict[str, object]]:
    blocks = load_blocks(LIBRARY)
    rows = []
    for group in ("dev", "formal"):
        for line in (CORPUS / (group + ".jsonl")).read_text().splitlines():
            row = json.loads(line)
            rows.append({"id": row["id"], "prompt": row["prompt"],
                         "dsl": flow_compact.reference(row["prompt"], blocks)})
            if group == "dev":
                rows.append({"id": row["id"] + "-checked", "prompt": row["prompt"],
                             "dsl": flow_compact.reference(row["prompt"], blocks, "checked")})
    for identity, prompt in maximum_cases():
        rows.append({"id": identity, "prompt": prompt,
                     "dsl": flow_compact.reference(prompt, blocks)})
    specification = importlib.util.spec_from_file_location(
        "compact_contract", ROOT / "som-core/tests/test_flow_compact_contract.py")
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    prompt = module.nullable_prompt()
    for variant in ("standard", "checked"):
        rows.append({"id": "null-refinement-" + variant, "prompt": prompt,
                     "dsl": flow_compact.reference(prompt, blocks, variant)})
    renamed = deepcopy(prompt)
    renamed["package"] = "parent"
    renamed["flow"]["name"] = "string"
    renamed["flow"]["result"]["note"] = {"literal": "型別：保留引用與順序", "type": "string"}
    rows.append({"id": "scalar-collision-unicode", "prompt": renamed,
                 "dsl": flow_compact.reference(renamed, blocks)})
    for index, names in enumerate((('Store', 'Run'), ('Context', 'Resource'), ('ParentInput', 'ChildInput'))):
        collision = deepcopy(rows[1]['prompt'])
        collision['capabilities']['database'] = 'mongodb'
        collision['resources']['parent']['name'], collision['resources']['child']['name'] = names
        rows.append({'id': 'helper-collision-' + str(index), 'prompt': collision,
                     'dsl': flow_compact.reference(collision, blocks)})
    scalar_prompt = rows[0]['prompt']
    for number in (0.0, -0.0, 0.1, 0.3, 1e-7, -1e-7, 1e-6, 1e-5,
                   1e-4, 1.23456789e-5, 1.234567891234567e-5,
                   0.84551240822557006, 2.2250738585072014e-308,
                   1e16, 1e20, 5e-324, 1.7976931348623157e308):
        scalar = deepcopy(scalar_prompt)
        scalar['flow']['steps'].append({
            'name': 'compare', 'op': 'if',
            'condition': {'op': 'eq',
                          'left': {'literal': number, 'type': 'float'},
                          'right': {'literal': 0.0, 'type': 'float'}},
            'then': [], 'else': [], 'exports': {},
        })
        rows.append({'id': 'finite-scalar-' + repr(number), 'prompt': scalar,
                     'dsl': flow_compact.reference(scalar, blocks)})
    for name, expected, actual, kind in (
            ('integer-is-not-boolean', True, 1, 'boolean'),
            ('float-is-not-integer', 1, 1.0, 'integer'),
            ('signed-zero', 0.0, -0.0, 'float')):
        scalar = deepcopy(scalar_prompt)
        scalar['flow']['result']['scalar'] = {'literal': expected, 'type': kind}
        plan = flow_compact.reference(scalar, blocks)
        plan['flow']['result']['scalar']['literal'] = actual
        rows.append({'id': name, 'prompt': scalar, 'dsl': plan,
                     'expected_code': 'INVALID_COMPOSITION'})
    rows.extend(negative_inputs())
    baseline = rows[0]
    prompt, standard = baseline["prompt"], baseline["dsl"]
    for label in ("wrong_inverse", "wrong_block", "missing_helper", "untyped_symbol"):
        plan = deepcopy(standard)
        if label == "wrong_inverse":
            plan["flow"]["steps"][1]["undo"] = "flow.postgresql.undo.delete_created"
        elif label == "wrong_block":
            plan["flow"]["steps"][1]["id"] = "flow.mongodb.parent.get"
        elif label == "missing_helper":
            file = next(file for file in plan["files"]
                        if file["path"] == flow_compact.metadata()["slots"]["flow_helpers"])
            file["blocks"].remove(next(block for block in file["blocks"]
                                       if block["id"].startswith("flow.")))
        else:
            block = next(block for file in plan["files"] for block in file["blocks"]
                         if block["refs"])
            block["refs"][0] = block["refs"][0]["symbol"]
        rows.append({"id": "dsl-" + label, "prompt": prompt, "dsl": plan})
    values = flow_codec.pool(prompt, metadata=flow_compact.metadata())
    index = values.index("version")
    encoded = flow_codec.encode(prompt, standard, flow_compact.metadata())
    invalid = {
        "truncated": encoded.removesuffix("<eos>"),
        "unknown": "<v:999999><eos>",
        "duplicate_key": f"<map><v:{index}><v:0><v:{index}><v:0></map><eos>",
        "extra": encoded + "<v:0>",
        "deep": "<list>" * 34 + "<eos>",
        "output_budget": "<list>" + "<v:0>" * 2046 + "</list><eos>",
    }
    rows += [{"id": "tokens-" + label, "prompt": prompt, "encoded": text}
             for label, text in invalid.items()]
    return rows


def evaluate(case: dict[str, object], catalog: dict[str, object]) -> dict[str, object]:
    prompt = case["prompt"]
    try:
        features = flow_codec.features(prompt, catalog)
        if case.get("action") == "prompt":
            return {"status": "ok", "features": features}
        plan = (flow_codec.decode(prompt, case["encoded"], catalog)
                if "encoded" in case else case["dsl"])
        encoded = flow_codec.encode(prompt, plan, catalog)
        return {"status": "ok", "features": features, "encoded": encoded,
                "dsl": flow_codec.decode(prompt, encoded, catalog)}
    except V1Error as error:
        return {"status": "error", "code": error.code}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    before = source_hashes()
    result = subprocess.run([str(BINARY), "--compact", "--identity"],
                            capture_output=True, text=True, check=True, timeout=10)
    identity = json.loads(result.stdout)
    expected_identity = {
        "catalog_sha256": before[str(flow_compact.CATALOG.relative_to(ROOT))],
        "contract_sha256": before["som-rs/contracts/related-flow.v1.md"],
        "codec_version": flow_codec.COMPACT_VERSION,
        "prompt_version": flow.VERSION,
        "dsl_version": flow.DSL_VERSION,
    }
    (args.output / "identity.json").write_text(json.dumps(identity, indent=2) + "\n")
    if identity != expected_identity:
        raise ValueError("Rust compiled assets differ; rebuild the candidate binary")
    rows = cases()
    sent = "".join(composed.canonical(row) + "\n" for row in rows)
    (args.output / "inputs.jsonl").write_text(sent)
    run = subprocess.run([str(BINARY), "--compact"], input=sent, text=True,
                         capture_output=True, check=False, timeout=120)
    (args.output / "rust.stdout").write_text(run.stdout)
    (args.output / "rust.stderr").write_text(run.stderr)
    if run.returncode:
        raise RuntimeError("Rust codec process failed")
    outputs = [json.loads(line) for line in run.stdout.splitlines()]
    if len(outputs) != len(rows):
        raise ValueError("Rust result count differs")
    python_results, records = [], []
    for row, rust in zip(rows, outputs, strict=True):
        python = evaluate(row, flow_compact.metadata())
        python_results.append(python)
        passed = (python == rust if python["status"] == "ok"
                  else rust.get("status") == "error" and rust.get("code") == python["code"])
        if "expected_code" in row:
            passed &= python.get("code") == row["expected_code"]
        record = {"id": row["id"], "passed": passed, "status": python["status"],
                  "python_code": python.get("code"), "rust_code": rust.get("code")}
        if not passed:
            record["different_keys"] = [key for key in set(python) | set(rust)
                                        if python.get(key) != rust.get(key)]
        records.append(record)
    (args.output / "python.jsonl").write_text(
        "".join(composed.canonical(result) + "\n" for result in python_results))
    unchanged = before == source_hashes()
    report = {"stage": "compact_codec_only", "product_acceptance": False,
              "source_sha256": before, "source_unchanged": unchanged,
              "identity": identity, "cases": len(records),
              "valid": sum(row["status"] == "ok" for row in records),
              "rejected": sum(row["status"] == "error" for row in records),
              "passed": unchanged and all(row["passed"] for row in records),
              "records": records,
              "inputs_sha256": hashlib.sha256(sent.encode()).hexdigest()}
    (args.output / "result.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({key: report[key] for key in
                      ("stage", "cases", "valid", "rejected", "passed")}))
    raise SystemExit(not report["passed"])


if __name__ == "__main__":
    main()

"""Compare closed compilation and final files; behavior remains a separate gate."""

import argparse
import hashlib
import json
from pathlib import Path
import subprocess

from som_core import composed, flow, flow_codec, flow_compact, quality
from som_core.v1 import V1Error, load_blocks

from check_flow_compact_codec import cases

ROOT = Path(__file__).resolve().parents[2]
BINARY = ROOT / "som-rs/target/flow-m2/debug/som-flow-compact-verify"
BLOCKS = ROOT / "som-code-python/data/blocks/flow-v7-compact/blocks.json"
FORMATTER = ROOT / "som-rs/models/composed-quality-candidate"


def hashes() -> dict[str, str]:
    paths = [BINARY, BLOCKS, flow_compact.CATALOG, flow.CONTRACT_PATH,
             Path(__file__).resolve(),
             ROOT / "som-core/scripts/check_flow_compact_codec.py",
             ROOT / "som-core/scripts/probe_flow_compact.py",
             FORMATTER / "ruff", FORMATTER / "quality.json",
             FORMATTER / "pyproject.toml"]
    paths += [ROOT / ("som-core/src/som_core/" + name + ".py")
              for name in ("flow", "flow_codec", "flow_compact", "v1", "quality", "composed", "compact_names")]
    paths += [ROOT / ("som-rs/src/" + name + ".rs")
              for name in ("flow", "flow_codec", "flow_compact", "lib", "quality", "composed")]
    paths += [ROOT / "som-rs/src/bin/som-flow-compact-verify.rs"]
    paths += sorted((ROOT / "som-core/block_templates/flow_compact").glob("*.tpl"))
    return {str(path.relative_to(ROOT)): quality.sha256(path) for path in paths}


def run(rows: list[dict[str, object]], directory: Path, label: str) -> list[dict[str, object]]:
    sent = "".join(composed.canonical(row) + "\n" for row in rows)
    (directory / (label + "-inputs.jsonl")).write_text(sent)
    result = subprocess.run([str(BINARY), str(BLOCKS)], input=sent, text=True,
                            capture_output=True, check=False, timeout=900)
    (directory / (label + "-rust.jsonl")).write_text(result.stdout)
    (directory / (label + "-rust.stderr")).write_text(result.stderr)
    if result.returncode:
        raise RuntimeError("Rust candidate process failed")
    output = [json.loads(line) for line in result.stdout.splitlines()]
    if len(rows) != len(output):
        raise ValueError("Rust result count differs")
    return output


def program_cases(directory: Path) -> list[dict[str, object]]:
    rows = [dict(row, action="program") for row in cases()]
    rust = run(rows, directory, "program")
    python, records = [], []
    for row, actual in zip(rows, rust, strict=True):
        try:
            flow.validate(row["prompt"])
            plan = (flow_codec.decode(row["prompt"], row["encoded"], flow_compact.metadata())
                    if "encoded" in row else row["dsl"])
            flow_compact.resolve(row["prompt"], plan)
            expected = {"status": "ok", "program": flow_compact.compile_program(
                {"prompt": row["prompt"], "plan": plan["flow"]})}
        except V1Error as error:
            expected = {"status": "error", "code": error.code}
        python.append(expected)
        passed = (expected == actual if expected["status"] == "ok" else
                  actual.get("status") == "error" and actual.get("code") == expected["code"])
        records.append({"id": row["id"], "kind": expected["status"], "passed": passed,
                        "python_code": expected.get("code"), "rust_code": actual.get("code")})
    (directory / "program-python.jsonl").write_text(
        "".join(composed.canonical(row) + "\n" for row in python))
    return records


def artifacts(directory: Path, diagnostic: Path) -> list[dict[str, object]]:
    frozen = json.loads((diagnostic / "result.json").read_text())
    if (frozen["blocks_sha256"] != quality.sha256(BLOCKS)
            or frozen["catalog_sha256"] != quality.sha256(flow_compact.CATALOG)):
        raise ValueError("diagnostic product source differs")
    rows, expected, records = [], [], []
    library = load_blocks(BLOCKS.parent)
    for record in frozen["records"]:
        source = diagnostic / record["id"]
        prompt = json.loads((source / "prompt.json").read_text())
        plan = json.loads((source / "dsl.json").read_text())
        # Behavior checks may install a venv beside the frozen files. Read
        # only the declared product paths, never dependencies or run logs.
        files = {path: (source / "product" / path).read_text()
                 for path in record['file_bytes']}
        if sum(len(text.encode()) for text in files.values()) != record["formatted_bytes"]:
            raise ValueError("diagnostic file size differs")
        rows.append({"prompt": prompt, "dsl": plan, "action": "diagnostic_files",
                     "formatter_model": str(FORMATTER)})
        expected.append(files)
        entry = {"id": record["id"], "formatted_bytes": record["formatted_bytes"],
                 "source_sha256": {path: hashlib.sha256(text.encode()).hexdigest()
                                    for path, text in files.items()}}
        try:
            final = flow_compact.assemble(prompt, plan, library, FORMATTER / "ruff")
            entry.update(python_product="accepted", normal_bytes_match=final == files)
        except V1Error as error:
            entry.update(python_product=error.code,
                         normal_bytes_match=False)
        entry["format_idempotent"] = all(
            not path.endswith(".py") or quality.format_source(path, text, FORMATTER / "ruff",
                                                              package=prompt["package"]) == text
            for path, text in files.items())
        records.append(entry)
    actual = run(rows, directory, "draft")
    normal = run([dict(row, action="assemble") for row in rows], directory, "normal")
    for record, files, draft, final in zip(records, expected, actual, normal, strict=True):
        record["draft_byte_parity"] = (
            draft.get("status") == "diagnostic" and draft.get("product_acceptance") is False
            and draft.get("files") == files)
        record["rust_product"] = final.get("code", "accepted")
        record["normal_byte_or_rejection_parity"] = (
            final.get("files") == files if record["python_product"] == "accepted"
            else final.get("code") == record["python_product"])
        record["passed"] = all(record[key] for key in
                               ("normal_bytes_match", "format_idempotent", "draft_byte_parity",
                                "normal_byte_or_rejection_parity"))
    return records


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--diagnostic-root", type=Path)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    before = hashes()
    result = subprocess.run([str(BINARY), "--identity"], capture_output=True,
                            text=True, check=True, timeout=10)
    identity = json.loads(result.stdout)
    expected = {"catalog_sha256": quality.sha256(flow_compact.CATALOG),
                "contract_sha256": flow.contract_hash(),
                "support_sha256": quality.sha256(ROOT / "som-core/block_templates/flow_compact/forward_support.py.tpl"),
                "codec_version": flow_codec.COMPACT_VERSION}
    if identity != expected:
        raise ValueError("Rust compiled assets differ")
    programs = program_cases(args.output)
    products = artifacts(args.output, args.diagnostic_root) if args.diagnostic_root else []
    unchanged = before == hashes()
    report = {"stage": "compact_compiler_and_artifact_parity", "behavior_acceptance": False,
              "source_sha256": before, "source_unchanged": unchanged, "identity": identity,
              "program_cases": len(programs), "program_passed": sum(row["passed"] for row in programs),
              "artifact_cases": len(products), "artifact_passed": sum(row["passed"] for row in products),
              "product_accepted": sum(row["python_product"] == "accepted" for row in products),
              "programs": programs, "artifacts": products,
              "passed": unchanged and all(row["passed"] for row in programs + products)}
    (args.output / "result.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({key: report[key] for key in ("stage", "program_cases", "program_passed",
                                                 "artifact_cases", "artifact_passed", "product_accepted", "passed")}))
    raise SystemExit(not report["passed"])


if __name__ == "__main__":
    main()

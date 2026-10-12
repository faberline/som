"""Admit seen-input weights only after normal artifacts pass scope and quality.

This tool does not train, create weights, switch current or close a milestone.
Final UTF-8 sizes are observations under the approved M2 budget policy.
"""

import argparse
import hashlib
import json
from pathlib import Path

from freeze_m2_candidate import sources
from probe_flow_maximum_matrix import maximum_matrix
from som_core import composed, flow, flow_codec, flow_compact, quality
from som_core.train_flow import (
    ADMISSION_VERSION,
    BLOCKS,
    BUDGET_POLICY,
    DEVELOPMENT,
    LIMITS,
    digest,
    verify_files,
)
from som_core.v1 import V1Error, load_blocks
from verify_python_quality import verify as verify_quality

ROOT = Path(__file__).resolve().parents[2]
FORMATTER = ROOT / "som-rs/models/composed-quality-candidate/ruff"


def cases() -> list[tuple[str, dict[str, object], str]]:
    """Keep all frozen cases, all maximum tuples and two legal alternatives."""
    result = []
    for split in ("formal", "dev"):
        path = ROOT / ("som-core/data/flow-v7-r10/" + split + ".jsonl")
        for line in path.read_text().splitlines():
            row = json.loads(line)
            result.append((row["id"], row["prompt"], "standard"))
    result.extend(
        (identity, prompt, "standard") for identity, prompt in maximum_matrix()
    )
    first = json.loads(DEVELOPMENT.read_text().splitlines()[0])["prompt"]
    result.extend(
        ("alternative-" + variant, first, variant)
        for variant in ("standard", "checked")
    )
    if len(result) != 218 or len({row[0] for row in result}) != 218:
        raise ValueError("M2 prerequisite case set differs")
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-seal", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    destination = args.output.resolve()
    if not destination.is_relative_to(ROOT):
        raise ValueError("M2 evidence must be inside this checkout")
    seal = json.loads(args.source_seal.read_text())
    if (
        seal.get("version") != "som.flow.compact-source-seal.v1"
        or seal.get("source_unchanged") is not True
        or seal.get("counts") != {"dev": 12, "formal": 114}
        or seal.get("actual_input_overlap") != 0
        or seal.get("structural_overlap") != 0
        or seal.get("limits") != LIMITS
    ):
        raise ValueError("M2 source and input seal is incomplete")
    verify_files(seal.get("source_sha256"), "source")
    expected_sources = {
        str(path.relative_to(ROOT)): digest(path) for path in sources()
    }
    if seal["source_sha256"] != expected_sources:
        raise ValueError(
            "source seal does not cover the full current candidate"
        )
    destination.mkdir(parents=True, exist_ok=False)
    library = load_blocks(BLOCKS.parent)
    catalog = flow_compact.metadata()
    records = []
    for identity, prompt, variant in cases():
        directory = destination / identity
        directory.mkdir()
        record = {"id": identity, "normal_accepted": False, "passed": False}
        try:
            plan = flow_compact.reference(prompt, library, variant)
            (directory / "prompt.json").write_text(
                composed.canonical(prompt) + "\n"
            )
            (directory / "dsl.json").write_text(
                composed.canonical(plan) + "\n"
            )
            features = flow_codec.features(prompt, catalog)
            encoded = flow_codec.encode(prompt, plan, catalog)
            if flow_codec.decode(prompt, encoded, catalog) != plan:
                raise ValueError("compact codec changes a reference choice")
            (directory / "model-input.txt").write_text(features)
            (directory / "reference-output.txt").write_text(encoded)
            files = flow_compact.assemble(prompt, plan, library, FORMATTER)
            product = directory / "product"
            product.mkdir()
            for path, source in files.items():
                target = product / path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(source)
            response = {"status": "ok", "files": files}
            (directory / "response.json").write_text(
                composed.canonical(response) + "\n"
            )
            count = sum(len(file["blocks"]) for file in plan["files"])
            size = sum(len(source.encode()) for source in files.values())
            bounded = (
                len(files) <= 12
                and count <= 64
                and max(features.count("<"), encoded.count("<")) <= 2048
            )
            if not bounded:
                raise ValueError(
                    "normal assembler returned an over-budget product"
                )
            record.update(
                normal_accepted=True,
                formatted_bytes=size,
                files=len(files),
                blocks=count,
                input_tokens=features.count("<"),
                output_tokens=encoded.count("<"),
                source_sha256={
                    path: hashlib.sha256(source.encode()).hexdigest()
                    for path, source in files.items()
                },
            )
        except V1Error as error:
            record.update(rejection=error.code, detail=str(error))
        except Exception as error:
            record.update(error=type(error).__name__, detail=str(error))
        records.append(record)
    # No quality phase or admission file follows a failed normal scope gate.
    if all(row["normal_accepted"] for row in records):
        for record in records:
            directory = destination / record["id"]
            check = verify_quality(
                directory / "product", directory / "quality"
            )
            record["quality_report"] = str(
                (directory / "quality/result.json").relative_to(ROOT)
            )
            record["format_idempotent"] = all(
                not path.endswith(".py")
                or quality.format_source(
                    path,
                    (directory / "product" / path).read_text(),
                    FORMATTER,
                    package=json.loads(
                        (directory / "prompt.json").read_text()
                    )["package"],
                )
                == (directory / "product" / path).read_text()
                for path in record["source_sha256"]
            )
            record["passed"] = check["passed"] and record["format_idempotent"]
            print(json.dumps({"id": record["id"], "formatted_bytes": record["formatted_bytes"],
                              "quality_passed": record["passed"]}), flush=True)
    unchanged = expected_sources == {
        str(path.relative_to(ROOT)): digest(path) for path in sources()
    }
    report = {
        "stage": "m2_size_quality_training_prerequisite",
        "required": 218,
        "accepted": sum(row["normal_accepted"] for row in records),
        "formal_cases": 114,
        "development_cases": 12,
        "maximum_cases": 90,
        "alternative_cases": 2,
        "records": records,
        "source_sha256": expected_sources,
        "source_unchanged": unchanged,
        "limits": LIMITS,
        "output_budget_sha256": digest(BUDGET_POLICY),
        "passed": unchanged and all(row["passed"] for row in records),
        "weights_created": False,
        "formal_training_examples": 0,
        "behavior_acceptance": False,
        "m2_passed": False,
    }
    report_path = destination / "result.json"
    report_path.write_text(json.dumps(report, indent=2) + "\n")
    if report["passed"]:
        evidence = [report_path]
        for record in records:
            directory = destination / record["id"]
            evidence.extend(
                [directory / "response.json", ROOT / record["quality_report"]]
            )
        admission = {
            "version": ADMISSION_VERSION,
            "size_quality_passed": True,
            "formal_cases": 114,
            "development_cases": 12,
            "limits": LIMITS,
            "development_sha256": digest(DEVELOPMENT),
            "blocks_sha256": digest(BLOCKS),
            "catalog_sha256": digest(flow_compact.CATALOG),
            "contract_sha256": flow.contract_hash(),
            "codec_version": flow_codec.COMPACT_VERSION,
            "source_seal_sha256": digest(args.source_seal),
            "output_budget_sha256": digest(BUDGET_POLICY),
            "size_quality_report": str(report_path.relative_to(ROOT)),
            "evidence_sha256": {
                str(path.relative_to(ROOT)): digest(path) for path in evidence
            },
        }
        (destination / "admission.json").write_text(
            json.dumps(admission, indent=2) + "\n"
        )
    print(
        json.dumps(
            {
                key: report[key]
                for key in (
                    "required",
                    "accepted",
                    "passed",
                    "weights_created",
                )
            }
        ),
        flush=True,
    )
    raise SystemExit(not report["passed"])


if __name__ == "__main__":
    main()

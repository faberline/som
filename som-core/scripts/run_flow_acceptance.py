"""One entry point for compact artifacts, quality and independent HTTP checks.

No partial set, diagnostic artifact or unfinished fault matrix can pass M2.
Reference plans test product behavior, not model accuracy or generalization.
"""

import argparse
import hashlib
import json
from pathlib import Path
import time
import traceback

from som_core import flow_compact, quality
from som_core.v1 import load_blocks

from audit_business_transactions import source_hashes
from composed_evidence import failure_details
from verify_flow_service import verify
from verify_python_quality import verify as verify_quality

ROOT = Path(__file__).resolve().parents[2]
CORPUS = ROOT / "som-core/data/flow-v7-r10/formal.jsonl"


def source_receipt():
    paths = list((ROOT / "som-core/scripts").glob("*flow*.py"))
    paths += list((ROOT / "som-core/block_templates/flow_compact").glob("*.tpl"))
    paths += [ROOT / "som-core/scripts/verify_python_quality.py", ROOT / "som-core/scripts/quality_policy.py",
              ROOT / "som-core/scripts/business_database_checks.py", ROOT / "som-core/scripts/business_test_support.py",
              ROOT / "som-core/scripts/fixture_guard.py", ROOT / "som-core/scripts/verify_business_workflows.py",
              ROOT / "som-core/scripts/local_business_services.py", ROOT / "som-core/scripts/verify_composed_services.py",
              ROOT / "som-core/scripts/verify_composed_related.py", ROOT / "som-core/scripts/audit_business_transactions.py",
              ROOT / "som-core/scripts/verify_composed_journeys.py", ROOT / "som-core/scripts/composed_evidence.py"]
    paths += [ROOT / ("som-core/src/som_core/" + name + ".py")
              for name in ("flow", "flow_compact", "flow_codec", "composed", "v1", "quality", "compact_names")]
    paths += [flow_compact.CATALOG, CORPUS,
              ROOT / "som-code-python/data/blocks/flow-v7-compact/blocks.json",
              ROOT / "som-rs/models/composed-quality-candidate/ruff"]
    return {str(path.relative_to(ROOT)): quality.sha256(path) for path in paths}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--fixture", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--ids", help="diagnostic subset; cannot pass the full milestone")
    args = parser.parse_args()
    args.output = args.output.resolve()
    args.output.mkdir(parents=True, exist_ok=False)
    requested = args.ids.split(",") if args.ids else None
    rows = [json.loads(line) for line in CORPUS.read_text().splitlines()]
    selected = [row for row in rows if requested is None or row["id"] in requested]
    if requested and set(requested) != {row["id"] for row in selected}:
        raise ValueError("unknown case id")
    before = source_receipt()
    fixture = json.loads(args.fixture.read_text())
    (args.output / "fixture.json").write_text(json.dumps(fixture, indent=2) + "\n")
    blocks = load_blocks(ROOT / "som-code-python/data/blocks/flow-v7-compact")
    records = []
    for row in selected:
        started = time.monotonic()
        case = args.output / row["id"]
        case.mkdir()
        prompt = row["prompt"]
        (case / "requirement.json").write_text(json.dumps(row, indent=2) + "\n")
        record = {"id": row["id"], "plan_origin": "reference; not model inference"}
        try:
            plan = flow_compact.reference(prompt, blocks)
            (case / "dsl.json").write_text(json.dumps(plan, indent=2) + "\n")
            files = flow_compact.assemble(prompt, plan, blocks,
                                          ROOT / "som-rs/models/composed-quality-candidate/ruff")
            (case / "response.json").write_text(json.dumps({"status": "ok", "files": files}) + "\n")
            product = case / "product"
            product.mkdir()
            for path, source in files.items():
                destination = product / path
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_text(source)
            hashes = source_hashes(product)
            record["formatted_bytes"] = sum(len(value.encode()) for value in files.values())
            record["source_sha256"] = hashes
            record["quality"] = verify_quality(product, case / "quality")
            if not record["quality"]["passed"]:
                raise ValueError("product quality failed")
            record["behavior"] = verify(prompt, product, fixture)
            record["source_unchanged"] = source_hashes(product) == hashes
            record["core_passed"] = record["source_unchanged"]
            record["full_passed"] = record["core_passed"] and record["behavior"]["full_m2_acceptance"]
        except Exception as error:
            record.update(core_passed=False, full_passed=False, error=traceback.format_exc(),
                          failure=failure_details(error), rejection=getattr(error, "code", None))
        record["seconds"] = time.monotonic() - started
        (case / "result.json").write_text(json.dumps(record, indent=2, default=str) + "\n")
        records.append(record)
        report = {"stage": "reference_product_acceptance", "source_sha256": before,
                  "source_unchanged": source_receipt() == before, "required": len(rows),
                  "attempted": len(records), "core_passed": sum(row["core_passed"] for row in records),
                  "full_passed": sum(row["full_passed"] for row in records), "records": records}
        report["passed"] = (report["source_unchanged"] and len(records) == len(rows)
                            and all(row["full_passed"] for row in records))
        (args.output / "result.json").write_text(json.dumps(report, indent=2, default=str) + "\n")
        print(json.dumps({key: record.get(key) for key in
                          ("id", "core_passed", "full_passed", "seconds", "rejection", "failure")}), flush=True)
    raise SystemExit(not report["passed"])


if __name__ == "__main__":
    main()

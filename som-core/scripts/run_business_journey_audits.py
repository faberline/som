"""Audit completed saved products; never edit the frozen agent protocol."""

import argparse
import fcntl
import hashlib
import json
import time
import traceback
from pathlib import Path

from audit_business_journeys import verify_journeys
from audit_business_transactions import source_hashes

ROOT = Path(__file__).resolve().parents[2]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--kit", type=Path, required=True)
    parser.add_argument("--fixture", type=Path, required=True)
    parser.add_argument("--transactions", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--watch", action="store_true")
    args = parser.parse_args()
    scripts = [Path(__file__), *(Path(__file__).with_name(name) for name in (
        "audit_business_journeys.py", "audit_business_transactions.py", "business_database_checks.py", "business_test_support.py", "verify_related.py"))]

    def hashes():
        return {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest() for path in scripts}

    frozen = hashes()
    jobs = {job["id"]: job for job in json.loads((args.kit / "jobs.json").read_text())["jobs"]}
    prompts = [json.loads(line)["prompt"] for line in (ROOT / "som-core/data/business-service/requirements.jsonl").read_text().splitlines()]
    if args.output.exists():
        report = json.loads(args.output.read_text())
        assert report["verifier_sha256"] == frozen, "keep prior evidence and choose a new receipt after audit changes"
    else:
        report = {"verifier_sha256": frozen, "kit_seal_sha256": hashlib.sha256((args.kit / "seal.json").read_bytes()).hexdigest(),
                  "expected": 148, "results": [], "supplemental_only": True, "agent_protocol_changed": False}
    args.output.parent.mkdir(parents=True, exist_ok=True)

    def save():
        report["completed"] = len(report["results"])
        report["passed"] = sum(row["status"] == "passed" for row in report["results"])
        report["gate_passed"] = report["completed"] == report["passed"] == report["expected"]
        temporary = args.output.with_suffix(".tmp")
        temporary.write_text(json.dumps(report, indent=2) + "\n")
        temporary.replace(args.output)

    while True:
        targets = json.loads(args.transactions.read_text())["results"]
        done = {row["id"] for row in report["results"]}
        for target in targets:
            if target["id"] in done:
                continue
            assert hashes() == frozen, "audit changed during the run"
            prompt = jobs[target["id"]]["prompt"] if target["kind"] == "paired" else prompts[int(target["id"].split("-")[-1])]
            root = Path(target["directory"])
            result = {key: target[key] for key in ("id", "kind", "directory")}
            result["source_sha256"] = source_hashes(root)
            started = time.monotonic()
            try:
                with Path("/private/tmp/som-business-eval-fixture.lock").open("a") as lock:
                    fcntl.flock(lock, fcntl.LOCK_EX)
                    result.update(verify_journeys(prompt, root, json.loads(args.fixture.read_text())))
            except Exception:  # noqa: BLE001 -- keep failed products in the matrix
                result.update(status="failed", error=traceback.format_exc())
            result["seconds"] = round(time.monotonic() - started, 3)
            report["results"].append(result)
            save()
            print(json.dumps({key: result[key] for key in ("id", "status", "seconds")} | {"completed": report["completed"]}), flush=True)
        save()
        if not args.watch or report["completed"] == report["expected"]:
            break
        time.sleep(30)
    if any(row["status"] != "passed" for row in report["results"]):
        raise SystemExit(1)


if __name__ == "__main__":
    main()

"""Audit saved reference, Rust, and completed paired products without edits."""

import argparse
import fcntl
import hashlib
import json
import time
import traceback
from pathlib import Path

from audit_business_transactions import source_hashes, verify_transactions

ROOT = Path(__file__).resolve().parents[2]


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def finished(root):
    attempts = [json.loads(p.read_text()) for p in sorted((root / "run-logs").glob("*/metadata.json"))]
    checks = sorted((root / "check-results").glob("*.json"))
    if not attempts:
        return False
    last = attempts[-1]
    passed = bool(checks and json.loads(checks[-1].read_text()).get("status") == "passed")
    return ((last["exit_code"] == 0 and last["usage"] is not None and not last["usage"].get("is_error") and passed)
            or len(attempts) >= 3 or sum(a["elapsed_seconds"] for a in attempts) >= 1800)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--kit", type=Path, required=True)
    parser.add_argument("--fixture", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--include-reference", action="store_true")
    parser.add_argument("--watch", action="store_true")
    args = parser.parse_args()
    scripts = [Path(__file__), *(Path(__file__).with_name(name) for name in (
        "audit_business_transactions.py", "business_database_checks.py", "business_test_support.py", "verify_related.py"))]
    hashes = {str(path.relative_to(ROOT)): digest(path) for path in scripts}
    jobs = json.loads((args.kit / "jobs.json").read_text())["jobs"]
    targets = [{"id": job["id"], "kind": "paired", "prompt": job["prompt"], "directory": job["directory"]} for job in jobs]
    if args.include_reference:
        prompts = [json.loads(line)["prompt"] for line in (ROOT / "som-core/data/business-service/requirements.jsonl").read_text().splitlines()]
        for kind, filename, key in (("reference", "business-reference-r2.json", "results"), ("rust", "candidate-r2-apps.json", "apps")):
            rows = json.loads((ROOT / "som-rs/evals/related-service" / filename).read_text())[key]
            targets[0:0] = [{"id": f"{kind}-{row['index']:02d}", "kind": kind, "prompt": prompts[row["index"]], "directory": row["directory"]} for row in rows]
    if args.output.exists():
        report = json.loads(args.output.read_text())
        assert report["verifier_sha256"] == hashes, "audit changed; keep old evidence and choose a new output path"
    else:
        report = {"verifier_sha256": hashes, "kit_seal_sha256": digest(args.kit / "seal.json"), "results": [],
                  "expected": len(targets), "supplemental_only": True, "agent_protocol_changed": False}
    args.output.parent.mkdir(parents=True, exist_ok=True)

    def save():
        report["completed"] = len(report["results"])
        report["passed"] = sum(row["status"] == "passed" for row in report["results"])
        report["gate_passed"] = report["completed"] == report["expected"] == report["passed"]
        temporary = args.output.with_suffix(".tmp")
        temporary.write_text(json.dumps(report, indent=2) + "\n")
        temporary.replace(args.output)

    while True:
        done = {row["id"] for row in report["results"]}
        for target in targets:
            root = Path(target["directory"])
            if target["id"] in done or target["kind"] == "paired" and not finished(root):
                continue
            assert hashes == {str(path.relative_to(ROOT)): digest(path) for path in scripts}, "audit source changed"
            started = time.monotonic()
            result = {key: target[key] for key in ("id", "kind", "directory")}
            result["source_sha256"] = source_hashes(root)
            try:
                with Path("/private/tmp/som-business-eval-fixture.lock").open("a") as lock:
                    fcntl.flock(lock, fcntl.LOCK_EX)
                    result.update(verify_transactions(target["prompt"], root, json.loads(args.fixture.read_text())))
            except Exception:  # noqa: BLE001 -- preserve every failing audit
                result.update(status="failed", error=traceback.format_exc())
            result["seconds"] = round(time.monotonic() - started, 3)
            report["results"].append(result)
            save()
            print(json.dumps({key: result[key] for key in ("id", "status", "seconds")} | {"completed": report["completed"]}), flush=True)
        save()
        if not args.watch or report["completed"] == len(targets):
            break
        time.sleep(30)
    if any(row["status"] != "passed" for row in report["results"]):
        raise SystemExit(1)


if __name__ == "__main__":
    main()

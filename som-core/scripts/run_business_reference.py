"""Install reference outputs into fresh directories and run independent checks."""

import argparse
import fcntl
import hashlib
import json
import tempfile
import time
import traceback
from pathlib import Path

from verify_related import verify


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--fixture", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--indices", default=",".join(map(str, range(14))))
    args = parser.parse_args()
    repo = Path(__file__).resolve().parents[2]
    data = repo / "som-core/data/business-service"
    requirements = [json.loads(line)["prompt"] for line in (data / "requirements.jsonl").read_text().splitlines()]
    outputs = [json.loads(line) for line in (data / "reference.jsonl").read_text().splitlines()]
    fixture = json.loads(args.fixture.read_text())
    verifier_files = [Path(__file__).with_name(name) for name in ("verify_related.py", "business_database_checks.py", "business_test_support.py", "verify_business_workflows.py")]
    verifier_hashes = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in verifier_files}
    report = {"kind": "reference-development", "started_at": time.time(), "fixture": str(args.fixture), "verifier_sha256": verifier_hashes, "results": []}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    for index in map(int, args.indices.split(",")):
        prompt = requirements[index]
        root = Path(tempfile.mkdtemp(prefix="som-business-" + prompt["package"] + "-", dir="/private/tmp"))
        for name, source in outputs[index].items():
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(source)
        (root / "requirement.json").write_text(json.dumps(prompt, indent=2) + "\n")
        started = time.monotonic()
        result = {"index": index, "package": prompt["package"], "directory": str(root), "source_sha256": {name: hashlib.sha256(source.encode()).hexdigest() for name, source in outputs[index].items()}}
        try:
            with Path("/private/tmp/som-business-eval-fixture.lock").open("a") as lock:
                fcntl.flock(lock, fcntl.LOCK_EX)
                result.update(verify(prompt, root, fixture))
        except Exception:
            result.update(status="failed", error=traceback.format_exc())
        result["seconds"] = round(time.monotonic() - started, 3)
        if verifier_hashes != {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in verifier_files}:
            result.update(status="invalidated", error="verifier changed during the run")
        report["results"].append(result)
        args.output.write_text(json.dumps(report, indent=2) + "\n")
        print(json.dumps(result), flush=True)
    if any(row["status"] != "passed" for row in report["results"]):
        raise SystemExit(1)


if __name__ == "__main__":
    main()

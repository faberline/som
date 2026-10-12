"""Run complete independent gates in one fixture, with immutable saved responses."""

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[2]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--index", type=int, required=True)
    parser.add_argument("--parts", type=int, default=4)
    parser.add_argument("--fixture", type=Path, required=True)
    parser.add_argument("--runtime", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    assert 0 <= args.index < args.parts
    args.output.mkdir(parents=True, exist_ok=False)
    assets = [ROOT / "som-code-python/data/blocks/composed-quality/blocks.json",
              ROOT / "som-code-python/data/block-contracts/composed-quality.json",
              ROOT / "som-rs/models/composed-quality-candidate/manifest.json"]
    assets += list((ROOT / "som-rs/contracts/python-quality-v1").glob("*"))
    assets += list((ROOT / "som-core/scripts").glob("*.py"))
    hashes = {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in assets}
    record = {"index": args.index, "asset_hashes": hashes, "results": [], "passed": False}
    for data, group in (("regression", "services14"), ("heldout", "compositions120"), ("blind", "blind80")):
        started = time.monotonic()
        rows = (ROOT / f"som-core/data/composed/{data}.jsonl").read_text().splitlines()
        indices = list(range(args.index, len(rows), args.parts))
        command = [sys.executable, str(ROOT / "som-core/scripts/verify_quality_services.py"),
                   "--data", str(ROOT / f"som-core/data/composed/{data}.jsonl"),
                   "--fixture", str(args.fixture.resolve()),
                   "--responses", str((args.runtime / "responses" / group).resolve()),
                   "--indices", ",".join(map(str, indices)),
                   "--output", str((args.output / data).resolve())]
        with (args.output / (data + ".log")).open("w") as log:
            process = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, check=False)
        record["results"].append({"data": data, "count": len(indices), "exit_code": process.returncode,
                                  "seconds": time.monotonic()-started})
        (args.output / "result.json").write_text(json.dumps(record, indent=2) + "\n")
        print(json.dumps(record["results"][-1]), flush=True)
    record["assets_unchanged"] = hashes == {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in assets}
    record["passed"] = record["assets_unchanged"] and all(row["exit_code"] == 0 for row in record["results"])
    (args.output / "result.json").write_text(json.dumps(record, indent=2) + "\n")
    raise SystemExit(not record["passed"])


if __name__ == "__main__":
    main()

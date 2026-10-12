"""One quality-and-behavior gate for complete generated services."""

import argparse
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import time
import traceback

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "som-core/src"))
from som_core import quality
from som_core.v1 import load_blocks
from verify_composed_services import verify_all
from verify_python_quality import verify as check_quality


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", type=Path, default=ROOT / "som-core/data/composed/regression.jsonl")
    parser.add_argument("--fixture", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--indices")
    parser.add_argument("--url")
    parser.add_argument("--responses", type=Path,
                        help="sealed responses from the Rust runtime gate")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    rows = [json.loads(line) for line in args.data.read_text().splitlines()]
    fixture = json.loads(args.fixture.read_text())
    library = load_blocks(ROOT / "som-code-python/data/blocks/composed-quality")
    indices = list(map(int, args.indices.split(","))) if args.indices else list(range(len(rows)))
    record = {"data_sha256": hashlib.sha256(args.data.read_bytes()).hexdigest(),
              "blocks_sha256": quality.sha256(ROOT / "som-code-python/data/blocks/composed-quality/blocks.json"),
              "catalog_sha256": quality.sha256(quality.CATALOG),
              "profile_sha256": quality.sha256(quality.PROFILE),
              "kind": "rust" if args.url or args.responses else "reference", "results": []}
    for index in indices:
        row = rows[index]
        started = time.monotonic()
        root = Path(tempfile.mkdtemp(prefix="som-quality-service-", dir="/private/tmp"))
        result = {"index": index, "package": row["prompt"]["package"], "directory": str(root)}
        try:
            if args.responses:
                response_path = args.responses / f"{index:04d}.json"
                response_bytes = response_path.read_bytes()
                (args.output / f"{index}-response.json").write_bytes(response_bytes)
                result["response_sha256"] = hashlib.sha256(response_bytes).hexdigest()
                body = json.loads(response_bytes)
                assert body["status"] == "ok", body
                files = body["files"]
            elif args.url:
                import httpx
                response = httpx.post(args.url + "/v1/generate", json=row["prompt"], timeout=65)
                (args.output / f"{index}-response.json").write_bytes(response.content)
                response.raise_for_status()
                files = response.json()["files"]
            else:
                files = quality.reference(row["prompt"], row["dsl"], library,
                                          Path(sys.executable).parent / "ruff")
                (args.output / f"{index}-response.json").write_text(json.dumps({"status": "ok", "files": files}) + "\n")
            for name, source in files.items():
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(source)
            result["quality"] = check_quality(root, args.output / str(index))
            if not result["quality"]["passed"]:
                raise ValueError("product quality checks failed")
            result.update(verify_all(row["prompt"], root, fixture,
                                     standard=args.data.name == "regression.jsonl"))
        except Exception as error:
            from composed_evidence import failure_details
            result.update(status="failed", error=traceback.format_exc(), failure=failure_details(error))
        result["seconds"] = time.monotonic() - started
        record["results"].append(result)
        record["passed"] = len(record["results"]) == len(indices) and all(r["status"] == "passed" for r in record["results"])
        (args.output / "results.json").write_text(json.dumps(record, indent=2) + "\n")
        print(json.dumps({k: result.get(k) for k in ("index", "status", "seconds", "failure")}), flush=True)
    raise SystemExit(not record["passed"])


if __name__ == "__main__":
    main()

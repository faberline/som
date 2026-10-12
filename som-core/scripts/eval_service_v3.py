"""Run all frozen standalone services through Rust and independent PostgreSQL checks."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import tempfile
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PYTHON = ROOT / "som-code-python/.venv/bin/python"
VERIFY = ROOT / "som-core/scripts/verify_service_v3.py"


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def get_json(url: str, body: bytes | None = None) -> dict:
    request = urllib.request.Request(
        url, data=body, headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(request, timeout=65) as response:
        return json.load(response)


def check(index: int, path: Path, cases: Path) -> dict:
    started = time.monotonic()
    result = subprocess.run(
        [
            str(PYTHON),
            str(VERIFY),
            "--cases",
            str(cases),
            "--heldout-index",
            str(index),
            "--files-json",
            str(path),
        ],
        capture_output=True,
        text=True,
        env=os.environ | {"PYTHONPATH": str(ROOT / "som-core/src")},
        timeout=120,
    )
    return {
        "index": index,
        "passed": result.returncode == 0,
        "seconds": round(time.monotonic() - started, 3),
        "detail": (result.stdout + result.stderr)[-2000:] if result.returncode else "",
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8766")
    parser.add_argument(
        "--cases", type=Path, default=ROOT / "som-core/data/service-v3/heldout.jsonl"
    )
    parser.add_argument(
        "--model", type=Path, default=ROOT / "som-rs/models/crud-query-service-v3"
    )
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not 1 <= args.workers <= 8:
        parser.error("--workers must be 1..8")
    cases = args.cases.resolve()
    health = get_json(args.url.rstrip("/") + "/healthz")
    if health["model_manifest_sha256"] != digest(args.model / "manifest.json"):
        raise ValueError("running model differs from requested model")
    rows = [json.loads(line) for line in cases.read_text().splitlines()]
    generation_failures = []
    with tempfile.TemporaryDirectory(prefix="som-v3-eval-") as temporary:
        folder = Path(temporary)
        generated = []
        for index, row in enumerate(rows):
            try:
                payload = get_json(
                    args.url.rstrip("/") + "/v1/generate",
                    json.dumps(row["prompt"]).encode(),
                )
                if payload.get("status") != "ok":
                    raise ValueError(payload)
                path = folder / f"case-{index:02d}.json"
                path.write_text(json.dumps(payload))
                generated.append((index, path))
            except Exception as error:
                generation_failures.append({"index": index, "detail": str(error)})
        results = []
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            pending = {
                pool.submit(check, index, path, cases): index
                for index, path in generated
            }
            for future in as_completed(pending):
                try:
                    results.append(future.result())
                except Exception as error:
                    results.append(
                        {
                            "index": pending[future],
                            "passed": False,
                            "seconds": None,
                            "detail": str(error),
                        }
                    )
    results.sort(key=lambda result: result["index"])
    passed = sum(item["passed"] for item in results)
    receipt = {
        "model_manifest_sha256": health["model_manifest_sha256"],
        "blocks_sha256": health["blocks_sha256"],
        "cases_sha256": digest(cases),
        "cases": len(rows),
        "generated": len(generated),
        "passed": passed,
        "pass_fraction": round(passed / len(rows), 4),
        "generation_failures": generation_failures,
        "results": results,
        "gate_passed": not generation_failures and passed >= 0.9 * len(rows),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(receipt, indent=2) + "\n")
    print(
        json.dumps(
            {
                "cases": len(rows),
                "generated": len(generated),
                "passed": passed,
                "gate_passed": receipt["gate_passed"],
            }
        )
    )
    if not receipt["gate_passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()

"""Evaluate Rust service responses on the frozen PostgreSQL hold-out set."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8765")
    parser.add_argument(
        "--cases", type=Path, default=ROOT / "som-core/data/v1/heldout.jsonl"
    )
    parser.add_argument(
        "--python",
        type=Path,
        default=ROOT / "som-code-python" / ".venv" / "bin" / "python",
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--model-manifest", type=Path)
    args = parser.parse_args()
    health_url = args.url.rstrip("/") + "/healthz"
    with urllib.request.urlopen(health_url, timeout=10) as response:
        health = json.load(response)
    if health.get("status") != "ok" or not health.get("model_loaded"):
        raise RuntimeError("SOM service is not healthy")
    if args.model_manifest:
        expected_hash = hashlib.sha256(args.model_manifest.read_bytes()).hexdigest()
        if health.get("model_manifest_sha256") != expected_hash:
            raise RuntimeError("service model does not match --model-manifest")
    rows = [json.loads(line) for line in args.cases.read_text().splitlines()]
    results = []
    with tempfile.TemporaryDirectory(prefix="som-eval-") as temporary:
        for index, row in enumerate(rows):
            started = time.monotonic()
            request = urllib.request.Request(
                args.url.rstrip("/") + "/v1/generate",
                data=json.dumps(row["prompt"]).encode(),
                headers={"Content-Type": "application/json"},
            )
            try:
                with urllib.request.urlopen(request, timeout=65) as response:
                    payload = json.load(response)
            except urllib.error.HTTPError as error:
                payload = json.load(error)
            except (OSError, ValueError) as error:
                payload = {
                    "status": "error",
                    "code": "TRANSPORT_ERROR",
                    "detail": str(error),
                }
            seconds = time.monotonic() - started
            result = {
                "index": index,
                "seconds": round(seconds, 3),
                "status": "failed",
                "code": payload.get("code", "INVALID_RESPONSE"),
            }
            if payload.get("status") == "ok" and isinstance(payload.get("files"), dict):
                path = Path(temporary) / f"files-{index}.json"
                path.write_text(json.dumps(payload["files"]))
                check = subprocess.run(
                    [
                        str(args.python),
                        str(ROOT / "som-core" / "scripts" / "verify_v1_postgres.py"),
                        "--heldout-index",
                        str(index),
                        "--cases",
                        str(args.cases),
                        "--files-json",
                        str(path),
                    ],
                    capture_output=True,
                    text=True,
                    check=False,
                    env=os.environ | {"PYTHONPATH": str(ROOT / "som-core" / "src")},
                )
                result["code"] = "PASS" if check.returncode == 0 else "POSTGRES_FAILED"
                if check.returncode:
                    result["detail"] = (check.stdout + check.stderr)[-2000:]
                result["status"] = "passed" if check.returncode == 0 else "failed"
            results.append(result)
            if (index + 1) % 10 == 0:
                passed_so_far = sum(r["status"] == "passed" for r in results)
                print(
                    f"checked {index + 1}/{len(rows)}; passed {passed_so_far}",
                    flush=True,
                )
    passed = sum(row["status"] == "passed" for row in results)
    summary = {
        "model_manifest_sha256": health.get("model_manifest_sha256"),
        "blocks_sha256": health.get("blocks_sha256"),
        "heldout_sha256": hashlib.sha256(args.cases.read_bytes()).hexdigest(),
        "cases": len(rows),
        "passed": passed,
        "pass_rate": passed / len(rows),
        "under_60_seconds": all(row["seconds"] <= 60 for row in results),
        "results": results,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(summary, indent=2) + "\n")
    print(
        json.dumps({key: value for key, value in summary.items() if key != "results"})
    )
    if passed / len(rows) < 0.9 or not summary["under_60_seconds"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()

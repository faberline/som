"""Check that the Rust model and assembler match all frozen Python outputs."""

from __future__ import annotations

import argparse
import hashlib
import json
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8765")
    parser.add_argument(
        "--cases", type=Path, default=ROOT / "som-core/data/crud-query-v2/heldout.jsonl"
    )
    parser.add_argument(
        "--expected",
        type=Path,
        default=ROOT / "som-core/data/crud-query-v2/heldout.expected.jsonl",
    )
    parser.add_argument(
        "--model", type=Path, default=ROOT / "som-rs/models/crud-query-v2"
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    base_url = args.url.rstrip("/")
    with urllib.request.urlopen(base_url + "/healthz", timeout=10) as response:
        health = json.load(response)
    if health.get("model_manifest_sha256") != digest(args.model / "manifest.json"):
        raise ValueError("running service model differs from --model")
    cases = [json.loads(line) for line in args.cases.read_text().splitlines()]
    expected = [json.loads(line) for line in args.expected.read_text().splitlines()]
    if len(cases) != len(expected) or not cases:
        raise ValueError("case and expected files differ in length")
    failures = []
    for index, (row, files) in enumerate(zip(cases, expected, strict=True)):
        request = urllib.request.Request(
            base_url + "/v1/generate",
            data=json.dumps(row["prompt"]).encode(),
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=65) as response:
                payload = json.load(response)
        except urllib.error.HTTPError as error:
            payload = json.load(error)
        if payload.get("status") != "ok" or payload.get("files") != files:
            failures.append({"index": index, "code": payload.get("code", "MISMATCH")})
    receipt = {
        "model_manifest_sha256": health["model_manifest_sha256"],
        "blocks_sha256": health["blocks_sha256"],
        "cases_sha256": digest(args.cases),
        "expected_sha256": digest(args.expected),
        "cases": len(cases),
        "exact_matches": len(cases) - len(failures),
        "failures": failures,
        "passed": not failures,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(receipt, indent=2) + "\n")
    print(json.dumps(receipt))
    if failures:
        raise SystemExit(1)


if __name__ == "__main__":
    main()

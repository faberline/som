"""Check current SOM HTTP prompt rejection and response envelopes."""

from __future__ import annotations

import argparse
import hashlib
import json
import urllib.error
import urllib.request
from pathlib import Path

from som_core.v1 import V1Error, validate_prompt

ROOT = Path(__file__).resolve().parents[2]
REJECTIONS = (
    ROOT / "som-core/data/service-v3/rejections.jsonl",
    ROOT / "som-core/data/date-service-v4/rejections.jsonl",
)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def post(base: str, data: bytes) -> tuple[int, dict]:
    request = urllib.request.Request(
        base + "/v1/generate",
        data=data,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, json.load(response)
    except urllib.error.HTTPError as error:
        return error.code, json.load(error)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8765")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    base = args.url.rstrip("/")
    with urllib.request.urlopen(base + "/healthz", timeout=10) as response:
        health = json.load(response)
    if health["status"] != "ok" or health["block_count"] != 34:
        raise ValueError("current model and block library are not loaded")

    checked = []
    for source in REJECTIONS:
        for index, line in enumerate(source.read_text().splitlines()):
            row = json.loads(line)
            try:
                validate_prompt(row["prompt"])
            except V1Error as error:
                assert error.code == row["code"]
            else:
                continue  # DSL rejection is tested by the assembler, not this endpoint.
            status, body = post(base, json.dumps(row["prompt"]).encode())
            if (
                status < 400
                or body.get("status") != "error"
                or body.get("code") != row["code"]
            ):
                raise AssertionError(f"{source.name}:{index}: {status} {body}")
            checked.append(
                {"source": source.parent.name, "index": index, "code": row["code"]}
            )

    status, body = post(base, b"{")
    if (
        status != 400
        or body.get("status") != "error"
        or body.get("code") != "INVALID_JSON"
    ):
        raise AssertionError(f"malformed JSON: {status} {body}")
    assert len(checked) == 10
    receipt = {
        "model_manifest_sha256": health["model_manifest_sha256"],
        "blocks_sha256": health["blocks_sha256"],
        "rejection_sources_sha256": {
            str(path.relative_to(ROOT)): sha256(path) for path in REJECTIONS
        },
        "prompt_rejections": len(checked),
        "malformed_json_rejections": 1,
        "passed": 11,
        "expected": 11,
        "checks": checked,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(receipt, indent=2) + "\n")
    print(json.dumps({"passed": receipt["passed"], "expected": receipt["expected"]}))


if __name__ == "__main__":
    main()

"""Measure a loaded SOM service and compare optional reference values.

This measures one request at a time. It excludes service startup and flagship
agent work. Correctness remains the job of the independent held-out evaluation.
"""

from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import math
import struct
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[2]
MB = 1_000_000


def get_json(url: str, data: bytes | None = None) -> dict:
    request = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"} if data is not None else {},
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        return json.load(response)


def listener_pid(port: int) -> int:
    result = subprocess.run(
        ["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-t"],
        capture_output=True,
        text=True,
        check=True,
    )
    pids = {int(line) for line in result.stdout.splitlines() if line.strip()}
    if len(pids) != 1:
        raise ValueError(f"expected one listener on port {port}, found {sorted(pids)}")
    return pids.pop()


def rss_bytes(pid: int) -> int:
    # macOS proc_taskinfo reports resident bytes without a shell process.
    libproc = ctypes.CDLL("/usr/lib/libproc.dylib", use_errno=True)
    libproc.proc_pidinfo.argtypes = [
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_uint64,
        ctypes.c_void_p,
        ctypes.c_int,
    ]
    libproc.proc_pidinfo.restype = ctypes.c_int
    task_info = ctypes.create_string_buffer(96)
    size = libproc.proc_pidinfo(pid, 4, 0, task_info, len(task_info))
    if size != len(task_info):
        raise OSError(ctypes.get_errno(), f"cannot read resident memory for pid {pid}")
    return struct.unpack_from("=QQ", task_info.raw)[1]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8765")
    parser.add_argument(
        "--model", type=Path, default=ROOT / "som-rs/models/crud-query-v2"
    )
    parser.add_argument(
        "--cases", type=Path, default=ROOT / "som-core/data/crud-query-v2/heldout.jsonl"
    )
    parser.add_argument("--max-model-mb", type=float, default=50)
    parser.add_argument("--max-rss-mb", type=float, default=1000)
    parser.add_argument("--max-p95-ms", type=float, default=100)
    parser.add_argument("--enforce-limits", action="store_true", help="Opt in to enforcing the supplied reference values; default is measurement only")
    parser.add_argument("--warmup", type=int, default=5)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if min(args.max_model_mb, args.max_rss_mb, args.max_p95_ms) <= 0:
        parser.error("all budgets must be positive")
    if args.warmup < 0:
        parser.error("warmup must be nonnegative")
    parsed_url = urlparse(args.url)
    if parsed_url.scheme != "http" or parsed_url.hostname != "127.0.0.1":
        parser.error("--url must use http://127.0.0.1")
    if not parsed_url.port:
        parser.error("--url must include the service port")
    base_url = args.url.rstrip("/")
    manifest = args.model / "manifest.json"
    model_files = sorted(path for path in args.model.rglob("*") if path.is_file())
    if not model_files or not manifest.is_file():
        parser.error("--model must contain a manifest and model files")
    model_bytes = sum(path.stat().st_size for path in model_files)
    manifest_sha = hashlib.sha256(manifest.read_bytes()).hexdigest()
    health = get_json(base_url + "/healthz")
    if (
        health.get("status") != "ok"
        or health.get("model_loaded") is not True
        or health.get("blocks_loaded") is not True
        or health.get("model_manifest_sha256") != manifest_sha
    ):
        raise ValueError("running service does not match the loaded model")
    rows = [json.loads(line) for line in args.cases.read_text().splitlines()]
    if not rows:
        parser.error("--cases must contain at least one prompt")
    payloads = [json.dumps(row["prompt"]).encode() for row in rows]
    pid = listener_pid(parsed_url.port)
    peak_rss = rss_bytes(pid)
    generate_url = base_url + "/v1/generate"
    for index in range(args.warmup):
        response = get_json(generate_url, payloads[index % len(payloads)])
        if response.get("status") != "ok" or not isinstance(
            response.get("files"), dict
        ):
            raise ValueError(f"warmup {index}: generation failed: {response}")
        peak_rss = max(peak_rss, rss_bytes(pid))
    durations = []
    for index, payload in enumerate(payloads):
        started = time.perf_counter()
        response = get_json(generate_url, payload)
        durations.append((time.perf_counter() - started) * 1000)
        if response.get("status") != "ok" or not isinstance(
            response.get("files"), dict
        ):
            raise ValueError(f"case {index}: generation failed: {response}")
        peak_rss = max(peak_rss, rss_bytes(pid))
    p95_ms = sorted(durations)[math.ceil(0.95 * len(durations)) - 1]
    limits = {
        "model_mb": args.max_model_mb,
        "peak_rss_mb": args.max_rss_mb,
        "p95_ms": args.max_p95_ms,
    }
    measured = {
        "model_mb": round(model_bytes / MB, 3),
        "peak_rss_mb": round(peak_rss / MB, 3),
        "p95_ms": round(p95_ms, 3),
        "max_ms": round(max(durations), 3),
    }
    checks = {
        "model": model_bytes <= args.max_model_mb * MB,
        "memory": peak_rss <= args.max_rss_mb * MB,
        "latency": p95_ms <= args.max_p95_ms,
    }
    receipt = {
        "scope": "warm, sequential HTTP requests; full response parsed; startup excluded",
        "model_manifest_sha256": manifest_sha,
        "blocks_sha256": health["blocks_sha256"],
        "cases_sha256": hashlib.sha256(args.cases.read_bytes()).hexdigest(),
        "cases": len(payloads),
        "warmup": args.warmup,
        "limits": limits,
        "measured": measured,
        "checks": checks,
        "limits_enforced": args.enforce_limits,
        "reference_values_met": all(checks.values()),
        "measurement_complete": True,
        "passed": all(checks.values()) if args.enforce_limits else None,
    }
    result = json.dumps(receipt, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(result)
    else:
        sys.stdout.write(result)
    if args.enforce_limits and not receipt["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()

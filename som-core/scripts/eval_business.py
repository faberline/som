"""Rust HTTP parity, three-round performance measurements, and app checks."""

import argparse
import fcntl
import hashlib
import json
import math
import tempfile
import threading
import time
from pathlib import Path

import httpx
import psutil
from verify_related import verify

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "som-core/data/business-service"


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def p95(values):
    return sorted(values)[math.ceil(.95 * len(values)) - 1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8766")
    parser.add_argument("--model", type=Path, default=ROOT / "som-rs/models/business-service-candidate")
    parser.add_argument("--pid", type=int, required=True)
    parser.add_argument("--fixture", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--mode", choices=["budget", "apps", "all"], default="all")
    parser.add_argument("--enforce-historical-budgets", action="store_true", help="Reproduce the old performance gate; default is measurement only")
    args = parser.parse_args()
    rows = [json.loads(line) for line in (DATA / "regression.jsonl").read_text().splitlines()]
    old_expected = [json.loads(line) for line in (ROOT / "som-core/data/service-regression-v4/heldout.expected.jsonl").read_text().splitlines()]
    new_expected = [json.loads(line) for line in (DATA / "reference.jsonl").read_text().splitlines()]
    expected = old_expected + new_expected
    process = psutil.Process(args.pid)
    idle = process.memory_info().rss
    rss = [idle]
    stop = threading.Event()

    def sample():
        while not stop.wait(.005):
            rss.append(process.memory_info().rss)

    thread = threading.Thread(target=sample, daemon=True)
    model_bytes = sum(p.stat().st_size for p in args.model.rglob("*") if p.is_file())
    result = {"rows": len(rows), "new_apps": len(new_expected), "rounds": [], "apps": [], "model_directory_bytes": model_bytes, "idle_rss_bytes": idle, "psutil_version": psutil.__version__, "case_sha256": digest(DATA / "regression.jsonl"), "reference_sha256": digest(DATA / "reference.jsonl")}
    result["performance_limits_enforced"] = args.enforce_historical_budgets
    args.output.parent.mkdir(parents=True, exist_ok=True)

    def save():
        args.output.write_text(json.dumps(result, indent=2) + "\n")

    with httpx.Client(base_url=args.url, timeout=65) as client:
        health = client.get("/healthz").json()
        assert health["model_manifest_sha256"] == digest(args.model / "manifest.json")
        assert health["blocks_sha256"] == digest(ROOT / "som-code-python/data/blocks/v5/blocks.json")
        result["health"] = health
        thread.start()
        try:
            for round_index in range(3 if args.mode != "apps" else 1):
                for index in (0, 50, 100, 105, 110):
                    warm = client.post("/v1/generate", json=rows[index]["prompt"])
                    warm.raise_for_status()
                measured = []
                for index, row in enumerate(rows):
                    start = time.perf_counter_ns()
                    response = client.post("/v1/generate", json=row["prompt"])
                    payload = response.json()
                    elapsed = (time.perf_counter_ns() - start) / 1_000_000
                    assert response.status_code == 200, (index, payload)
                    assert payload["files"] == expected[index], index
                    measured.append({"index": index, "app": row["prompt"]["package"], "milliseconds": elapsed, "response_bytes": len(response.content), "source_bytes": sum(len(text.encode()) for text in payload["files"].values())})
                groups = {"old_stress_50": measured[:50], "old_date_50": measured[50:100], "business_14": measured[100:], "all": measured}
                summary = {"round": round_index + 1, "p95_ms": {key: p95([r["milliseconds"] for r in group]) for key, group in groups.items()}, "samples": measured}
                result["rounds"].append(summary)
                save()
                print(json.dumps({"round": round_index + 1, "p95_ms": summary["p95_ms"]}), flush=True)
        finally:
            stop.set(); thread.join()
        result["peak_rss_bytes"] = max(rss)
        result["memory_samples"] = len(rss)
        result["max_round_p95_ms"] = {key: max(r["p95_ms"][key] for r in result["rounds"]) for key in result["rounds"][0]["p95_ms"]}
        result["budget_passed"] = model_bytes <= 50_000_000 and max(rss) <= 1_000_000_000 and max(result["max_round_p95_ms"].values()) <= 100
        result["budget_comparison_scope"] = "historical 50 MB, 1 GB, and 100 ms reference values; not current capability limits"
        save()
        if args.mode != "budget":
            fixture = json.loads(args.fixture.read_text())
            for index, row in enumerate(rows[100:]):
                payload = client.post("/v1/generate", json=row["prompt"]).json()
                root = Path(tempfile.mkdtemp(prefix="som-rust-business-", dir="/private/tmp"))
                for name, source in payload["files"].items():
                    path = root / name
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text(source)
                with Path("/private/tmp/som-business-eval-fixture.lock").open("a") as lock:
                    fcntl.flock(lock, fcntl.LOCK_EX)
                    checked = verify(row["prompt"], root, fixture)
                result["apps"].append({"index": index, "directory": str(root), **checked})
                save()
                print(json.dumps({"app": checked["package"], "status": checked["status"], "checks": len(checked["checks"])}), flush=True)
    result["correctness_passed"] = True
    save()
    print(json.dumps({key: result[key] for key in ("model_directory_bytes", "idle_rss_bytes", "peak_rss_bytes", "max_round_p95_ms", "budget_passed")}), flush=True)
    if args.enforce_historical_budgets and not result["budget_passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()

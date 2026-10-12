"""Fault injection only against disposable copies of the quality candidate."""

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import socket
import subprocess
import tempfile
import time

import httpx

ROOT = Path(__file__).resolve().parents[2]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    model = ROOT / "som-rs/models/composed-quality-candidate"
    command = ROOT / "som-rs/target/composed-quality/release/som"
    blocks = ROOT / "som-code-python/data/blocks/composed-quality/blocks.json"
    before = {str(path): hashlib.sha256(path.read_bytes()).hexdigest()
              for path in [blocks, command, *model.rglob("*")] if path.is_file()}
    row = json.loads((ROOT / "som-core/data/composed/regression.jsonl").read_text().splitlines()[3])
    results = []
    for fault in ("missing_formatter", "wrong_version", "changed_profile", "execution_failure", "timeout"):
        root = Path(tempfile.mkdtemp(prefix="som-quality-fault-", dir="/private/tmp"))
        copy = root / "model"
        shutil.copytree(model, copy)
        if fault == "missing_formatter":
            (copy / "ruff").unlink()
        elif fault == "changed_profile":
            with (copy / "pyproject.toml").open("a") as profile:
                profile.write("\n# deliberate profile mutation\n")
        elif fault == "wrong_version":
            quality = json.loads((copy / "quality.json").read_text())
            quality["formatter_version"] = "ruff 0.16.5"
            (copy / "quality.json").write_text(json.dumps(quality) + "\n")
            manifest = json.loads((copy / "manifest.json").read_text())
            manifest["files"]["quality.json"] = hashlib.sha256((copy / "quality.json").read_bytes()).hexdigest()
            (copy / "manifest.json").write_text(json.dumps(manifest) + "\n")
        with socket.socket() as socket_:
            socket_.bind(("127.0.0.1", 0))
            port = socket_.getsockname()[1]
        log_path = args.output / (fault + ".log")
        result = {"fault": fault, "copy": str(copy)}
        with log_path.open("w") as log:
            process = subprocess.Popen([str(command), "serve", "--model", str(copy),
                                        "--blocks", str(blocks), "--port", str(port)],
                                       stdout=log, stderr=log)
            try:
                if fault in {"missing_formatter", "wrong_version", "changed_profile"}:
                    result["exit_code"] = process.wait(timeout=20)
                    assert process.returncode != 0, "invalid formatter started"
                else:
                    url = "http://127.0.0.1:" + str(port)
                    with httpx.Client(timeout=30) as client:
                        for _ in range(600):
                            try:
                                health = client.get(url + "/healthz")
                                if health.status_code == 200:
                                    break
                            except httpx.HTTPError:
                                pass
                            assert process.poll() is None, "fault service did not start"
                            time.sleep(.1)
                        else:
                            raise AssertionError("fault service did not become healthy")
                        result["startup_health"] = health.json()
                        # This fake native tool runs only in the fault copy.
                        (copy / "ruff").write_text("#!/bin/sh\n" +
                                                   ("exit 2\n" if fault == "execution_failure" else "exec /bin/sleep 11\n"))
                        started = time.monotonic()
                        response = client.post(url + "/v1/generate", json=row["prompt"])
                        result.update(http_status=response.status_code, response=response.json(),
                                      seconds=time.monotonic()-started)
                        assert response.status_code == 500 and response.json()["code"] == "OUTPUT_QUALITY_FAILED"
                        assert "files" not in response.json()
                result["passed"] = True
            finally:
                if process.poll() is None:
                    process.terminate()
                    process.wait(timeout=10)
        results.append(result)
        (args.output / "result.json").write_text(json.dumps({"results": results}, indent=2) + "\n")
    unchanged = before == {name: hashlib.sha256(Path(name).read_bytes()).hexdigest() for name in before}
    report = {"results": results, "original_assets_unchanged": unchanged,
              "passed": unchanged and all(row["passed"] for row in results)}
    (args.output / "result.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({"passed": report["passed"], "cases": len(results)}))


if __name__ == "__main__":
    main()

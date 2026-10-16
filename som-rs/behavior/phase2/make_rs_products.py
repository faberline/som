#!/usr/bin/env python3
"""Make the Python products of the phase 1 scope refusal cases RS01 to RS22 (issue #52).

usage:
  make_rs_products.py --out .local/rust-output-phase2/rs-products-NNN

The script starts its own instance of the current default binary with the current default assets
on a QA port (18919 to 18998, never 8765 or 18765), requires its /healthz to equal the live
default's /healthz exactly, and stops it on exit. For each RS case it compiles the frozen
python_control_body (the case without a target), assembles the bundle, and keeps the request,
the responses, the prompt and the files under --out/<id>/. The bundle payload hash must equal
the one the phase 1 Python control run recorded. The live default is only read.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request

HERE = pathlib.Path(__file__).resolve().parent
SOM_RS = HERE.parents[1]
REPO = SOM_RS.parent
sys.path.insert(0, str(SOM_RS / "scripts" / "compiler_acceptance"))
sys.path.insert(0, str(SOM_RS / "tests" / "rust-output-v1"))
import qa_harness as qa  # noqa: E402
import rust_profile  # noqa: E402

BINARY_SHA256 = "05f1bfe6eef7045f054b1323a193e8f7bea1b4afe4f287a47cd0d0f9347ae11c"
ASSETS_SHA256 = "872eb4dc162e54c0cf4ada196652417161cffc3cb4a0382d987fc668f94d416e"
BINARY = SOM_RS / "runtime" / "versions" / BINARY_SHA256 / "som"
ASSETS = SOM_RS / "compiler-assets" / "versions" / ASSETS_SHA256
LIVE = "http://127.0.0.1:8765"
CASES = SOM_RS / "contracts" / "rust-output-v1" / "negative-cases.json"
CONTROLS = SOM_RS / "evidence" / "rust-output-v1" / "negative-python-controls-2026-10-15.json"
FORBIDDEN_PORTS = {8765, 18765}


def file_sha(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def request(base: str, path: str, body: bytes | None = None) -> tuple[int, bytes]:
    req = urllib.request.Request(base + path, data=body,
                                 headers={"content-type": "application/json"} if body is not None else {})
    try:
        with urllib.request.urlopen(req, timeout=120) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as error:
        return error.code, error.read()


def free_qa_port() -> int:
    for port in range(18919, 18999):
        if port in FORBIDDEN_PORTS:
            continue
        with socket.socket() as sock:
            try:
                sock.bind(("127.0.0.1", port))
            except OSError:
                continue
            return port
    raise SystemExit("no free QA port in 18919-18998")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=pathlib.Path, required=True)
    args = parser.parse_args()
    out = args.out.resolve()
    if out.exists():
        raise SystemExit(f"{out} exists; use a new output directory")
    if file_sha(BINARY) != BINARY_SHA256:
        raise SystemExit("default binary hash differs")
    status, live_raw = request(LIVE, "/healthz")
    if status != 200:
        raise SystemExit(f"live default /healthz returned {status}")
    live_health = json.loads(live_raw)
    if live_health.get("asset_manifest_sha256") != ASSETS_SHA256:
        raise SystemExit("live default does not serve the pinned assets")
    cases = [c for c in json.loads(CASES.read_text())["cases"] if c["id"].startswith("RS")]
    controls = {row["id"]: row for row in json.loads(CONTROLS.read_text())["results"]}
    out.mkdir(parents=True)
    port = free_qa_port()
    argv = [str(BINARY), "serve", "--assets", str(ASSETS), "--port", str(port)]
    log = (out / "qa-server.log").open("wb")
    process = subprocess.Popen(argv, stdout=log, stderr=log, start_new_session=True)
    base = f"http://127.0.0.1:{port}"
    rows = []
    try:
        for _ in range(300):
            if process.poll() is not None:
                raise SystemExit(f"QA instance exited {process.returncode}")
            try:
                status, raw = request(base, "/healthz")
            except OSError:
                time.sleep(0.2)
                continue
            if status == 200:
                break
            time.sleep(0.2)
        else:
            raise SystemExit("QA instance did not become healthy")
        qa_health = json.loads(raw)
        if qa_health != live_health:
            raise SystemExit("QA instance /healthz differs from the live default")
        for case in cases:
            case_dir = out / case["id"]
            case_dir.mkdir()
            body = case["python_control_body"].encode()
            (case_dir / "compile.request.raw").write_bytes(body)
            status, raw = request(base, "/v1/compile", body)
            (case_dir / "compile.response.raw").write_bytes(raw)
            if status != 200:
                raise SystemExit(f"{case['id']}: compile returned {status}")
            bundle = json.loads(raw)["bundle"]
            payload = bundle["hashes"]["payload_sha256"]
            if payload != controls[case["id"]].get("payload_sha256"):
                raise SystemExit(f"{case['id']}: bundle payload differs from the phase 1 Python control")
            assemble = json.dumps({"bundle": bundle}, ensure_ascii=False).encode()
            status, raw = request(base, "/v1/assemble", assemble)
            (case_dir / "assemble.response.raw").write_bytes(raw)
            if status != 200:
                raise SystemExit(f"{case['id']}: assemble returned {status}")
            files = json.loads(raw)["files"]
            prompt = bundle["prompt"]
            refusal = rust_profile.check(prompt)
            if refusal is None:
                raise SystemExit(f"{case['id']}: the phase 1 profile admits this prompt")
            (case_dir / "prompt.json").write_text(json.dumps(prompt, indent=2, ensure_ascii=False) + "\n")
            (case_dir / "files.json").write_text(json.dumps(files, indent=1, ensure_ascii=False) + "\n")
            row = {"id": case["id"], "form": case["form"], "base_example": case["base_example"],
                   "prompt_version": prompt["version"], "phase1_refusal": refusal["rule"],
                   "payload_sha256": payload, "prompt_sha256": qa.sha(prompt),
                   "product_sha256": qa.sha(files), "files": len(files)}
            (case_dir / "result.json").write_text(json.dumps(row, indent=2) + "\n")
            rows.append(row)
            print(case["id"], prompt["version"], refusal["rule"], len(files), flush=True)
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=15)
        log.close()
    summary = {"version": "som.rust-output-phase2-rs-products.v1",
               "binary_sha256": BINARY_SHA256, "assets_sha256": ASSETS_SHA256,
               "qa_argv": argv, "qa_health_equals_live_default": True,
               "negative_cases_sha256": file_sha(CASES), "python_controls_sha256": file_sha(CONTROLS),
               "cases": rows}
    (out / "result.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(f"wrote {len(rows)} RS products to {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

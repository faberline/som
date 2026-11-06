#!/usr/bin/env python3
"""Exercise the service HTTP and CLI paths and materialize actual generated code."""
import argparse
import hashlib
import http.client
import json
import os
from pathlib import Path
import select
import signal
import subprocess
import time

SERVICE = Path(__file__).resolve().parents[1]


def sha(data):
    return hashlib.sha256(data).hexdigest()


def request(port, method, path, data=None):
    client = http.client.HTTPConnection("127.0.0.1", port, timeout=15)
    try:
        client.request(method, path, data, {"content-type": "application/json"})
        response = client.getresponse()
        body = response.read()
        return response.status, json.loads(body)
    finally:
        client.close()


def start(binary, arguments, output, name):
    error = (output / (name + ".stderr")).open("w+")
    process = subprocess.Popen([str(binary), *map(str, arguments)], stdout=subprocess.DEVNULL, stderr=error)
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        assert process.poll() is None, f"{name} stopped before readiness"
        error.flush()
        text = (output / (name + ".stderr")).read_text()
        for line in text.splitlines():
            if line.startswith("som-service listening on "):
                return process, error, int(line.rsplit(":", 1)[1])
            if line.startswith("{"):
                event = json.loads(line)
                if event.get("stage") == "listening":
                    return process, error, int(event["address"].rsplit(":", 1)[1])
        time.sleep(.05)
    process.terminate()
    error.close()
    raise AssertionError(f"{name} readiness timed out")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--legacy-binary", type=Path)
    parser.add_argument("--legacy-assets", type=Path)
    args = parser.parse_args()
    args.binary = args.binary.resolve()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    processes = []
    checks = []
    try:
        process, error, port = start(args.binary, ["serve", "--port", "0"], output, "service")
        processes.append((process, error))
        url = f"http://127.0.0.1:{port}"
        cnl = (SERVICE / "contract/task-status.cnl").read_text()
        prompt = json.loads((SERVICE / "contract/prompt.json").read_text())
        expected_dsl = json.loads((SERVICE / "contract/dsl.json").read_text())
        status, health = request(port, "GET", "/health")
        assert status == 200 and health["formats"] == ["som.cnl.service.v1", "som.prompt-json.service.v1"]
        assert health["model_loaded"] is False
        checks.append("service health and version isolation")
        status, compiled = request(port, "POST", "/compile", json.dumps({"format":"som.cnl.service.v1", "source":cnl}))
        assert status == 200 and compiled["bundle"]["prompt"] == prompt and compiled["bundle"]["dsl"] == expected_dsl
        bundle = compiled["bundle"]
        status, typed = request(port, "POST", "/compile", json.dumps({"format":"som.prompt-json.service.v1", "prompt":prompt}))
        assert status == 200 and typed["bundle"] == bundle
        checks.append("HTTP CNL and typed input share frozen prompt and DSL")
        bundle_file = output / "task-status.bundle.json"
        bundle_file.write_text(json.dumps(bundle, indent=2) + "\n")
        def cli(command, source, key):
            result = subprocess.run([str(args.binary), command, str(source), "--url", url], capture_output=True)
            (output / (key+".stdout")).write_bytes(result.stdout)
            (output / (key+".stderr")).write_bytes(result.stderr)
            assert result.returncode == 0, result.stderr.decode()
            return json.loads(result.stdout)
        assert cli("compile", SERVICE/"contract/task-status.cnl", "cli-cnl") == bundle
        assert cli("generate", SERVICE/"contract/prompt.json", "cli-typed") == bundle
        status, assembled = request(port, "POST", "/assemble", json.dumps({"bundle":bundle}))
        assert status == 200
        assert cli("assemble", bundle_file, "cli-assemble") == assembled["files"]
        checks.append("CLI compile/generate/assemble equal HTTP output")
        generated = output / "generated"
        expected_paths = {"Cargo.toml", "Cargo.lock", "src/lib.rs", "README.md"}
        assert set(assembled["files"]) == expected_paths
        for path, text in assembled["files"].items():
            destination = generated / path
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(text)
        for format in ["som.cnl.v1", "som.prompt-json.v1", "som.cnl.service.v2"]:
            status, body = request(port, "POST", "/compile", json.dumps({"format":format, "source":cnl}))
            assert status == 422 and body["code"] == "UNSUPPORTED_VERSION"
        for raw in [b'{"format":"som.cnl.service.v1","source":"","source":""}', b'{"format":"som.prompt-json.service.v1","prompt":{},"extra":true}']:
            status, body = request(port, "POST", "/compile", raw)
            assert status == 400 and body["code"] == "INVALID_SCHEMA"
        status, body = request(port, "POST", "/compile", b'\xff')
        assert status == 400 and body["code"] == "INVALID_JSON"
        status, body = request(port, "POST", "/compile", b' ' * 262145)
        assert status == 413 and body["code"] == "REQUEST_TOO_LARGE"
        changed = json.loads(json.dumps(bundle))
        changed["dsl"]["operation"]["method"] = "DELETE"
        status, body = request(port, "POST", "/assemble", json.dumps({"bundle":changed}))
        assert status == 400 and body["code"] == "INVALID_BUNDLE"
        checks.append("HTTP old/unknown formats, malformed and duplicate JSON, body limit and bundle refusal")
        duplicate_prompt = output / "duplicate-prompt.json"
        duplicate_prompt.write_text('{"version":"som.prompt.service.v1","version":"som.prompt.service.v1"}')
        duplicate_cli = subprocess.run([str(args.binary), "generate", str(duplicate_prompt), "--url", url], capture_output=True)
        assert duplicate_cli.returncode != 0 and b'INVALID_SCHEMA' in duplicate_cli.stderr
        checks.append("CLI preserves duplicate keys for server refusal")
        if args.legacy_binary:
            assert args.legacy_assets is not None
            old, log, old_port = start(args.legacy_binary, ["serve", "--port", "0", "--assets", args.legacy_assets], output, "legacy")
            processes.append((old, log))
            status, old_health = request(old_port, "GET", "/healthz")
            assert status == 200
            assert "som.cnl.service.v1" not in json.dumps(old_health)
            for format, field, value in [("som.cnl.service.v1","source",cnl),("som.prompt-json.service.v1","prompt",prompt), ("som.cnl.service.v1-draft","source",cnl),("som.prompt-json.service.v1-draft","prompt",prompt)]:
                status, body = request(old_port, "POST", "/v1/compile", json.dumps({"format":format,field:value}))
                assert status == 422 and body["code"] == "UNSUPPORTED_VERSION"
            assert request(old_port, "GET", "/healthz") == (200,old_health)
            (output / "legacy-health.json").write_text(json.dumps(old_health, indent=2)+"\n")
            checks.append("legacy compile refuses new/draft envelopes and retains unchanged health")
        identity = json.loads(subprocess.check_output([str(args.binary), "identity"]))
        assert identity["assets"] == bundle["assets"] == health["assets"]
        canonical = json.dumps(identity["manifest"], sort_keys=True, separators=(",",":"), ensure_ascii=False).encode()
        assert sha(canonical) == identity["assets"]["sha256"]
        for name, digest in identity["manifest"]["files"].items():
            if not name.startswith("nlp/"):
                assert sha((SERVICE/name).read_bytes()) == digest, name
        (output / "identity.json").write_text(json.dumps(identity, indent=2)+"\n")
        result={"status":"passed","checks":checks,"binary_sha256":sha(args.binary.read_bytes()),"assets":identity["assets"],"generated_dir":str(generated),"files_sha256":{p:sha((generated/p).read_bytes()) for p in sorted(expected_paths)}}
        (output / "report.json").write_text(json.dumps(result, indent=2)+"\n")
        print(json.dumps(result, indent=2))
    finally:
        for process, log in reversed(processes):
            if process.poll() is None:
                process.send_signal(signal.SIGINT)
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
            log.close()


if __name__ == "__main__":
    main()

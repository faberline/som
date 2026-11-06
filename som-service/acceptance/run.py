#!/usr/bin/env python3
"""Check an actual generated Rust client against pinned Defer and fixed wire cases."""
from __future__ import annotations
import argparse
import hashlib
import http.client
import http.server
import io
import json
import os
from pathlib import Path
import shutil
import signal
import socket
import subprocess
import sys
import tarfile
import threading
import time
import tomllib
from urllib.parse import quote, urlsplit

HERE = Path(__file__).resolve().parent
SERVICE = HERE.parent
DEFER_REV = "5f7adac1fe1333b33e3ec69a92b5ca4a634a97d0"
CORE_REV = "029e765efaac4080bb9e8283005a9c6792953f54"
SECRET = "secret-fixture-value"


def sha(data):
    return hashlib.sha256(data).hexdigest()


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False, sort_keys=True) + "\n")


def source_files(root):
    return {p.relative_to(root).as_posix(): sha(p.read_bytes()) for p in sorted(root.rglob("*"))
            if p.is_file() and not any(x in {".git", "target", "__pycache__"} for x in p.relative_to(root).parts)}


def run(command, output, stem, env=None):
    command = [str(x) for x in command]
    started = time.time()
    with (output / (stem + ".stdout")).open("wb") as stdout, (output / (stem + ".stderr")).open("wb") as stderr:
        result = subprocess.run(command, stdout=stdout, stderr=stderr, env=env)
    record = {"argv": command, "exit_code": result.returncode, "elapsed_seconds": round(time.time() - started, 3)}
    with (output / "commands.jsonl").open("a") as log:
        log.write(json.dumps(record) + "\n")
    if result.returncode:
        raise RuntimeError(f"{stem} failed; see {output / (stem + '.stderr')}")
    return (output / (stem + ".stdout")).read_bytes()


def cargo_env(args, kind):
    env = os.environ.copy()
    home = args.native_cargo_home if kind == "native" else args.client_cargo_home
    target = args.native_target_dir if kind == "native" else args.client_target_dir
    env.update(CARGO_HOME=str(home), CARGO_TARGET_DIR=str(target), CARGO_NET_OFFLINE="true", CARGO_BUILD_JOBS="2")
    env["PATH"] = str(args.cargo.parent) + os.pathsep + env.get("PATH", "")
    return env


def local_http_env():
    env = os.environ.copy()
    for key in ["HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"]:
        env.pop(key, None)
    env["NO_PROXY"] = env["no_proxy"] = "127.0.0.1,localhost,::1"
    return env


def freeze_sources(args, output, wire):
    archive = subprocess.check_output(["git", "-C", str(args.defer_repo), "archive", DEFER_REV])
    snapshot = output / "defer"
    snapshot.mkdir()
    with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
        tar.extractall(snapshot, filter="data")
    for name, expected in wire["source"]["source_files"].items():
        assert sha((snapshot / name).read_bytes()) == expected, ("Defer source mismatch", name)
    identity = {"defer_revision": DEFER_REV, "core_revision": CORE_REV,
                "defer_archive_sha256": sha(archive), "defer_files": source_files(snapshot),
                "acceptance_files": source_files(HERE), "contract_files": source_files(SERVICE / "contract")}
    write_json(output / "source-identity.json", identity)
    return snapshot, identity


def build_native(args, output, snapshot, identity, wire):
    harness = output / "native"
    (harness / "src").mkdir(parents=True)
    shutil.copyfile(HERE / "native/main.rs", harness / "src/main.rs")
    shutil.copyfile(HERE / "native/Cargo.toml.in", harness / "Cargo.toml")
    shutil.copyfile(snapshot / "Cargo.lock", harness / "Cargo.lock")
    env = cargo_env(args, "native")
    metadata = json.loads(run([args.cargo, "metadata", "--offline", "--format-version", "1", "--manifest-path", harness / "Cargo.toml"], output, "native-metadata", env))
    original = tomllib.loads((snapshot / "Cargo.lock").read_text())["package"]
    pins = {(x["name"], x["version"], x.get("source")) for x in original}
    core_roots = set()
    for package in metadata["packages"]:
        if package["name"] != "som-defer-dfr07-native":
            assert (package["name"], package["version"], package.get("source")) in pins, ("Native dependency changed", package["id"])
        if package.get("source", "") and "github.com/faberline/core" in package["source"]:
            assert package["source"].endswith("#" + CORE_REV), package["source"]
            root = subprocess.check_output(["git", "-C", str(Path(package["manifest_path"]).parent), "rev-parse", "--show-toplevel"], text=True).strip()
            core_roots.add(Path(root))
    assert len(core_roots) == 1, core_roots
    core = core_roots.pop()
    assert subprocess.check_output(["git", "-C", str(core), "rev-parse", "HEAD"], text=True).strip() == CORE_REV
    assert not subprocess.check_output(["git", "-C", str(core), "status", "--porcelain", "--untracked-files=no"]), "Core cache has tracked edits"
    for name, expected in wire["dependencies"][0]["source_files"].items():
        assert sha((core / name).read_bytes()) == expected, ("Core source mismatch", name)
    identity["core_files"] = source_files(core)
    identity["native_lock_sha256"] = sha((harness / "Cargo.lock").read_bytes())
    identity["native_resolved_packages"] = [{"name": p["name"], "version": p["version"], "source": p.get("source")} for p in metadata["packages"]]
    run([args.cargo, "build", "--locked", "--offline", "--manifest-path", harness / "Cargo.toml"], output, "native-build", env)
    binary = args.native_target_dir / "debug/som-defer-dfr07-native"
    identity["native_binary_sha256"] = sha(binary.read_bytes())
    return binary


def build_probe(args, output, identity):
    original = source_files(args.generated_dir)
    generated = output / "generated"
    shutil.copytree(args.generated_dir, generated, ignore=shutil.ignore_patterns("target", ".git", "__pycache__"))
    assert source_files(generated) == original
    for required in ["Cargo.toml", "Cargo.lock", "src/lib.rs"]:
        assert (generated / required).is_file(), ("Missing generated artifact", required)
    package = tomllib.loads((generated / "Cargo.toml").read_text())["package"]["name"]
    env = cargo_env(args, "client")
    run([args.cargo, "test", "--locked", "--offline", "--manifest-path", generated / "Cargo.toml"], output, "generated-test", env)
    probe = output / "probe"
    (probe / "src").mkdir(parents=True)
    shutil.copyfile(HERE / "probe/main.rs", probe / "src/main.rs")
    shutil.copyfile(generated / "Cargo.lock", probe / "Cargo.lock")
    (probe / "Cargo.toml").write_text('[package]\nname = "som-defer-client-probe"\nversion = "0.1.0"\nedition = "2021"\n[workspace]\n[dependencies]\ngenerated-client = { package = ' + json.dumps(package) + ', path = "../generated" }\nserde_json = "1.0"\ntokio = { version = "1", features = ["macros", "rt-multi-thread"] }\n[profile.dev]\ndebug = 0\n')
    run([args.cargo, "metadata", "--offline", "--format-version", "1", "--manifest-path", probe / "Cargo.toml"], output, "probe-metadata", env)
    run([args.cargo, "build", "--locked", "--offline", "--manifest-path", probe / "Cargo.toml"], output, "probe-build", env)
    assert source_files(generated) == original, "Generated source or lock changed during acceptance"
    identity["generated_files"] = original
    identity["probe_lock_sha256"] = sha((probe / "Cargo.lock").read_bytes())
    binary = args.client_target_dir / "debug/som-defer-client-probe"
    identity["probe_binary_sha256"] = sha(binary.read_bytes())
    return binary


def native_get(info, row):
    key = "open_base_url" if row.get("server") == "open" else "base_url"
    url = urlsplit(info[key])
    client = http.client.HTTPConnection(url.hostname, url.port, timeout=5)
    headers = {} if row.get("token") is None else {"Authorization": "Bearer " + row["token"]}
    client.request("GET", row["path"], headers=headers)
    response = client.getresponse()
    body = response.read()
    status = response.status
    client.close()
    return status, body


def native_checks(info, oracle):
    records = []
    for row in oracle["cases"]:
        status, body = native_get(info, row)
        assert status == row["expected_status"], (row["id"], status)
        assert json.loads(body) == row["expected_body"], (row["id"], body)
        records.append({"id": row["id"], "status": status, "body": list(body)})
    edges = []
    for edge in oracle["native_path_edge_cases"]:
        status, body = native_get(info, {**edge, "token": "reader"})
        assert (status, body.decode()) == (edge["status"], edge["body_utf8"]), edge
        edges.append(edge)
    return records, edges


class CaptureHandler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *_):
        pass

    def do_GET(self):
        body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
        record = {"method": self.command, "path": self.path, "authorization": self.headers.get("Authorization"), "body": list(body),
                  "content_length": int(self.headers.get("Content-Length", "0")), "transfer_encoding": self.headers.get("Transfer-Encoding")}
        self.server.records.append(record)
        scripted = self.server.responses.get(self.path)
        if scripted is None:
            scripted = {"response_status": 500, "response_body_hex": b"unexpected request".hex()}
        if scripted.get("timeout"):
            time.sleep(31)
        raw = bytes.fromhex(scripted["response_body_hex"])
        self.send_response(scripted["response_status"])
        self.send_header("Content-Length", str(len(raw) + scripted.get("declared_extra_bytes", 0)))
        self.send_header("Content-Type", "application/json")
        for key, value in scripted.get("response_headers", {}).items():
            self.send_header(key, value)
        self.end_headers()
        try:
            self.wfile.write(raw)
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass
        if scripted.get("close_after_body"):
            self.close_connection = True

    do_POST = do_PUT = do_DELETE = do_GET


def check_probe(records, cases, expected_requests, captures):
    assert len(records) == len(cases), (len(records), len(cases))
    for actual, case in zip(records, cases):
        assert actual["id"] == case["id"]
        assert actual["kind"] == case["expected_kind"], (case["id"], actual, case)
        kind = actual["kind"]
        if kind == "Ok":
            assert actual["body"] == case["expected_decoded"], (case["id"], actual)
        elif kind in {"Http", "InvalidResponse"}:
            assert actual["status"] == case["expected_status"], (case["id"], actual)
            assert actual["body"] == case["expected_body"], (case["id"], actual)
            if kind == "Http":
                assert actual["native"] == case["expected_native"], (case["id"], actual)
        elif kind == "InvalidInput":
            assert actual["parameter"] == case["expected_parameter"], (case["id"], actual)
        elif kind == "Network":
            assert actual["timeout"] == case["expected_timeout"], (case["id"], actual)
            assert actual["status"] == case.get("expected_status"), (case["id"], actual)
            assert actual["body"] == case.get("expected_body", []), (case["id"], actual)
        for field in ["debug", "display", "client_debug"]:
            assert SECRET not in actual.get(field, ""), ("Secret leaked in diagnostic", case["id"], field)
    assert captures == expected_requests, ("HTTP request mismatch", captures, expected_requests)


def client_checks(args, output, binary, info, oracle, native_records, capture_oracle):
    cases = []
    native_by_id = {r["id"]: r for r in native_records}
    for row in oracle["cases"]:
        raw = native_by_id[row["id"]]["body"]
        case = {"id": row["id"], "base_url": info["open_base_url" if row["server"] == "open" else "base_url"],
                "queue": row["queue"], "task_id": row["task_id"], "token": row["token"],
                "expected_kind": "Ok" if row["expected_status"] == 200 else "Http"}
        if case["expected_kind"] == "Ok":
            case["expected_decoded"] = row["expected_body"]
        else:
            case.update(expected_status=row["expected_status"], expected_body=raw, expected_native=row["expected_body"])
        cases.append(case)
    capture = http.server.ThreadingHTTPServer(("127.0.0.1", 0), CaptureHandler)
    capture.daemon_threads = True
    capture.records = []
    capture.responses = {}
    thread = threading.Thread(target=capture.serve_forever, daemon=True)
    thread.start()
    base = "http://127.0.0.1:" + str(capture.server_port)
    expected_requests = []
    def request_case(case, scripted):
        path = "/v1/queues/" + quote(case["queue"], safe="") + "/tasks/" + quote(case["task_id"], safe="")
        capture.responses[path] = scripted
        expected_requests.append({"method": "GET", "path": path, "authorization": "Bearer " + SECRET, "body": [], "content_length": 0, "transfer_encoding": None})
        cases.append({"base_url": base, "token": SECRET, **case})
    for index, row in enumerate(oracle["cases"]):
        if row["id"].startswith("path-"):
            body = row["expected_body"]
            request_case({"id": "capture-" + row["id"], "queue": row["queue"], "task_id": row["task_id"], "expected_kind": "Ok", "expected_decoded": body},
                         {"response_status": 200, "response_body_hex": json.dumps(body).encode().hex()})
    for row in capture_oracle["responses"]:
        case = {"id": row["id"], "queue": "response-" + row["id"], "task_id": "t", "expected_kind": row["expected_kind"]}
        if row["expected_kind"] == "Ok":
            case["expected_decoded"] = row["expected_decoded"]
        elif row["expected_kind"] == "Network":
            case.update(expected_status=row["response_status"], expected_body=list(bytes.fromhex(row["expected_body_hex"])), expected_timeout=False)
        else:
            case.update(expected_status=row["response_status"], expected_body=list(bytes.fromhex(row["expected_body_hex"])))
            if row["expected_kind"] == "Http":
                case["expected_native"] = row["expected_native"]
        request_case(case, row)
    for index, edge in enumerate(oracle["client_transport_refusals"]):
        case = {"id": f"invalid-path-{index}", "base_url": base, "token": SECRET, "queue": "valid", "task_id": "valid", "expected_kind": "InvalidInput", "expected_parameter": edge["parameter"]}
        case[edge["parameter"]] = edge["value"]
        cases.append(case)
    for index, origin in enumerate(capture_oracle["invalid_origins"]):
        cases.append({"id": f"invalid-origin-{index}", "base_url": origin, "token": SECRET, "configure_only": True, "expected_kind": "InvalidConfiguration"})
    cases.append({"id": "invalid-bearer", "base_url": base, "token": SECRET + "\r\nInjected: x", "configure_only": True, "expected_kind": "InvalidConfiguration"})
    for origin in [base, base + "/", "https://example.invalid"]:
        cases.append({"id": "valid-origin-" + origin, "base_url": origin, "token": SECRET, "configure_only": True, "expected_kind": "Configured"})
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        closed_port = sock.getsockname()[1]
    cases.append({"id": "network-connection-refused", "base_url": f"http://127.0.0.1:{closed_port}", "token": SECRET, "queue": "jobs", "task_id": "t", "expected_kind": "Network", "expected_timeout": False})
    request_case({"id": "request-timeout", "queue": "timeout", "task_id": "t", "expected_kind": "Network", "expected_timeout": True}, {"timeout": True, "response_status": 200, "response_body_hex": b'{}'.hex()})
    write_json(output / "probe-cases.json", cases)
    try:
        run([binary, output / "probe-cases.json", output / "probe-results.json"], output, "probe-run", local_http_env())
        records = json.loads((output / "probe-results.json").read_text())
        write_json(output / "captured-requests.json", capture.records)
        check_probe(records, cases, expected_requests, capture.records)
        return {"generated_client_cases": len(cases), "captured_requests": len(capture.records), "generated_client_passed": True}
    finally:
        capture.shutdown()
        capture.server_close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--generated-dir", required=True, type=Path, help="Actual compiler output for contract/task-status.cnl")
    parser.add_argument("--defer-repo", required=True, type=Path, help="Local Git repository containing the pinned Defer commit; never fetched or changed")
    parser.add_argument("--output", required=True, type=Path, help="New empty evidence directory")
    parser.add_argument("--cargo", type=Path, default=Path(shutil.which("cargo") or "cargo"))
    parser.add_argument("--native-cargo-home", required=True, type=Path, help="Offline cache with pinned Defer dependencies")
    parser.add_argument("--client-cargo-home", required=True, type=Path, help="Offline cache with generated client dependencies")
    parser.add_argument("--native-target-dir", type=Path, help="Optional warm target directory")
    parser.add_argument("--client-target-dir", type=Path, help="Optional warm target directory")
    args = parser.parse_args()
    for name in ["generated_dir", "defer_repo", "output", "cargo", "native_cargo_home", "client_cargo_home"]:
        setattr(args, name, getattr(args, name).resolve())
    output = args.output
    if output.exists() and any(output.iterdir()):
        parser.error("--output must be absent or empty")
    output.mkdir(parents=True, exist_ok=True)
    args.native_target_dir = (args.native_target_dir or output / "native-target").resolve()
    args.client_target_dir = (args.client_target_dir or output / "client-target").resolve()
    oracle = json.loads((HERE / "native-oracle.json").read_text())
    capture_oracle = json.loads((HERE / "capture-oracle.json").read_text())
    wire = json.loads((SERVICE / "contract/wire.json").read_text())
    assert wire["source"]["revision"] == DEFER_REV
    assert wire["dependencies"][0]["revision"] == CORE_REV
    success = {json.dumps(r["expected_body"], sort_keys=True) for r in oracle["cases"] if r["id"].startswith("native-")}
    assert success == {json.dumps(b, sort_keys=True) for b in wire["success_bodies"]}
    for error in wire["errors"]:
        assert any(r["expected_status"] == error["status"] and r["expected_body"] == error["body"] for r in oracle["cases"])
    identity = {}
    native = None
    server_logs = []
    try:
        for tool in [args.cargo, args.cargo.parent / "rustc"]:
            run([tool, "--version"], output, tool.name + "-version")
        snapshot, identity = freeze_sources(args, output, wire)
        identity["python_version"] = sys.version
        native_binary = build_native(args, output, snapshot, identity, wire)
        probe_binary = build_probe(args, output, identity)
        ready = output / "native-ready.json"
        server_logs = [(output / "native-server.stdout").open("wb"), (output / "native-server.stderr").open("wb")]
        native_command = [str(native_binary), str(ready)]
        with (output / "commands.jsonl").open("a") as log:
            log.write(json.dumps({"argv": native_command, "phase": "server-start"}) + "\n")
        native = subprocess.Popen(native_command, stdout=server_logs[0], stderr=server_logs[1], env=local_http_env())
        deadline = time.monotonic() + 20
        while not ready.exists():
            assert native.poll() is None, "Native server stopped before readiness"
            assert time.monotonic() < deadline, "Native server readiness timed out"
            time.sleep(.1)
        info = json.loads(ready.read_text())
        records, edges = native_checks(info, oracle)
        write_json(output / "native-results.json", {"records": records, "path_edges": edges})
        report = client_checks(args, output, probe_binary, info, oracle, records, capture_oracle)
        report.update(status="passed", native_http_cases=len(records), native_path_edges=len(edges), defer_revision=DEFER_REV, core_revision=CORE_REV)
        write_json(output / "report.json", report)
        print(json.dumps(report, indent=2))
    except Exception as error:
        write_json(output / "report.json", {"status": "failed", "error": str(error)})
        raise
    finally:
        if native is not None and native.poll() is None:
            native.send_signal(signal.SIGINT)
            try:
                native.wait(timeout=10)
            except subprocess.TimeoutExpired:
                native.kill()
                native.wait()
        if native is not None:
            with (output / "commands.jsonl").open("a") as log:
                log.write(json.dumps({"argv": native_command, "phase": "server-exit", "exit_code": native.returncode}) + "\n")
        for log in server_logs:
            log.close()
        if identity:
            write_json(output / "source-identity.json", identity)


if __name__ == "__main__":
    main()

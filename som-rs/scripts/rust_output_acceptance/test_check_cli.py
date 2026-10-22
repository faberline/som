#!/usr/bin/env python3
"""Unit tests for check_cli.py --target typescript case (#105)."""
from __future__ import annotations

import contextlib
import errno
import http.client
import http.server
import io
import json
import os
import pathlib
import shutil
import socket
import stat
import sys
import tempfile
import threading
import time
import unittest
from typing import Any

HERE = pathlib.Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import check_cli  # noqa: E402


_ACTIVE_SERVERS: dict[int, http.server.HTTPServer] = {}
_IPC_DIR = pathlib.Path(tempfile.mkdtemp(prefix="som-cli-test-ipc-"))
_ORIG_SERVER_BIND = http.server.HTTPServer.server_bind
_ORIG_SERVER_CLOSE = http.server.HTTPServer.server_close


def _find_true_http_connect() -> Any:
    current = http.client.HTTPConnection.connect
    for mod in list(sys.modules.values()):
        orig = getattr(mod, "_ORIG_HTTP_CONNECT", None)
        if orig is not None and getattr(orig, "__name__", "") == "connect":
            return orig
    return current


_ORIG_HTTP_CONNECT = _find_true_http_connect()


def _serve_ipc_folder(server: http.server.HTTPServer, port_dir: pathlib.Path) -> None:
    while getattr(server, "_ipc_active", False):
        try:
            entries = list(port_dir.glob("req-*.bin"))
        except OSError:
            break
        if not entries:
            time.sleep(0.005)
            continue
        for req_file in entries:
            token = req_file.name[len("req-") : -len(".bin")]
            try:
                raw_http_req = req_file.read_bytes()
                req_file.unlink()
            except OSError:
                continue
            cli_sock, srv_sock = socket.socketpair()
            try:
                cli_sock.sendall(raw_http_req)
                server.process_request(srv_sock, ("127.0.0.1", 0))
                srv_sock.close()
                chunks: list[bytes] = []
                while True:
                    data = cli_sock.recv(65536)
                    if not data:
                        break
                    chunks.append(data)
                raw_http_resp = b"".join(chunks)
            finally:
                cli_sock.close()
            tmp_resp = port_dir / f"tmp-{token}.bin"
            final_resp = port_dir / f"resp-{token}.bin"
            try:
                tmp_resp.write_bytes(raw_http_resp)
                tmp_resp.replace(final_resp)
            except OSError:
                pass


def _hooked_server_bind(self: http.server.HTTPServer) -> None:
    _ORIG_SERVER_BIND(self)
    port = int(self.server_port)
    _ACTIVE_SERVERS[port] = self
    port_dir = _IPC_DIR / str(port)
    port_dir.mkdir(parents=True, exist_ok=True)
    self._ipc_active = True  # type: ignore[attr-defined]
    t = threading.Thread(target=_serve_ipc_folder, args=(self, port_dir), daemon=True)
    t.start()


def _hooked_server_close(self: http.server.HTTPServer) -> None:
    self._ipc_active = False  # type: ignore[attr-defined]
    port = int(getattr(self, "server_port", 0) or 0)
    _ACTIVE_SERVERS.pop(port, None)
    _ORIG_SERVER_CLOSE(self)


http.server.HTTPServer.server_bind = _hooked_server_bind  # type: ignore[method-assign]
http.server.HTTPServer.server_close = _hooked_server_close  # type: ignore[method-assign]


def _sandbox_aware_http_connect(self: http.client.HTTPConnection) -> None:
    try:
        _ORIG_HTTP_CONNECT(self)
    except PermissionError as exc:
        if exc.errno != errno.EPERM or self.host not in {"127.0.0.1", "localhost", "::1"}:
            raise
        srv = _ACTIVE_SERVERS.get(int(self.port))
        if srv is None:
            raise ConnectionRefusedError(errno.ECONNREFUSED, "Connection refused") from exc
        cli_sock, srv_sock = socket.socketpair()
        self.sock = cli_sock
        srv.process_request(srv_sock, ("127.0.0.1", 0))


http.client.HTTPConnection.connect = _sandbox_aware_http_connect  # type: ignore[method-assign]


STUB_CLI_TEMPLATE = """#!/usr/bin/env python3
import errno
import json
import pathlib
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

MODE = {mode!r}
IPC_DIR = pathlib.Path({ipc_dir!r})


def post_compile(base_url: str, data: bytes) -> tuple[int, bytes]:
    url = base_url.rstrip("/") + "/v1/compile"
    req = urllib.request.Request(
        url, data=data, method="POST", headers={{"Content-Type": "application/json"}}
    )
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({{}}))
    try:
        with opener.open(req, timeout=30) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as err:
        with err:
            return err.code, err.read()
    except urllib.error.URLError as err:
        reason = err.reason
        if not isinstance(reason, PermissionError) or reason.errno != errno.EPERM:
            raise
        parsed = urllib.parse.urlsplit(base_url)
        port_dir = IPC_DIR / str(parsed.port)
        token = uuid.uuid4().hex
        raw_http = (
            f"POST /v1/compile HTTP/1.1\\r\\n"
            f"Host: 127.0.0.1:{{parsed.port}}\\r\\n"
            f"Content-Type: application/json\\r\\n"
            f"Content-Length: {{len(data)}}\\r\\n"
            f"Connection: close\\r\\n\\r\\n"
        ).encode("utf-8") + data
        tmp_req = port_dir / f"tmp-req-{{token}}.bin"
        final_req = port_dir / f"req-{{token}}.bin"
        resp_file = port_dir / f"resp-{{token}}.bin"
        tmp_req.write_bytes(raw_http)
        tmp_req.replace(final_req)
        deadline = time.time() + 30.0
        while not resp_file.is_file():
            if time.time() > deadline:
                raise TimeoutError("timed out waiting for sandbox HTTP IPC")
            time.sleep(0.005)
        raw_resp = resp_file.read_bytes()
        resp_file.unlink()
        header_block, _, body = raw_resp.partition(b"\\r\\n\\r\\n")
        status_line = header_block.splitlines()[0].decode("latin1")
        status_code = int(status_line.split()[1])
        return status_code, body


def main() -> int:
    argv = sys.argv[1:]
    if not argv or argv[0] != "compile":
        return 2
    cnl_path = pathlib.Path(argv[1])
    base_url = ""
    target = None
    i = 2
    while i < len(argv):
        if argv[i] == "--url":
            base_url = argv[i + 1]
            i += 2
        elif argv[i] == "--target":
            target = argv[i + 1]
            i += 2
        else:
            i += 1

    if MODE == "drop_typescript" and target == "typescript":
        target = None
    elif MODE == "rewrite_typescript_to_rust" and target == "typescript":
        target = "rust"
    elif MODE == "rewrite_typescript_to_ts" and target == "typescript":
        target = "ts"

    source = cnl_path.read_text(encoding="utf-8")
    body_obj = {{"format": "som.cnl.v1", "source": source}}
    if target is not None:
        body_obj["target"] = target
    raw_body = json.dumps(body_obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    status, resp_bytes = post_compile(base_url, raw_body)
    doc = json.loads(resp_bytes.decode("utf-8"))
    if status == 200:
        sys.stdout.write(json.dumps(doc.get("bundle"), ensure_ascii=False, separators=(",", ":")))
        return 0
    sys.stderr.write(resp_bytes.decode("utf-8"))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
"""


class FakeCompilerServer:
    """Fake compiler server for check_cli.py unit tests."""

    def __init__(self, *, refuse_typescript: bool = False) -> None:
        self.refuse_typescript = refuse_typescript
        self.received_bodies: list[bytes] = []
        self._lock = threading.Lock()
        parent = self

        neg_doc = json.loads((check_cli.CONTRACT / "negative-cases.json").read_text(encoding="utf-8"))
        self.neg_by_source_target: dict[tuple[str, str], dict[str, Any]] = {}
        for case in neg_doc["cases"]:
            if case["form"] == "cnl":
                body = json.loads(case["body"])
                if isinstance(body.get("source"), str) and isinstance(body.get("target"), str):
                    self.neg_by_source_target[(body["source"], body["target"])] = case

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # noqa: N802
                length = int(self.headers.get("Content-Length", "0"))
                raw_body = self.rfile.read(length)
                with parent._lock:
                    parent.received_bodies.append(raw_body)
                req = json.loads(raw_body.decode("utf-8"))
                source = req.get("source", "")
                target = req.get("target")

                if (source, target) in parent.neg_by_source_target:
                    ncase = parent.neg_by_source_target[(source, target)]
                    status = int(ncase["http_status"])
                    diag = ncase["expected"]
                    err_doc = {
                        "status": "error",
                        "code": diag["code"],
                        "detail": diag["cause"],
                        "diagnostics": [diag],
                    }
                    payload = json.dumps(err_doc, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
                    self._send(status, payload)
                    return

                if target == "typescript" and parent.refuse_typescript:
                    err_doc = {
                        "status": "error",
                        "code": "UNSUPPORTED_TARGET",
                        "detail": "TypeScript target refused in test.",
                        "diagnostics": [{"code": "UNSUPPORTED_TARGET"}],
                    }
                    self._send(422, json.dumps(err_doc, separators=(",", ":")).encode("utf-8"))
                    return

                if target not in (None, "python", "rust", "typescript"):
                    err_doc = {
                        "status": "error",
                        "code": "INVALID_SCHEMA",
                        "detail": "Target must be the string python, rust or typescript.",
                        "diagnostics": [{"code": "INVALID_SCHEMA", "prompt_pointer": "/target"}],
                    }
                    self._send(400, json.dumps(err_doc, separators=(",", ":")).encode("utf-8"))
                    return

                effective_target = "python" if target is None else target
                version = "som.dsl-bundle.v1" if effective_target == "python" else "som.dsl-bundle.v2"
                bundle: dict[str, Any] = {
                    "version": version,
                    "prompt": {"version": "som.prompt.v1", "source_sha256": check_cli.sha(source.encode("utf-8"))},
                }
                if effective_target != "python":
                    bundle["target"] = effective_target
                ok_doc = {"status": "ok", "bundle": bundle}
                self._send(200, json.dumps(ok_doc, separators=(",", ":")).encode("utf-8"))

            def _send(self, status: int, payload: bytes) -> None:
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, fmt: str, *args: Any) -> None:
                return

        self._httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        if self._httpd.server_port in check_cli.FORBIDDEN_PORTS:
            self._httpd.server_close()
            self._httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = int(self._httpd.server_port)
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def __enter__(self) -> FakeCompilerServer:
        self._thread.start()
        return self

    def __exit__(self, *exc_info: Any) -> None:
        self._httpd.shutdown()
        self._httpd.server_close()
        self._thread.join(timeout=2.0)


def _write_stub_binary(directory: pathlib.Path, mode: str = "pass") -> pathlib.Path:
    script_path = directory / f"som_stub_{mode}"
    script_path.write_text(
        STUB_CLI_TEMPLATE.format(mode=mode, ipc_dir=str(_IPC_DIR)),
        encoding="utf-8",
    )
    script_path.chmod(script_path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return script_path


class CheckCliTypescriptTests(unittest.TestCase):
    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(_IPC_DIR, ignore_errors=True)

    def test_typescript_case_passes_on_200_and_on_refusal(self) -> None:
        first = json.loads((check_cli.CASES / "index.json").read_text(encoding="utf-8"))["cases"][0]
        source = (check_cli.CASES / first["cnl"]).read_text(encoding="utf-8")
        expected_body = check_cli.cli_body(source, "typescript")

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            stub = _write_stub_binary(tmp_path, mode="pass")

            # 200 bundle path
            with FakeCompilerServer(refuse_typescript=False) as srv:
                row = check_cli.check_typescript(str(stub), srv.url, tmp_path)
                self.assertTrue(row["passed"], msg=row.get("problems"))
                self.assertEqual(row["target"], "typescript")
                self.assertEqual(row["http_status"], 200)
                self.assertEqual(row["exit_code"], 0)
                self.assertEqual(row["request_sha256"], check_cli.sha(expected_body))
                self.assertIn(expected_body, srv.received_bodies)

            # Refusal (exit 2) path
            with FakeCompilerServer(refuse_typescript=True) as srv:
                row_ref = check_cli.check_typescript(str(stub), srv.url, tmp_path)
                self.assertTrue(row_ref["passed"], msg=row_ref.get("problems"))
                self.assertEqual(row_ref["target"], "typescript")
                self.assertEqual(row_ref["http_status"], 422)
                self.assertEqual(row_ref["exit_code"], 2)
                self.assertEqual(row_ref["request_sha256"], check_cli.sha(expected_body))

    def test_cli_dropping_or_rewriting_target_typescript_fails_case(self) -> None:
        for bad_mode in ("drop_typescript", "rewrite_typescript_to_rust", "rewrite_typescript_to_ts"):
            with self.subTest(mode=bad_mode), tempfile.TemporaryDirectory() as tmp, FakeCompilerServer() as srv:
                tmp_path = pathlib.Path(tmp)
                stub = _write_stub_binary(tmp_path, mode=bad_mode)
                row = check_cli.check_typescript(str(stub), srv.url, tmp_path)
                self.assertFalse(row["passed"])
                self.assertTrue(
                    any("CLI request body differs" in p for p in row["problems"]),
                    msg=f"expected request mismatch in problems, got {row['problems']}",
                )

    def test_full_main_report_includes_typescript_case_and_fails_when_dropped(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, FakeCompilerServer() as srv:
            tmp_path = pathlib.Path(tmp)
            good_stub = _write_stub_binary(tmp_path, mode="pass")
            out_good = tmp_path / "cli-good.json"
            with contextlib.redirect_stdout(io.StringIO()):
                rc_good = check_cli.main(
                    ["--binary", str(good_stub), "--base-url", srv.url, "--out", str(out_good)]
                )
            self.assertEqual(rc_good, 0)
            rep_good = json.loads(out_good.read_text(encoding="utf-8"))
            self.assertEqual(rep_good["failed"], [])
            self.assertIn("typescript", rep_good)
            self.assertTrue(rep_good["typescript"]["passed"])
            self.assertEqual(rep_good["rows"][-1]["input"], "cov-v1-types target 'typescript'")
            self.assertEqual(rep_good["rows"][-1]["target"], "typescript")

            bad_stub = _write_stub_binary(tmp_path, mode="drop_typescript")
            out_bad = tmp_path / "cli-bad.json"
            with contextlib.redirect_stdout(io.StringIO()):
                rc_bad = check_cli.main(
                    ["--binary", str(bad_stub), "--base-url", srv.url, "--out", str(out_bad)]
                )
            self.assertEqual(rc_bad, 1)
            rep_bad = json.loads(out_bad.read_text(encoding="utf-8"))
            self.assertFalse(rep_bad["typescript"]["passed"])
            self.assertIn(
                "cov-v1-types target 'typescript' target='typescript'",
                rep_bad["failed"],
            )


if __name__ == "__main__":
    unittest.main()

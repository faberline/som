"""Task-owned M2 fixtures with exact object versions and post-commit faults.

All fault controls stay in this test process. No generated app imports it.
The legacy fixture implementation and its sealed results remain unchanged.
"""

import argparse
import base64
from contextlib import redirect_stdout
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import os
from pathlib import Path
import signal
import threading
from urllib.parse import parse_qs, unquote, urlsplit

import requests
import jwt

from flow_gcs_fixture import VersionedGCS
from local_business_services import Fixture, port


class FaultDatabases(Fixture):
    """Enable command faults only on this task's replica set."""

    def spawn(self, name, args):
        if name == 'mongo':
            args = [*args, '--setParameter', 'enableTestCommands=1']
        return super().spawn(name, args)


class FlowFixture:
    """Reuse real isolated databases and identity mocks behind a local proxy."""

    def __init__(self, root: Path) -> None:
        self.legacy = FaultDatabases(root)
        self.upstream = self.legacy.base
        self.proxy_port = port()
        self.legacy.base = "http://127.0.0.1:" + str(self.proxy_port)
        self.gcs = VersionedGCS()
        self.lock = threading.RLock()
        self.faults: list[dict[str, str]] = []
        self.events: list[dict[str, object]] = []
        self.gates: dict[str, threading.Event] = {}
        self.sequence = 0

    def after_commit(self, provider: str, operation: str, key: str) -> bool:
        """Observe a provider commit before withholding its acknowledgement."""
        with self.lock:
            for index, fault in enumerate(self.faults):
                if (fault.get("provider"), fault.get("operation"), fault.get("key"), fault.get("when")) != (provider, operation, key, "after_commit"):
                    continue
                self.faults.pop(index)
                gate = self.gates.setdefault(fault["gate"], threading.Event()) if "gate" in fault else None
                self.events.append({"provider": provider, "operation": operation, "key": key,
                                    "event": "paused" if gate else "response_lost",
                                    "gate": fault.get("gate")})
                break
            else:
                return False
        if gate is not None:
            gate.wait(timeout=60)
            return False
        return True

    def before_commit(self, provider: str, key: str, body: bytes) -> None:
        """Hold a real conditional create before it reaches provider state."""
        with self.lock:
            for index, fault in enumerate(self.faults):
                if (fault.get("provider"), fault.get("operation"), fault.get("key"), fault.get("when")) != (provider, "put", key, "before_commit"):
                    continue
                self.faults.pop(index)
                gate = self.gates.setdefault(fault["gate"], threading.Event())
                self.events.append({"provider": provider, "operation": "put", "key": key,
                                    "event": "paused_before_commit", "gate": fault["gate"],
                                    "body": base64.b64encode(body).decode()})
                break
            else:
                return
        gate.wait(timeout=60)

    def handler(self) -> type[BaseHTTPRequestHandler]:
        fixture = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args: object) -> None:
                pass

            def send(self, status: int, body: bytes, headers: dict[str, str]) -> None:
                self.send_response(status)
                self.send_header("Content-Length", str(len(body)))
                for key, value in headers.items():
                    if key.lower() not in {"transfer-encoding", "content-length", "connection", "content-encoding"}:
                        self.send_header(key, value)
                self.end_headers()
                if self.command != "HEAD":
                    self.wfile.write(body)

            def json(self, status: int, value: object) -> None:
                self.send(status, json.dumps(value).encode(), {"Content-Type": "application/json"})

            def dropped(self) -> None:
                self.connection.shutdown(2)
                self.connection.close()

            def request(self) -> None:
                parsed = urlsplit(self.path)
                query = parse_qs(parsed.query, keep_blank_values=True)
                body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
                if parsed.path == "/__flow/control" and self.command == "POST":
                    action = json.loads(body)
                    with fixture.lock:
                        for rule in action.get("faults", []):
                            if (rule.get("provider") not in {"s3", "gcs"}
                                    or rule.get("operation") not in {"put", "delete"}
                                    or not isinstance(rule.get("key"), str)
                                    or rule.get("when") not in {"before_commit", "after_commit"}
                                    or (rule.get("when") == "before_commit"
                                        and (rule.get("operation") != "put" or not rule.get("gate")))):
                                return self.json(400, {"error": "invalid fault"})
                        fixture.faults = action.get("faults", fixture.faults)
                        for name in action.get("release", []):
                            fixture.gates.setdefault(name, threading.Event()).set()
                    return self.json(200, {"status": "ok"})
                if parsed.path == "/__flow/events" and self.command == "GET":
                    with fixture.lock, fixture.gcs.lock:
                        return self.json(200, {"events": fixture.events, "gcs": fixture.gcs.events})
                if parsed.path == "/__control" and self.command == "POST":
                    action = json.loads(body)
                    if "bucket" in action:
                        fixture.gcs.bucket(action["bucket"])
                if parsed.path.startswith(("/storage/v1/", "/upload/storage/v1/", "/download/storage/v1/")):
                    if fixture.legacy.faults.get("gcs"):
                        return self.json(503, {"error": {"code": 503, "message": "test failure"}})
                    if self.command == "POST" and parsed.path.startswith("/upload/"):
                        metadata, content = fixture.gcs.upload_data(dict(self.headers), body)
                        fixture.before_commit("gcs", str(metadata["name"]), content)
                    status, content, headers, drop = fixture.gcs.handle(
                        self.command, parsed.path, query, dict(self.headers), body)
                    if 200 <= status < 300 and self.command in {"POST", "DELETE"}:
                        operation = "put" if self.command == "POST" else "delete"
                        key = (json.loads(content)["name"] if self.command == "POST"
                               else unquote(parsed.path.split("/o/", 1)[1]))
                        drop |= fixture.after_commit("gcs", operation, key)
                    if drop:
                        return self.dropped()
                    return self.send(status, content, headers)
                headers = {key: value for key, value in self.headers.items()
                           if key.lower() not in {"host", "content-length", "connection"}}
                if self.command == "PUT" and parsed.path.startswith("/s3/"):
                    parts = parsed.path.removeprefix("/s3/").split("/", 1)
                    if len(parts) == 2:
                        fixture.before_commit("s3", unquote(parts[1]), body)
                response = requests.request(self.command, fixture.upstream + self.path,
                                            data=body, headers=headers, timeout=20,
                                            allow_redirects=False)
                if parsed.path.endswith('accounts:signInWithPassword') and response.status_code == 200:
                    subject = json.loads(body)['email'].split('@', 1)[0]
                    role = fixture.legacy.users.get(subject, {}).get('role')
                    if role in {'editor', 'reader'}:
                        content = response.json()
                        claims = jwt.decode(content['idToken'], options={'verify_signature': False})
                        content['idToken'] = jwt.encode({**claims, 'role': role}, key=None, algorithm='none')
                        return self.json(200, content)
                if parsed.path.startswith("/s3/"):
                    parts = parsed.path.removeprefix("/s3/").split("/", 1)
                    key = unquote(parts[1]) if len(parts) == 2 else ""
                    operation = {"PUT": "put", "DELETE": "delete"}.get(self.command, self.command.lower())
                    with fixture.lock:
                        fixture.sequence += 1
                        fixture.events.append({"sequence": fixture.sequence, "provider": "s3",
                                               "operation": operation, "key": key,
                                               "status": response.status_code,
                                               "version_id": response.headers.get("x-amz-version-id"),
                                               "requested_version": query.get("versionId"),
                                               "body_sha256": hashlib.sha256(body).hexdigest()})
                    if key and operation in {"put", "delete"} and 200 <= response.status_code < 300:
                        if fixture.after_commit("s3", operation, key):
                            return self.dropped()
                self.send(response.status_code, response.content, dict(response.headers))

            do_GET = request
            do_POST = request
            do_PUT = request
            do_DELETE = request
            do_HEAD = request

        return Handler

    def start(self) -> None:
        self.server = ThreadingHTTPServer(("127.0.0.1", self.proxy_port), self.handler())
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        with redirect_stdout(io.StringIO()):
            self.legacy.start()
        path = self.legacy.root / "manifest.json"
        manifest = json.loads(path.read_text())
        manifest.update(kind="m2-local-versioned-protocol-mocks-not-cloud",
                        gcs_versioned=True, post_commit_faults=True,
                        pre_commit_observations=True,
                        mongo_command_faults=True, pid=os.getpid())
        manifest["ports"]["flow_proxy"] = self.proxy_port
        manifest["source_sha256"] = {
            name: hashlib.sha256(Path(__file__).with_name(name).read_bytes()).hexdigest()
            for name in ("local_flow_services.py", "flow_gcs_fixture.py", "local_business_services.py")}
        path.write_text(json.dumps(manifest, indent=2) + "\n")
        print(json.dumps({"status": "ready", "manifest": str(path)}), flush=True)

    def close(self) -> None:
        for gate in self.gates.values():
            gate.set()
        if hasattr(self, "server"):
            self.server.shutdown()
            self.server.server_close()
            self.thread.join(timeout=5)
        self.legacy.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    args = parser.parse_args()
    fixture = FlowFixture(args.root)
    stopped = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_args: stopped.set())
    signal.signal(signal.SIGINT, lambda *_args: stopped.set())
    try:
        fixture.start()
        stopped.wait()
    finally:
        fixture.close()


if __name__ == "__main__":
    main()

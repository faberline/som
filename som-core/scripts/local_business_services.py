"""Task-owned MongoDB, Redis, S3 emulator and local identity/GCS protocol mocks.

These are test doubles, not evidence about production cloud behavior. The
control API exists only in this test process. Generated applications have no
control endpoint. All listeners bind to loopback and all state is disposable.
"""

import argparse
import base64
import hashlib
import json
import os
import secrets
import signal
import socket
import subprocess
import threading
import time
from email.parser import BytesParser
from email.policy import default
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlencode, urlsplit

import boto3
import google_crc32c
import jwt
import pymongo
import redis
import requests
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa


def port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class Fixture:
    def __init__(self, root):
        self.root = root
        root.mkdir(parents=True, exist_ok=False)
        os.chmod(root, 0o700)
        self.ports = {k: port() for k in ("mongo", "redis", "s3", "protocol")}
        self.processes = {}
        self.logs = []
        self.lock = threading.Lock()
        self.faults = {}
        self.codes = {}
        self.buckets = {}
        self.users = {}
        self.next_user = "editor"
        self.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        private = root / "private.pem"
        private.write_bytes(self.key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
        os.chmod(private, 0o600)
        (root / "public.pem").write_bytes(self.key.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo))
        self.base = f"http://127.0.0.1:{self.ports['protocol']}"

    def spawn(self, name, args):
        with self.lock:
            existing = self.processes.get(name)
            if existing and existing.poll() is None:
                return
            log = (self.root / (name + ".log")).open("a")
            self.logs.append(log)
            self.processes[name] = subprocess.Popen(args, stdout=log, stderr=log)

    def start_redis(self):
        self.spawn("redis", ["redis-server", "--bind", "127.0.0.1", "--port", str(self.ports["redis"]), "--save", "", "--appendonly", "no", "--dir", str(self.root)])
        for _ in range(100):
            try:
                if redis.Redis(port=self.ports["redis"]).ping():
                    return
            except redis.RedisError:
                time.sleep(.1)
        raise RuntimeError("isolated Redis did not start")

    def stop_one(self, name):
        process = self.processes.get(name)
        if process and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill(); process.wait()

    def start(self):
        self.start_redis()
        (self.root / "mongo").mkdir()
        self.mongo_command = ["mongod", "--bind_ip", "127.0.0.1", "--port", str(self.ports["mongo"]), "--dbpath", str(self.root / "mongo"), "--replSet", "som", "--nounixsocket"]
        self.spawn("mongo", self.mongo_command)
        mongo_direct = f"mongodb://127.0.0.1:{self.ports['mongo']}/?directConnection=true"
        client = pymongo.MongoClient(mongo_direct, serverSelectionTimeoutMS=1000)
        for _ in range(100):
            try:
                client.admin.command("ping"); break
            except pymongo.errors.PyMongoError:
                time.sleep(.1)
        client.admin.command("replSetInitiate", {"_id": "som", "members": [{"_id": 0, "host": f"127.0.0.1:{self.ports['mongo']}"}]})
        for _ in range(200):
            if client.admin.command("hello").get("isWritablePrimary"):
                break
            time.sleep(.1)
        else:
            raise RuntimeError("isolated Mongo replica set did not become primary")
        client.close()
        self.spawn("s3", [str(Path(os.sys.executable).with_name("moto_server")), "-H", "127.0.0.1", "-p", str(self.ports["s3"])])
        for _ in range(100):
            try:
                requests.get(f"http://127.0.0.1:{self.ports['s3']}", timeout=1); break
            except requests.RequestException:
                time.sleep(.1)
        fixture = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def send(self, status, body, headers=None):
                if isinstance(body, dict):
                    body = json.dumps(body).encode()
                    headers = {"Content-Type": "application/json", **(headers or {})}
                if isinstance(body, str):
                    body = body.encode()
                self.send_response(status)
                self.send_header("Content-Length", str(len(body)))
                for key, value in (headers or {}).items():
                    self.send_header(key, value)
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self): self.handle_request()
            def do_POST(self): self.handle_request()
            def do_PUT(self): self.handle_request()
            def do_DELETE(self): self.handle_request()
            def do_HEAD(self): self.handle_request()

            def handle_request(self):
                parsed = urlsplit(self.path)
                path, query = parsed.path, parse_qs(parsed.query)
                body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
                if path == "/__control" and self.command == "POST":
                    action = json.loads(body)
                    if "faults" in action:
                        fixture.faults = action["faults"]
                    if "next_user" in action:
                        fixture.next_user = action["next_user"]
                    if "users" in action:
                        fixture.users.update(action["users"])
                    if action.get("redis") == "stop":
                        fixture.stop_one("redis")
                    if action.get("redis") == "start":
                        fixture.start_redis()
                    if action.get("mongo") == "stop":
                        fixture.stop_one("mongo")
                    if action.get("mongo") == "start":
                        fixture.spawn("mongo", fixture.mongo_command)
                        probe = pymongo.MongoClient(f"mongodb://127.0.0.1:{fixture.ports['mongo']}/?replicaSet=som", serverSelectionTimeoutMS=1000)
                        try:
                            for _ in range(20):
                                try:
                                    probe.admin.command("ping")
                                    break
                                except pymongo.errors.PyMongoError:
                                    time.sleep(.1)
                            else:
                                return self.send(503, {"error": "mongo restart failed"})
                        finally:
                            probe.close()
                    if "bucket" in action:
                        fixture.buckets.setdefault(action["bucket"], {})
                    return self.send(200, {"status": "ok"})
                if path == "/healthz":
                    return self.send(200, {"status": "ok", "kind": "test-protocol-mocks"})
                if path.startswith("/s3"):
                    if fixture.faults.get("s3"):
                        return self.send(503, "<Error><Code>ServiceUnavailable</Code></Error>", {"Content-Type": "application/xml"})
                    target = f"http://127.0.0.1:{fixture.ports['s3']}" + self.path[3:]
                    headers = {k: v for k, v in self.headers.items() if k.lower() not in {"host", "content-length", "connection"}}
                    response = requests.request(self.command, target, data=body, headers=headers, timeout=10)
                    headers = {k: v for k, v in response.headers.items() if k.lower() not in {"transfer-encoding", "content-length", "connection", "content-encoding"}}
                    return self.send(response.status_code, response.content, headers)
                if path == "/jwks":
                    if fixture.faults.get("oidc"):
                        return self.send(503, {"error": "unavailable"})
                    key = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(fixture.key.public_key()))
                    return self.send(200, {"keys": [key | {"kid": "local-key", "use": "sig", "alg": "RS256"}]})
                if path == "/authorize":
                    required = {"client_id", "redirect_uri", "state", "nonce", "code_challenge", "code_challenge_method"}
                    if not required.issubset(query) or query["code_challenge_method"] != ["S256"] or urlsplit(query["redirect_uri"][0]).hostname != "127.0.0.1":
                        return self.send(400, {"error": "invalid_request"})
                    code = secrets.token_urlsafe(24)
                    fixture.codes[code] = {k: v[0] for k, v in query.items()} | {"sub": fixture.next_user}
                    return self.send(302, b"", {"Location": query["redirect_uri"][0] + "?" + urlencode({"code": code, "state": query["state"][0]})})
                if path == "/token":
                    params = {k: v[0] for k, v in parse_qs(body.decode()).items()}
                    saved = fixture.codes.pop(params.get("code", ""), None)
                    challenge = base64.urlsafe_b64encode(hashlib.sha256(params.get("code_verifier", "").encode()).digest()).decode().rstrip("=")
                    if not saved or params.get("client_secret") != "local-test-secret" or params.get("client_id") != saved["client_id"] or params.get("redirect_uri") != saved["redirect_uri"] or challenge != saved["code_challenge"]:
                        return self.send(400, {"error": "invalid_grant"})
                    now = int(time.time())
                    claims = {"iss": fixture.base, "aud": saved["client_id"], "sub": saved["sub"], "iat": now, "exp": now + 3600, "nonce": saved["nonce"]}
                    if fixture.faults.get("nonce"):
                        claims["nonce"] = "wrong"
                    encoded = jwt.encode(claims, fixture.key, algorithm="RS256", headers={"kid": "local-key"})
                    return self.send(200, {"access_token": "local-access", "token_type": "Bearer", "expires_in": 3600, "id_token": encoded})
                if path.endswith("accounts:lookup"):
                    uid = json.loads(body)["localId"][0]
                    user = fixture.users.get(uid, {})
                    return self.send(200, {"users": [{"localId": uid, "validSince": user.get("validSince", "0"), "disabled": user.get("disabled", False)}]})
                if path.endswith("accounts:signInWithPassword"):
                    now = int(time.time()); user = json.loads(body).get("email", "editor").split("@")[0]
                    claims = {"iss": "https://securetoken.google.com/demo-som", "aud": "demo-som", "sub": user, "user_id": user, "iat": now, "auth_time": now, "exp": now+3600, "role": "editor" if user == "editor" else "reader", "firebase": {"sign_in_provider": "password"}}
                    return self.send(200, {"idToken": jwt.encode(claims, key=None, algorithm="none"), "localId": user, "expiresIn": "3600"})
                if path.startswith(("/storage/v1/", "/upload/storage/v1/", "/download/storage/v1/")):
                    return self.gcs(path, query, body)
                return self.send(404, {"error": "no test route"})

            def gcs(self, path, query, body):
                if fixture.faults.get("gcs"):
                    return self.send(503, {"error": {"code": 503, "message": "test failure"}})
                parts = path.split("/b/", 1)
                if len(parts) != 2:
                    return self.send(400, {"error": {"code": 400}})
                bucket, separator, rest = parts[1].partition("/o")
                objects = fixture.buckets.get(unquote(bucket))
                if objects is None:
                    return self.send(404, {"error": {"code": 404, "message": "bucket missing"}})
                if not separator:
                    return self.send(200, {"name": bucket, "kind": "storage#bucket"})
                if self.command == "POST" and path.startswith("/upload/"):
                    message = BytesParser(policy=default).parsebytes(b"Content-Type: " + self.headers["Content-Type"].encode() + b"\r\nMIME-Version: 1.0\r\n\r\n" + body)
                    chunks = list(message.iter_parts())
                    meta = json.loads(chunks[0].get_payload(decode=True)); data = chunks[1].get_payload(decode=True)
                    key = meta["name"]
                    with fixture.lock:
                        if query.get("ifGenerationMatch") == ["0"] and key in objects:
                            return self.send(412, {"error": {"code": 412, "message": "already exists"}})
                        description = {"kind": "storage#object", "name": key, "bucket": bucket, "generation": "1", "metageneration": "1", "size": str(len(data)), "contentType": "application/octet-stream", "md5Hash": base64.b64encode(hashlib.md5(data).digest()).decode(), "crc32c": base64.b64encode(google_crc32c.value(data).to_bytes(4, "big")).decode(), "metadata": meta.get("metadata", {})}
                        objects[key] = (data, description)
                    return self.send(200, description)
                key = unquote(rest.lstrip("/"))
                with fixture.lock:
                    item = objects.get(key)
                    if not item:
                        return self.send(404, {"error": {"code": 404, "message": "object missing"}})
                    if self.command == "DELETE":
                        del objects[key]
                        return self.send(204, b"")
                data, description = item
                if query.get("alt") == ["media"] or path.startswith("/download/"):
                    headers = {"Content-Type": "application/octet-stream", "x-goog-hash": "crc32c=" + description["crc32c"]}
                    if "Range" in self.headers:
                        start, end = self.headers["Range"].removeprefix("bytes=").split("-")
                        start = int(start or 0); end = min(int(end) if end else len(data)-1, len(data)-1)
                        headers["Content-Range"] = f"bytes {start}-{end}/{len(data)}"
                        return self.send(206, data[start:end+1], headers)
                    return self.send(200, data, headers)
                return self.send(200, description)

        self.server = ThreadingHTTPServer(("127.0.0.1", self.ports["protocol"]), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        manifest = {"kind": "local-protocol-mocks-not-cloud", "root": str(self.root), "pid": os.getpid(), "ports": self.ports, "base_url": self.base, "mongodb_url": f"mongodb://127.0.0.1:{self.ports['mongo']}/?replicaSet=som", "redis_url": f"redis://127.0.0.1:{self.ports['redis']}/0", "s3_url": self.base + "/s3", "public_key": str(self.root / "public.pem"), "private_key": str(self.root / "private.pem")}
        (self.root / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
        print(json.dumps({"status": "ready", "manifest": str(self.root / "manifest.json")}), flush=True)

    def close(self):
        if hasattr(self, "server"):
            self.server.shutdown()
        for name in self.processes:
            self.stop_one(name)
        for log in self.logs:
            log.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    args = parser.parse_args()
    fixture = Fixture(args.root)
    stopped = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stopped.set())
    signal.signal(signal.SIGINT, lambda *_: stopped.set())
    try:
        fixture.start()
        stopped.wait()
    finally:
        fixture.close()


if __name__ == "__main__":
    main()

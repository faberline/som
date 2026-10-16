# Adapted by som-rs/behavior/phase2/adapt.py from som-core/scripts/verify_related.py (sha256 ec9cc11116c2ceb46dac687da9291faefd7c56aefbb282a5e088e1b5272b1ba1). Do not edit.
"""Independent HTTP and PostgreSQL checks for an installed related application.

This verifier reads a requirement and a candidate directory. It does not read
blocks, DSL, reference output, or training data. Every database is disposable.
"""

import argparse
import concurrent.futures
import json
import os
import socket
import subprocess
import tempfile
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import httpx
from phase2_host import host_for


def run(args, cwd=None, env=None, timeout=180):
    result = subprocess.run(args, cwd=cwd, env=env, text=True, capture_output=True, timeout=timeout, check=False)
    if result.returncode:
        raise RuntimeError(f"{args[0]} exited {result.returncode}\n{result.stdout}\n{result.stderr}")
    return result.stdout


def payload(resource, serial, optional=False):
    values = {}
    for f in resource["fields"]:
        if f["required"] or optional:
            values[f["name"]] = {"str": f["name"] + "_" + str(serial), "int": serial, "float": serial + 0.25, "bool": bool(serial % 2), "date": "2030-01-02"}[f["type"]]
    return values


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class Server:
    def __init__(self, root, package, env):
        self.root, self.package, self.env = root, package, env
        self.port = free_port()
        self.url = f"http://127.0.0.1:{self.port}"
        self.process = None
        self.log = tempfile.TemporaryFile(mode="w+")  # noqa: SIM115 -- closed by verify's finally

    def start(self):
        self.process = subprocess.Popen(host_for(self.root).serve_command(self.package, self.port), cwd=self.root, env=self.env, stdout=self.log, stderr=self.log)
        last_health = "no response"
        for _ in range(150):
            if self.process.poll() is not None:
                self.log.seek(0)
                raise RuntimeError(self.log.read())
            try:
                health = httpx.get(self.url + "/healthz", timeout=1)
                last_health = f"HTTP {health.status_code}: {health.text[:2000]}"
                if health.status_code == 200:
                    return
            except httpx.HTTPError:
                pass
            time.sleep(0.1)
        self.log.seek(0)
        raise RuntimeError(f"app startup timed out; health: {last_health}\n{self.log.read()[-12000:]}")

    def stop(self):
        if self.process is not None and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()


def verify(prompt, root, fixture=None):
    from business_database_checks import Mongo, Postgres
    root = root.resolve()
    dbname = "som_test_" + uuid.uuid4().hex[:16]
    caps = prompt.get("capabilities", {})
    backend = (Mongo if caps.get("database") == "mongodb" else Postgres)(prompt, dbname, fixture)
    inherited = {key: value for key, value in os.environ.items()
                 if not key.startswith("AWS_") and key != "GOOGLE_APPLICATION_CREDENTIALS"}
    env = inherited | backend.env | {"UV_CACHE_DIR": "/private/tmp/som-uv-cache", "UV_PYTHON_DOWNLOADS": "never"}
    providers = None
    if caps:
        if fixture is None:
            raise ValueError("business checks require a task-owned local fixture manifest")
        from business_test_support import Providers
        providers = Providers(fixture, prompt)
        env.update(providers.environment())
    report = {"package": prompt["package"], "checks": [], "status": "running"}

    def done(name):
        report["checks"].append(name)

    backend.create(run)
    server = None
    try:
        run(["uv", "sync", "--python", "3.12"], cwd=root, env=env)
        done("empty_directory_uv_sync")
        python = str(root / ".venv/bin/python")
        server = Server(root, prompt["package"], env)
        if providers:
            env["GOOGLE_REDIRECT_URI"] = server.url + "/auth/google/callback"
        missing = env.copy(); missing.pop(backend.config_key)
        probe_source = f"from fastapi.testclient import TestClient\nfrom {prompt['package']}.app import app\nwith TestClient(app):\n    pass\n"
        probe = subprocess.run([python, "-c", probe_source], cwd=root, env=missing, capture_output=True, text=True, check=False)
        assert probe.returncode != 0 and backend.config_key in probe.stderr, probe.stderr
        done("missing_database_configuration")
        run(backend.migration_command(python, True), cwd=root, env=env)
        done("migration_upgrade")
        parent, child = prompt["resources"]["parent"], prompt["resources"]["child"]
        fk = prompt["relation"]["foreign_key"]
        backend.inspect()
        done("database_indexes_and_storage_constraints")
        snapshot = backend.snapshot
        server.start()
        proute = parent["path"]
        batch_route = proute + "/with-children"
        with httpx.Client(base_url=server.url, timeout=15) as client:
            if providers:
                providers.login(client)
                providers.auth_checks(client, proute, payload(parent, 777), env, done)
                env["TEST_AUTH_HEADERS"] = json.dumps({key: value for key, value in client.headers.items() if key.lower() in {"authorization", "x-csrf-token"}})
                env["TEST_AUTH_COOKIES"] = json.dumps(dict(client.cookies))
            run([python, "-m", "unittest", "discover", "-s", "tests", "-v"], cwd=root, env=env)
            done("generated_unittest")
            def request(method, route, expected, **kwargs):
                response = client.request(method, route, **kwargs)
                assert response.status_code == expected, (method, route, expected, response.status_code, response.text)
                return response.json() if response.content else None

            p1 = request("POST", proute, 201, json=payload(parent, 101))
            p2 = request("POST", proute, 201, json=payload(parent, 102))
            pi = proute + "/" + str(p1["id"])
            cr = pi + child["path"]
            other = proute + "/" + str(p2["id"]) + child["path"]
            c1 = request("POST", cr, 201, json=payload(child, 201))
            ci = cr + "/" + str(c1["id"])
            assert c1[fk] == p1["id"]
            request("GET", ci, 200)
            assert request("GET", other, 200) == []
            for method, kwargs in (("GET", {}), ("PATCH", {"json": {}}), ("DELETE", {})):
                request(method, other + "/" + str(c1["id"]), 404, **kwargs)
            absent = proute + "/99999999"
            for route in (absent, absent + child["path"], absent + child["path"] + "/1"):
                request("GET", route, 404)
            request("POST", absent + child["path"], 404, json=payload(child, 290))
            request("DELETE", pi, 409)
            done("parent_child_crud_cross_parent_isolation_and_delete_conflict")

            for resource, route, item, created in ((parent, proute, pi, p1), (child, cr, ci, c1)):
                assert created["created_at"] and created["updated_at"]
                assert request("GET", item, 200) == created
                assert abs((datetime.now(timezone.utc) - datetime.fromisoformat(created["created_at"])).total_seconds()) < 120
                for f in resource["fields"]:
                    name = f["name"]
                    if not f["required"]:
                        assert created[name] == f["default"]
                    if f["required"]:
                        body = payload(resource, 300); body.pop(name)
                        request("POST", route, 422, json=body)
                    if f["required"] or f.get("default") is False:
                        request("PATCH", item, 422, json={name: None})
                for key, value in (("id", 9), ("created_at", "2030-01-01T00:00:00Z"), ("updated_at", "2030-01-01T00:00:00Z"), ("unknown_field", "x")):
                    request("PATCH", item, 422, json={key: value})
                q = resource["query"]
                boolean, day = q["filter_field"], q["date_field"]
                if boolean:
                    before = request("GET", item, 200)
                    updated = request("PATCH", item, 200, json={boolean: True})
                    assert updated["created_at"] == before["created_at"]
                    assert updated["updated_at"] >= before["updated_at"]
                    assert all(updated[k] == v for k, v in before.items() if k not in {boolean, "updated_at"})
                    assert any(row["id"] == created["id"] for row in request("GET", route, 200, params={boolean: "true"}))
                    assert all(row["id"] != created["id"] for row in request("GET", route, 200, params={boolean: "false"}))
                if day:
                    assert all(row["id"] != created["id"] for row in request("GET", route, 200, params={"before": "2030-01-03"}))
                    request("PATCH", item, 200, json={day: "2030-01-02"})
                    for boundary, matched in (("2030-01-01", False), ("2030-01-02", False), ("2030-01-03", True)):
                        rows = request("GET", route, 200, params={"before": boundary, boolean: "true"})
                        assert any(row["id"] == created["id"] for row in rows) == matched
                    request("GET", route, 422, params={"before": "invalid"})
                    request("PATCH", item, 422, json={day: "2030-02-31"})
                    request("PATCH", item, 200, json={day: None})
                for f in resource["fields"]:
                    if not f["required"] and f.get("default") is None:
                        value = payload(resource, 900, True)[f["name"]]
                        request("PATCH", item, 200, json={f["name"]: value})
                        assert request("PATCH", item, 200, json={f["name"]: None})[f["name"]] is None
                    if f["type"] in {"int", "float"} and not f["unique"]:
                        assert request("PATCH", item, 200, json={f["name"]: 0})[f["name"]] == 0
                        request("PATCH", item, 422, json={f["name"]: "not-a-number"})
                for bad in ({"limit": 0}, {"limit": q["max_limit"] + 1}, {"offset": -1}):
                    request("GET", route, 422, params=bad)
            request("PATCH", ci, 422, json={fk: p2["id"]})
            done("defaults_types_nullability_readonly_fields_partial_patch_dates_and_filters")

            for i in range(22):
                request("POST", cr, 201, json=payload(child, 1000+i))
            page = request("GET", cr, 200)
            assert len(page) == 20
            all_rows = request("GET", cr, 200, params={"limit": child["query"]["max_limit"]})
            assert [r["id"] for r in all_rows] == sorted(r["id"] for r in all_rows)
            assert request("GET", cr, 200, params={"limit": 1, "offset": 1})[0]["id"] == page[1]["id"]
            done("default_page_limit_offset_and_id_order")
            start = snapshot()
            for count in (0, 21):
                request("POST", batch_route, 422, json={"parent": payload(parent, 4000), "children": [payload(child, 4000+i) for i in range(count)]})
                assert snapshot() == start
            required = next((f["name"] for f in child["fields"] if f["required"]), None)
            invalid = payload(child, 4010)
            if required:
                invalid.pop(required)
            else:
                invalid["unexpected_field"] = True
            request("POST", batch_route, 422, json={"parent": payload(parent, 4010), "children": [payload(child, 4011), invalid]})
            assert snapshot() == start
            successful = request("POST", batch_route, 201, json={"parent": payload(parent, 5000), "children": [payload(child, 5000+i) for i in range(20)]})
            assert len(successful["children"]) == 20
            assert all(c[fk] == successful["parent"]["id"] for c in successful["children"])
            done("batch_one_transaction_success_and_input_boundaries")
            unique = next((f for f in child["fields"] if f["unique"]), None)
            if unique:
                start = snapshot()
                a, b = payload(child, 6001, True), payload(child, 6002, True)
                b[unique["name"]] = a[unique["name"]]
                request("POST", batch_route, 409, json={"parent": payload(parent, 6000), "children": [a, b]})
                assert snapshot() == start
                # The next request must succeed after the failed transaction.
                request("POST", cr, 201, json=payload(child, 6010, True))
                done("late_database_unique_conflict_rolls_back_parent_and_earlier_child_then_recovers")

            for i in range(5):
                racing = request("POST", proute, 201, json=payload(parent, 7000+i))
                path = proute + "/" + str(racing["id"])
                with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
                    delete = pool.submit(client.delete, path)
                    create = pool.submit(client.post, path + child["path"], json=payload(child, 7000+i))
                    codes = (delete.result().status_code, create.result().status_code)
                assert codes in {(204, 404), (409, 201)}, codes
            backend.no_orphans()
            done("concurrent_parent_delete_and_child_create_no_orphans")
            if providers:
                from verify_business_workflows import check_workflows
                check_workflows(prompt, client, providers, snapshot, done)
            server.stop(); server.start()
            request("GET", ci, 200)
            done("real_process_restart_persists_rows")
            for row in request("GET", cr, 200, params={"limit": child["query"]["max_limit"]}):
                request("DELETE", cr + "/" + str(row["id"]), 204)
            while request("GET", cr, 200):
                for row in request("GET", cr, 200):
                    request("DELETE", cr + "/" + str(row["id"]), 204)
            request("DELETE", pi, 204)
            request("GET", pi, 404)
            done("delete_children_then_parent")
        server.stop()
        run(backend.migration_command(python, False), cwd=root, env=env)
        backend.absent()
        run(backend.migration_command(python, True), cwd=root, env=env)
        assert snapshot() == (0, 0)
        done("migration_downgrade_and_upgrade_again")
        # A reachable app with an unavailable DB must give a failed health response.
        backend.unavailable_health(run, python, root, env, server, fixture)
        done("unavailable_database_health_503")
        report["status"] = "passed"
        return report
    finally:
        if server:
            server.stop()
            server.log.close()
        backend.close(run)
        if providers:
            providers.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--prompt", type=Path, required=True)
    parser.add_argument("--candidate-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--fixture", type=Path)
    args = parser.parse_args()
    result = verify(json.loads(args.prompt.read_text()), args.candidate_dir, json.loads(args.fixture.read_text()) if args.fixture else None)
    text = json.dumps(result, indent=2) + "\n"
    if args.output:
        args.output.write_text(text)
    print(text)


if __name__ == "__main__":
    main()

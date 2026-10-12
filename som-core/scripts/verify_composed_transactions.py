"""Transaction evidence for variable compositions, including no unique fields."""

import argparse
import fcntl
import hashlib
import json
import os
import time
import traceback
import uuid
from pathlib import Path

import httpx
import sqlalchemy as sa
from business_database_checks import Mongo, Postgres
from business_test_support import Providers
from verify_related import Server, payload, run


def source_hashes(root):
    excluded = {".venv", ".git", "__pycache__", ".pytest_cache", "run-logs", "check-results", "som-results"}
    return {str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(root.rglob("*"))
            if path.is_file() and path.suffix in {".py", ".toml", ".ini", ".md"}
            and not excluded.intersection(path.relative_to(root).parts)}


def assert_one_transaction(events, parent_table, child_table, response):
    expected = {(parent_table, response["parent"]["id"])} | {
        (child_table, child["id"]) for child in response["children"]}
    inserted = {(event["table"], event["id"]) for event in events if event["operation"] == "insert"}
    assert inserted == expected, {"expected": sorted(expected), "observed": sorted(inserted)}
    identities = {event["transaction"] for event in events}
    assert None not in identities and len(identities) == 1, {"transactions": sorted(map(str, identities))}
    return {"transaction": str(next(iter(identities))), "events": events}


from audit_business_transactions import PostgresObserver, MongoObserver as OriginalMongoObserver


class MongoObserver(OriginalMongoObserver):
    def install_failure(self, body):
        last = self.db[self.child].find_one(sort=[('_id', -1)])
        watermark = last['_id'] if last else 0
        # Only future rows enter this private index. All future rows omit its
        # key. Thus one child is written before the next child conflicts.
        self.db[self.child].create_index(
            '_som_eval_duplicate_guard', name='_som_eval_late_unique',
            unique=True, partialFilterExpression={'_id': {'$gt': watermark}})
        self.profile_started = self.db.command('hello')['localTime']
        self.db.command('profile', 2)


def verify_transactions(prompt, root, fixture):
    root = root.resolve()
    original = source_hashes(root)
    dbname = "som_test_tx_" + uuid.uuid4().hex[:16]
    mongo = prompt["capabilities"]["database"] == "mongodb"
    backend = (Mongo if mongo else Postgres)(prompt, dbname, fixture)
    providers = Providers(fixture, prompt)
    inherited = {key: value for key, value in os.environ.items()
                 if not key.startswith("AWS_") and key != "GOOGLE_APPLICATION_CREDENTIALS"}
    env = inherited | backend.env | {"UV_CACHE_DIR": "/private/tmp/som-uv-cache", "UV_PYTHON_DOWNLOADS": "never"}
    report = {"package": prompt["package"], "database": "mongodb" if mongo else "postgresql", "source_sha256": original, "checks": []}
    backend.create(run)
    server = None
    try:
        env.update(providers.environment())
        run(["uv", "sync", "--python", "3.12"], cwd=root, env=env)
        python = str(root / ".venv/bin/python")
        server = Server(root, prompt["package"], env)
        env["GOOGLE_REDIRECT_URI"] = server.url + "/auth/google/callback"
        run(backend.migration_command(python, True), cwd=root, env=env)
        backend.inspect()
        observer = (MongoObserver if mongo else PostgresObserver)(backend)
        server.start()
        parent, child = (prompt["resources"][role] for role in ("parent", "child"))
        route = parent["path"] + "/with-children"

        def body(serial):
            return {"parent": payload(parent, serial, True), "children": [payload(child, serial + i, True) for i in (1, 2)]}

        with httpx.Client(base_url=server.url, timeout=15) as client:
            providers.login(client)

            def create(value):
                response = client.post(route, json=value)
                assert response.status_code == 201, (response.status_code, response.text)
                return response.json()

            report["success"] = observer.capture_success(lambda: create(body(81000)))
            report["checks"].append("successful_parent_and_children_share_one_database_transaction")
            before = backend.snapshot()
            failing = body(82000)
            observer.install_failure(failing)
            try:
                response = client.post(route, json=failing)
                assert response.status_code == 409, (response.status_code, response.text)
                report["late_failure"] = observer.late_evidence()
                assert backend.snapshot() == before, {"before": before, "after": backend.snapshot()}
                report["checks"].append("forced_database_conflict_after_parent_and_child_writes_rolls_back_all_rows")
            finally:
                observer.remove_failure()
            report["recovery"] = observer.capture_success(lambda: create(failing))
            after = backend.snapshot()
            assert after == (before[0] + 1, before[1] + 2), {"before": before, "after": after}
            report["checks"].append("same_valid_payload_succeeds_after_removing_database_fault")
        assert source_hashes(root) == original, "product source changed during supplemental audit"
        report["status"] = "passed"
        return report
    finally:
        if server:
            server.stop()
            server.log.close()
        try:
            providers.close()
        finally:
            backend.close(run)


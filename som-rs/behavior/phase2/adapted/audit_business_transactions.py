# Adapted by som-rs/behavior/phase2/adapt.py from som-core/scripts/audit_business_transactions.py (sha256 2f8f617bed62963db89066db18772bbd08f9ae31bac54166aee322191eb4122a). Do not edit.
"""Supplemental database evidence for the already declared batch contract.

This audit does not read templates or change product source. It creates its own
database, observes successful writes, and forces a database error after writes.
The frozen paired evaluator remains unchanged.
"""

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
from phase2_host import host_for


def source_hashes(root):
    excluded = {".venv", ".git", "__pycache__", ".pytest_cache", "run-logs", "check-results", "som-results"}
    return {str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(root.rglob("*"))
            if path.is_file() and path.suffix in {".py", ".toml", ".ini", ".md", ".rs", ".sql"}
            and not excluded.intersection(path.relative_to(root).parts)}


def assert_one_transaction(events, parent_table, child_table, response):
    expected = {(parent_table, response["parent"]["id"])} | {
        (child_table, child["id"]) for child in response["children"]}
    inserted = {(event["table"], event["id"]) for event in events if event["operation"] == "insert"}
    assert inserted == expected, {"expected": sorted(expected), "observed": sorted(inserted)}
    identities = {event["transaction"] for event in events}
    assert None not in identities and len(identities) == 1, {"transactions": sorted(map(str, identities))}
    return {"transaction": str(next(iter(identities))), "events": events}


class PostgresObserver:
    def __init__(self, backend):
        self.backend = backend
        self.engine = backend.engine
        self.parent = backend.prompt["resources"]["parent"]["table"]
        self.child = backend.prompt["resources"]["child"]["table"]
        self.quote = self.engine.dialect.identifier_preparer.quote
        with self.engine.begin() as connection:
            connection.exec_driver_sql("CREATE TABLE _som_eval_txlog (ordinal bigserial, resource text, row_id bigint, xid bigint)")
            connection.exec_driver_sql("""CREATE FUNCTION _som_eval_record() RETURNS trigger LANGUAGE plpgsql AS $$
                BEGIN
                    INSERT INTO _som_eval_txlog(resource, row_id, xid) VALUES (TG_TABLE_NAME, NEW.id, txid_current());
                    RETURN NEW;
                END $$""")
            for name in (self.parent, self.child):
                connection.exec_driver_sql(f"CREATE TRIGGER _som_eval_record AFTER INSERT ON {self.quote(name)} FOR EACH ROW EXECUTE FUNCTION _som_eval_record()")

    def capture_success(self, request):
        with self.engine.connect() as connection:
            start = connection.scalar(sa.text("SELECT COALESCE(MAX(ordinal), 0) FROM _som_eval_txlog"))
        response = request()
        with self.engine.connect() as connection:
            rows = connection.execute(sa.text("SELECT resource, row_id, xid FROM _som_eval_txlog WHERE ordinal > :start ORDER BY ordinal"), {"start": start}).all()
        events = [{"table": row[0], "id": row[1], "operation": "insert", "transaction": row[2]} for row in rows]
        return assert_one_transaction(events, self.parent, self.child, response)

    def install_failure(self, body):
        fk = self.quote(self.backend.prompt["relation"]["foreign_key"])
        parent, child = self.quote(self.parent), self.quote(self.child)
        with self.engine.begin() as connection:
            connection.exec_driver_sql("CREATE SEQUENCE _som_eval_late_seq")
            connection.exec_driver_sql(f"""CREATE FUNCTION _som_eval_fail_late() RETURNS trigger LANGUAGE plpgsql AS $$
                BEGIN
                    IF EXISTS (SELECT 1 FROM {parent} WHERE id = NEW.{fk})
                       AND EXISTS (SELECT 1 FROM {child} WHERE {fk} = NEW.{fk}) THEN
                        PERFORM nextval('_som_eval_late_seq');
                        RAISE EXCEPTION 'isolated evaluator late unique conflict' USING ERRCODE = '23505';
                    END IF;
                    RETURN NEW;
                END $$""")
            connection.exec_driver_sql(f"CREATE TRIGGER _som_eval_fail_late BEFORE INSERT ON {child} FOR EACH ROW EXECUTE FUNCTION _som_eval_fail_late()")

    def late_evidence(self):
        with self.engine.connect() as connection:
            called = connection.scalar(sa.text("SELECT is_called FROM _som_eval_late_seq"))
        assert called, "database fault was not reached after parent and first child existed"
        return {"parent_and_first_child_visible_before_error": True, "error_sqlstate": "23505", "nontransactional_sequence_called": True}

    def remove_failure(self):
        with self.engine.begin() as connection:
            connection.exec_driver_sql(f"DROP TRIGGER IF EXISTS _som_eval_fail_late ON {self.quote(self.child)}")
            connection.exec_driver_sql("DROP FUNCTION IF EXISTS _som_eval_fail_late()")
            connection.exec_driver_sql("DROP SEQUENCE IF EXISTS _som_eval_late_seq")


class MongoObserver:
    def __init__(self, backend):
        self.backend = backend
        self.db = backend.db
        self.parent = backend.prompt["resources"]["parent"]["table"]
        self.child = backend.prompt["resources"]["child"]["table"]
        self.profile_started = None

    def capture_success(self, request):
        pipeline = [{"$match": {"ns.coll": {"$in": [self.parent, self.child]},
                                 "operationType": {"$in": ["insert", "update", "replace", "delete"]}}}]
        events = []
        with self.db.watch(pipeline, max_await_time_ms=100) as stream:
            response = request()
            deadline = time.monotonic() + 5
            quiet = 0
            while time.monotonic() < deadline:
                event = stream.try_next()
                if event is None:
                    quiet += 1
                    if events and quiet >= 3:
                        break
                    continue
                quiet = 0
                session = event.get("lsid", {}).get("id")
                transaction = event.get("txnNumber")
                identity = f"{bytes(session).hex()}:{int(transaction)}" if session is not None and transaction is not None else None
                events.append({"table": event["ns"]["coll"], "id": event["documentKey"]["_id"],
                               "operation": event["operationType"], "transaction": identity})
        return assert_one_transaction(events, self.parent, self.child, response)

    def install_failure(self, body):
        field = next(field["name"] for field in self.backend.prompt["resources"]["child"]["fields"] if field["unique"])
        values = [child[field] for child in body["children"]]
        assert len(set(values)) == 2 and all(value is not None for value in values)
        # Both matching documents omit this private field. The first insert can
        # succeed; the second collides in the database despite distinct inputs.
        self.db[self.child].create_index("_som_eval_duplicate_guard", name="_som_eval_late_unique",
                                        unique=True, partialFilterExpression={field: {"$in": values}})
        self.profile_started = self.db.command("hello")["localTime"]
        self.db.command("profile", 2)

    def late_evidence(self):
        self.db.command("profile", 0)
        rows = list(self.db["system.profile"].find({
            "ts": {"$gte": self.profile_started}, "op": "insert",
            "ns": {"$in": [self.db.name + "." + name for name in (self.parent, self.child)]},
        }).sort("ts", 1))
        events = [{"table": row["ns"].split(".", 1)[1], "inserted": row.get("ninserted", 0),
                   "in_transaction": row.get("command", {}).get("autocommit") is False} for row in rows]
        for table in (self.parent, self.child):
            assert sum(event["inserted"] for event in events if event["table"] == table) >= 1, {
                "missing_pre_abort_write": table, "events": events}
        return {"parent_and_first_child_written_before_error": True, "forced_unique_index": "_som_eval_late_unique", "profile_events": events}

    def remove_failure(self):
        self.db.command("profile", 0)
        self.db[self.child].drop_index("_som_eval_late_unique")


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
        host_for(root).prepare(env)
        python = str(root / ".venv/bin/python")
        server = Server(root, prompt["package"], env)
        env["GOOGLE_REDIRECT_URI"] = server.url + "/auth/google/callback"
        run(host_for(root).migration_command(backend, True), cwd=root, env=env)
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


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--prompt", type=Path, required=True)
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--fixture", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    started = time.monotonic()
    result = {"audit_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
    try:
        prompt = json.loads(args.prompt.read_text())
        with Path("/private/tmp/som-business-eval-fixture.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            result.update(verify_transactions(prompt, args.directory, json.loads(args.fixture.read_text())))
    except Exception:  # noqa: BLE001 -- keep failure evidence and return a failing exit code
        result.update(status="failed", error=traceback.format_exc())
    result["seconds"] = round(time.monotonic() - started, 3)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))
    if result["status"] != "passed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()

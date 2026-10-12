"""Independent M2 storage observations, using only the public requirement."""

from contextlib import closing, contextmanager
import sys

import boto3
import sqlalchemy as sa

import business_database_checks
import business_test_support
from business_database_checks import Mongo, Postgres
from business_test_support import Providers


class FlowPostgres(Postgres):
    """Require both the declared schema and private revision/journal fields."""

    def inspect(self):
        self.engine = sa.create_engine(self.url)
        inspector = sa.inspect(self.engine)
        metadata = sa.MetaData()
        foreign_key = self.prompt["relation"]["foreign_key"]
        for role in ("parent", "child"):
            resource = self.prompt["resources"][role]
            columns = {column["name"]: column for column in inspector.get_columns(resource["table"])}
            expected = {"id", "created_at", "updated_at", "_som_revision"}
            expected |= {field["name"] for field in resource["fields"]}
            expected |= {foreign_key} if role == "child" else set()
            assert set(columns) == expected, (expected, set(columns))
            assert not columns["_som_revision"]["nullable"]
            assert columns["_som_revision"]["default"] is not None
            for field in resource["fields"]:
                assert columns[field["name"]]["nullable"] == (not field["required"] and field.get("default") is None)
                if field["unique"]:
                    assert any(constraint["column_names"] == [field["name"]]
                               for constraint in inspector.get_unique_constraints(resource["table"]))
            indexes = [index["column_names"] for index in inspector.get_indexes(resource["table"])]
            prefix = [foreign_key] if role == "child" else []
            if prefix:
                assert prefix + ["id"] in indexes
                assert not columns[foreign_key]["nullable"]
            for field in (resource["query"]["filter_field"], resource["query"]["date_field"]):
                if field:
                    assert prefix + [field, "id"] in indexes
            table = sa.Table(resource["table"], metadata, autoload_with=self.engine)
            setattr(self, role, table)
        keys = inspector.get_foreign_keys(self.child.name)
        assert any(key["constrained_columns"] == [foreign_key]
                   and key["referred_table"] == self.parent.name
                   and key["options"].get("ondelete") == "RESTRICT" for key in keys)
        self.runs = sa.Table("_som_flow_runs", metadata, autoload_with=self.engine)
        self.leases = sa.Table("_som_flow_leases", metadata, autoload_with=self.engine)
        assert any(constraint["column_names"] == ["owner", "flow", "key"]
                   for constraint in inspector.get_unique_constraints(self.runs.name))

    def journal(self, key):
        with self.engine.connect() as connection:
            row = connection.execute(sa.select(self.runs.c.record).where(self.runs.c.key == key)).scalar_one()
        return row

    def rows(self, role):
        with self.engine.connect() as connection:
            table = getattr(self, role)
            return [dict(row) for row in connection.execute(sa.select(table).order_by(table.c.id)).mappings()]

    def expire(self, run_id):
        with self.engine.begin() as connection:
            connection.execute(self.leases.update().where(self.leases.c.run_id == run_id).values(until=0))


class FlowMongo(Mongo):
    """Observe the actual replica-set documents and their execution epoch."""

    def inspect(self):
        super().inspect()
        indexes = list(self.db["_som_flow_runs"].list_indexes())
        assert any(list(index["key"]) == ["owner", "flow", "key"] and index.get("unique")
                   for index in indexes)

    def journal(self, key):
        rows = list(self.db["_som_flow_runs"].find({"key": key}))
        assert len(rows) == 1
        return rows[0]["record"]

    def rows(self, role):
        table = self.prompt["resources"][role]["table"]
        return list(self.db[table].find({}).sort("_id", 1))

    def expire(self, run_id):
        result = self.db["_som_flow_leases"].update_one({"_id": run_id}, {"$set": {"until": 0}})
        assert result.matched_count == 1


class FlowProviders(Providers):
    """Enable S3 versioning through the provider API before app startup."""

    def environment(self):
        if not (self.manifest.get("gcs_versioned") and self.manifest.get("post_commit_faults")):
            raise ValueError("M2 requires version-aware task-owned provider fixtures")
        environment = super().environment()
        self.control(users={'other-flow-owner': {'role': 'editor'}})
        environment['GOOGLE_EDITOR_SUBJECTS'] = '["editor","other-flow-owner"]'
        if self.caps["storage"] == "s3":
            with closing(boto3.client("s3", endpoint_url=self.manifest["s3_url"], region_name="us-east-1",
                                     aws_access_key_id="test", aws_secret_access_key="test")) as client:
                client.put_bucket_versioning(Bucket=self.bucket, VersioningConfiguration={"Status": "Enabled"})
                assert client.get_bucket_versioning(Bucket=self.bucket)["Status"] == "Enabled"
        return environment

    def login_other_owner(self, client):
        """Use a second real fixture identity with the same editor role."""
        if self.caps['auth'] == 'jwt':
            client.headers['Authorization'] = 'Bearer ' + self.jwt(changes={'sub': 'other-flow-owner'})
        else:
            # The parent helper also asserts role == subject. This fixture
            # identity separates those two independent claims.
            if self.caps['auth'] == 'firebase':
                import httpx
                response = httpx.post(self.manifest['base_url'] + '/identitytoolkit.googleapis.com/v1/accounts:signInWithPassword',
                                      json={'email': 'other-flow-owner@example.test', 'password': 'test-only'})
                response.raise_for_status()
                client.headers['Authorization'] = 'Bearer ' + response.json()['idToken']
            else:
                self.control(next_user='other-flow-owner')
                first = client.get('/auth/google/login')
                assert first.status_code == 302, first.text
                provider = client.get(first.headers['location'])
                assert provider.status_code == 302, provider.text
                callback = client.get(provider.headers['location'])
                assert callback.status_code == 200, callback.text
                client.headers['X-CSRF-Token'] = client.get('/auth/me').json()['csrf']
        identity = client.get('/auth/me')
        assert identity.status_code == 200 and identity.json()['sub'] == 'other-flow-owner'
        assert identity.json()['role'] == 'editor', identity.text


@contextmanager
def shared_oracles():
    """Use M2 schema checks in old HTTP oracles; keep all old files unchanged."""
    replacements = []
    for module in [business_database_checks, business_test_support, *list(sys.modules.values())]:
        name = getattr(module, "__name__", "")
        if module not in {business_database_checks, business_test_support} and not name.startswith(("verify_", "audit_business_")):
            continue
        for name, original, candidate in (("Postgres", Postgres, FlowPostgres),
                                           ("Mongo", Mongo, FlowMongo),
                                           ("Providers", Providers, FlowProviders)):
            if getattr(module, name, None) is original:
                replacements.append((module, name, original))
                setattr(module, name, candidate)
    try:
        yield
    finally:
        for module, name, original in reversed(replacements):
            setattr(module, name, original)

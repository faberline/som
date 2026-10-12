"""Database oracles inspect storage directly, independently of generated code."""

import sqlalchemy as sa


class Postgres:
    config_key = "DATABASE_URL"

    def __init__(self, prompt, name, fixture):
        self.prompt, self.name = prompt, name
        self.url = "postgresql+psycopg:///" + name
        self.env = {"DATABASE_URL": self.url, "TEST_DATABASE_URL": self.url}
        self.engine = None

    def create(self, run):
        run(["createdb", self.name])

    def migration_command(self, python, up):
        return [python, "-m", "alembic", "upgrade" if up else "downgrade", "head" if up else "base"]

    def inspect(self):
        p = self.prompt
        self.engine = sa.create_engine(self.url)
        inspector = sa.inspect(self.engine)
        parent, child = p["resources"]["parent"], p["resources"]["child"]
        fk = p["relation"]["foreign_key"]
        for role, resource in (("parent", parent), ("child", child)):
            columns = {c["name"]: c for c in inspector.get_columns(resource["table"])}
            expected = {"id", "created_at", "updated_at"} | {f["name"] for f in resource["fields"]} | ({fk} if role == "child" else set())
            assert set(columns) == expected
            for f in resource["fields"]:
                assert columns[f["name"]]["nullable"] == (not f["required"] and f.get("default") is None)
            indexes = [i["column_names"] for i in inspector.get_indexes(resource["table"])]
            prefix = [fk] if role == "child" else []
            if role == "child":
                assert prefix + ["id"] in indexes
            for field in (resource["query"]["filter_field"], resource["query"]["date_field"]):
                if field:
                    assert prefix + [field, "id"] in indexes
        keys = inspector.get_foreign_keys(child["table"])
        assert any(k["constrained_columns"] == [fk] and k["referred_table"] == parent["table"] and k["options"].get("ondelete") == "RESTRICT" for k in keys)
        assert columns[fk]["nullable"] is False
        metadata = sa.MetaData()
        self.parent = sa.Table(parent["table"], metadata, autoload_with=self.engine)
        self.child = sa.Table(child["table"], metadata, autoload_with=self.engine)

    def snapshot(self):
        with self.engine.connect() as connection:
            return tuple(connection.scalar(sa.select(sa.func.count()).select_from(t)) for t in (self.parent, self.child))

    def no_orphans(self):
        fk = self.prompt["relation"]["foreign_key"]
        with self.engine.connect() as connection:
            assert connection.scalar(sa.select(sa.func.count()).select_from(self.child.outerjoin(self.parent, self.child.c[fk] == self.parent.c.id)).where(self.parent.c.id.is_(None))) == 0

    def absent(self):
        assert all(not sa.inspect(self.engine).has_table(self.prompt["resources"][role]["table"]) for role in ("parent", "child"))

    def unavailable_health(self, run, python, root, env, server, fixture):
        import httpx
        # Disconnect only this verifier-owned database after app startup.
        # Import-time initialization and ASGI lifespan initialization are both valid.
        assert self.name.startswith("som_test_")
        admin = sa.create_engine("postgresql+psycopg:///postgres", isolation_level="AUTOCOMMIT")
        quoted = admin.dialect.identifier_preparer.quote(self.name)
        server.start()
        try:
            with admin.connect() as connection:
                connection.exec_driver_sql(f"ALTER DATABASE {quoted} ALLOW_CONNECTIONS false")
                connection.execute(sa.text("SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = :name AND pid <> pg_backend_pid()"), {"name": self.name})
            assert httpx.get(server.url + "/healthz", timeout=15).status_code == 503
        finally:
            with admin.connect() as connection:
                connection.exec_driver_sql(f"ALTER DATABASE {quoted} ALLOW_CONNECTIONS true")
            admin.dispose()
            server.stop()

    def close(self, run):
        if self.engine:
            self.engine.dispose()
        run(["dropdb", "--if-exists", "--force", self.name])


class Mongo:
    config_key = "MONGODB_URL"

    def __init__(self, prompt, name, fixture):
        from pymongo import MongoClient
        self.prompt, self.name = prompt, name
        self.client = MongoClient(fixture["mongodb_url"], serverSelectionTimeoutMS=2000, tz_aware=True)
        self.db = self.client[name]
        self.env = {"MONGODB_URL": fixture["mongodb_url"], "MONGODB_DATABASE": name, "SOM_TEST_DATABASE_NAME": name, "TEST_DATABASE_URL": fixture["mongodb_url"]}

    def create(self, run):
        assert self.name.startswith("som_test_")
        assert self.name not in self.client.list_database_names()

    def migration_command(self, python, up):
        return [python, "-m", self.prompt["package"] + ".migrate", "upgrade" if up else "downgrade"]

    def inspect(self):
        for role in ("parent", "child"):
            resource = self.prompt["resources"][role]
            indexes = list(self.db[resource["table"]].list_indexes())
            fields = [list(i["key"]) for i in indexes]
            prefix = [self.prompt["relation"]["foreign_key"]] if role == "child" else []
            if prefix:
                assert prefix + ["_id"] in fields
            for name in (resource["query"]["filter_field"], resource["query"]["date_field"]):
                if name:
                    assert prefix + [name, "_id"] in fields
            for field in resource["fields"]:
                if field["unique"]:
                    assert any(i.get("unique") and list(i["key"]) == [field["name"]] for i in indexes)

    def snapshot(self):
        return tuple(self.db[self.prompt["resources"][role]["table"]].count_documents({}) for role in ("parent", "child"))

    def no_orphans(self):
        p, c = (self.db[self.prompt["resources"][role]["table"]] for role in ("parent", "child"))
        for row in c.find({}):
            assert p.find_one({"_id": row[self.prompt["relation"]["foreign_key"]]})

    def absent(self):
        assert all(self.prompt["resources"][role]["table"] not in self.db.list_collection_names() for role in ("parent", "child"))

    def unavailable_health(self, run, python, root, env, server, fixture):
        import httpx
        server.start()
        control = fixture["base_url"] + "/__control"
        httpx.post(control, json={"mongo": "stop"}, timeout=15).raise_for_status()
        try:
            assert httpx.get(server.url + "/healthz", timeout=15).status_code == 503
        finally:
            httpx.post(control, json={"mongo": "start"}, timeout=20).raise_for_status()
            server.stop()

    def close(self, run):
        self.client.drop_database(self.name)
        self.client.close()

"""Check the frozen migration schema through independent SQL inspection."""

import importlib.util
import io
import json
from pathlib import Path
import sys
from types import ModuleType

from alembic.migration import MigrationContext
from alembic.operations import Operations

from som_core import flow_compact
from som_core.v1 import assemble, load_blocks

ROOT = Path(__file__).resolve().parents[2]


def test_frozen_migration_retains_foreign_keys_uniqueness_and_indexes(tmp_path):
    row = json.loads(
        (ROOT / "som-core/data/flow-v7-r10/formal.jsonl").read_text().splitlines()[37]
    )
    prompt = row["prompt"]
    blocks = load_blocks(ROOT / "som-code-python/data/blocks/flow-v7-compact")
    plan = flow_compact.resolve(prompt, flow_compact.reference(prompt, blocks))
    files = {}
    for file in plan["files"]:
        if file["path"].endswith("/schema_v001.py") or file["path"].startswith("alembic/versions/"):
            files.update(assemble({"version": "som.dsl.v2", "files": [file]}, blocks))
    package = prompt["package"]
    parent = ModuleType(package)
    parent.__path__ = [str(tmp_path)]
    schema_path = tmp_path / "schema_v001.py"
    schema_path.write_text(files[package + "/schema_v001.py"])
    spec = importlib.util.spec_from_file_location(package + ".schema_v001", schema_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    old_package = sys.modules.get(package)
    old_schema = sys.modules.get(spec.name)
    sys.modules[package] = parent
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
        migration_source = next(source for path, source in files.items() if path.startswith("alembic/"))
        migration = ModuleType("frozen_migration")
        exec(compile(migration_source, "frozen-migration", "exec"), migration.__dict__)
        output = io.StringIO()
        context = MigrationContext.configure(
            dialect_name="postgresql",
            opts={"as_sql": True, "output_buffer": output},
        )
        with Operations.context(context):
            migration.upgrade()
            migration.downgrade()
            migration.upgrade()
        sql = output.getvalue()
        assert sql.count("REFERENCES parents (id) ON DELETE RESTRICT") == 2
        assert sql.count("UNIQUE (code)") == 2
        assert sql.count("UNIQUE (owner, flow, key)") == 2
        assert "CREATE INDEX ix_children_parent_id ON children (owner_id, id)" in sql
        assert '"_som_revision" INTEGER DEFAULT' not in sql
        assert "_som_revision INTEGER DEFAULT '0' NOT NULL" in sql
        assert sql.index("DROP TABLE _som_flow_leases") < sql.index("DROP TABLE _som_flow_runs")
        assert sql.index("DROP TABLE children") < sql.index("DROP TABLE parents")
        assert ".models import" not in migration_source
    finally:
        for name, prior in ((package, old_package), (spec.name, old_schema)):
            if prior is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = prior

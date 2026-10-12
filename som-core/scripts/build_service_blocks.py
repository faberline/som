"""Freeze approved standalone-service blocks beside the old CRUD blocks."""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def block(
    name: str, params: dict[str, str], template: str, imports: list[str] | None = None
) -> dict:
    return {
        "id": name,
        "version": 1,
        "imports": imports or [],
        "params": params,
        "template": template.strip("\n"),
    }


NEW_BLOCKS = [
    block(
        "service.project",
        {"package": "identifier"},
        """
[project]
name = "{{package}}"
version = "0.1.0"
requires-python = ">=3.12,<3.13"
dependencies = [
    "fastapi==0.118.0",
    "uvicorn==0.53.0",
    "sqlalchemy==2.0.44",
    "psycopg[binary]==3.3.6",
    "alembic==1.20.0",
    "pydantic==2.11.9",
]

[dependency-groups]
dev = ["httpx==0.28.1"]

[tool.uv]
package = false
""",
    ),
    block(
        "service.readme",
        {"package": "identifier"},
        """
# {{package}} service

Set DATABASE_URL to an isolated PostgreSQL URL with the postgresql+psycopg scheme.
The application does not use a local database fallback.

1. Run uv sync.
2. Run uv run alembic upgrade head.
3. Run uv run uvicorn {{package}}.app:app.
4. Open /docs for the API or /healthz for a database connection check.

Set TEST_DATABASE_URL to a separate PostgreSQL database before running
uv run python -m unittest discover -s tests -v.
Run uv run alembic downgrade base to undo the migration.
""",
    ),
    block(
        "service.alembic.ini",
        {},
        """
[alembic]
script_location = alembic
prepend_sys_path = .
""",
    ),
    block(
        "service.alembic.env",
        {"package": "identifier"},
        """
from alembic import context
from sqlalchemy import create_engine

from {{package}}.database import database_url
from {{package}}.models import Base


def run_migrations_offline() -> None:
    context.configure(
        url=database_url(),
        target_metadata=Base.metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = create_engine(database_url(), pool_pre_ping=True)
    try:
        with engine.connect() as connection:
            context.configure(connection=connection, target_metadata=Base.metadata)
            with context.begin_transaction():
                context.run_migrations()
    finally:
        engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
""",
    ),
    block("service.init", {}, '"""Generated standalone API package."""'),
    block(
        "service.database",
        {},
        """
import os
from collections.abc import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session


def database_url() -> str:
    value = os.environ.get("DATABASE_URL", "")
    if not value.startswith("postgresql+psycopg://"):
        raise RuntimeError("DATABASE_URL must be a postgresql+psycopg URL")
    return value


engine = create_engine(database_url(), pool_pre_ping=True)


def get_session() -> Iterator[Session]:
    with Session(engine) as session:
        yield session
""",
    ),
    block(
        "service.app",
        {},
        """
from fastapi import FastAPI, HTTPException
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from .api import create_router
from .database import engine, get_session

app = FastAPI()
app.include_router(create_router(get_session))


@app.get("/healthz")
def healthz() -> dict[str, str]:
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    except SQLAlchemyError as exc:
        raise HTTPException(status_code=503, detail="database unavailable") from exc
    return {"status": "ok"}
""",
    ),
    block(
        "orm.model.service",
        {
            "name": "identifier",
            "table": "identifier",
            "filter_field": "identifier",
            "fields": "fields_v3",
        },
        """
class {{name}}(Base):
    __tablename__ = "{{table}}"
    __table_args__ = (
        sa.Index("ix_{{table}}_{{filter_field}}_id", "{{filter_field}}", "id"),
    )

    id: Mapped[int] = mapped_column(sa.Integer, primary_key=True)
{{#fields}}    {{name}}: Mapped[{{read_type}}] = mapped_column(
        {{sql_type}}, nullable={{nullable}}, unique={{unique}}{{sql_default}}
    )
{{/fields}}    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.func.now(),
        onupdate=sa.func.now(), nullable=False,
    )
""",
        ["from datetime import datetime", "import sqlalchemy as sa"],
    ),
    block(
        "schema.service",
        {"name": "identifier", "fields": "fields_v3"},
        """
class {{name}}Create(BaseModel):
    model_config = ConfigDict(extra="forbid")
{{#fields}}    {{name}}: {{create_type}}{{create_default}}
{{/fields}}

class {{name}}Read(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
{{#fields}}    {{name}}: {{read_type}}
{{/fields}}    created_at: datetime
    updated_at: datetime


class {{name}}Patch(BaseModel):
    model_config = ConfigDict(extra="forbid")
{{#fields}}    {{name}}: {{patch_type}} = None
{{/fields}}
    @model_validator(mode="after")
    def reject_null_for_required_columns(self) -> "{{name}}Patch":
        nullable = {
{{#fields}}            "{{name}}": {{nullable}},
{{/fields}}        }
        for field in self.model_fields_set:
            if getattr(self, field) is None and not nullable[field]:
                raise ValueError(f"{field} cannot be null")
        return self
""",
        [
            "from datetime import datetime",
            "from pydantic import BaseModel, ConfigDict, model_validator",
        ],
    ),
    block(
        "api.list.boolean",
        {
            "name": "identifier",
            "path": "string",
            "filter_field": "identifier",
            "max_limit": "integer",
        },
        """
    @router.get({{path}}, response_model=list[schemas.{{name}}Read])
    def list_items(
        {{filter_field}}: bool | None = None,
        limit: int = Query(default=20, ge=1, le={{max_limit}}),
        offset: int = Query(default=0, ge=0),
        db: Session = Depends(get_session),
    ) -> list[models.{{name}}]:
        stmt = select(models.{{name}})
        if {{filter_field}} is not None:
            stmt = stmt.where(models.{{name}}.{{filter_field}} == {{filter_field}})
        stmt = stmt.order_by(models.{{name}}.id).limit(limit).offset(offset)
        return list(db.scalars(stmt))
""",
        ["from fastapi import Query"],
    ),
    block(
        "test.service",
        {
            "package": "identifier",
            "path": "string",
            "filter_field": "identifier",
            "max_limit": "integer",
            "fields": "fields_v3",
        },
        """
import os
import unittest

from fastapi.testclient import TestClient

os.environ["DATABASE_URL"] = os.environ["TEST_DATABASE_URL"]

from {{package}}.app import app
from {{package}}.database import engine
from {{package}}.models import Base


class ServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        Base.metadata.create_all(engine)
        self.client = TestClient(app)

    def tearDown(self) -> None:
        Base.metadata.drop_all(engine)

    def test_crud_and_database(self) -> None:
        self.assertEqual(self.client.get("/healthz").status_code, 200)
        payload = {
{{#fields}}{{required_entry}}{{/fields}}        }
        created = self.client.post({{path}}, json=payload)
        self.assertEqual(created.status_code, 201, created.text)
        row = created.json()
        self.assertIs(row["{{filter_field}}"], False)
        self.assertIsNotNone(row["created_at"])
        self.assertIsNotNone(row["updated_at"])
        item_id = row["id"]
        self.assertEqual(self.client.get({{path}} + f"/{item_id}").status_code, 200)
        self.assertEqual(len(self.client.get({{path}}).json()), 1)
        updated = self.client.patch(
            {{path}} + f"/{item_id}", json={"{{filter_field}}": True}
        )
        self.assertEqual(updated.status_code, 200, updated.text)
        self.assertIs(updated.json()["{{filter_field}}"], True)
        page = self.client.get(
            {{path}}, params={"{{filter_field}}": True, "limit": 1, "offset": 0}
        )
        self.assertEqual([item["id"] for item in page.json()], [item_id])
        self.assertEqual(
            self.client.patch(
                {{path}} + f"/{item_id}", json={"{{filter_field}}": None}
            ).status_code,
            422,
        )
        self.assertEqual(
            self.client.get({{path}}, params={"limit": 0}).status_code, 422
        )
        self.assertEqual(
            self.client.get({{path}}, params={"limit": {{max_limit}} + 1}).status_code,
            422,
        )
        self.assertEqual(self.client.delete({{path}} + f"/{item_id}").status_code, 204)
        self.assertEqual(self.client.get({{path}} + f"/{item_id}").status_code, 404)
""",
    ),
    block(
        "migration.service",
        {
            "revision": "identifier",
            "down_revision": "nullable_string",
            "table": "identifier",
            "filter_field": "identifier",
            "fields": "fields_v3",
        },
        """
revision = "{{revision}}"
down_revision = {{down_revision}}
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "{{table}}",
        sa.Column("id", sa.Integer(), primary_key=True),
{{#fields}}        sa.Column(
            "{{name}}", {{migration_type}}, nullable={{nullable}},
            unique={{unique}}{{migration_default}},
        ),
{{/fields}}        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False,
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False,
        ),
    )
    op.create_index("ix_{{table}}_{{filter_field}}_id", "{{table}}", ["{{filter_field}}", "id"])


def downgrade() -> None:
    op.drop_index("ix_{{table}}_{{filter_field}}_id", table_name="{{table}}")
    op.drop_table("{{table}}")
""",
        ["from alembic import op", "import sqlalchemy as sa"],
    ),
]


def main() -> None:
    old = ROOT / "som-code-python/data/blocks/v1/blocks.json"
    target = ROOT / "som-code-python/data/blocks/v3/blocks.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    blocks = json.loads(old.read_text()) + NEW_BLOCKS
    ids = [item["id"] for item in blocks]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate block id")
    target.write_text(json.dumps(blocks, indent=2) + "\n")
    print(target)


if __name__ == "__main__":
    main()

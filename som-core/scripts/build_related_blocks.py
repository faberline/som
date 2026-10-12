"""Build approved related-service blocks; keep all v4 block records unchanged."""

import json
from copy import deepcopy
from pathlib import Path

from build_service_blocks import block

ROOT = Path(__file__).resolve().parents[2]


def library():
    old = json.loads((ROOT / "som-code-python/data/blocks/v4/blocks.json").read_text())
    by_id = {b["id"]: b for b in old}
    new = []

    def add(name, params, source, imports=None):
        new.append(block(name, params, source, imports))

    add("schema.related.bounds", {}, '''
class BoundedInput(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    @model_validator(mode="after")
    def bounded_values(self):
        for name in self.model_fields_set:
            value = getattr(self, name)
            if isinstance(value, str) and len(value) > 255:
                raise ValueError("strings must have at most 255 characters")
            if type(value) is int and not -(2**31) <= value < 2**31:
                raise ValueError("integers must fit signed 32 bits")
        return self
''', ["from pydantic import BaseModel, ConfigDict, model_validator"])

    for role in ("parent", "child"):
        for kind in ("plain", "boolean", "date"):
            params = {"name": "identifier", "table": "string", "fields": "fields_v4"}
            indexes = []
            fk_column = ""
            if role == "child":
                params.update(foreign_key="identifier", target="string")
                indexes.append('        sa.Index("ix_" + {{table}} + "_parent_id", "{{foreign_key}}", "id"),')
                fk_column = '    {{foreign_key}}: Mapped[int] = mapped_column(sa.ForeignKey({{target}}, ondelete="RESTRICT"), nullable=False)\n'
            prefix = '"{{foreign_key}}", ' if role == "child" else ""
            for column in (("filter_field",) if kind == "boolean" else ("filter_field", "date_field") if kind == "date" else ()):
                params[column] = "string"
                indexes.append('        sa.Index("ix_" + {{table}} + "_" + {{' + column + '}}, ' + prefix + '{{' + column + '}}, "id"),')
            args = "    __table_args__ = (\n" + "\n".join(indexes) + "\n    )\n" if indexes else ""
            add(f"orm.related.{role}.{kind}", params, '''
class {{name}}(Base):
    __tablename__ = {{table}}
''' + args + '''
    id: Mapped[int] = mapped_column(sa.Integer, primary_key=True)
''' + fk_column + '''{{#fields}}    {{name}}: Mapped[{{read_type}}] = mapped_column(
        {{sql_type}}, nullable={{nullable}}, unique={{unique}}{{sql_default}}
    )
{{/fields}}    created_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), server_default=sa.func.now(), onupdate=sa.func.now(), nullable=False)
''', ["from datetime import date, datetime", "import sqlalchemy as sa"])
        schema = deepcopy(by_id["schema.service.date"])
        schema["id"] = f"schema.related.{role}"
        schema["template"] = schema["template"].replace("Create(BaseModel)", "Create(BoundedInput)").replace("Patch(BaseModel)", "Patch(BoundedInput)")
        if role == "child":
            schema["params"]["foreign_key"] = "identifier"
            schema["template"] = schema["template"].replace("    id: int\n", "    id: int\n    {{foreign_key}}: int\n")
        new.append(schema)

    add("schema.related.batch", {"parent_name": "identifier", "child_name": "identifier"}, '''
class BatchCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    parent: {{parent_name}}Create
    children: list[{{child_name}}Create] = Field(min_length=1, max_length=20)


class BatchRead(BaseModel):
    parent: {{parent_name}}Read
    children: list[{{child_name}}Read]
''', ["from pydantic import Field"])
    add("api.related.head", {"parent_name": "identifier", "child_name": "identifier", "foreign_key": "identifier"}, '''
from .models import {{parent_name}} as Parent, {{child_name}} as Child
from .schemas import {{parent_name}}Create as ParentCreate, {{parent_name}}Read as ParentRead, {{parent_name}}Patch as ParentPatch
from .schemas import {{child_name}}Create as ChildCreate, {{child_name}}Read as ChildRead, {{child_name}}Patch as ChildPatch
from .schemas import BatchCreate, BatchRead


def create_router(get_session):
    router = APIRouter()

    def require_parent(db, parent_id):
        row = db.get(Parent, parent_id)
        if row is None:
            raise HTTPException(status_code=404, detail="parent not found")
        return row

    def require_child(db, parent_id, child_id):
        require_parent(db, parent_id)
        row = db.scalar(select(Child).where(Child.id == child_id, Child.{{foreign_key}} == parent_id))
        if row is None:
            raise HTTPException(status_code=404, detail="child not found")
        return row

    def integrity_failure(db, exc, missing_parent=False):
        db.rollback()
        code = getattr(exc.orig, "sqlstate", None)
        if code == "23503" and missing_parent:
            raise HTTPException(status_code=404, detail="parent not found") from exc
        if code in {"23503", "23505"}:
            raise HTTPException(status_code=409, detail="relationship or unique value conflict") from exc
        raise exc
''', ["from datetime import date", "from fastapi import APIRouter, Depends, HTTPException, Query, Response", "from sqlalchemy import select", "from sqlalchemy.exc import IntegrityError", "from sqlalchemy.orm import Session"])
    for role in ("parent", "child"):
        model = role.title()
        child = role == "child"
        args = "parent_id: int, " if child else ""
        check = "        require_parent(db, parent_id)\n" if child else ""
        params = {"path": "string"}
        if child:
            params["foreign_key"] = "identifier"
        add(f"api.related.{role}.create", params, '''
    @router.post({{path}}, response_model=MODELRead, status_code=201)
    def create_ROLE(ARGSdata: MODELCreate, db: Session = Depends(get_session)):
CHECK        row = MODEL(EXTRA**data.model_dump())
        db.add(row)
        try:
            db.commit()
        except IntegrityError as exc:
            integrity_failure(db, exc, MISSING)
        db.refresh(row)
        return row
'''.replace("MODEL", model).replace("ROLE", role).replace("ARGS", args).replace("CHECK", check).replace("EXTRA", "{{foreign_key}}=parent_id, " if child else "").replace("MISSING", str(child)))
        item_args = "parent_id: int, child_id: int" if child else "parent_id: int"
        get = "require_child(db, parent_id, child_id)" if child else "require_parent(db, parent_id)"
        add(f"api.related.{role}.get", {"path": "string"}, '''
    @router.get({{path}}, response_model=MODELRead)
    def get_ROLE(ARGS, db: Session = Depends(get_session)):
        return GET
'''.replace("MODEL", model).replace("ROLE", role).replace("ARGS", item_args).replace("GET", get))
        add(f"api.related.{role}.patch", {"path": "string"}, '''
    @router.patch({{path}}, response_model=MODELRead)
    def patch_ROLE(ARGS, data: MODELPatch, db: Session = Depends(get_session)):
        row = GET
        for name, value in data.model_dump(exclude_unset=True).items():
            setattr(row, name, value)
        try:
            db.commit()
        except IntegrityError as exc:
            integrity_failure(db, exc)
        db.refresh(row)
        return row
'''.replace("MODEL", model).replace("ROLE", role).replace("ARGS", item_args).replace("GET", get))
        add(f"api.related.{role}.delete", {"path": "string"}, '''
    @router.delete({{path}}, status_code=204)
    def delete_ROLE(ARGS, db: Session = Depends(get_session)):
        row = GET
        db.delete(row)
        try:
            db.commit()
        except IntegrityError as exc:
            integrity_failure(db, exc)
        return Response(status_code=204)
'''.replace("ROLE", role).replace("ARGS", item_args).replace("GET", get))
        for kind in ("plain", "boolean", "date"):
            params = {"path": "string", "max_limit": "integer"}
            arguments = args
            where = ".where(Child.{{foreign_key}} == parent_id)" if child else ""
            filters = ""
            if child:
                params["foreign_key"] = "identifier"
            if kind != "plain":
                params["filter_field"] = "identifier"
                arguments += "{{filter_field}}: bool | None = None, "
                filters += "        if {{filter_field}} is not None:\n            statement = statement.where(MODEL.{{filter_field}} == {{filter_field}})\n"
            if kind == "date":
                params["date_field"] = "identifier"
                arguments += "before: date | None = None, "
                filters += "        if before is not None:\n            statement = statement.where(MODEL.{{date_field}} < before)\n"
            add(f"api.related.{role}.list.{kind}", params, '''
    @router.get({{path}}, response_model=list[MODELRead])
    def list_ROLE(ARGSlimIt: int = Query(20, alias="limit", ge=1, le={{max_limit}}), offset: int = Query(0, ge=0), db: Session = Depends(get_session)):
CHECK        statement = select(MODEL)WHERE
FILTERS        return list(db.scalars(statement.order_by(MODEL.id).limit(limIt).offset(offset)))
'''.replace("ROLE", role).replace("ARGS", arguments).replace("CHECK", check).replace("WHERE", where).replace("FILTERS", filters).replace("MODEL", model))
    add("api.related.batch", {"path": "string", "foreign_key": "identifier"}, '''
    @router.post({{path}}, response_model=BatchRead, status_code=201)
    def create_batch(data: BatchCreate, db: Session = Depends(get_session)):
        parent = Parent(**data.parent.model_dump())
        children = []
        try:
            db.add(parent)
            db.flush()
            for item in data.children:
                child = Child({{foreign_key}}=parent.id, **item.model_dump())
                db.add(child)
                db.flush()
                children.append(child)
            db.commit()
        except IntegrityError as exc:
            integrity_failure(db, exc)
        db.refresh(parent)
        for child in children:
            db.refresh(child)
        return {"parent": parent, "children": children}
''')
    add("migration.related.head", {"revision": "string"}, '''
revision = {{revision}}
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
''', ["from alembic import op", "import sqlalchemy as sa"])
    for role in ("parent", "child"):
        for kind in ("plain", "boolean", "date"):
            params = {"table": "string", "fields": "fields_v4"}
            fk = ""
            indexes = ""
            if role == "child":
                params.update(foreign_key="string", target="string")
                fk = '        sa.Column({{foreign_key}}, sa.Integer(), sa.ForeignKey({{target}}, ondelete="RESTRICT"), nullable=False),\n'
                indexes += '    op.create_index("ix_" + {{table}} + "_parent_id", {{table}}, [{{foreign_key}}, "id"])\n'
            for key in (("filter_field",) if kind == "boolean" else ("filter_field", "date_field") if kind == "date" else ()):
                params[key] = "string"
                prefix = "{{foreign_key}}, " if role == "child" else ""
                indexes += '    op.create_index("ix_" + {{table}} + "_" + {{' + key + '}}, {{table}}, [' + prefix + '{{' + key + '}}, "id"])\n'
            add(f"migration.related.{role}.{kind}", params, '''
    op.create_table(
        {{table}},
        sa.Column("id", sa.Integer(), primary_key=True),
''' + fk + '''{{#fields}}        sa.Column({{name_literal}}, {{migration_type}}, nullable={{nullable}}, unique={{unique}}{{migration_default}}),
{{/fields}}        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
''' + indexes)
    add("migration.related.tail", {"parent_table": "string", "child_table": "string"}, '''
def downgrade() -> None:
    op.drop_table({{child_table}})
    op.drop_table({{parent_table}})
''')
    add("test.related.head", {"package": "identifier"}, '''
import os
import unittest

from fastapi.testclient import TestClient

test_url = os.environ.get("TEST_DATABASE_URL", "")
if not test_url.startswith("postgresql+psycopg://"):
    raise RuntimeError("TEST_DATABASE_URL must name an isolated PostgreSQL database")
os.environ["DATABASE_URL"] = test_url

from {{package}}.app import app
''')
    for role in ("parent", "child"):
        add(f"test.related.payload.{role}", {"fields": "fields_v4"}, 'def ' + role + '_payload():\n    return {\n{{#fields}}{{required_entry}}{{/fields}}    }')
    add("test.related.behavior", {"parent_path": "string", "child_suffix": "string", "foreign_key": "string"}, '''
class RelatedServiceTest(unittest.TestCase):
    def test_scoped_crud_and_restriction(self):
        with TestClient(app) as client:
            parent = client.post({{parent_path}}, json=parent_payload())
            self.assertEqual(parent.status_code, 201, parent.text)
            path = {{parent_path}} + "/" + str(parent.json()["id"])
            children = path + {{child_suffix}}
            try:
                child = client.post(children, json=child_payload())
                self.assertEqual(child.status_code, 201, child.text)
                self.assertEqual(child.json()[{{foreign_key}}], parent.json()["id"])
                self.assertEqual(client.delete(path).status_code, 409)
                self.assertEqual(client.post({{parent_path}} + "/with-children", json={"parent": parent_payload(), "children": []}).status_code, 422)
                self.assertEqual(client.patch(children + "/" + str(child.json()["id"]), json={{{foreign_key}}: 999}).status_code, 422)
                self.assertEqual(client.delete(children + "/" + str(child.json()["id"])).status_code, 204)
            finally:
                self.assertEqual(client.delete(path).status_code, 204)


if __name__ == "__main__":
    unittest.main()
''')
    # PostgreSQL names have a 63-byte limit. Use a stable bounded name for all
    # new indexes. The indexed columns, not the name, define the query contract.
    import re
    for candidate in new:
        if candidate["id"].startswith(("orm.related.", "migration.related.")):
            candidate["template"] = re.sub(r'"ix_" \+ (\{\{table\}\}) \+ "_" \+ (\{\{(?:filter_field|date_field)\}\})', r'"ix_" + hashlib.sha256((\1 + ":" + \2).encode()).hexdigest()[:24]', candidate["template"])
            candidate["imports"].append("import hashlib")
    from build_business_blocks import extend
    return extend(old + new)


def main():
    target = ROOT / "som-code-python/data/blocks/v5/blocks.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(library(), indent=2) + "\n")
    from som_core.v1 import load_blocks
    print(f"validated {len(load_blocks(target.parent))} blocks: {target}")


if __name__ == "__main__":
    main()

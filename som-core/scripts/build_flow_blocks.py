"""Create separate v7 blocks; all older block objects remain byte-for-byte intact."""

from copy import deepcopy
import hashlib
import json
from pathlib import Path
from textwrap import dedent

from som_core import composed, flow

ROOT = Path(__file__).resolve().parents[2]
TEMPLATES = ROOT/"som-core/block_templates/flow"
OUT = ROOT/"som-code-python/data/blocks/flow-v7"
CATALOG = ROOT/"som-code-python/data/block-contracts/flow-v7.json"


def main():
    originals = json.loads((OUT.parent/"composed-quality/blocks.json").read_text())
    library = {block["id"]: deepcopy(block) for block in originals}
    catalog = json.loads((CATALOG.parent/"composed-quality.json").read_text())
    original_hash = hashlib.sha256((OUT.parent/"composed-quality/blocks.json").read_bytes()).hexdigest()
    entries = catalog["blocks"]
    catalog["slots"]["flow_helpers"] = "{package}/flows.py"
    catalog["required_database"]["postgresql"].remove("alembic_ini:config")

    def add(identity, source, imports, params, meta):
        block = {"id": identity, "version": 2, "imports": imports, "params": params, "template": source}
        library[identity] = block
        metadata = {"slot": meta["slot"], "provides": meta["provides"], "requires": meta.get("requires", []), "capabilities": meta.get("capabilities", {}),
                    "query": meta.get("query"), "operation": meta.get("operation"), "routes": meta.get("routes", []),
                    "authorization": meta.get("authorization"), "transaction": meta.get("transaction"),
                    "bindings": meta.get("bindings", {}), "params": params, "source_sha256": hashlib.sha256(composed.canonical(block).encode()).hexdigest()}
        entries[identity] = metadata

    def clone(identity, source=None, extra_imports=()):
        old = library[identity]
        new_id = "v7."+identity
        meta = entries.pop(identity)
        add(new_id, source if source is not None else old["template"], [*old["imports"], *extra_imports], old["params"], meta)
        return entries[new_id]

    clone("service.business.project", library["service.business.project"]["template"] + '\n[tool.alembic]\nscript_location = "alembic"\nprepend_sys_path = ["."]\n')
    # V7 routes use FlowStore. Do not emit the unused synchronous CRUD pool.
    database_source = library["service.database"]["template"]
    database_source = database_source[:database_source.index("\n\nengine =")]
    clone("service.database", database_source)

    for identity in list(entries):
        if identity.startswith("orm.related."):
            text = library[identity]["template"].replace("    id: Mapped[int]", "    _som_revision: Mapped[int] = mapped_column(sa.Integer, nullable=False, server_default=sa.text('0'))\n    id: Mapped[int]")
            clone(identity, text)
        elif identity == "orm.composed.mongo.base":
            clone(identity, library[identity]["template"] + '\n\ndef _som_alias(name: str) -> str:\n    """Keep SDK methods separate from business field names."""\n    return name.removeprefix("value_")\n')
        elif identity in {"orm.composed.mongo.parent", "orm.composed.mongo.child"}:
            text = library[identity]["template"].replace("    {{name}}: {{create_type}}{{create_default}}", "    value_{{name}}: {{create_type}}{{create_default}}")
            text = text.replace('    # pylint: disable=duplicate-code', '    model_config = ConfigDict(alias_generator=_som_alias, populate_by_name=True)\n    # pylint: disable=duplicate-code')
            clone(identity, text, ["from pydantic import ConfigDict"])
        elif identity.startswith("migration.related.") and any(part in identity for part in (".parent.", ".child.")):
            text = library[identity]["template"].replace('        sa.Column("id",', '        sa.Column("_som_revision", sa.Integer(), nullable=False, server_default=sa.text("0")),\n        sa.Column("id",')
            clone(identity, text)
    base_imports = ["import hashlib", "from typing import Annotated, Literal", "from pydantic import BaseModel, ConfigDict, Field, TypeAdapter", "from datetime import date", "from fastapi import HTTPException"]
    add("schema.flow", (TEMPLATES/"types.py.tpl").read_text(), base_imports,
        {"plan_json": "string", "flow_inputs": "flow_inputs", "parent_name": "identifier", "child_name": "identifier"},
        {"slot": "schemas", "provides": "schemas:flow", "requires": ["schemas:bounds", "schemas:parent", "schemas:child"],
         "bindings": {"plan_json": {"$ref": "dsl.flow"}, "flow_inputs": {"$ref": "flow.inputs"}, "parent_name": {"$ref": "parent.name"}, "child_name": {"$ref": "child.name"}}})
    catalog["required_common"].append("schemas:flow")
    journal = '''FLOW_RUNS = sa.Table("_som_flow_runs", Base.metadata,
    sa.Column("run_id", sa.String(32), primary_key=True),
    sa.Column("owner", sa.String(255), nullable=False),
    sa.Column("flow", sa.String(80), nullable=False),
    sa.Column("key", sa.String(128), nullable=False),
    sa.Column("request_hash", sa.String(64), nullable=False),
    sa.Column("epoch", sa.Integer(), nullable=False),
    sa.Column("record", JSONB(), nullable=False),
    sa.UniqueConstraint("owner", "flow", "key"))
FLOW_LEASES = sa.Table("_som_flow_leases", Base.metadata,
    sa.Column("run_id", sa.ForeignKey("_som_flow_runs.run_id", ondelete="CASCADE"), primary_key=True),
    sa.Column("epoch", sa.Integer(), nullable=False),
    sa.Column("until", sa.Float(), nullable=False))
'''
    add("orm.flow.journal", journal, ["import sqlalchemy as sa", "from sqlalchemy.dialects.postgresql import JSONB"], {},
        {"slot": "models", "provides": "models:journal", "requires": ["models:base", "models:child"], "capabilities": {"database": "postgresql"}})
    migration = '''    # pylint: disable-next=no-member
    op.create_table("_som_flow_runs", sa.Column("run_id", sa.String(32), primary_key=True), sa.Column("key", sa.String(128), nullable=False), sa.Column("owner", sa.String(255), nullable=False), sa.Column("epoch", sa.Integer(), nullable=False), sa.Column("flow", sa.String(80), nullable=False), sa.Column("record", JSONB(), nullable=False), sa.Column("request_hash", sa.String(64), nullable=False), sa.UniqueConstraint("owner", "flow", "key"))
    # pylint: disable-next=no-member
    op.create_table("_som_flow_leases", sa.Column("until", sa.Float(), nullable=False), sa.Column("epoch", sa.Integer(), nullable=False), sa.Column(sa.ForeignKey("_som_flow_runs.run_id", ondelete="CASCADE"), name="run_id", primary_key=True))
'''
    add("migration.flow.journal", migration, ["from sqlalchemy.dialects.postgresql import JSONB", "import sqlalchemy as sa"], {},
        {"slot": "migration", "provides": "migration:journal", "requires": ["migration:child"], "capabilities": {"database": "postgresql"}})
    tail = library["migration.related.tail"]["template"].replace('    # pylint: disable-next=no-member\n    op.drop_table({{child_table}})', '    # pylint: disable-next=no-member\n    op.drop_table("_som_flow_leases")\n    # pylint: disable-next=no-member\n    op.drop_table("_som_flow_runs")\n    # pylint: disable-next=no-member\n    op.drop_table({{child_table}})')
    meta = clone("migration.related.tail", tail); meta["requires"].append("migration:journal")
    catalog["required_database"]["postgresql"] += ["models:journal", "migration:journal"]
    database_text = library["service.business.database.mongodb"]["template"].replace("    await init_beanie", '    await database["_som_flow_runs"].create_index([("owner", 1), ("flow", 1), ("key", 1)], unique=True)\n    await init_beanie')
    clone("service.business.database.mongodb", database_text)
    migration_text = library["migration.mongo"]["template"].replace('"_som_counters")', '"_som_counters", "_som_flow_runs", "_som_flow_leases")')
    clone("migration.mongo", migration_text)
    store_bindings = {"parent_name": "parent.name", "child_name": "child.name", "parent_table": "parent.table", "child_table": "child.table", "foreign_key": "relation.foreign_key", "parent_filter": "parent.filter_field", "child_filter": "child.filter_field", "parent_date": "parent.date_field", "child_date": "child.date_field"}
    store_params = {key: "identifier" if key in {"parent_name", "child_name"} else "nullable_string" if key.endswith(("filter", "date")) else "string" for key in store_bindings}
    schema_imports = ["from .schemas import {{parent_name}}Create as ParentInput, {{parent_name}}Read as ParentView, {{parent_name}}Patch as ParentChanges", "from .schemas import {{child_name}}Create as ChildInput, {{child_name}}Read as ChildView, {{child_name}}Patch as ChildChanges", "from .schemas import CrudRequest, Effect, JsonObject, JsonValue, ResourceSpec, RunRecord, integer, json_object, JSON_VALUE", "from pydantic import TypeAdapter", "from datetime import UTC, date, datetime", "from contextlib import asynccontextmanager, ExitStack", "from collections.abc import AsyncIterator", "from fastapi import HTTPException", "import time"]
    for backend in ("postgresql", "mongodb"):
        imports = schema_imports + (["from .models import Base, FLOW_RUNS, FLOW_LEASES", "from sqlalchemy import select", "from sqlalchemy.ext.asyncio import AsyncConnection, create_async_engine", "from contextlib import AsyncExitStack", "from sqlalchemy.exc import IntegrityError"] if backend == "postgresql" else ["from .models import encode_date", "from pymongo.asynchronous.client_session import AsyncClientSession", "from pymongo import ReturnDocument", "from pymongo.errors import DuplicateKeyError"])
        source = (TEMPLATES/"store_header.py.tpl").read_text()+"\n\n"+(TEMPLATES/("store_"+backend+".py.tpl")).read_text()
        add("service.flow.store."+backend, source, imports, store_params,
            {"slot": "database", "provides": "database:store", "requires": ["database:database", "schemas:flow"] + (["models:journal"] if backend == "postgresql" else []), "capabilities": {"database": backend}, "bindings": {key: {"$ref": value} for key,value in store_bindings.items()}})
    catalog["required_common"].append("database:store")
    engine_imports = ["from .database import FlowStore, RESOURCES", "from .schemas import ACTION_IDS, INVERSE_IDS, CONTROL_IDS, CHECKED_IDS, PLAN, PLAN_HASH, ActionNode, BranchNode, TransactionNode, FlowNode, FlowInput, FlowRef, InputRef, StepRef, ConstantRef, Predicate, CrudRequest, Effect, RunRecord, JsonValue, JsonObject, integer, json_object, json_value, text_value",
        "from .integrations import Identity, require_access, ObjectMissing, ObjectExists, ObjectUnavailable, flow_object_put, flow_object_get, flow_object_reverse", "from collections.abc import AsyncIterator", "from contextlib import asynccontextmanager, suppress", "from datetime import date", "from decimal import Decimal", "from fastapi import APIRouter, Depends, Header, HTTPException", "from starlette.responses import JSONResponse", "from starlette.concurrency import run_in_threadpool", "from typing import Annotated", "import asyncio", "import base64", "import csv", "import gzip", "import hashlib", "import io", "import json", "import re", "import statistics", "import uuid"]
    for workflow in ("json_export", "gzip_export", "csv", "decimal_summary", "statistics"):
        source = (TEMPLATES/"workflow_common.py.tpl").read_text()+(TEMPLATES/("workflow_"+workflow+".py.tpl")).read_text()
        if workflow != "csv":
            source += '\n\ndef csv_records(_source: str) -> list[JsonObject]:\n    """Reject CSV use without that selected capability."""\n    raise HTTPException(status_code=422, detail="CSV capability required")\n'
        params = {key: kind for key, kind in {"value_field": "string", "group_field": "string", "custom_fields": "string", "child_name": "identifier"}.items() if "{{"+key+"}}" in source or workflow=="csv" and key=="child_name"}
        bindings = {key: {"$ref": "child.name" if key=="child_name" else "flow.custom_fields" if key=="custom_fields" else "capabilities."+key} for key in params}
        add("workflow.flow."+workflow, source, [*engine_imports, "from pydantic import TypeAdapter", "from .schemas import {{child_name}}Create as ChildInput"] if workflow=="csv" else engine_imports, params,
            {"slot":"flow_helpers", "provides":"flow_helpers:workflow_helper", "requires":["schemas:flow"], "capabilities":{"workflow":workflow}, "bindings":bindings})
    for backend, error, conflict in [("postgresql", "SQLAlchemyError", "IntegrityError"), ("mongodb", "PyMongoError", "DuplicateKeyError")]:
        imports = engine_imports+["from sqlalchemy.exc import SQLAlchemyError as BackendError, IntegrityError as ConflictError"] if backend == "postgresql" else engine_imports+["from pymongo.errors import PyMongoError as BackendError, DuplicateKeyError as ConflictError"]
        source = (TEMPLATES/"engine.py.tpl").read_text()
        add("api.flow.engine."+backend, source, imports, {},
            {"slot": "flow_helpers", "provides": "flow_helpers:engine", "requires": ["database:store", "schemas:flow", "integrations:flow_storage", "flow_helpers:workflow_helper"], "capabilities": {"database": backend}, "authorization": "require_access", "transaction": "durable_compensation", "routes": ["flow:execute", "flow:status", "flow:recover"], "bindings": {}})
        head = '''def create_router(_session_provider: object) -> APIRouter:
    """Register exactly the selected routes with shared typed operations."""
    router = APIRouter(dependencies=[Depends(require_access)])

    async def crud(request: CrudRequest) -> JsonValue:
        """One checked HTTP operation; writes use one actual transaction."""
        store = FlowStore()
        if request.operation == "get":
            if request.row_id is None: raise HTTPException(status_code=404, detail="row missing")
            return (await store.row(request.role, request.row_id, request.parent_id))[0]
        if request.operation == "list": return json_value(await store.rows(request))
        try:
            async with store.transaction(): return await store.mutate(request)
        except ConflictError as error:
            raise HTTPException(status_code=409, detail="resource conflict") from error

    async def workflow_rows(parent_id: int) -> list[JsonObject]:
        """The approved exports have a 1000 row bound."""
        rows = await FlowStore().rows(CrudRequest(role="child", operation="list", parent_id=parent_id, limit=1001))
        if len(rows) > 1000: raise HTTPException(status_code=413, detail="workflow limited to 1000 rows")
        return rows

    async def _workflow_import(parent_id: int, records: list[ChildCreate]) -> list[JsonObject]:
        """Commit a CSV import once after validating every row."""
        store = FlowStore()
        result: list[JsonObject] = []
        try:
            async with store.transaction():
                for record in records:
                    result.append(await store.mutate(CrudRequest(role="child", operation="create", parent_id=parent_id, data=json_object(record.model_dump(mode="json")))))
        except ConflictError as error:
            raise HTTPException(status_code=409, detail="resource conflict") from error
        return result
'''
        old = "api.business.head."+backend
        head = head[head.index('    router ='):]
        head = dedent(head)
        meta = clone(old, head, ["from .database import FlowStore", "from .schemas import CrudRequest, JsonValue, JsonObject, json_object, json_value", "from .flows import flow_router, workflow_payload, csv_records", imports[-1]])
        # The new body does not use the old class-name bindings.
        library["v7."+old]["params"] = {"parent_name": "identifier", "child_name": "identifier"}
        meta["params"] = deepcopy(library["v7."+old]["params"])
        meta["bindings"] = {key: {"$ref": role+".name"} for key,role in [("parent_name", "parent"), ("child_name", "child")]}
        meta["requires"].append("flow_helpers:engine")
    catalog["required_common"] += ["flow_helpers:engine", "flow_helpers:workflow_helper"]
    # Route wrappers keep concrete FastAPI request/response annotations.
    for identity in list(entries):
        meta = entries[identity]
        operation = meta.get("operation")
        if not operation: continue
        role, method = operation
        if role == "batch":
            source = '''    @router.post({{path}}, status_code=201)
    async def batch_create(payload: BatchCreate) -> BatchRead:
        """Create the parent and children in one actual transaction."""
        store = FlowStore()
        try:
            async with store.transaction():
                parent = await store.mutate(CrudRequest(role="parent", operation="create", data=json_object(payload.parent.model_dump(mode="json"))))
                children = [await store.mutate(CrudRequest(role="child", operation="create", parent_id=integer(parent["id"]), data=json_object(child.model_dump(mode="json")))) for child in payload.children]
        except ConflictError as error:
            raise HTTPException(status_code=409, detail="resource conflict") from error
        return BatchRead.model_validate({"parent": parent, "children": children})
'''
            params = {"path": "string"}; bindings = {"path": {"$ref": "batch.path"}}
        else:
            title = role.capitalize(); parent_arg = "parent_id: int, " if role == "child" else ""
            ids = ', parent_id=parent_id' if role == "child" else ''
            uri_arg = role+"_id: int, " if method in {"get", "patch", "delete"} else ""
            row = ', row_id='+role+'_id' if uri_arg else ''
            body = "payload: "+title+("Create" if method == "create" else "Patch")+", " if method in {"create", "patch"} else ""
            data = ', data=json_object(payload.model_dump(mode="json", exclude_unset=True))' if body else ''
            query = "limit: Annotated[int, Query(ge=1, le={{max_limit}})] = 20, offset: Annotated[int, Query(ge=0)] = 0, " if method == "list" else ''
            query_values = ', limit=limit, offset=offset' if query else ''
            if method == "list" and meta.get("query", [None, "plain"])[1] != "plain":
                query += 'filter_value: Annotated[bool | None, Query(alias={{filter_field}})] = None, '
                query_values += ', filter_value=filter_value'
            if method == "list" and meta.get("query", [None, "plain"])[1] == "date":
                query += 'before: date | None = None, '; query_values += ', before=before'
            result = "list["+title+"Read]" if method == "list" else "Response" if method == "delete" else title+"Read"
            status = ', status_code=201' if method == "create" else ', status_code=204' if method == "delete" else ''
            http_method = {"create":"post", "get":"get", "list":"get", "patch":"patch", "delete":"delete"}[method]
            source = '    @router.'+http_method+'({{path}}'+status+')\n    async def '+role+'_'+method+'('+parent_arg+uri_arg+body+query+')-> '+result+':\n        """Apply the declared '+role+' '+method+' contract."""\n        result = await crud(CrudRequest(role="'+role+'", operation="'+method+'"'+ids+row+data+query_values+'))\n'
            source += '        return [ParentRead.model_validate(row) for row in result]\n' if method == "list" and role == "parent" else '        return [ChildRead.model_validate(row) for row in result]\n' if method == "list" else '        return Response(status_code=204)\n' if method == "delete" else '        return '+title+'Read.model_validate(result)\n'
            if method == "list": source = source.replace('        return [', '        if not isinstance(result, list): raise RuntimeError("invalid list result")\n        return [')
            params = deepcopy(library[identity]["params"]); bindings = deepcopy(meta["bindings"])
            if "filter_field" in params: params["filter_field"] = "string"
            for key in list(params):
                if "{{"+key+"}}" not in source: del params[key]; del bindings[key]
        source = dedent(source)
        new_meta = clone(identity, source, ["from .schemas import CrudRequest, integer, json_object", "from fastapi import Query, Response", "from typing import Annotated"])
        if role != "batch" and method == "delete":
            library["v7."+identity]["template"] = source.replace("result = await crud", "await crud")
        library["v7."+identity]["params"] = params; new_meta["params"] = params; new_meta["bindings"] = bindings
    clone("api.tail", 'def create_router() -> APIRouter:\n    """Return only the registered approved routes."""\n    return router\n')
    for workflow in ("json_export", "gzip_export", "csv", "decimal_summary", "statistics"):
        source = '@router.get({{parent_path}} + "/{parent_id}/export")\nasync def export_rows(parent_id: int) -> Response:\n    """Return the selected workflow output."""\n    payload = workflow_payload(await workflow_rows(parent_id))\n'
        if workflow in {"statistics", "decimal_summary"}:
            source += '    return JSONResponse(payload["data"])\n'
        else:
            media = {"json_export":"application/json", "gzip_export":"application/gzip", "csv":"text/csv"}[workflow]
            source += '    return Response(base64.b64decode(text_value(payload["body"])), media_type="'+media+'")\n'
        if workflow == "csv":
            source += '\n\n@router.post({{parent_path}} + "/{parent_id}/import", status_code=201)\nasync def import_rows(parent_id: int, data: Annotated[str, Body(media_type="text/csv", max_length=1048576)]) -> list[JsonObject]:\n    """Validate and commit the entire CSV once."""\n    return await _workflow_import(parent_id, [ChildCreate.model_validate(row) for row in csv_records(data)])\n'
        meta = clone("api.business.workflow."+workflow, source, ["import base64", "from .schemas import text_value", "from starlette.responses import JSONResponse"])
        library["v7.api.business.workflow."+workflow]["params"] = {"parent_path":"string"}
        meta["params"] = {"parent_path":"string"}; meta["bindings"] = {"parent_path":{"$ref":"parent.path"}}
        meta["requires"].append("flow_helpers:workflow_helper")
    for backend in ("postgresql", "mongodb"):
        identity = "service.business.app."+backend
        source = library[identity]["template"].replace("app.include_router(object_router)", "app.include_router(object_router)\napp.include_router(flow_router)")
        source = source.replace("create_router(get_session)", "create_router()")
        source = source.replace("    try:\n        yield", "    try:\n        await run_in_threadpool(verify_flow_storage)\n        yield") if backend == "postgresql" else source.replace("        try:\n            yield", "        try:\n            await run_in_threadpool(verify_flow_storage)\n            yield")
        if backend == "postgresql":
            source = source.replace("        engine.dispose()", "        await flow_engine.dispose()")
            source = source.replace("def healthz()", "async def healthz()")
            source = source.replace("with engine.connect()", "async with flow_engine.connect()")
            source = source.replace('connection.execute(text("SELECT 1"))', 'await connection.execute(text("SELECT 1"))')
        meta = clone(identity, source, ["from .integrations import verify_flow_storage", "from starlette.concurrency import run_in_threadpool"]+(["from .database import flow_engine"] if backend == "postgresql" else []))
        if backend == "postgresql":
            library["v7."+identity]["imports"].remove("from .database import engine, get_session")
        meta["requires"].append("flow_helpers:engine")
    storage_imports = ["from pydantic import BaseModel, TypeAdapter, ValidationError", "import base64", "import hashlib"]
    for storage in ("none", "s3", "gcs"):
        source = (TEMPLATES/"storage_common.py.tpl").read_text()+"\n\n"+(TEMPLATES/("storage_"+storage+".py.tpl")).read_text()
        if storage == "none":
            source = 'MAX_OBJECT_BYTES = 1048576\n\nclass ObjectMissing(Exception):\n    """Missing object."""\n\nclass ObjectExists(Exception):\n    """Object conflict."""\n\nclass ObjectUnavailable(Exception):\n    """Object provider unavailable."""\n\ndef storage_get(_key: str) -> bytes:\n    """This service has no object provider."""\n    raise ObjectUnavailable()\n\n'+source
        add("storage.flow."+storage, source, storage_imports, {"versioned": "boolean"} if storage == "s3" else {},
            {"slot": "integrations", "provides": "integrations:flow_storage", "requires": ["integrations:storage"], "capabilities": {"storage": storage}, "bindings": {"versioned": {"$ref": "flow.versioned"}} if storage == "s3" else {}})
    catalog["required_common"].append("integrations:flow_storage")
    # Runtime registrations are emitted only for explicitly selected tree IDs.
    for identity, atomic in flow.catalog().items():
        registry = "INVERSE_IDS" if atomic["inverse"] else "CONTROL_IDS" if atomic["operation"] in {"if", "transaction"} else "ACTION_IDS"
        value = atomic["inverse"] or atomic["operation"]
        source = registry+'[{{identity}}] = '+json.dumps(value)+'\n'
        if identity.endswith(".checked"):
            source += 'CHECKED_IDS.add({{identity}})\n'
        add(identity, source, ["from .schemas import "+registry]+(["from .schemas import CHECKED_IDS"] if identity.endswith(".checked") else []), {"identity": "string"},
            {"slot": "flow_helpers", "provides": "flow_helpers:reg_"+identity.replace('.', '_'), "requires": ["flow_helpers:engine"], "capabilities": atomic["capabilities"], "bindings": {"identity": {"$ref": "block.id"}}})
    test_source = library["test.composed.behavior"]["template"].replace('if __name__ == "__main__":', '''class FlowContractTest(unittest.TestCase):
    """Flow route authentication contract."""
    def test_flow_has_authenticated_routes(self) -> None:
        """The model plan installs the complete public run interface."""
        application: object = importlib.import_module(APPLICATION + ".app").app
        if not isinstance(application, FastAPI):
            raise TypeError("service app must be a FastAPI application")
        with TestClient(application) as client:
            self.assertEqual(client.get("/flow-runs/missing").status_code, 401)
            self.assertEqual(client.post("/flow-runs/missing/recover").status_code, 401)


if __name__ == "__main__":''')
    clone("test.composed.behavior", test_source)
    catalog["version"] = "som.flow.block-contracts.v1"
    catalog["contract_sha256"] = flow.contract_hash()
    catalog["flow_blocks"] = flow.catalog()
    # Metadata hashes are computed after parameter schemas are finalized.
    for identity, meta in entries.items():
        meta["source_sha256"] = hashlib.sha256(composed.canonical(library[identity]).encode()).hexdigest()
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT/"blocks.json").write_text(json.dumps(list(library.values()), indent=2)+"\n")
    CATALOG.write_text(json.dumps(catalog, indent=2, sort_keys=True)+"\n")
    assert originals == [library[block["id"]] for block in originals]
    assert original_hash == hashlib.sha256((OUT.parent/"composed-quality/blocks.json").read_bytes()).hexdigest()
    print(json.dumps({"blocks": len(library), "selectable": len(entries), "old_blocks_unchanged": True}))


if __name__ == "__main__": main()

"""Additional candidates. Providers use their real SDKs against local fixtures."""

from copy import deepcopy
from pathlib import Path

from build_service_blocks import block

TEMPLATES = Path(__file__).resolve().parents[1] / "block_templates/business"


def extend(blocks):
    by_id = {b["id"]: b for b in blocks}
    new = []

    def add(name, params, source, imports=None):
        new.append(block(name, params, source, imports))

    def template(name, filename, params=None):
        add(name, params or {}, (TEMPLATES / (filename + ".py.tpl")).read_text())

    template("auth.common", "auth_common", {"package": "string"})
    for auth in ("jwt", "firebase", "google_oauth"):
        template("auth." + auth, "auth_" + auth)
    template("storage.common", "storage_common")
    for provider in ("s3", "gcs"):
        template("storage." + provider, "storage_" + provider)
    add("storage.none", {}, "object_router = APIRouter()")

    head = deepcopy(by_id["api.related.head"])
    head["id"] = "api.business.head.postgresql"
    head["template"] = "from .integrations import require_access\n\n" + head["template"].replace("router = APIRouter()", "router = APIRouter(dependencies=[Depends(require_access)])")
    # Async workflow adapters share one contract across SQL and Mongo.
    head["template"] += '''

    async def workflow_rows(parent_id):
        from starlette.concurrency import run_in_threadpool
        def read():
            from .database import engine
            with Session(engine) as db:
                require_parent(db, parent_id)
                rows = list(db.scalars(select(Child).where(Child.{{foreign_key}} == parent_id).order_by(Child.id).limit(1001)))
                if len(rows) > 1000:
                    raise HTTPException(status_code=413, detail="workflow is limited to 1000 rows")
                return [ChildRead.model_validate(row).model_dump(mode="json") for row in rows]
        return await run_in_threadpool(read)

    async def workflow_import(parent_id, records):
        from starlette.concurrency import run_in_threadpool
        def write():
            from .database import engine
            with Session(engine) as db:
                require_parent(db, parent_id)
                rows = []
                try:
                    for data in records:
                        row = Child({{foreign_key}}=parent_id, **data.model_dump())
                        db.add(row)
                        db.flush()
                        rows.append(row)
                    db.commit()
                except IntegrityError as exc:
                    integrity_failure(db, exc, True)
                return [ChildRead.model_validate(row).model_dump(mode="json") for row in rows]
        return await run_in_threadpool(write)
'''
    new.append(head)
    app = '''
from fastapi import FastAPI, HTTPException
from .integrations import auth_router, object_router
from .database import engine, get_session
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

app = FastAPI()
app.include_router(auth_router)
app.include_router(object_router)
app.include_router(create_router(get_session))

@app.get("/healthz")
def healthz():
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    except SQLAlchemyError as exc:
        raise HTTPException(status_code=503, detail="database unavailable") from exc
    return {"status": "ok"}
'''
    add("service.business.app.postgresql", {}, app)
    add("service.business.app.mongodb", {}, '''
from fastapi import FastAPI, HTTPException
from pymongo.errors import PyMongoError
from .integrations import auth_router, object_router
from .database import database, get_session, lifespan

app = FastAPI(lifespan=lifespan)
app.include_router(auth_router)
app.include_router(object_router)
app.include_router(create_router(get_session))

@app.get("/healthz")
async def healthz():
    try:
        await database.command("ping")
    except PyMongoError as exc:
        raise HTTPException(status_code=503, detail="database unavailable") from exc
    return {"status": "ok"}
''')
    project = deepcopy(by_id["service.project"])
    project["id"] = "service.business.project"
    deps = ["PyJWT[crypto]==2.15.1", "Authlib==1.8.0", "firebase-admin==7.7.0", "google-cloud-storage==3.15.1", "boto3==1.43.105", "beanie==2.2.0", "pymongo==4.18.2", "redis==8.1.0", "requests==2.32.5"]
    project["template"] = project["template"].replace("dependencies = [", "dependencies = [\n" + "\n".join('    "' + dep + '",' for dep in deps))
    new.append(project)
    for backend in ("postgresql", "mongodb"):
        setup = "Set DATABASE_URL to a PostgreSQL URL. Run uv run alembic upgrade head." if backend == "postgresql" else "Set MONGODB_URL and MONGODB_DATABASE. MongoDB must be a replica set. Run uv run python -m {{package}}.migrate upgrade."
        add("service.business.readme." + backend, {"package": "identifier"}, "# {{package}}\n\nUse Python 3.12. Run uv sync.\n" + setup + "\nRun uv run uvicorn {{package}}.app:app. GET /healthz checks the database.\nAll readers share this app workspace. Editors can change records.\nIDs, timestamps and the child foreign key are read-only.\nUse only synthetic data for evaluation. Tests require their own database.\nSet TEST_AUTH_HEADERS to a JSON object of test client headers.\nSet TEST_AUTH_COOKIES to a JSON object of test client cookies.\nThese are test-client inputs. The application never reads them.\nRun uv run python -m unittest discover -s tests -v.\n")
    readmes = {
        "jwt": "Set JWT_PUBLIC_KEY_FILE, JWT_ISSUER and JWT_AUDIENCE. Use an RS256 token with sub, iat, exp, iss, aud, and role=editor for writes. Public RSA keys must have at least 2048 bits.",
        "firebase": "Set FIREBASE_PROJECT_ID. Use Firebase bearer tokens. Set the trusted custom role claim to editor for writes. Revoked or disabled users are rejected. Local tests may set APP_ENV=test and FIREBASE_AUTH_EMULATOR_HOST to a loopback mock.",
        "google_oauth": "Set GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, GOOGLE_REDIRECT_URI, GOOGLE_EDITOR_SUBJECTS (JSON subject list), REDIS_URL and REDIS_NAMESPACE. Use /auth/google/login. Sessions and one-use login state live in Redis. Use the csrf value from /auth/me as X-CSRF-Token for writes. POST /auth/logout ends the session. Outside tests, HTTPS is required. Only local tests may use SOM_TEST_OIDC_ISSUER with APP_ENV=test.",
    }
    for name, text in readmes.items():
        add("readme.auth." + name, {}, "## Authentication\n\n" + text)
    for name, text in {"none": "This app has no object store.", "s3": "Set S3_BUCKET and AWS credentials through the SDK environment or credential chain. Local tests may set SOM_TEST_S3_ENDPOINT with APP_ENV=test.", "gcs": "Set GCS_BUCKET and GCS_PROJECT. Use Google application default credentials. Local tests may set STORAGE_EMULATOR_HOST with APP_ENV=test."}.items():
        add("readme.storage." + name, {}, "## Objects\n\n" + text + "\nPUT, GET and DELETE /objects/{key} accept safe keys of 1 to 80 characters. Objects are at most 1 MiB. PUT returns SHA-256. A same-body retry is safe. A different body with the same key returns 409. Object writes and database writes are separate operations.")
    for name in ("json_export", "gzip_export", "csv", "decimal_summary", "statistics"):
        add("readme.workflow." + name, {}, "## Workflow\n\nGET <parent-path>/{parent_id}/export processes up to 1000 children in ID order. The workflow is " + name + ". Larger results return 413. CSV apps also accept POST <parent-path>/{parent_id}/import with text/csv and 1 to 20 rows. Headers must match custom child fields. \\N means null. CSV import commits once or rolls back all rows.")
    for backend in ("postgresql", "mongodb"):
        if backend == "postgresql":
            head = by_id["test.related.head"]["template"]
        else:
            head = '''import os
import unittest
from fastapi.testclient import TestClient
name = os.environ.get("SOM_TEST_DATABASE_NAME", "")
if not name.startswith("som_test_") or name != os.environ.get("MONGODB_DATABASE"):
    raise RuntimeError("tests require an explicitly named isolated MongoDB database")
from {{package}}.app import app
'''
        add("test.business.head." + backend, {"package": "identifier"}, head)
    behavior = deepcopy(by_id["test.related.behavior"])
    behavior["id"] = "test.business.behavior"
    behavior["imports"] = ["import json"]
    behavior["template"] = behavior["template"].replace("with TestClient(app) as client:", 'with TestClient(app, headers=json.loads(os.environ["TEST_AUTH_HEADERS"]), cookies=json.loads(os.environ.get("TEST_AUTH_COOKIES", "{}"))) as client:')
    new.append(behavior)
    add_mongo(add, template, by_id)
    add_workflows(add)
    return blocks + new


def add_workflows(add):
    for mode in ("json_export", "gzip_export", "csv", "decimal_summary", "statistics"):
        params = {"parent_path": "string"}
        body = '''
    @router.get({{parent_path}} + "/{parent_id}/export")
    async def export_rows(parent_id: int):
        rows = await workflow_rows(parent_id)
'''
        if mode in {"json_export", "gzip_export"}:
            body += '        content = json.dumps(rows, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode()\n'
            body += ('        return Response(gzip.compress(content, mtime=0), media_type="application/gzip")\n' if mode == "gzip_export" else '        return Response(content, media_type="application/json")\n')
        elif mode == "csv":
            body += '''        output = io.StringIO(newline="")
        columns = list(ChildCreate.model_fields)
        writer = csv.DictWriter(output, fieldnames=columns, lineterminator="\\n")
        writer.writeheader()
        for row in rows:
            writer.writerow({key: "\\\\N" if row[key] is None else row[key] for key in columns})
        return Response(output.getvalue(), media_type="text/csv")

    @router.post({{parent_path}} + "/{parent_id}/import", status_code=201)
    async def import_rows(parent_id: int, data: str = Body(media_type="text/csv", max_length=1048576)):
        try:
            reader = csv.DictReader(io.StringIO(data, newline=""), strict=True)
            columns = list(ChildCreate.model_fields)
            if reader.fieldnames != columns:
                raise ValueError("CSV headers must match the child fields in order")
            records = []
            for row in reader:
                if None in row or any(value is None for value in row.values()):
                    raise ValueError("invalid CSV row width")
                records.append(ChildCreate.model_validate({key: None if value == "\\\\N" else value for key, value in row.items()}))
                if len(records) > 20:
                    raise ValueError("CSV import accepts at most 20 rows")
            if not records:
                raise ValueError("CSV import requires at least one row")
        except (ValueError, ValidationError, csv.Error) as exc:
            raise HTTPException(status_code=422, detail="invalid CSV input") from exc
        return await workflow_import(parent_id, records)
'''
        elif mode == "decimal_summary":
            params.update(value_field="string", group_field="string")
            body += '''        totals = {}
        for row in rows:
            if row[{{value_field}}] is not None:
                key = row[{{group_field}}]
                totals[key] = totals.get(key, Decimal(0)) + Decimal(str(row[{{value_field}}]))
        return {"totals": {key: str(value) for key, value in sorted(totals.items())}}
'''
        else:
            params.update(value_field="string")
            body += '''        values = [row[{{value_field}}] for row in rows if row[{{value_field}}] is not None]
        return {"count": len(values), "mean": statistics.fmean(values) if values else None, "median": statistics.median(values) if values else None, "minimum": min(values) if values else None, "maximum": max(values) if values else None}
'''
        add("api.business.workflow." + mode, params, body, ["import csv", "import gzip", "import io", "import json", "import statistics", "from decimal import Decimal", "from fastapi import Body", "from pydantic import ValidationError"])


def add_mongo(add, template, by_id):
    template("service.business.database.mongodb", "mongo_database", {"parent_name": "identifier", "child_name": "identifier"})
    template("migration.mongo", "mongo_migrate", {"parent_table": "string", "child_table": "string"})
    add("orm.mongo.base", {}, '''from datetime import date, datetime, time, timezone
from typing import ClassVar
from beanie import Document
from pydantic import Field
''')
    for role in ("parent", "child"):
        params = {"name": "identifier", "table": "string", "fields": "fields_v4", "filter_field": "nullable_string", "date_field": "nullable_string"}
        fk = ""
        prefix = ""
        base_index = ""
        if role == "child":
            params["foreign_key"] = "identifier"
            fk = "    {{foreign_key}}: int\n"
            prefix = '"{{foreign_key}}", '
            base_index = '["{{foreign_key}}", "_id"], '
        add("orm.mongo." + role, params, '''
class {{name}}(Document):
    id: int = Field(alias="_id")
''' + fk + '''{{#fields}}    {{name}}: {{create_type}}{{create_default}}
{{/fields}}    created_at: datetime
    updated_at: datetime
    UNIQUE_FIELDS: ClassVar[dict] = {
{{#fields}}        "{{name}}": {"unique": {{unique}}, "bson_type": {"str": "string", "int": "int", "float": "double", "bool": "bool", "date": "date"}["{{type}}"]},
{{/fields}}    }
    QUERY_INDEXES: ClassVar[list] = [''' + base_index + '''] + [[''' + prefix + '''key, "_id"] for key in [{{filter_field}}, {{date_field}}] if key]

    class Settings:
        name = {{table}}
        bson_encoders = {datetime: lambda value: value, date: lambda value: datetime.combine(value, time(), timezone.utc)}
''')
    source = (TEMPLATES / "mongo_head.py.tpl").read_text() + '''

    async def workflow_rows(parent_id):
        await require_parent(parent_id)
        rows = await Child.find(Child.{{foreign_key}} == parent_id).sort("_id").limit(1001).to_list()
        if len(rows) > 1000:
            raise HTTPException(status_code=413, detail="workflow is limited to 1000 rows")
        return [ChildRead.model_validate(row).model_dump(mode="json") for row in rows]

    async def workflow_import(parent_id, records):
        async def write(session):
            await require_parent(parent_id, session, touch=True)
            return [ChildRead.model_validate(await insert(Child, {"{{foreign_key}}": parent_id, **data.model_dump()}, session)).model_dump(mode="json") for data in records]
        return await transaction(write)
'''
    add("api.business.head.mongodb", {"parent_name": "identifier", "child_name": "identifier", "foreign_key": "identifier"}, source)
    for role in ("parent", "child"):
        model = role.title()
        child = role == "child"
        params = {"path": "string"}
        if child:
            params["foreign_key"] = "identifier"
        args = "parent_id: int, " if child else ""
        item_args = "parent_id: int, child_id: int" if child else "parent_id: int"
        get = "await require_child(parent_id, child_id, session)" if child else "await require_parent(parent_id, session)"
        touch = "            await require_parent(parent_id, session, touch=True)\n" if child else ""
        add("api.mongo." + role + ".create", params, '''
    @router.post({{path}}, response_model=MODELRead, status_code=201)
    async def create_ROLE(ARGSdata: MODELCreate):
        async def write(session):
TOUCH            return await insert(MODEL, {EXTRA**data.model_dump()}, session)
        return await transaction(write)
'''.replace("MODEL", model).replace("ROLE", role).replace("ARGS", args).replace("TOUCH", touch).replace("EXTRA", '"{{foreign_key}}": parent_id, ' if child else ""))
        add("api.mongo." + role + ".get", {"path": "string"}, '''
    @router.get({{path}}, response_model=MODELRead)
    async def get_ROLE(ARGS):
        session = None
        return GET
'''.replace("MODEL", model).replace("ROLE", role).replace("ARGS", item_args).replace("GET", get))
        add("api.mongo." + role + ".patch", {"path": "string"}, '''
    @router.patch({{path}}, response_model=MODELRead)
    async def patch_ROLE(ARGS, data: MODELPatch):
        async def write(session):
            row = GET
            changes = encode(data.model_dump(exclude_unset=True))
            if changes:
                changes["updated_at"] = datetime.now(timezone.utc)
                await MODEL.get_pymongo_collection().update_one({"_id": row.id}, {"$set": changes}, session=session)
            return await MODEL.find_one(MODEL.id == row.id, session=session)
        return await transaction(write)
'''.replace("MODEL", model).replace("ROLE", role).replace("ARGS", item_args).replace("GET", get))
        restrict = ""
        if not child:
            params_delete = {"path": "string", "foreign_key": "identifier"}
            restrict = '''            await require_parent(parent_id, session, touch=True)
            if await Child.find_one(Child.{{foreign_key}} == parent_id, session=session):
                raise HTTPException(status_code=409, detail="parent has children")
'''
        else:
            params_delete = {"path": "string"}
        add("api.mongo." + role + ".delete", params_delete, '''
    @router.delete({{path}}, status_code=204)
    async def delete_ROLE(ARGS):
        async def write(session):
            row = GET
RESTRICT            await row.delete(session=session)
        await transaction(write)
        return Response(status_code=204)
'''.replace("ROLE", role).replace("ARGS", item_args).replace("GET", get).replace("RESTRICT", restrict))
        for kind in ("plain", "boolean", "date"):
            params = {"path": "string", "max_limit": "integer"}
            arguments = args
            filters = '        filters = {"{{foreign_key}}": parent_id}\n        await require_parent(parent_id)\n' if child else "        filters = {}\n"
            if child:
                params["foreign_key"] = "identifier"
            if kind != "plain":
                params["filter_field"] = "identifier"
                arguments += "{{filter_field}}: bool | None = None, "
                filters += '        if {{filter_field}} is not None:\n            filters["{{filter_field}}"] = {{filter_field}}\n'
            if kind == "date":
                params["date_field"] = "identifier"
                arguments += "before: date | None = None, "
                filters += '        if before is not None:\n            filters["{{date_field}}"] = {"$lt": datetime.combine(before, time(), timezone.utc), "$ne": None}\n'
            add("api.mongo." + role + ".list." + kind, params, '''
    @router.get({{path}}, response_model=list[MODELRead])
    async def list_ROLE(ARGSlimIt: int = Query(20, alias="limit", ge=1, le={{max_limit}}), offset: int = Query(0, ge=0)):
FILTERS        return await MODEL.find(filters).sort("_id").skip(offset).limit(limIt).to_list()
'''.replace("MODEL", model).replace("ROLE", role).replace("ARGS", arguments).replace("FILTERS", filters))
    add("api.mongo.batch", {"path": "string", "foreign_key": "identifier"}, '''
    @router.post({{path}}, response_model=BatchRead, status_code=201)
    async def create_batch(data: BatchCreate):
        async def write(session):
            parent = await insert(Parent, data.parent.model_dump(), session)
            children = [await insert(Child, {"{{foreign_key}}": parent.id, **item.model_dump()}, session) for item in data.children]
            return {"parent": parent, "children": children}
        return await transaction(write)
''')

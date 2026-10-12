"""Build a separate compact library; preserve both older libraries."""

from copy import deepcopy
import ast
import hashlib
import io
import json
from pathlib import Path
import re
import tokenize
import tomllib
from textwrap import dedent

from som_core import composed
from som_core.compact_names import source_names

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "som-code-python/data/blocks/flow-v7/blocks.json"
ORIGINAL_CATALOG = ROOT / "som-code-python/data/block-contracts/flow-v7.json"
OUT = ROOT / "som-code-python/data/blocks/flow-v7-compact"
CATALOG = ORIGINAL_CATALOG.with_name("flow-v7-compact.json")
TEMPLATES = ROOT / "som-core/block_templates/flow_compact"


def wrap_static_literals(source):
    """Author readable string pieces before freezing the block source."""
    lines=source.splitlines(keepends=True)
    offsets=[0]
    for line in lines: offsets.append(offsets[-1]+len(line))
    replacements=[]
    try: tokens=list(tokenize.generate_tokens(io.StringIO(source).readline))
    except tokenize.TokenError: return source
    for token in tokens:
        if token.type != tokenize.STRING or token.string.startswith(('"""', "'''")): continue
        try: value=ast.literal_eval(token.string)
        except (SyntaxError, ValueError): continue
        if not isinstance(value,str) or len(token.string) <= 75: continue
        pieces=[json.dumps(value[i:i+40],ensure_ascii=False) for i in range(0,len(value),40)]
        start=offsets[token.start[0]-1]+token.start[1]
        end=offsets[token.end[0]-1]+token.end[1]
        replacements.append((start,end,"(\n"+"\n".join(pieces)+"\n)"))
    for start,end,value in reversed(replacements): source=source[:start]+value+source[end:]
    return source


def main():
    originals = {block["id"]: block for block in json.loads(SOURCE.read_text())}
    library = deepcopy(originals)
    catalog = json.loads(ORIGINAL_CATALOG.read_text())
    entries = {}
    for identity, meta in catalog["blocks"].items():
        block = deepcopy(originals[identity])
        meta = deepcopy(meta)
        new_id = identity if identity.startswith("flow.") else "compact."+identity
        block.update(id=new_id, version=3)
        if identity.startswith("flow."):
            block.update(template="", params={}, imports=[])
            meta.update(params={}, bindings={})
        elif identity == 'v7.service.business.project':
            digest = hashlib.sha256((ROOT/'som-rs/contracts/python-quality-v1/exceptions.json').read_bytes()).hexdigest()
            block['template'] = re.sub(r"exception_registry = '''.*?'''", 'exception_registry_ref = "sha256:'+digest+'"', block['template'], flags=re.S)
            block['template'] = block['template'].replace(
                '"sqlalchemy==2.0.44",',
                '"sqlalchemy[asyncio]==2.0.44",\n    "greenlet==3.5.6",',
            )
            dependency_config = tomllib.loads(block['template'])
            dependencies = dependency_config['project']['dependencies'] + dependency_config['dependency-groups']['dev']
            dependencies_hash = hashlib.sha256(json.dumps(dependencies, separators=(',', ':')).encode()).hexdigest()
            block['template'] = re.sub(r'dependencies_sha256 = "[a-f0-9]+"',
                                       'dependencies_sha256 = "'+dependencies_hash+'"', block['template'])
        elif identity == 'orm.base':
            block['template'] = (TEMPLATES/'schema_postgresql.py.tpl').read_text()
            block['imports'] = ['import hashlib', 'import sqlalchemy as sa', 'from sqlalchemy.sql.schema import SchemaItem', 'from sqlalchemy.sql.functions import now as database_now']
        elif identity == 'auth.common':
            block['template'] = (TEMPLATES/'auth_common.py.tpl').read_text()
            block['version'] = 4
        elif identity in {'auth.jwt', 'auth.firebase'}:
            block['template'] = (TEMPLATES/'auth_bearer.py.tpl').read_text() + block['template']
            block['version'] = 4
        elif identity == 'auth.google_oauth':
            block['template'] = (TEMPLATES/'auth_google_oauth.py.tpl').read_text()
            block['imports'] += ['from contextlib import contextmanager', 'from collections.abc import Iterator']
            block['version'] = 5
        elif identity.startswith('v7.service.business.app.'):
            start = block['template'].index('JSON_VALUE:')
            block['template'] = block['template'][:start] + '''class _ValidationIssue(TypedDict):
    """Public validation details omit untrusted input and exception objects."""
    type: str
    loc: tuple[str | int, ...]
    msg: str


ISSUES: TypeAdapter[list[_ValidationIssue]] = TypeAdapter(list[_ValidationIssue])


@app.exception_handler(RequestValidationError)
async def validation_error(_request: Request, error: RequestValidationError) -> JSONResponse:
    """Return safe, typed diagnostics, including non-finite input failures."""
    issues = ISSUES.validate_python(error.errors(), strict=True)
    return JSONResponse(status_code=422, content={"detail": issues})
'''
            block['imports'].append('from typing import TypedDict')
            # MongoDB keeps its existing lifespan and client binding.
            if identity.endswith('.mongodb'):
                block['imports'] = [statement.replace('import client, database,', 'import mongo,').replace('import database, get_session,', 'import mongo,') for statement in block['imports']]
                block['template'] = block['template'].replace('database.command(', 'mongo.database.command(').replace('client.close()', 'mongo.close()')
        elif identity in {'storage.s3', 'storage.gcs', 'storage.common'}:
            block['template'] = (TEMPLATES/(identity.replace('.', '_')+'.py.tpl')).read_text()
            if identity != 'storage.common':
                block['imports'] += ['from contextlib import contextmanager', 'from collections.abc import Iterator']
            else:
                block['template'] = (TEMPLATES/'storage_errors_common.py.tpl').read_text() + block['template']
                block['imports'] += ['from contextlib import suppress', 'from typing import ClassVar']
            block['version'] = 4
        elif identity.startswith('v7.orm.related.'):
            child = '.child.' in identity
            filters = '{{filter_field}}' if not identity.endswith('.plain') else 'None'
            date = '{{date_field}}' if identity.endswith('.date') else 'None'
            relation = ', {{foreign_key}}, {{target}}' if child else ''
            if child:
                block['params']['foreign_key'] = 'string'
                meta['params']['foreign_key'] = 'string'
            block['template'] = '{{name}} = resource_table({{table}}, (\n{{#fields}}    sa.Column({{name_literal}}, {{migration_type}}{{migration_options}}),\n{{/fields}}), ('+filters+', '+date+')'+relation+')\n'
            block['imports'] = ['import sqlalchemy as sa']
        elif identity == 'orm.flow.journal':
            block['template'] = block['template'].replace('JSONB', '_SomJSONB').replace('Base.metadata', 'SCHEMA')
            block['imports'] = [statement.replace('import JSONB', 'import JSONB as _SomJSONB') for statement in block['imports']]
        elif identity == 'migration.related.head':
            block['template'] = block['template'][:block['template'].index('def upgrade')]
            block['params']['package'] = 'identifier'
            meta['params']['package'] = 'identifier'
            meta['bindings']['package'] = {'$ref': 'package'}
            block['imports'] = ['from alembic import op', 'from {{package}}.schema_v001 import SCHEMA']
        elif identity.startswith('v7.migration.related.') and not identity.endswith('.tail') or identity == 'migration.flow.journal':
            block['template'] = ''
            block['imports'] = []
        elif identity == 'v7.migration.related.tail':
            block['template'] = '''def upgrade() -> None:
    """Create the immutable version 1 schema."""
    for table in SCHEMA.sorted_tables:
        # pylint: disable-next=no-member
        op.create_table(table.name, *table.columns, *table.constraints, _constraints_included=True)
        for index in table.indexes:
            if index.name is None:
                raise RuntimeError("migration index name is required")
            # pylint: disable-next=no-member
            op.create_index(index.name, table.name, [column.name for column in index.columns], unique=index.unique)


def downgrade() -> None:
    """Drop this schema in dependency order."""
    for table in reversed(SCHEMA.sorted_tables):
        # pylint: disable-next=no-member
        op.drop_table(table.name)
'''
            block['imports'] = ['from alembic import op']
        elif identity == 'service.alembic.env':
            block['template'] = block['template'].replace('Base.metadata', 'SCHEMA')
            block['imports'] = [statement.replace('models import Base', 'schema_v001 import SCHEMA') for statement in block['imports']]
        elif identity == 'schema.related.bounds':
            block['template'] = block['template'].replace('def bounded_values(', 'def _som_bounds(')
            block['template'] += '''

class PartialInput(BoundedInput):
    """Validate null values in a partial update."""
    _som_non_null: ClassVar[set[str]] = set()

    @model_validator(mode="after")
    def _som_nulls(self) -> Self:
        """Reject null for a non-null column."""
        for name in self.model_fields_set & self._som_non_null:
            if getattr(self, name) is None: raise ValueError(f"{name} cannot be null")
        return self


class ManagedRead(BaseModel):
    """Expose managed fields without private database data."""
    model_config = ConfigDict(from_attributes=True, extra="ignore")
    id: int
    created_at: datetime
    updated_at: datetime
'''
        elif identity == "schema.flow":
            block['version'] = 5
            source = block["template"]
            source = source[:source.index("class InputRef")] + source[source.index("class Effect"):source.index("PLAN_SOURCE")] + source[source.index("def integer"):]
            source = 'FLOW_NAME = {{flow_name}}\nPLAN_HASH = {{plan_hash}}\n\n' + source
            source = source.replace('class CrudRequest(BaseModel):', 'type CrudOperation = Literal["create", "get", "list", "patch", "delete"]\n\n\nclass CrudRequest(BaseModel):')
            source = source.replace('    operation: Literal["create", "get", "list", "patch", "delete"]', '    operation: CrudOperation')
            source = source.replace('    group: str\n', '    group: str = ""\n')
            source = source.replace('    block: str\n', '')
            source = source.replace('    undo: str\n', '    undo: Literal["delete_created", "restore_patch"]\n')
            source = source.replace('    fields: list[str] = Field(default_factory=list)\n', '')
            source = source.replace('    read: type[BaseModel]', '    read: type[ParentRow] | type[ChildRow]')
            source = source.replace('class ResourceSpec(BaseModel):', 'class ResourceSpec(NamedTuple):')
            block['imports'].append('from typing import NamedTuple')
            source = source.replace('def integer(value: JsonValue)', 'def integer(value: object)').replace('def text_value(value: JsonValue)', 'def text_value(value: object)')
            source = source.replace('    limit: int = 20', '    limit: int = Field(default=20, ge=1, le=1001)').replace('    offset: int = 0', '    offset: int = Field(default=0, ge=0)')
            source = source.replace('    {{name}}: {{input_type}}', '    {{variable}}: {{input_type}} = Field(alias={{name_literal}})')
            source = source.replace('model_config = ConfigDict(extra="forbid", allow_inf_nan=False)', 'model_config = ConfigDict(extra="forbid", allow_inf_nan=False, serialize_by_alias=True)')
            block['params']['flow_inputs'] = 'flow_inputs_v7'
            source += '\n\ndef failure(status: int, detail: str) -> HTTPException:\n    """Create a controlled HTTP failure."""\n    return HTTPException(status_code=status, detail=detail)\n'
            source += '''

type ResourceRow = ParentRow | ChildRow


class WorkflowReceipt(BaseModel):
    """Typed output from the selected workflow."""
    body: str
    size: int
    sha256: str
    data: JsonValue


def result_json(value: object) -> JsonValue:
    """Serialize checked service data at an HTTP or journal boundary."""
    if isinstance(value, BaseModel):
        return json_object(value.model_dump(mode="json"))
    if isinstance(value, list):
        return [result_json(item) for item in value]
    return json_value(value)


def model_json(value: BaseModel, partial: bool = False) -> JsonObject:
    """Validate model data at a JSON boundary, preserving field aliases."""
    return json_object(value.model_dump(mode="json", exclude_unset=partial, by_alias=True))
'''
            block["template"] = source
            block["params"].pop("plan_json")
            block["params"].update(flow_name="string", plan_hash="string")
            meta["bindings"].pop("plan_json")
            meta["bindings"].update(flow_name={"$ref":"flow.name"}, plan_hash={"$ref":"flow.hash"})
            meta["params"] = deepcopy(block["params"])
        elif identity.startswith("api.flow.engine."):
            block["template"] = "{{flow_program}}"
            block["params"] = {"flow_program":"flow_engine"}
            block["version"] = 5
            meta.update(params=deepcopy(block["params"]), bindings={"flow_program":{"$ref":"flow.program"}})
            block["imports"] += ["from .schemas import FLOW_NAME, PLAN_HASH, ResourceRow, result_json, model_json, BatchRead, ParentRow, ChildRow, ParentInputData, ChildInputData, ParentPatchData, ChildPatchData, WorkflowReceipt", "from .integrations import flow_object_version, FlowObject, check_flow_body", "from pydantic import BaseModel, ConfigDict, Field, TypeAdapter", "from datetime import date"]
            block['imports'].append('from collections.abc import Awaitable, Callable')
        elif identity.startswith("v7.api.business.head."):
            block["template"] = (TEMPLATES/"routes.py.tpl").read_text()
            block['template'] = block['template'][:block['template'].index('\n\nasync def _workflow_import(')]
            block["imports"] += ["from .database import RESOURCES", "from pydantic import BaseModel, Field, TypeAdapter, ValidationError", "from fastapi import Request", "from datetime import date", "from types import GenericAlias", "from .schemas import CrudOperation, ResourceRow, result_json", "from .flows import workflow_rows"]
            if identity.endswith('.mongodb'):
                block['imports'] = [statement for statement in block['imports'] if statement != 'from .database import client, database']
                block['imports'] += ['import asyncio', 'from pymongo.errors import OperationFailure as _ConcurrentWrite']
                block['template'] = block['template'].replace(
                    '    async with store.transaction():\n        return await store.mutate(request)\n',
                    '''    for attempt in range(3):
        try:
            async with store.transaction():
                return await store.mutate(request)
        except _ConcurrentWrite as error:
            if error.code != 112 or not error.has_error_label("TransientTransactionError"):
                raise failure(503, "database write outcome unavailable") from error
            if attempt == 2:
                raise failure(409, "concurrent resource write") from error
            await asyncio.sleep(0.01)
    raise RuntimeError("unreachable mutation state")
''',
                )
        elif identity == 'v7.api.business.workflow.csv':
            importer = (TEMPLATES/'routes.py.tpl').read_text().split('\n\nasync def _workflow_import(', 1)[1]
            block['template'] = 'async def _workflow_import(' + importer + '\n\n' + block['template']
            block['template'] = block['template'].replace(
                '[ChildCreate.model_validate(row) for row in csv_records(data)]',
                'csv_records(data)',
            )
            block['imports'] += ['from .database import FlowStore', 'from .schemas import CrudRequest, json_object']
            block['imports'].append('from .schemas import ChildInputData, ChildRow')
            block['template'] = block['template'].replace('-> list[JsonObject]:', '-> list[ChildRow]:')
        elif identity in {'test.related.payload.parent', 'test.related.payload.child'}:
            # The public acceptance suite supplies its own independent data.
            # These fixtures are not called by the generated unit tests.
            block['template'] = ''
            block['imports'] = []
            block['version'] = 4
        elif identity == 'v7.test.composed.behavior':
            block['template'] = (TEMPLATES/'tests.py.tpl').read_text()
            block['params'] = {'routes': 'routes_v7', 'flow_name': 'string'}
            meta['params'] = deepcopy(block['params'])
            meta['bindings']['flow_name'] = {'$ref': 'flow.name'}
            block['version'] = 4
        elif identity in {"schema.related.parent", "schema.related.child"}:
            source = block["template"]
            source = source.replace('class {{name}}Read(BaseModel):', 'class {{name}}Read({{name}}Create, ManagedRead):')
            source = source.replace('    model_config: ClassVar[ConfigDict] = ConfigDict(from_attributes=True)\n', '')
            source = re.sub(r'^    (?:id|created_at|updated_at): (?:int|datetime)\n', '', source, flags=re.M)
            source = source.replace('{{#fields}}    {{name}}: {{read_type}}\n{{/fields}}', '')
            source = source.replace('    created_at: datetime\n', '')
            source = source.replace('class {{name}}Patch(BoundedInput):', 'class {{name}}Patch(PartialInput):')
            source = source[:source.index('    @model_validator')] + '    _som_non_null: ClassVar[set[str]] = {\n{{#fields}}        {{non_null_entry}}\n{{/fields}}    }\n'
            source = source.replace('    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid")\n', '')
            role = 'Parent' if identity.endswith('.parent') else 'Child'
            source += '\n\n'+role+'Row = {{name}}Read\n'+role+'InputData = {{name}}Create\n'+role+'PatchData = {{name}}Patch\n'
            block['template'] = source
        elif identity == "service.flow.store.postgresql":
            source = (TEMPLATES/'store_resources.py.tpl').read_text()
            source += '\n\nflow_engine = create_async_engine(database_url(), pool_pre_ping=True)\n\n\n'
            source += '''ROW: TypeAdapter[dict[str, object]] = TypeAdapter(dict[str, object])


def table_for(role: str) -> sa.Table:
    """Use only frozen resource tables."""
    return SCHEMA.tables[RESOURCES[role].table]


def run_key(record: RunRecord) -> dict[str, str | int]:
    """Bind the exact execution epoch."""
    return {"run_id": record.run_id, "epoch": record.epoch}


def patch_statement(table: sa.Table, values: Mapping[str, object]) -> Update:
    """Change fields and their private revision together."""
    return table.update().values(values).values(updated_at=datetime.now(UTC), _som_revision=table.c["_som_revision"] + 1)


'''
            block["template"] = source + (TEMPLATES/"store_postgresql.py.tpl").read_text()
            block["imports"] = [entry for entry in block["imports"] if entry != 'from sqlalchemy import select']
            block["imports"] += ["import sqlalchemy as sa", "from .schemas import json_value", "from sqlalchemy.sql import Executable", "from sqlalchemy.sql.dml import Update", "from sqlalchemy.dialects.postgresql import insert as pg_insert", "from collections.abc import Mapping"]
            block['template'] += '\n\n' + (TEMPLATES/'store_batch.py.tpl').read_text()
            block['version'] = 5
        elif identity == 'service.flow.store.mongodb':
            header = (TEMPLATES/'store_resources.py.tpl').read_text() + '\n\n'
            store = (TEMPLATES/'store_mongodb_lean.py.tpl').read_text()
            store = store.replace('client.start_session()', 'mongo.client.start_session()').replace('database[', 'mongo.database[')
            block['template'] = header + store
            block['imports'].append('from .schemas import json_value')
            block['imports'].append('from pymongo.errors import OperationFailure as _ClaimConflict')
            block['template'] += '\n\n' + (TEMPLATES/'store_batch.py.tpl').read_text()
            block['version'] = 5
        elif meta.get('operation') == ['batch', 'batch_create']:
            assert 'integer(parent["id"])' in block['template'], identity
            block['template'] = block['template'].replace(
                'children = [await store.mutate(CrudRequest(role="child", operation="create", parent_id=integer(parent["id"]), data=json_object(child.model_dump(mode="json")))) for child in payload.children]',
                'children = await store.batch(ParentRow.model_validate(parent).id, payload.children)',
            )
            block['imports'].append('from .schemas import ParentRow')
            block['version'] = 4
        elif meta.get("operation") and meta["operation"][0] in {"parent", "child"}:
            role, action = meta["operation"]
            path = "{{path}}"
            block["template"] = 'register_route('+path+', "'+role+'", "'+action+'"'+(', {{max_limit}}' if action == "list" else '')+')\n'
            block["imports"] = []
            kept = {"path", "max_limit"} if action == "list" else {"path"}
            block["params"] = {key:kind for key,kind in block["params"].items() if key in kept}
            meta["params"] = deepcopy(block["params"])
            meta["bindings"] = {key:binding for key,binding in meta["bindings"].items() if key in kept}
        elif identity in {"v7.orm.composed.mongo.parent", "v7.orm.composed.mongo.child"}:
            child = identity.endswith('.child')
            prefix = '["{{foreign_key}}"]' if child else '[]'
            index = '[ ["{{foreign_key}}", "_id"], *_query_indexes([{{filter_field}}, {{date_field}}], '+prefix+') ]' if child else '_query_indexes([{{filter_field}}, {{date_field}}], [])'
            block['template'] = ('CHILD' if child else 'PARENT') + ' = _Collection({{table}}, {\n{{#fields}}    {{unique_index_entry}}\n{{/fields}}}, '+index+')\n'
            block['imports'] = []
        elif identity == "v7.orm.composed.mongo.base":
            block['template'] = (TEMPLATES/'schema_mongodb.py.tpl').read_text()
            block['imports'] = ['from datetime import UTC, date, datetime, time', 'from typing import NamedTuple']
        elif identity == 'v7.service.business.database.mongodb':
            block['imports'] = [statement.replace(
                'import {{parent_name}}, {{child_name}}',
                'import PARENT as _ParentDocument, CHILD as _ChildDocument',
            ) for statement in block['imports']]
            block['template'] = (TEMPLATES/'database_mongodb.py.tpl').read_text()
            block['imports'] = [statement for statement in block['imports'] if statement != 'from beanie import init_beanie']
            block['imports'].append('from pymongo.asynchronous.database import AsyncDatabase')
        elif identity in {'v7.migration.mongo', 'v7.service.business.app.mongodb'}:
            block['imports'] = [statement.replace('import client, database,', 'import mongo,').replace('import database, get_session,', 'import mongo,') for statement in block['imports']]
            block['template'] = block['template'].replace('database.command(', 'mongo.database.command(').replace('database.drop_collection(', 'mongo.database.drop_collection(').replace('client.close()', 'mongo.close()')
        elif identity == "storage.flow.none":
            block["template"] = '''

def verify_flow_storage() -> None:
    """This capability has no object provider."""
'''
            block["version"] = 4
        elif identity == "storage.flow.s3":
            block['template'] = (TEMPLATES/'storage_receipts.py.tpl').read_text() + (TEMPLATES/'storage_flow_s3.py.tpl').read_text()
            block['imports'].append('from pydantic import ConfigDict, TypeAdapter')
            block['version'] = 4
        elif identity == "storage.flow.gcs":
            block['template'] = (TEMPLATES/'storage_receipts.py.tpl').read_text() + (TEMPLATES/'storage_flow_gcs.py.tpl').read_text()
            block['imports'].append('from pydantic import ConfigDict, TypeAdapter')
            block['version'] = 4
        if identity.startswith('workflow.flow.'):
            source = block['template']
            source = source.replace('-> JsonObject:', '-> WorkflowReceipt:')
            source = source.replace('rows: list[JsonObject]', 'rows: list[ChildRow]')
            old_receipt = '{"body": base64.b64encode(body).decode(), "size": len(body), "sha256": hashlib.sha256(body).hexdigest(), "data": data}'
            assert old_receipt in source
            source = source.replace(old_receipt, 'WorkflowReceipt(body=base64.b64encode(body).decode(), size=len(body), sha256=hashlib.sha256(body).hexdigest(), data=data)')
            if identity.endswith(('.json_export', '.gzip_export', '.csv')):
                source = source.replace('json_value(rows)', 'result_json(rows)')
            if identity.endswith('.csv'):
                source = source[:source.index('CUSTOM_FIELD_SOURCE')] + (TEMPLATES/'workflow_csv.py.tpl').read_text()
                block['params']['fields'] = 'fields_v7'
                meta['bindings']['fields'] = {'$ref': 'child.fields'}
                block['imports'].append('from .schemas import ChildInputData')
                block['imports'].append('from itertools import islice')
            if identity.endswith(('.decimal_summary', '.statistics')):
                source = source.replace('VALUE_FIELD = {{value_field}}\n', '').replace('GROUP_FIELD = {{group_field}}\n', '')
                source = source.replace('row[VALUE_FIELD]', 'row.{{value_field}}').replace('row[GROUP_FIELD]', 'row.{{group_field}}')
                for key in ('value_field', 'group_field'):
                    if key in block['params']:
                        block['params'][key] = 'identifier'
                        meta['params'][key] = 'identifier'
            block['template'] = source
            block['imports'].append('from .schemas import WorkflowReceipt, ChildRow, result_json, json_object')
            block['version'] = 4
        if identity.startswith('v7.api.business.workflow.'):
            block['template'] = block['template'].replace('text_value(payload["body"])', 'payload.body').replace('payload["data"]', 'payload.data')
            block['version'] = 4
        # Models stay typed inside the service. Only these shared boundaries
        # serialize them for HTTP, provider data, or the durable journal.
        block['template'] = re.sub(
            r'json_object\(([A-Za-z_]\w*(?:\.\w+)*)\.model_dump\(mode="json"(?:, exclude_unset=(True|patch))?\)\)',
            lambda match: 'model_json('+match[1]+(', '+match[2] if match[2] else '')+')',
            block['template'],
        )
        if 'model_json(' in block['template'] and meta['slot'] != 'schemas':
            block['imports'].append('from .schemas import model_json')
        block["template"] = re.sub(r'"""Typed service contract\."""', '"""Typed data."""', block["template"])
        if 'fields' in block['params']:
            block['params']['fields'] = 'fields_v7'
            meta['params']['fields'] = 'fields_v7'
            block['template'] = block['template'].replace(', nullable={{nullable}}, unique={{unique}}{{sql_default}}', '{{column_options}}')
            block['template'] = block['template'].replace(', nullable={{nullable}}, unique={{unique}}{{migration_default}}', '{{migration_options}}')
        if identity in {'service.flow.store.postgresql', 'service.flow.store.mongodb'}:
            block['imports'] = [entry for entry in block['imports']
                                if not entry.startswith(('from .schemas import {{parent_name}}Create',
                                                         'from .schemas import {{child_name}}Create'))]
            block['template'] = re.sub(r'integer\(json_value\(([^()\n]+)\)\)', r'integer(\1)', block['template'])
            block['template'] = block['template'].replace(
                'def read_row(role: str, value: object) -> JsonObject:',
                'def read_row(role: str, value: object) -> ResourceRow:',
            ).replace(
                'return json_object(RESOURCES[role].read.model_validate(value).model_dump(mode="json"))',
                'return RESOURCES[role].read.model_validate(value)',
            )
            block['imports'].append('from .schemas import ParentRow, ParentInputData, ParentPatchData, ChildRow, ChildInputData, ChildPatchData, ResourceRow')
        if catalog['slots'][meta['slot']].endswith('.py') and meta['slot'] not in {'schemas', 'migration', 'models'}:
            block['template'] = re.sub(r'HTTPException\(status_code=([^,\n]+), detail=([^\n]+?)\)', r'failure(\1, \2)', block['template'])
            block['imports'].append('from .schemas import failure')
        if meta["slot"] == "app":
            block["imports"] = [statement for statement in block["imports"] if statement != "import json"]
            if 'ChildCreate' in block['template'] or '{{child_name}}Create.model_validate' in block['template']:
                block['template'] = block['template'].replace('ChildCreate', '_ChildInput').replace('{{child_name}}Create.model_validate', '_ChildInput.model_validate')
                block['imports'].append('from .schemas import {{child_name}}Create as _ChildInput')
                block['params']['child_name'] = 'identifier'
                meta['bindings']['child_name'] = {'$ref': 'child.name'}
        block['imports'] = [statement.replace('.models import Base,', '.schema_v001 import SCHEMA,').replace('.models import', '.schema_v001 import') for statement in block['imports']]
        if identity == 'service.alembic.env':
            block['template'] = block['template'].replace('Base.metadata', 'SCHEMA')
        sections = re.findall(r'\{\{#(\w+)\}\}', block['template'])
        removed = re.sub(r'\{\{#\w+\}\}.*?\{\{/\w+\}\}', '', block['template'], flags=re.S)
        used = set(re.findall(r'\{\{([A-Za-z_.]+)\}\}', removed + '\n'.join(block['imports']))) | set(sections)
        if 'flow_inputs' in sections:
            used.update({'parent_name', 'child_name'})
        block['params'] = {key:kind for key,kind in block['params'].items() if key in used}
        meta['params'] = deepcopy(block['params'])
        meta['bindings'] = {key:value for key,value in meta['bindings'].items() if key in used}
        if catalog["slots"][meta["slot"]].endswith(".py"):
            block["template"] = source_names(block["template"])
            block["imports"] = [source_names(statement) for statement in block["imports"]]
            block["template"] = wrap_static_literals(block["template"])
        meta["source_sha256"] = hashlib.sha256(composed.canonical(block).encode()).hexdigest()
        entries[new_id] = meta
        library[new_id] = block
    catalog.update(version="som.flow.compact-block-contracts.v1", blocks=entries)
    catalog['slots']['models'] = '{package}/schema_v001.py'
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT/"blocks.json").write_text(json.dumps(list(library.values()),indent=2)+"\n")
    CATALOG.write_text(json.dumps(catalog,indent=2)+"\n")
    old = json.loads((SOURCE.parent.parent/"composed-quality/blocks.json").read_text())
    assert all(library[block["id"]] == block for block in old)
    print(json.dumps({"blocks":len(library),"selectable":len(entries),"old_blocks_unchanged":True}))


if __name__ == "__main__": main()

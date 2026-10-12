"""Check both database blocks at their first-party value boundary."""

import ast
from datetime import UTC, date, datetime
import json
from pathlib import Path
from types import ModuleType

from pydantic import TypeAdapter
import pytest

from som_core import flow_compact
from som_core.v1 import assemble, load_blocks

ROOT = Path(__file__).resolve().parents[2]


def boundary(database):
    """Load the actual schemas and shared value code without database I/O."""
    prompt = json.loads((ROOT / 'som-core/data/flow-v7-r10/dev.jsonl')
                        .read_text().splitlines()[0])['prompt']
    prompt['capabilities']['database'] = database
    library = load_blocks(ROOT / 'som-code-python/data/blocks/flow-v7-compact')
    plan = flow_compact.resolve(prompt, flow_compact.reference(prompt, library))
    files = {}
    for file in plan['files']:
        if file['path'].endswith(('/schemas.py', '/database.py')):
            files.update(assemble({'version': 'som.dsl.v2', 'files': [file]}, library))
    module = ModuleType('database_value_boundary')
    exec(compile(files[prompt['package'] + '/schemas.py'], 'approved-schema', 'exec'),
         module.__dict__)
    for role in ('parent', 'child'):
        name = prompt['resources'][role]['name']
        stem = role.capitalize()
        for suffix, alias in [('Create', 'Input'), ('Patch', 'Changes'), ('Read', 'View')]:
            module.__dict__['_' + stem + alias] = module.__dict__[name + suffix]
    source = files[prompt['package'] + '/database.py']
    nodes = []
    for node in ast.parse(source).body:
        if isinstance(node, ast.FunctionDef) and node.name in {'read_row', 'write_values'}:
            nodes.append(node)
        elif isinstance(node, ast.TypeAlias) and node.name.id == 'ColumnValues':
            nodes.append(node)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            if node.target.id in {'RESOURCES', 'COLUMN_VALUES'}:
                nodes.append(node)
    assert len(nodes) == 5
    exec(compile(ast.Module(body=nodes, type_ignores=[]), 'approved-value-boundary', 'exec'),
         module.__dict__)
    return module, prompt


@pytest.mark.parametrize('database', ['postgresql', 'mongodb'])
def test_resource_bindings_are_typed_and_immutable(database):
    module, _ = boundary(database)
    spec = module.RESOURCES['parent']
    assert spec.create is module._ParentInput
    assert spec.patch is module._ParentChanges
    assert spec.read is module._ParentView
    with pytest.raises(AttributeError):
        spec.table = 'other_table'


@pytest.mark.parametrize('database', ['postgresql', 'mongodb'])
def test_create_defaults_and_date_conversion_keep_exact_values(database):
    module, _ = boundary(database)
    payload = {'title': 'recipe', 'servings': 0}
    first = module.write_values('parent', payload)
    adapter = module.COLUMN_VALUES
    assert first == {**payload, 'active': False, 'due': None, 'memo': None}
    second = module.write_values('parent', {**payload, 'due': '2026-10-04'})
    assert second['due'] == date(2026, 10, 4)
    assert second['active'] is False
    assert module.COLUMN_VALUES is adapter
    assert payload == {'title': 'recipe', 'servings': 0}


@pytest.mark.parametrize('database', ['postgresql', 'mongodb'])
def test_patch_keeps_unsubmitted_fields_and_can_clear_nullable_fields(database):
    module, _ = boundary(database)
    assert module.write_values('parent', {}, True) == {}
    assert module.write_values('parent', {'memo': None, 'active': False}, True) == {
        'memo': None, 'active': False,
    }


@pytest.mark.parametrize('database', ['postgresql', 'mongodb'])
@pytest.mark.parametrize('payload,patch', [
    ({'title': 'missing servings'}, False),
    ({'servings': 2**31, 'title': 'too large'}, False),
    ({'title': 'x' * 256, 'servings': 1}, False),
    ({'active': None}, True),
    ({'title': None}, True),
    ({'id': 1}, True),
    ({'created_at': '2026-10-04T00:00:00Z'}, True),
    ({'unknown': 'private input'}, True),
    ({'due': '2026-02-30'}, True),
])
def test_invalid_business_values_still_fail_before_database_writes(database, payload, patch):
    module, _ = boundary(database)
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as error:
        module.write_values('parent', payload, patch)
    assert error.value.status_code == 422
    assert error.value.detail == 'invalid resource input'


@pytest.mark.parametrize('database', ['postgresql', 'mongodb'])
def test_read_model_discards_private_columns_and_keeps_managed_values(database):
    module, _ = boundary(database)
    now = datetime.now(UTC)
    source = {'id': 7, 'title': 'saved', 'servings': 0,
              'created_at': now, 'updated_at': now, '_som_revision': 11}
    result = module.read_row('parent', source)
    assert result.id == 7 and result.servings == 0
    assert result.created_at == now and result.updated_at == now
    assert '_som_revision' not in module.model_json(result)

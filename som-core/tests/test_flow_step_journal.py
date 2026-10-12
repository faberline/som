"""Check compiled result timing and staged journal rollback."""

import ast
import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
import json
from pathlib import Path
from types import ModuleType

from fastapi import HTTPException
import pytest

from som_core import flow_compact
from som_core.v1 import assemble, load_blocks

ROOT = Path(__file__).resolve().parents[2]


class Store:
    """Keep unit checkpoints visible without connecting to a database."""

    def __init__(self):
        self.saved = []
        self.fail = False

    @asynccontextmanager
    async def transaction(self):
        yield

    async def save(self, record):
        if self.fail:
            raise HTTPException(503, 'lease lost')
        self.saved.append(record.model_copy(deep=True))


def context():
    """Load the actual generated schemas and four journal methods."""
    prompt = json.loads((ROOT / 'som-core/data/flow-v7-r10/dev.jsonl')
                        .read_text().splitlines()[0])['prompt']
    blocks = load_blocks(ROOT / 'som-code-python/data/blocks/flow-v7-compact')
    plan = flow_compact.reference(prompt, blocks)
    lowered = flow_compact.resolve(prompt, plan)
    file = next(item for item in lowered['files'] if item['path'].endswith('/schemas.py'))
    schema = assemble({'version': 'som.dsl.v2', 'files': [file]}, blocks)[file['path']]
    module = ModuleType('compiled_step_journal')
    exec(compile(schema, 'approved-schema', 'exec'), module.__dict__)
    engine = flow_compact.compile_engine({'prompt': prompt, 'plan': plan['flow']})
    tree = ast.parse(engine)
    node = next(item for item in tree.body if isinstance(item, ast.ClassDef)
                and item.name == '_Context')
    node.body = [item for item in node.body if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
                 and item.name in {'__init__', 'save_result', 'transaction', 'persist'}]
    assert len(node.body) == 4
    module.__dict__.update(_Store=Store, AsyncIterator=AsyncIterator,
                           asynccontextmanager=asynccontextmanager)
    exec(compile(ast.Module(body=[node], type_ignores=[]), 'approved-journal', 'exec'),
         module.__dict__)
    record = module._Run(run_id='unit-run', owner='owner', flow='lookup', key='key',
                         request_hash='hash', inputs={'parent_id': 7})
    return module._Context(record), module


def test_read_result_is_recorded_before_its_checkpoint():
    ctx, module = context()
    value = module.ParentInputData(title='saved', servings=0)
    assert asyncio.run(ctx.save_result('step', value)) is value
    assert len(ctx.store.saved) == 1
    assert ctx.store.saved[0].values['step'] == module.model_json(value)
    value.title = 'changed later'
    assert ctx.record.values['step']['title'] == 'saved'


def test_transaction_result_waits_for_its_single_group_checkpoint():
    ctx, module = context()
    value = module.ParentInputData(title='saved', servings=0)

    async def run():
        async with ctx.transaction():
            assert await ctx.save_result('step', value, False) is value
            assert ctx.store.saved == []

    asyncio.run(run())
    assert len(ctx.store.saved) == 1
    assert ctx.store.saved[0].values['step'] == module.model_json(value)


def test_failed_group_discards_staged_values_and_effects():
    ctx, module = context()
    ctx.store.fail = True

    async def run():
        async with ctx.transaction():
            ctx.record.effects.append(module.Effect(step='created', undo='delete_created', owned=True))
            await ctx.save_result('step', {'title': 'uncommitted'}, False)

    with pytest.raises(HTTPException) as error:
        asyncio.run(run())
    assert error.value.status_code == 503
    assert ctx.record.values == {} and ctx.record.effects == []
    assert ctx.store.saved == []


def test_unserializable_result_cannot_enter_the_journal():
    ctx, _ = context()
    with pytest.raises(ValueError):
        asyncio.run(ctx.save_result('step', object()))
    assert ctx.record.values == {} and ctx.store.saved == []


def test_object_write_uses_the_helper_checkpoint_and_resources_keep_validation():
    entries = (ROOT / 'som-core/data/flow-v7-r10/formal.jsonl').read_text().splitlines()
    prompt = next(json.loads(line)['prompt'] for line in entries
                  if json.loads(line)['id'] == 'cap-005')
    blocks = load_blocks(ROOT / 'som-code-python/data/blocks/flow-v7-compact')
    plan = flow_compact.reference(prompt, blocks)
    source = flow_compact.compile_program({'prompt': prompt, 'plan': plan['flow']})
    tree = ast.parse(source)
    forward = next(node for node in tree.body if isinstance(node, ast.AsyncFunctionDef)
                   and node.name == 'forward')
    writes = [node for node in ast.walk(forward) if isinstance(node, ast.Call)
              and isinstance(node.func, ast.Name) and node.func.id == 'flow_put']
    assert writes
    assignments = [node for node in ast.walk(forward) if isinstance(node, ast.Assign)
                   and isinstance(node.value, ast.Await) and isinstance(node.value.value, ast.Call)
                   and isinstance(node.value.value.func, ast.Name)
                   and node.value.value.func.id == 'flow_put']
    assert len(assignments) == len(writes)
    assert 'ParentRow.model_validate(await ctx.resource(' in source
    assert 'ChildRow.model_validate(await ctx.resource(' in source
    assert 'ctx.checked(' not in source

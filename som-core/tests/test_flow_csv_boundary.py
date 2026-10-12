"""Verify the bounded CSV boundary with real generated input models."""

import base64
import csv
import hashlib
import io
import json
from itertools import islice
from pathlib import Path
from types import ModuleType

from fastapi import HTTPException
from pydantic import TypeAdapter
import pytest

from som_core import flow_compact
from som_core.v1 import _render, assemble, load_blocks

ROOT = Path(__file__).resolve().parents[2]
FIELDS = ['title', 'code', 'amount', 'unit', 'active', 'due', 'memo', 'count']


def boundary():
    rows = [json.loads(line) for line in
            (ROOT / 'som-core/data/flow-v7-r10/formal.jsonl').read_text().splitlines()]
    prompt = next(item['prompt'] for item in rows if item['id'] == 'cap-017')
    blocks = load_blocks(ROOT / 'som-code-python/data/blocks/flow-v7-compact')
    plan = flow_compact.resolve(prompt, flow_compact.reference(prompt, blocks))
    file = next(item for item in plan['files'] if item['path'].endswith('/schemas.py'))
    schema = assemble({'version': 'som.dsl.v2', 'files': [file]}, blocks)[file['path']]
    module = ModuleType('csv_boundary')
    exec(compile(schema, file['path'], 'exec'), module.__dict__)
    module.__dict__.update(TypeAdapter=TypeAdapter, csv=csv, io=io,
                           hashlib=hashlib, base64=base64, json=json, islice=islice)
    block = blocks['compact.workflow.flow.csv']
    values = {'fields': prompt['resources']['child']['fields'],
              'child_name': prompt['resources']['child']['name']}
    source = _render(block['template'], values, block['params'], 'csv', True)
    exec(compile(source, 'approved-csv-block', 'exec'), module.__dict__)
    return module


def data(rows, fields=FIELDS):
    output = io.StringIO(newline='')
    writer = csv.writer(output, lineterminator='\n')
    writer.writerow(fields)
    writer.writerows(rows)
    return output.getvalue()


def row(**changes):
    values = dict(zip(FIELDS, ['item', 'unique', '0', 'kg', 'false',
                              '2026-10-03', r'\N', '0'], strict=True))
    values.update(changes)
    return [values[name] for name in FIELDS]


def test_scalars_nulls_zero_and_quoted_text():
    module = boundary()
    result = module.csv_records(data([row(title='item, with\nquotes "')]))
    assert len(result) == 1
    item = result[0]
    assert item.title == 'item, with\nquotes "'
    assert item.amount == 0 and item.count == 0
    assert item.active is False and item.memo is None
    assert item.due.isoformat() == '2026-10-03'
    assert list(module.CUSTOM_FIELDS) == FIELDS


@pytest.mark.parametrize('count', [1, 20])
def test_valid_batch_boundaries(count):
    module = boundary()
    result = module.csv_records(data([row(code=str(index)) for index in range(count)]))
    assert len(result) == count


@pytest.mark.parametrize('source', [
    '', '"unterminated\n', data([]), data([row()] * 21),
    data([row()[:-1]]), data([row() + ['extra']]),
    data([row()], list(reversed(FIELDS))),
    data([row(active='yes')]), data([row(active=r'\N')]),
    data([row(title=r'\N')]), data([row(title='x' * 256)]),
    data([row(count=str(2**31))]), data([row(count='1.5')]),
    data([row(due='2026-02-30')]),
    data([row(amount='NaN')]), data([row(amount='Infinity')]),
    data([row(amount='-Infinity')]),
    ','.join(FIELDS) + '\n"unterminated',
])
def test_invalid_batches_fail_at_the_boundary(source):
    with pytest.raises(HTTPException) as error:
        boundary().csv_records(source)
    assert error.value.status_code == 422

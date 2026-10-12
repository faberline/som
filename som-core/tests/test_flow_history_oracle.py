"""Ensure final rows cannot stand in for an earlier flow result."""

from copy import deepcopy
import csv
import importlib.util
import io
from pathlib import Path
import sys
from types import SimpleNamespace

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / 'som-core/scripts'
sys.path.insert(0, str(SCRIPTS))
try:
    SPEC = importlib.util.spec_from_file_location('history_oracle', SCRIPTS / 'verify_flow_service.py')
    assert SPEC and SPEC.loader
    ORACLE = importlib.util.module_from_spec(SPEC)
    SPEC.loader.exec_module(ORACLE)
finally:
    sys.path.pop(0)


def fixture():
    fields = [{'name': 'title', 'type': 'str', 'required': True},
              {'name': 'memo', 'type': 'str', 'required': False, 'default': None}]
    prompt = {'resources': {'parent': {'fields': fields}, 'child': {'fields': fields}},
              'relation': {'foreign_key': 'owner_id'}, 'flow': {
                  'steps': [
                      {'op': 'transaction', 'name': 'first', 'steps': [
                          {'op': 'parent.create', 'name': 'created', 'args': {'data': {'input': 'parent'}}}]},
                      {'op': 'transaction', 'name': 'second', 'steps': [
                          {'op': 'parent.patch', 'name': 'patched', 'args': {
                              'parent_id': {'step': 'created', 'field': 'id'}, 'data': {'input': 'patch'}}}]},
                  ], 'result': {'before': {'step': 'created', 'field': 'title'},
                                'after': {'step': 'patched', 'field': 'title'}}}}
    created = {'id': 1, 'title': 'original', 'memo': 'keep',
               'created_at': '2030-01-01T00:00:00Z', 'updated_at': '2030-01-01T00:00:00Z'}
    patched = created | {'title': 'changed', 'updated_at': '2030-01-01T00:00:01Z'}
    history = [{'role': 'parent', 'operation': 'insert', 'row': created, 'transaction': 'one'},
               {'role': 'parent', 'operation': 'update', 'row': patched, 'transaction': 'two'}]
    backend = SimpleNamespace(rows=lambda role: [patched] if role == 'parent' else [])
    inputs = {'parent': {'title': 'original', 'memo': 'keep'}, 'patch': {'title': 'changed'}}
    return prompt, inputs, backend, {'parent': [], 'child': []}, history


def test_create_result_keeps_its_value_after_patch():
    prompt, inputs, backend, initial, history = fixture()
    result, branches = ORACLE.expected_run(prompt, inputs, backend, None, initial, history)
    assert result == {'before': 'original', 'after': 'changed'}
    assert branches == {}


def test_patch_cannot_clear_an_omitted_field():
    prompt, inputs, backend, initial, history = fixture()
    history[-1]['row'] = history[-1]['row'] | {'memo': None}
    with pytest.raises(AssertionError):
        ORACLE.expected_run(prompt, inputs, backend, None, initial, history)


def test_declared_transaction_scopes_must_have_distinct_native_ids():
    prompt, inputs, backend, initial, history = fixture()
    history[-1]['transaction'] = 'one'
    with pytest.raises(AssertionError):
        ORACLE.expected_run(prompt, inputs, backend, None, initial, history)


def test_unrequested_committed_write_cannot_pass():
    prompt, inputs, backend, initial, history = fixture()
    history.append(deepcopy(history[-1]))
    with pytest.raises(AssertionError, match='unexpected committed resource write'):
        ORACLE.expected_run(prompt, inputs, backend, None, initial, history)


def test_missing_native_write_cannot_pass_even_when_final_row_matches():
    prompt, inputs, backend, initial, history = fixture()
    with pytest.raises(AssertionError):
        ORACLE.expected_run(prompt, inputs, backend, None, initial, history[1:])


def csv_fixture():
    """Use public fields and observed rows, without reading a block template."""
    fields = [{'name': 'title', 'type': 'str', 'required': True},
              {'name': 'amount', 'type': 'float', 'required': True},
              {'name': 'active', 'type': 'bool', 'required': True},
              {'name': 'memo', 'type': 'str', 'required': False, 'default': None}]
    prompt = {'resources': {'parent': {'path': '/parents', 'fields': []},
                            'child': {'fields': fields}},
              'relation': {'foreign_key': 'owner_id'},
              'capabilities': {'workflow': 'csv'}, 'flow': {
                  'steps': [{'op': 'workflow.run', 'name': 'exported',
                             'args': {'parent_id': {'input': 'parent_id'}}}],
                  'result': {'data': {'step': 'exported', 'field': 'data'}}}}
    row = {'id': 5, 'owner_id': 1, 'title': 'item, with\nquotes "',
           'amount': 0.0, 'active': False, 'memo': None,
           'created_at': '2030-01-01T00:00:00Z',
           'updated_at': '2030-01-01T00:00:01Z'}
    snapshot = {'parent': [{'id': 1}], 'child': [row]}
    output = io.StringIO(newline='')
    names = [field['name'] for field in fields]
    writer = csv.DictWriter(output, names, lineterminator='\n')
    writer.writeheader()
    writer.writerow({name: r'\N' if row[name] is None else row[name] for name in names})
    response = httpx.Response(200, text=output.getvalue())

    def get(path):
        assert path == '/parents/1/export'
        return response

    client = SimpleNamespace(get=get)
    backend = SimpleNamespace(rows=lambda role: snapshot[role])
    return prompt, client, backend, snapshot


def test_csv_flow_data_uses_full_observed_rows_instead_of_null():
    prompt, client, backend, snapshot = csv_fixture()
    result, branches = ORACLE.expected_run(prompt, {'parent_id': 1}, backend,
                                           client, snapshot, [])
    assert result == {'data': snapshot['child']}
    assert branches == {}
    assert not ORACLE.equivalent({'data': None}, result)
    assert not ORACLE.equivalent({'data': [{'title': snapshot['child'][0]['title']}]}, result)


def test_csv_receipt_still_rejects_wrong_export_values():
    prompt, _, backend, snapshot = csv_fixture()
    wrong = httpx.Response(200, text='title,amount,active,memo\nwrong,0.0,False,\\N\n')
    client = SimpleNamespace(get=lambda _path: wrong)
    with pytest.raises(AssertionError):
        ORACLE.expected_run(prompt, {'parent_id': 1}, backend, client, snapshot, [])

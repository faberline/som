"""Verify independent branch probes at bool, number, date and null boundaries."""

from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "som-core/scripts"))
from verify_flow_service import expected_run, flow_inputs
SPEC = importlib.util.spec_from_file_location('branch_cases', ROOT / 'som-core/scripts/flow_branch_cases.py')
assert SPEC is not None and SPEC.loader is not None
BRANCHES = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BRANCHES)


def prompts():
    return [json.loads(line)['prompt'] for split in ('dev', 'formal')
            for line in (ROOT / f'som-core/data/flow-v7-r10/{split}.jsonl').read_text().splitlines()]


def test_all_sealed_branch_conditions_have_public_input_probes():
    for prompt in prompts():
        matrix = BRANCHES.branch_inputs(prompt)
        assert not matrix['unresolved'], (prompt['package'], matrix['unresolved'])
        assert matrix['cases']


def test_nested_negative_boolean_visits_both_numeric_arms():
    prompt = next(prompt for prompt in prompts()
                  if any(node.get('condition', {}).get('op') == 'ne'
                         for node in prompt['flow']['steps'])
                  and any(node.get('condition', {}).get('op') == 'gt'
                          for node in BRANCHES.nodes_in(prompt['flow']['steps'])))
    matrix = BRANCHES.branch_inputs(prompt)
    pairs = set()
    original = {'choose': True, 'parent': {'servings': 3}}
    for changes in matrix['cases']:
        inputs = BRANCHES.apply_case(original, changes)
        if inputs['choose'] is False:
            pairs.add(inputs['parent']['servings'] > 0)
    assert pairs == {False, True}
    assert original == {'choose': True, 'parent': {'servings': 3}}


def test_null_and_date_probes_include_missing_and_equal_boundaries():
    prompt = deepcopy(next(prompt for prompt in prompts()
                           if {'name': 'parent', 'type': 'parent_input'}
                           in prompt['flow']['inputs']))
    prompt['flow']['steps'] = [
        {'op': 'parent.create', 'name': 'created', 'args': {'data': {'input': 'parent'}}},
        {'op': 'if', 'name': 'nullable', 'condition': {'op': 'is_null', 'left': {'step': 'created', 'field': 'due'}}, 'then': [], 'else': [], 'exports': {}},
        {'op': 'if', 'name': 'date', 'condition': {'op': 'le', 'left': {'input': 'cutoff'}, 'right': {'type': 'date', 'literal': '2030-01-01'}}, 'then': [], 'else': [], 'exports': {}},
    ]
    prompt['flow']['inputs'].append({'name': 'cutoff', 'type': 'date'})
    matrix = BRANCHES.branch_inputs(prompt)
    cases = [BRANCHES.apply_case({'parent': {}, 'cutoff': '2030-01-01'}, item)
             for item in matrix['cases']]
    assert {item['parent']['due'] for item in cases} == {None, '2030-01-01'}
    assert {item['cutoff'] for item in cases} == {'2029-12-31', '2030-01-01', '2030-01-02'}


def test_missing_inner_branch_arm_cannot_pass():
    matrix = {'branches': ['outer', 'inner'], 'unresolved': []}
    records = [{'branches': {'outer': False}}, {'branches': {'outer': True, 'inner': False}}]
    with pytest.raises(AssertionError, match='unvisited branch arms'):
        BRANCHES.require_branch_coverage(matrix, records)
    records.append({'branches': {'outer': True, 'inner': True}})
    assert BRANCHES.require_branch_coverage(matrix, records)['passed']


def test_same_typed_branch_inputs_have_observable_different_data():
    prompt = deepcopy(next(prompt for prompt in prompts()
                           if any(item['type'] == 'child_input'
                                  for item in prompt['flow']['inputs'])))
    prompt['flow']['inputs'] = [
        {'name': 'yes_data', 'type': 'child_input'},
        {'name': 'no_data', 'type': 'child_input'},
    ]
    inputs = flow_inputs(prompt, 100, True, 3, 1)
    yes = inputs['yes_data']
    no = inputs['no_data']
    assert yes != no
    unique_fields = [field['name'] for field in prompt['resources']['child']['fields']
                     if field.get('unique')]
    assert unique_fields
    assert all(yes[name] != no[name] for name in unique_fields)
    prompt['flow']['steps'] = [{'op': 'transaction', 'name': 'db', 'steps': [
        {'op': 'child.create', 'name': 'created', 'args': {
            'data': {'input': 'yes_data'}, 'parent_id': {'literal': 1}}}]}]
    prompt['flow']['result'] = {'created': {'step': 'created', 'field': ''}}
    def observed(data):
        stored = {field['name']: data.get(field['name'], field.get('default'))
                  for field in prompt['resources']['child']['fields']}
        row = {**stored, prompt['relation']['foreign_key']: 1, 'id': 2,
               'created_at': '2030-01-01T00:00:00+00:00',
               'updated_at': '2030-01-01T00:00:00+00:00'}
        backend = SimpleNamespace(rows=lambda role: [row] if role == 'child' else [])
        history = [{'role': 'child', 'operation': 'insert', 'row': row,
                    'transaction': 'tx-1'}]
        return expected_run(prompt, inputs, backend, None,
                            {'parent': [], 'child': []}, history)
    assert observed(yes)[0]['created']
    with pytest.raises(AssertionError):
        observed(no)

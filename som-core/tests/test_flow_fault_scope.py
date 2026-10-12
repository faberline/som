"""Apply compensation probes only to flows with business writes."""

from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from flow_fault_checks import (
    checkpoint_failures,
    interrupted_forward,
    resource_write_operations,
    checkpoint_target,
    committed_changes,
    first_database_write,
    require_restored,
)


def test_read_only_flow_has_no_business_compensation():
    steps = [{"op": "authorize"}, {"op": "parent.get"}]
    prompt = {"flow": {"steps": steps}}
    assert resource_write_operations(steps) == set()
    for result in (
        checkpoint_failures(prompt, None, None, None),
        interrupted_forward(prompt, None, None, None, None),
    ):
        assert result["passed"] is True
        assert result["applicable"] is False
        assert result["reason"]


@pytest.mark.parametrize(
    "operation",
    ["parent.create", "child.create", "batch.create", "workflow.import",
     "parent.patch", "child.patch", "object.put"],
)
def test_each_public_mutation_requires_compensation_probes(operation):
    steps = [{"op": "transaction", "steps": [{"op": operation}]}]
    assert resource_write_operations(steps) == {operation}
    prompt = {"flow": {"steps": steps}}
    if operation != 'object.put':
        assert checkpoint_target(steps) == {'name': None, 'container': 'values', 'skip': 1}
    else:
        assert checkpoint_target(steps) is None


def test_mutations_in_either_nested_branch_remain_required():
    steps = [{
        "op": "if",
        "then": [{"op": "parent.get"}],
        "else": [{
            "op": "if",
            "then": [{"op": "transaction", "steps": [{"op": "child.patch"}]}],
            "else": [{"op": "object.put"}],
        }],
    }]
    assert resource_write_operations(steps) == {"child.patch", "object.put"}


def test_nested_branch_is_not_a_committed_checkpoint():
    steps = [{'name': 'access', 'op': 'authorize'},
             {'name': 'db', 'op': 'transaction', 'steps': [
                 {'name': 'inside', 'op': 'if', 'then': [{'op': 'child.create'}], 'else': []}]},
             {'name': 'exported', 'op': 'workflow.run'}]
    assert checkpoint_target(steps) == {'name': 'exported', 'container': 'values', 'skip': 2}
    assert first_database_write(steps) == ('child', 'create')


def test_existing_row_patch_counts_as_a_physical_write_and_can_restore():
    row = {'id': 1, 'title': 'old', 'created_at': 1, 'updated_at': 1, '_som_revision': 0, '_som_guard': 0}
    before = {'parent': [row], 'child': []}
    written = {'parent': [dict(row, title='new', updated_at=2, _som_revision=1)], 'child': []}
    assert committed_changes(before, written)[0]['created'] is False
    restored = {'parent': [dict(row, updated_at=3, _som_revision=2, _som_guard=2)], 'child': []}
    require_restored(before, restored)
    with pytest.raises(AssertionError):
        require_restored(before, written)
    with pytest.raises(AssertionError):
        require_restored(before, {'parent': [dict(row, created_at=2)], 'child': []})
    with pytest.raises(AssertionError):
        committed_changes(before, before)

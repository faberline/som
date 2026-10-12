"""A timestamp's fractional-second spelling must not change time order."""
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from verify_composed_journeys import assert_partial_update


def test_patch_compares_timestamp_instants_and_preserves_other_fields():
    before = {'id': 1, 'value': 4, 'note': 'keep', 'updated_at': '2030-01-01T00:00:00Z'}
    after = before | {'value': 0, 'updated_at': '2030-01-01T00:00:00.001000Z'}
    assert_partial_update(before, after, 'value', 0)
    for change in ({'note': None}, {'id': 2}, {'updated_at': '2029-12-31T23:59:59.999000Z'}):
        with pytest.raises(AssertionError):
            assert_partial_update(before, after | change, 'value', 0)

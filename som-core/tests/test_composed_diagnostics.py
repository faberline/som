"""An assertion must show the real difference without issuing another request."""
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from composed_diagnostics import compact, failure_details


def test_created_readback_difference_uses_saved_http_without_calling_again(tmp_path):
    folder = tmp_path / 'check-results/evidence-1'
    folder.mkdir(parents=True)
    created = {'id': 1, 'created_at': '2026-10-01T00:00:00.000123Z'}
    actual = {'id': 1, 'created_at': '2026-10-01T00:00:00Z'}
    (folder / 'events.jsonl').write_text(json.dumps({'kind': 'http', 'method': 'GET', 'path': '/items/1',
        'status': 200, 'json': actual, 'response_sha256': hashlib.sha256(json.dumps(actual).encode()).hexdigest()}) + '\n')
    calls = []
    def request(method, path, status):
        calls.append((method, path, status))
        return actual
    try:
        assert request('GET', '/items/1', 200) == created
    except AssertionError as error:
        result = failure_details(error, tmp_path)
    assert len(calls) == 1
    assert result['expected'] == created and result['actual'] == actual
    assert compact(result)['differences'] == [{'path': '$.created_at',
        'expected': created['created_at'], 'actual': actual['created_at']}]


def test_plain_assertion_retains_actual_values_and_redacts_credentials(tmp_path):
    expected = {'count': 2, 'access_token': 'synthetic-secret'}
    actual = {'count': 1, 'access_token': 'different-secret'}
    try:
        assert actual == expected
    except AssertionError as error:
        result = failure_details(error, tmp_path)
    assert result['expected']['count'] == 2 and result['actual']['count'] == 1
    assert result['differences'] == [{'path': '$.count', 'expected': 2, 'actual': 1}]
    assert 'synthetic-secret' not in json.dumps(result)


def test_unknown_function_is_never_executed_by_diagnostics(tmp_path):
    calls = []
    def operation():
        calls.append(True)
        return 1
    try:
        assert operation() == 2
    except AssertionError as error:
        result = failure_details(error, tmp_path)
    assert len(calls) == 1
    assert result['comparison'] is None
    assert result['actual']

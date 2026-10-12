"""Provider cleanup must not replace the failed product response."""
import json
import os
from pathlib import Path
import sys
import time
import subprocess

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from composed_diagnostics_recorded import compact, failure_details
from composed_evidence import capture


@pytest.mark.parametrize('extra_read', [False, True])
def test_failed_readback_survives_real_capture_and_finally_cleanup(tmp_path, extra_read):
    created = {'id': 1, 'created_at': '2026-10-01T00:00:00.123456Z'}
    actual = {'id': 1, 'created_at': '2026-10-01T00:00:00.123Z'}
    calls = []
    def respond(request):
        calls.append((request.method, request.url.path))
        return httpx.Response(200, json=actual if request.url.path == '/items/1' else {'unrelated': True})
    with httpx.Client(transport=httpx.MockTransport(respond), base_url='http://fixture') as client:
        def request(method, path, status):
            response = client.request(method, path)
            assert response.status_code == status
            return response.json()
        try:
            with capture(tmp_path):
                try:
                    assert request('GET', '/items/1', 200) == created
                finally:
                    if extra_read:
                        client.get('/unrelated')
                    client.post('/__control', json={'reset': True})
        except AssertionError as error:
            before = list(calls)
            result = failure_details(error, tmp_path)
    assert calls == before
    assert result['actual'] == actual and result['expected'] == created
    assert result['request'] == {'method': 'GET', 'path': '/items/1', 'status': 200}
    assert compact(result)['differences'] == [{'path': '$.created_at',
        'expected': created['created_at'], 'actual': actual['created_at']}]


def test_old_check_evidence_is_not_used_for_a_new_failure(tmp_path):
    folder = tmp_path / 'check-results/evidence-old'
    folder.mkdir(parents=True)
    (folder / 'events.jsonl').write_text(json.dumps({'kind': 'http', 'method': 'GET',
        'path': '/items/1', 'status': 200, 'json': {'old': True}, 'response_sha256': 'old'}) + '\n')
    old = time.time() - 60
    os.utime(folder, (old, old))
    try:
        raise AssertionError('current failure before any request')
    except AssertionError as error:
        result = failure_details(error, tmp_path, time.time() - 1)
    assert result['actual'] == 'current failure before any request'
    assert 'request' not in result


def test_unknown_calls_are_never_reissued(tmp_path):
    calls = []
    def mutate():
        calls.append(True)
        return 1
    try:
        assert mutate() == 2
    except AssertionError as error:
        result = failure_details(error, tmp_path)
    assert calls == [True]
    assert result['comparison'] is None


def test_failed_process_reports_exit_values_and_original_output(tmp_path):
    def run():
        result = subprocess.CompletedProcess(['synthetic-test'], 1, 'one test failed', 'failure detail')
        raise RuntimeError('process failed')
    try:
        run()
    except RuntimeError as error:
        result = failure_details(error, tmp_path)
    assert result['expected'] == {'exit_code': 0}
    assert result['actual'] == {'exit_code': 1, 'stdout': 'one test failed', 'stderr': 'failure detail'}
    assert result['comparison'] == 'process_exit'


def test_nonassertion_error_has_an_explicit_expected_state(tmp_path):
    try:
        raise ValueError('invalid result')
    except ValueError as error:
        result = failure_details(error, tmp_path)
    assert result['expected'] == {'exception': None}
    assert result['actual'] == {'exception': 'ValueError', 'message': 'invalid result'}

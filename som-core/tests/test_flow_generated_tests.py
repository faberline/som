"""The behavior entry must execute a nonempty generated test suite."""

from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import verify_flow_service


@pytest.mark.parametrize('status,output,accepted', [
    (0, 'Ran 1 test in 0.01s\n\nOK\n', True),
    (0, 'Ran 0 tests in 0.01s\n\nOK\n', False),
    (0, 'Ran 1 test in 0.01s\n\nOK (skipped=1)\n', False),
    (0, '', False),
    (1, 'Ran 1 test in 0.01s\n\nFAILED (failures=1)\n', False),
])
def test_missing_skipped_or_failed_tests_cannot_pass(monkeypatch, tmp_path, status, output, accepted):
    tests = tmp_path / 'tests'
    tests.mkdir()
    (tests / 'test_example.py').write_text('source identity')
    calls = []

    def run(command, **options):
        calls.append((command, options))
        return SimpleNamespace(returncode=status, stdout='', stderr=output)

    monkeypatch.setattr(verify_flow_service.subprocess, 'run', run)
    if accepted:
        result = verify_flow_service.generated_tests('owned-python', tmp_path, {'TEST_DATABASE_URL': 'isolated'})
        assert result['passed'] and result['tests'] == 1
        assert set(result['source_sha256']) == {'test_example.py'}
    else:
        with pytest.raises(AssertionError):
            verify_flow_service.generated_tests('owned-python', tmp_path, {'TEST_DATABASE_URL': 'isolated'})
    assert calls[0][0] == ['owned-python', '-m', 'unittest', 'discover', '-s', 'tests', '-v']
    assert calls[0][1]['cwd'] == tmp_path
    assert calls[0][1]['env'] == {'TEST_DATABASE_URL': 'isolated'}

"""A failed product check must retain source identity and useful differences."""
import hashlib
import importlib.util
import json
from pathlib import Path
import sys

import pytest


@pytest.mark.parametrize('round_name', ['codex-r2', 'codex-r3'])
def test_failed_service_receipt_binds_source_and_requirement(tmp_path, monkeypatch, round_name):
    root = Path(__file__).resolve().parents[2]
    path = root / 'som-rs/evals/composable-2026-10-01' / round_name / 'check.py'
    spec = importlib.util.spec_from_file_location('round_two_check', path)
    checker = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(checker)
    kit, work = tmp_path / 'kit', tmp_path / 'work'
    kit.mkdir(); work.mkdir()
    prompt = {'version': 'fixture'}
    (kit / 'jobs.json').write_text(json.dumps({'jobs': [{'id': 'example', 'directory': str(work), 'mode': 'direct', 'prompt': prompt}]}))
    (kit / 'seal.json').write_text(json.dumps({'files': {}, 'prepared': {'example': {}}}))
    fixture = kit / 'fixture.json'; fixture.write_text('{}')
    (kit / 'runtime.json').write_text(json.dumps({'fixture': str(fixture), 'fixture_sha256': hashlib.sha256(fixture.read_bytes()).hexdigest()}))
    source = b'value = 1\n'; (work / 'app.py').write_bytes(source)
    def fail_check(*args, **kwargs):
        expected = {'created_at': 'exact saved time'}
        actual = {'created_at': 'different time'}
        assert actual == expected
    monkeypatch.setattr(checker, 'KIT', kit)
    monkeypatch.setattr(checker, 'verify_all', fail_check)
    monkeypatch.setattr(sys, 'argv', ['check.py', 'example'])
    monkeypatch.chdir(work)
    with pytest.raises(SystemExit) as error:
        checker.main()
    assert error.value.code == 1
    report = json.loads((work / 'check-results/0001.json').read_text())
    assert report['status'] == 'failed'
    assert report['source_sha256'] == {'app.py': hashlib.sha256(source).hexdigest()}
    assert report['requirement_sha256'] == hashlib.sha256(b'{"version":"fixture"}').hexdigest()
    assert report['failure']['expected'] == {'created_at': 'exact saved time'}
    assert report['failure']['actual'] == {'created_at': 'different time'}
    assert (work / 'app.py').read_bytes() == source

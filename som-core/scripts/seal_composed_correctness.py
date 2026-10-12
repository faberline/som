"""Bind complete behavior checks to the exact Rust candidate and source bytes.

This is the admission gate for the paid Codex comparison. It cannot promote a
model. A reference behavior check can be reused only for identical Rust source.
"""
import hashlib
import json
from pathlib import Path
import sys
import urllib.request

ROOT = Path(__file__).resolve().parents[2]
EVAL = ROOT / 'som-rs/evals/composable-2026-10-01'
MODEL = ROOT / 'som-rs/models/composed-candidate'
sys.path.insert(0, str(ROOT / 'som-core/src'))
from som_core import composed as c
from som_core.v1 import load_blocks
from audit_business_transactions import source_hashes
from eval_transport import native_outputs, observed_ranges


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read(name):
    return json.loads((EVAL / name).read_text())


def complete(report, count):
    assert report['passed'] and len(report['results']) == count
    assert {row['index'] for row in report['results']} == set(range(count))
    assert all(row.get('status') == 'passed' or row.get('passed') is True
               for row in report['results'])


def bind_services(report_name, data_name, response_group, count):
    report = read(report_name)
    complete(report, count)
    data = ROOT / 'som-core/data/composed' / data_name
    assert digest(data) == report['data_sha256']
    prompts = [json.loads(line)['prompt'] for line in data.read_text().splitlines()]
    bindings = []
    for row in report['results']:
        response = EVAL / 'runtime-2/responses' / response_group / f'{row["index"]:04d}.json'
        body = json.loads(response.read_text())
        expected = {name: hashlib.sha256(source.encode()).hexdigest()
                    for name, source in body['files'].items()}
        assert body['status'] == 'ok' and row['source_sha256'] == expected
        assert source_hashes(Path(row['directory'])) == expected
        assert row['requirement_sha256'] == hashlib.sha256(
            c.canonical(prompts[row['index']]).encode()).hexdigest()
        for name, value in row['evidence_sha256'].items():
            assert digest(Path(row['evidence_directory']) / name) == value
        bindings.append({'index': row['index'], 'response_sha256': digest(response),
                         'source_sha256': expected,
                         'functional_result': report_name,
                         'functional_origin': report['kind'],
                         'evidence_sha256': row['evidence_sha256']})
    return bindings


def main():
    destination = EVAL / 'candidate-correctness.json'
    if destination.exists():
        raise ValueError('preserve the existing admission receipt')
    runtime, audit, parity = (read(name) for name in
        ('runtime-2/result.json', 'data-audit-3.json', 'parity-4.json'))
    assert runtime['passed'] and len(runtime['cases']) == 528
    assert all(row['passed'] for row in runtime['cases'] + runtime['rejections'])
    for row in runtime['cases']:
        path = EVAL / 'runtime-2/responses' / row['group'] / f'{row["index"]:04d}.json'
        assert digest(path) == row['response_sha256']
    assert audit['passed'] and not audit['uncovered_blocks']
    assert not any(audit['overlap'].values())
    for group, expected in audit['files'].items():
        assert digest(ROOT / 'som-core/data/composed' / (group + '.jsonl')) == expected
    assert parity['passed'] and parity['valid'] == 20734 and parity['rejected'] == 25
    assert not parity['failures']
    coverage = read('capability-coverage.json')
    assert coverage['passed'] and coverage['training_sha256'] == audit['files']['train']
    assert len(coverage['expected_pairs']) == len(coverage['observed_pairs']) == 61
    assert not coverage['missing_pairs']
    visibility = read('transport-probe-003/visibility.json')
    delivered = Path('/private/tmp/som-composable-transport-probe-2026-10-01/largest-current-response.json')
    native = Path(visibility['session'])
    assert digest(delivered) == visibility['response_sha256']
    assert digest(native) == visibility['session_sha256']
    assert observed_ranges(delivered.read_bytes(), native_outputs(native))['complete']
    assert max(row['response_bytes'] for row in runtime['cases']) <= visibility['response_bytes']

    manifest = json.loads((MODEL / 'manifest.json').read_text())
    manifest_hash = digest(MODEL / 'manifest.json')
    assert manifest_hash == runtime['model_manifest_sha256']
    assert manifest['data_sha256'] == audit['files']['train']
    assert manifest['development_sha256'] == audit['files']['dev']
    checks = manifest['development_checks']
    assert manifest['development_perfect'] and checks[-1]['correct'] == checks[-1]['total'] == 600
    assert all(row['correct'] < row['total'] for row in checks[:-1])
    assert [row['step'] for row in checks] == list(range(1000, manifest['training_steps'] + 1, 1000))
    for name, expected in manifest['files'].items():
        assert digest(MODEL / name) == expected
    assert digest(ROOT / 'som-code-python/data/blocks/composed/blocks.json') == manifest['blocks_sha256']
    c.validate_library(load_blocks(ROOT / 'som-code-python/data/blocks/composed'))
    config = json.loads((MODEL / 'config.json').read_text())
    assert {key: config[key] for key in ('n_layer', 'n_embd', 'n_head', 'vocab_size', 'n_ctx')} == {
        'n_layer': 2, 'n_embd': 64, 'n_head': 4, 'vocab_size': 1024, 'n_ctx': 4096}
    assert manifest['seed'] == 17

    bindings = {
        'services14': bind_services('rust-services.json', 'regression.jsonl', 'services14', 14),
        'compositions120': bind_services('reference-compositions-3.json', 'heldout.jsonl', 'compositions120', 120),
        'blind80': bind_services('rust-blind.json', 'blind.jsonl', 'blind80', 80),
    }
    legacy = read('legacy-postgres-2/result.json')
    complete(legacy, 100)
    assert legacy['cases_sha256'] == runtime['sources_sha256']['legacy100']
    assert digest(ROOT / 'som-core/scripts/verify_service_v3.py') == legacy['verifier_sha256']
    bindings['legacy100'] = []
    for row in legacy['results']:
        old = EVAL / 'runtime/responses/legacy100' / f'{row["index"]:04d}.json'
        new = EVAL / 'runtime-2/responses/legacy100' / f'{row["index"]:04d}.json'
        assert digest(old) == row['response_sha256']
        assert digest(ROOT / row['log']) == row['log_sha256']
        assert json.loads(old.read_text())['files'] == json.loads(new.read_text())['files']
        bindings['legacy100'].append({'index': row['index'], 'tested_response_sha256': digest(old),
            'current_response_sha256': digest(new), 'functional_result': 'legacy-postgres-2/result.json',
            'log_sha256': row['log_sha256'], 'source_bytes_identical': True})

    baseline = read('baseline.json')
    protected = {name: value for name, value in baseline['files'].items()
                 if name.startswith(('som-rs/models/', 'som-code-python/data/blocks/'))}
    for name, expected in protected.items():
        assert digest(ROOT / name) == expected, 'protected asset changed: ' + name
    for name, expected in baseline['current'].items():
        assert str((ROOT / name).resolve()) == expected
    with urllib.request.urlopen('http://127.0.0.1:8765/healthz', timeout=10) as response:
        default_health = json.load(response)
    with urllib.request.urlopen('http://127.0.0.1:8766/healthz', timeout=10) as response:
        candidate_health = json.load(response)
    assert default_health == baseline['health']
    assert candidate_health == runtime['health']

    reports = ['baseline.json', 'runtime-2/result.json', 'data-audit-3.json', 'parity-4.json',
               'reference-compositions-3.json', 'rust-services.json', 'rust-blind.json',
               'legacy-postgres-2/result.json', 'python-tests.log', 'focused-tests.log',
               'rust-tests.log', 'capability-coverage.json', 'transport-probe-003/visibility.json']
    result = {'passed': True, 'model_manifest_sha256': manifest_hash,
              'binary_sha256': digest(ROOT / 'som-rs/target/release/som'),
              'reports_sha256': {name: digest(EVAL / name) for name in reports},
              'protected_assets_sha256': protected, 'default_health': default_health,
              'candidate_health': candidate_health, 'behavior_bindings': bindings,
              'source_binding_rule': 'The full behavior verdict applies only to the identical source bytes. '
              'The 120 Python-reference services and 100 legacy services are linked to the current Rust responses by exact source identity.',
              'promotion': 'none; Codex token gate, Claude and real cloud remain pending'}
    destination.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps({'passed': True, 'model_manifest_sha256': manifest_hash,
                      'behavior_services': {name: len(rows) for name, rows in bindings.items()}}))


if __name__ == '__main__':
    main()

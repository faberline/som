"""Verify exact candidate assets and preserve the existing default."""
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import urllib.request

ROOT = Path(__file__).resolve().parents[2]
EVAL = ROOT / 'som-rs/evals/composable-2026-10-01'


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def health(port):
    with urllib.request.urlopen(f'http://127.0.0.1:{port}/healthz', timeout=10) as response:
        return json.load(response)


def main():
    destination = EVAL / 'final-verification'
    destination.mkdir(exist_ok=False)
    baseline = json.loads((EVAL / 'baseline.json').read_text())
    gate = json.loads((EVAL / 'candidate-correctness.json').read_text())
    tokens = json.loads((EVAL / 'codex-r3/results.json').read_text())
    seal = json.loads((EVAL / 'codex-r3/seal.json').read_text())
    for name, expected in seal['files'].items():
        assert digest(ROOT / name) == expected, 'sealed file changed: ' + name
    for name, expected in gate['reports_sha256'].items():
        assert digest(EVAL / name) == expected, 'correctness evidence changed: ' + name
    protected = {name: value for name, value in baseline['files'].items()
                 if name.startswith(('som-rs/models/', 'som-code-python/data/blocks/'))}
    for name, expected in protected.items():
        assert digest(ROOT / name) == expected, 'prior asset changed: ' + name
    for name, expected in baseline['current'].items():
        assert str((ROOT / name).resolve()) == expected, 'current target changed: ' + name
    old_archive = ROOT / 'som-rs/evals/business-paired-2026-09-30-r3/archive/records.tar.gz'
    old_archive_hash = digest(old_archive)
    assert old_archive_hash == '38a4682ab9b1d5fdde56d9cfdabacbf61b2d5dd0887791daf1e1175b76145027'
    current, candidate = health(8765), health(8766)
    assert current == baseline['health']
    assert candidate == gate['candidate_health']
    with tempfile.TemporaryDirectory(prefix='som-final-cli-', dir='/private/tmp') as empty:
        command = [str(ROOT / 'som-rs/target/release/som'), 'generate',
                   str(ROOT / 'som-rs/examples/composed-prompt.json'), '--url', 'http://127.0.0.1:8766']
        result = subprocess.run(command, cwd=empty, capture_output=True, timeout=65)
        (destination / 'cli-response.json').write_bytes(result.stdout)
        (destination / 'cli-stderr.log').write_bytes(result.stderr)
        assert result.returncode == 0
        body = json.loads(result.stdout)
        assert body['status'] == 'ok' and 1 <= len(body['files']) <= 12
        assert sum(len(source.encode()) for source in body['files'].values()) <= 65536
        assert list(Path(empty).iterdir()) == [], 'CLI wrote product files'
    receipt = {'preservation_passed': True, 'candidate_correctness_passed': gate['passed'],
               'codex_gate_passed': tokens['passed'],
               'candidate_accepted': bool(gate['passed'] and tokens['passed']),
               'model_manifest_sha256': gate['model_manifest_sha256'],
               'protected_assets_sha256': protected, 'current_targets': baseline['current'],
               'prior_archive_sha256': old_archive_hash, 'current_health': current,
               'candidate_health': candidate, 'cli_response_sha256': digest(destination / 'cli-response.json'),
               'cli_exit_code': result.returncode, 'cli_product_files': len(body['files']),
               'cli_wrote_files': False,
               'git_status': subprocess.check_output(['git', '--no-optional-locks', 'status', '--porcelain=v1'], cwd=ROOT, text=True),
               'pending': ([] if tokens['passed'] else
                           ['Codex token or evidence gate: ' + ', '.join(pair['app'] for pair in tokens['pairs'] if not pair['passed'])])
                          + ['Claude validation', 'real cloud acceptance', 'default switch'],
               'promotion': 'none'}
    (destination / 'receipt.json').write_text(json.dumps(receipt, indent=2) + '\n')
    print(json.dumps({key: receipt[key] for key in
                      ('preservation_passed', 'candidate_accepted', 'cli_product_files', 'cli_wrote_files')}))


if __name__ == '__main__':
    main()

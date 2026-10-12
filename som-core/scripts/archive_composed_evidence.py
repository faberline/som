"""Preserve task-owned source, native Codex records and full check diagnostics."""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import tarfile

ROOT = Path(__file__).resolve().parents[2]
EVAL = ROOT / 'som-rs/evals/composable-2026-10-01'
EXCLUDED = {'.venv', '.git', '__pycache__', '.pytest_cache', '.ruff_cache', 'archive'}


def files(root):
    for directory, subdirectories, names in os.walk(root):
        subdirectories[:] = [name for name in subdirectories if name not in EXCLUDED]
        for name in names:
            path = Path(directory) / name
            if path.is_file() and not path.is_symlink():
                yield path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    selected = {}

    def add_tree(root, prefix):
        for path in files(root):
            selected[path] = str(Path(prefix) / path.relative_to(root))

    add_tree(EVAL, 'evaluation')
    for name in ('som-core/src', 'som-core/scripts', 'som-core/tests',
                 'som-rs/schemas', 'som-rs/examples', 'som-rs/contracts'):
        add_tree(ROOT / name, 'repository/' + name)
    for name in ('README.md', 'som-core/pyproject.toml', 'som-core/uv.lock',
                 'som-rs/README.md', 'som-rs/COMPOSABLE.md'):
        selected[ROOT / name] = 'repository/' + name
    for name in ('service-regression-v4/heldout.jsonl', 'v1/heldout.jsonl',
                 'query-v2/heldout.jsonl', 'service-v3/heldout.jsonl',
                 'related-service/heldout.jsonl', 'business-service/requirements.jsonl'):
        selected[ROOT / 'som-core/data' / name] = 'repository/som-core/data/' + name
    baseline = json.loads((EVAL / 'baseline.json').read_text())
    for name, expected in baseline['files'].items():
        if not name.startswith(('som-rs/models/', 'som-code-python/data/blocks/')):
            continue
        path = (ROOT / name).resolve()
        assert path.is_relative_to(ROOT)
        assert hashlib.sha256(path.read_bytes()).hexdigest() == expected
        selected[path] = 'repository/' + str(path.relative_to(ROOT))
    workspaces = set()
    for path in EVAL.glob('*.json'):
        report = json.loads(path.read_text())
        if not isinstance(report, dict):
            continue
        for row in report.get('results', []):
            if isinstance(row, dict) and row.get('directory'):
                root = Path(row['directory']).resolve()
                assert root.parent == Path('/private/tmp') and root.name.startswith('som-composed-')
                workspaces.add(root)
    for kit, workspace_name in (
        ('codex', 'som-composable-codex-2026-10-01'),
        ('codex-r2', 'som-composable-codex-2026-10-01-r2'),
        ('codex-r3', 'som-composable-codex-2026-10-01-r3'),
    ):
        jobs_path = EVAL / kit / 'jobs.json'
        if not jobs_path.exists():
            continue
        seal = json.loads((EVAL / kit / 'seal.json').read_text())
        runtime = json.loads((EVAL / kit / 'runtime.json').read_text())
        fixture = Path(runtime['fixture'])
        assert fixture.parent == Path('/private/tmp/som-composable-fixtures-2026-10-01')
        assert hashlib.sha256(fixture.read_bytes()).hexdigest() == runtime['fixture_sha256']
        selected[fixture] = 'local-fixtures/manifest.json'
        for name, expected in seal['files'].items():
            path = ROOT / name
            assert hashlib.sha256(path.read_bytes()).hexdigest() == expected, 'sealed source changed: ' + name
            selected[path] = 'repository/' + name
        add_tree(ROOT / 'som-core/data/composed', 'repository/som-core/data/composed')
        spec = importlib.util.spec_from_file_location('composed_check', EVAL / kit / 'check.py')
        checker = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(checker)
        for job in json.loads(jobs_path.read_text())['jobs']:
            root = Path(job['directory']).resolve()
            assert root.parent == Path('/private/tmp') / workspace_name
            workspaces.add(root)
            for session in checker.sessions(root):
                selected[session] = 'native-sessions/' + session.name
    for root in sorted(workspaces):
        assert root.is_dir(), 'missing raw evidence workspace: ' + str(root)
        add_tree(root, 'workspaces/' + root.parent.name + '/' + root.name)
    probe_workspace = Path('/private/tmp/som-composable-transport-probe-2026-10-01')
    add_tree(probe_workspace, 'transport-probe-workspace')
    for probe in EVAL.glob('transport-probe-*/visibility.json'):
        record = json.loads(probe.read_text())
        if record.get('session'):
            session = Path(record['session'])
            assert hashlib.sha256(session.read_bytes()).hexdigest() == record['session_sha256']
            selected[session] = 'native-sessions/' + session.name

    archive = args.output / 'evidence.tar.gz'
    manifest = []
    with tarfile.open(archive, 'w:gz', compresslevel=6) as output:
        for path, member in sorted(selected.items(), key=lambda pair: pair[1]):
            if path.is_relative_to(args.output.resolve()):
                continue
            manifest.append({'source': str(path), 'member': member, 'bytes': path.stat().st_size,
                             'sha256': hashlib.sha256(path.read_bytes()).hexdigest()})
            output.add(path, arcname=member, recursive=False)
    assert len({row['member'] for row in manifest}) == len(manifest), 'duplicate archive member'
    with tarfile.open(archive, 'r:gz') as saved:
        assert len(saved.getmembers()) == len(manifest), 'archive file count differs'
        for row in manifest:
            data = saved.extractfile(row['member']).read()
            assert len(data) == row['bytes'] and hashlib.sha256(data).hexdigest() == row['sha256'], 'archive verification failed: ' + row['member']
    result = {'archive': str(archive), 'archive_sha256': hashlib.sha256(archive.read_bytes()).hexdigest(),
              'contents_verified': True,
              'files': manifest, 'excluded': sorted(EXCLUDED),
              'note': 'Source, failed attempts and actual native transcripts are retained. '
              'Rebuildable virtual environments and temporary database storage are excluded.'}
    (args.output / 'manifest.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps({'archive': str(archive), 'files': len(manifest),
                      'bytes': archive.stat().st_size, 'sha256': result['archive_sha256']}))


if __name__ == '__main__':
    main()

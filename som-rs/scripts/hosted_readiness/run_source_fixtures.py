"""Run the complete source fixture inventory in separate GitHub processes."""
import argparse
import ast
import configparser
import hashlib
import io
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import time
import zipfile
from types import ModuleType

BASE = 'som-rs/scripts/hosted_readiness/'
SELF = BASE + 'run_source_fixtures.py'
MANIFEST = BASE + 'inputs/source-fixture-python-imports.json'
REQUIREMENTS = BASE + 'inputs/source-fixture-python-imports.txt'
WHEELS = BASE + 'product_python/produce_wheels.py'
MANIFEST_SHA256 = '128f6ea899a8820797e742f56014b339efb02c6dbe7d57f64b03c97cc65f2f24'
REQUIREMENTS_SHA256 = '3fd973f04529d2284e9a3000b29ebadbe9f96fc08f29a4293d0db0f3b7dabda6'
BOOTSTRAP = '''import hashlib,json,sys
from pathlib import Path
from types import ModuleType
site,inventory_sha,entry_sha,test_root,entry,*arguments=sys.argv[1:]
root=Path(site)
inventory_raw=(root.parent/'site-inventory.json').read_bytes()
assert hashlib.sha256(inventory_raw).hexdigest()==inventory_sha
expected=json.loads(inventory_raw)
actual={p.relative_to(root).as_posix():{'sha256':hashlib.sha256(p.read_bytes()).hexdigest(),'size':p.stat().st_size,'mode':p.stat().st_mode&0o777} for p in root.rglob('*') if p.is_file()}
assert actual==expected and not any(p.is_symlink() for p in root.rglob('*'))
assert not any(p.suffix in {'.pyc','.pyo','.pth'} or p.name in {'sitecustomize.py','usercustomize.py'} for p in root.rglob('*'))
assert sys.flags.isolated and sys.flags.no_site and sys.dont_write_bytecode
raw=Path(entry).read_bytes()
assert hashlib.sha256(raw).hexdigest()==entry_sha
sys.path[:0]=[test_root,site]
sys.argv=[entry,*arguments]
module=ModuleType('__main__')
module.__file__,module.__cached__,module.__package__,module.__spec__=entry,None,'',None
module.__som_raw_sha256__=entry_sha
sys.modules['__main__']=module
exec(compile(raw,entry,'exec',dont_inherit=True),module.__dict__)
'''


def require(value, message):
    if not value:
        raise ValueError(message)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def save(path, value):
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + '\n')


def git(repo, *args, input=None):
    return subprocess.check_output(['/usr/bin/git', '--no-replace-objects', '-C', str(repo), *args],
                                   stderr=subprocess.PIPE, input=input)


def source(repo, revision, name):
    path = repo / name
    require(not any(p.is_symlink() for p in (path, *path.parents)), 'linked source refused')
    body = path.read_bytes()
    require(body == git(repo, 'cat-file', 'blob', revision + ':' + name), 'source differs from exact Git bytes')
    return body


def source_inventory(repo, revision):
    require(git(repo, 'rev-parse', 'HEAD').decode().strip() == revision, 'fixture HEAD changed')
    entries = []
    for item in git(repo, 'ls-tree', '-r', '-z', revision).split(b'\0'):
        if not item:
            continue
        header, name = item.split(b'\t', 1)
        mode, kind, oid = header.decode().split()
        require(kind == 'blob' and mode in {'100644', '100755', '120000'}, 'unsupported fixture Git source entry')
        entries.append((name.decode(), mode, oid))
    data = io.BytesIO(git(repo, 'cat-file', '--batch', input=''.join(oid + '\n' for _, _, oid in entries).encode()))
    expected = {}
    for name, mode, oid in entries:
        actual_oid, kind, size = data.readline().decode().split()
        require(actual_oid == oid and kind == 'blob' and size.isdecimal(), 'actual Git source blob differs')
        body = data.read(int(size)); require(len(body) == int(size) and data.read(1) == b'\n', 'source blob length differs')
        expected[name] = {'mode': mode, 'sha256': sha(body), 'bytes': len(body)}
    require(data.read() == b'', 'extra source object bytes')
    verify_sources(repo, revision, expected)
    return expected


def verify_sources(repo, revision, expected):
    require(git(repo, 'rev-parse', 'HEAD').decode().strip() == revision, 'fixture HEAD changed')
    actual_names = {n.decode() for n in git(repo, 'ls-tree', '-r', '-z', '--name-only', revision).split(b'\0') if n}
    require(actual_names == set(expected), 'complete fixture source inventory changed')
    python_roots = set()
    for name, wanted in expected.items():
        path = repo / name
        require(not any(p.is_symlink() for p in path.parents), 'linked source parent')
        info = path.lstat()
        if wanted['mode'] == '120000':
            require(stat.S_ISLNK(info.st_mode), 'tracked link type changed')
            raw = os.fsencode(os.readlink(path))
        else:
            require(stat.S_ISREG(info.st_mode) and bool(info.st_mode & 0o111) == (wanted['mode'] == '100755'),
                    'tracked fixture source mode changed')
            raw = path.read_bytes()
        require(len(raw) == wanted['bytes'] and sha(raw) == wanted['sha256'], 'tracked source changed: ' + name)
        if name.endswith('.py'):
            python_roots.add(path.parent)
    roots = [p for p in python_roots if not any(other in p.parents for other in python_roots)]
    for root in roots:
        for path in root.rglob('*'):
            name = path.relative_to(repo).as_posix()
            info = path.lstat()
            if stat.S_ISLNK(info.st_mode):
                require(name in expected and expected[name]['mode'] == '120000',
                        'unowned repository import directory or file alias: ' + name)
                continue
            require(stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode),
                    'special repository import entry: ' + name)
            if path.is_file() and path.suffix in {'.py', '.pyc', '.pyo', '.so', '.pth'}:
                require(name in expected and path.suffix not in {'.pyc', '.pyo', '.pth'},
                        'unowned repository import or startup payload: ' + name)


def fixture_entry_points(audits, packages, wheel_root):
    commands = {}
    for package in packages:
        audit = audits[package['name']]
        member = audit['dist_info_dir'].rstrip('/') + '/entry_points.txt'
        entries = [row for row in audit['members'] if row['path'] == member]
        if not entries:
            continue
        wheel = wheel_root / package['filename']
        raw = wheel.read_bytes()
        require(len(raw) == package['bytes'] and sha(raw) == package['sha256'], 'entry point wheel identity differs')
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            body = archive.read(member)
        require(len(entries) == 1 and len(body) == entries[0]['size'] and sha(body) == entries[0]['sha256'],
                'original entry point metadata differs')
        parser = configparser.ConfigParser(interpolation=None, strict=True)
        parser.optionxform = str
        parser.read_string(body.decode('utf-8', 'strict'))
        for group in ('console_scripts', 'gui_scripts'):
            if not parser.has_section(group):
                continue
            for name, target in parser.items(group):
                require(re.fullmatch('[A-Za-z0-9_-]+', name) is not None and target,
                        'bounded extensionless fixture entry point required')
                path = 'bin/' + name
                require(path not in commands, 'overlapping generated fixture entry point')
                commands[path] = {'distribution': package['name'], 'wheel_sha256': package['sha256'],
                    'source_member': member, 'source_sha256': sha(body), 'declared_target': target}
    return commands


def check_installed_scope(site, audits, packages, wheel_root):
    expected = {}
    records, metadata_roles = set(), {}
    commands = fixture_entry_points(audits, packages, wheel_root)
    for name, audit in audits.items():
        prefix = audit['dist_info_dir'].rstrip('/')
        records.add(prefix + '/RECORD')
        for member in audit['members']:
            path = member['path']
            data_prefix = prefix.removesuffix('.dist-info') + '.data/'
            if path.startswith(data_prefix):
                role, separator, relative = path[len(data_prefix):].partition('/')
                require(separator and role == 'headers' and relative
                        and all(part not in {'', '.', '..'} for part in relative.split('/')),
                        'unsupported fixture wheel data role')
                path = 'include/python/' + name + '/' + relative
            else:
                require('.data/' not in path, 'unbound fixture wheel data path')
            require(path not in expected, 'overlapping fixture wheel payload')
            expected[path] = member
        for leaf in ('INSTALLER', 'REQUESTED', 'direct_url.json'):
            metadata_roles[prefix + '/' + leaf] = (name, leaf)
    observed = {}
    for path in site.rglob('*'):
        info = path.lstat()
        require(not path.is_symlink() and (stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode)),
                'linked or special fixture dependency member')
        if not path.is_file():
            continue
        name = path.relative_to(site).as_posix()
        require(path.suffix not in {'.pyc', '.pyo', '.pth'} and path.name not in {'sitecustomize.py', 'usercustomize.py'},
                'cached or automatic startup code refused')
        raw = path.read_bytes()
        if name in expected:
            require(name in records or (len(raw) == expected[name]['size'] and sha(raw) == expected[name]['sha256']),
                    'installed fixture bytes differ from the original audited wheel')
        elif name in metadata_roles:
            package_name, role = metadata_roles[name]
            if role == 'INSTALLER':
                require(raw == b'pip\n', 'installer metadata differs')
            elif role == 'REQUESTED':
                require(raw == b'', 'requested metadata differs')
            else:
                package = next(row for row in packages if row['name'] == package_name)
                value = json.loads(raw)
                require(set(value) == {'archive_info', 'url'}
                        and value['url'] == (wheel_root / package['filename']).as_uri()
                        and value['archive_info'].get('hashes') == {'sha256': package['sha256']},
                        'generated wheel origin metadata differs')
        else:
            # These retained extensionless commands come from the original
            # wheel declarations. Fixtures import package files, not commands.
            require(name in commands,
                    'unowned installed fixture member: ' + name)
        observed[name] = {'sha256': sha(raw), 'size': len(raw), 'mode': stat.S_IMODE(info.st_mode)}
    require(set(expected) <= set(observed), 'original wheel members were omitted')
    return observed


def command(argv, directory, label, *, timeout=300):
    started = time.monotonic()
    record = {'argv': argv, 'wait_completed': False, 'exit_code': None,
              'product_acceptance_passed': False}
    try:
        result = subprocess.run(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                timeout=timeout, check=False)
        (directory / (label + '.stdout.raw')).write_bytes(result.stdout)
        (directory / (label + '.stderr.raw')).write_bytes(result.stderr)
        record.update(wait_completed=True, exit_code=result.returncode)
        sys.stdout.buffer.write(result.stdout)
        sys.stdout.buffer.write(result.stderr)
        sys.stdout.flush()
        return record
    except subprocess.TimeoutExpired as error:
        (directory / (label + '.stdout.raw')).write_bytes(error.stdout or b'')
        (directory / (label + '.stderr.raw')).write_bytes(error.stderr or b'')
        record['error'] = 'timeout; no successful completion claimed'
        return record
    finally:
        record['elapsed_seconds'] = time.monotonic() - started
        save(directory / (label + '.command.json'), record)


def prepare_dependencies(repo, revision, out):
    raw = source(repo, revision, MANIFEST)
    requirements = source(repo, revision, REQUIREMENTS)
    require(sha(raw) == MANIFEST_SHA256 and sha(requirements) == REQUIREMENTS_SHA256,
            'exact independently audited fixture dependency inputs required')
    manifest = json.loads(raw)
    packages = manifest['packages']
    require(len(packages) == 13 and {p['name'] for p in packages} == {
        'jsonschema', 'attrs', 'jsonschema-specifications', 'referencing', 'rpds-py', 'typing-extensions',
        'psycopg', 'psycopg-binary', 'alembic', 'sqlalchemy', 'mako', 'markupsafe', 'greenlet'},
        'complete minimal fixture dependency closure required')
    require(sum(p['bytes'] for p in packages) == 10402540, 'fixture wheel size vector differs')
    root = out / 'dependencies'; root.mkdir()
    wheels = root / 'wheels'; wheels.mkdir()
    site = root / 'site'
    selected = root / 'requirements.selected.lock'
    selected.write_text(''.join(p['name'] + '==' + p['version'] + ' --hash=sha256:' + p['sha256'] + '\n'
                                for p in packages))
    for label, argv in [
        ('pip-version', [sys.executable, '-I', '-B', '-m', 'pip', '--version']),
        ('download', [sys.executable, '-I', '-B', '-m', 'pip', '--disable-pip-version-check',
            '--no-cache-dir', 'download', '--no-deps', '--only-binary=:all:', '--require-hashes',
            '--dest', str(wheels), '-r', str(repo / REQUIREMENTS)])]:
        require(command(argv, root, label)['exit_code'] == 0, 'fixture dependency command failed: ' + label)
    require({p.name for p in wheels.iterdir()} == {p['filename'] for p in packages}, 'selected wheel universe differs')
    helper = ModuleType('_source_fixture_wheel_auditor')
    helper.__file__ = str(repo / WHEELS)
    exec(compile(source(repo, revision, WHEELS), helper.__file__, 'exec', dont_inherit=True), helper.__dict__)
    audits = {}
    for package in packages:
        path = wheels / package['filename']
        require(path.stat().st_size == package['bytes'] and sha(path.read_bytes()) == package['sha256'],
                'downloaded fixture wheel differs')
        notices = root / ('audit-' + package['name']); notices.mkdir()
        audit = helper.audit_wheel(path, package['name'], package['version'], notices)
        require(audit['metadata_sha256'] == package['metadata_sha256']
                and audit['wheel_metadata_sha256'] == package['wheel_metadata_sha256']
                and audit['record_sha256'] == package['record_sha256'], 'original fixture wheel metadata differs')
        save(notices / 'inventory.json', audit)
        audits[package['name']] = audit
    result = command([sys.executable, '-I', '-B', '-m', 'pip', '--disable-pip-version-check',
        '--no-cache-dir', 'install', '--no-deps', '--no-index', '--find-links', str(wheels),
        '--only-binary=:all:', '--require-hashes', '--no-compile', '--target', str(site), '-r', str(selected)], root, 'install')
    require(result['exit_code'] == 0, 'fixture dependency installation failed')
    inventory = check_installed_scope(site, audits, packages, wheels)
    require(inventory, 'fixture dependency inventory is empty')
    save(root / 'site-inventory.json', inventory)
    save(root / 'qualification.json', {'scope': 'thirteen-wheel source fixture dependencies only',
        'manifest_sha256': sha(raw), 'requirements_sha256': sha(requirements),
        'site_inventory_sha256': sha((root / 'site-inventory.json').read_bytes()),
        'generated_console_script_origins': fixture_entry_points(audits, packages, wheels),
        'wheels': packages, 'product_environment_qualified': False, 'product_acceptance_passed': False})
    return site, sha((root / 'site-inventory.json').read_bytes())


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--repo', type=Path, required=True)
    parser.add_argument('--helper-revision', required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    require(os.environ.get('GITHUB_ACTIONS') == 'true' and sys.platform == 'linux'
            and sys.version_info[:2] == (3, 12), 'GitHub Linux Python3.12 source fixtures only')
    repo = args.repo.absolute()
    require(re.fullmatch('[0-9a-f]{40}', args.helper_revision)
            and git(repo, 'rev-parse', 'HEAD').decode().strip() == args.helper_revision,
            'actual checked fixture source required')
    source(repo, args.helper_revision, SELF)
    temporary = Path(os.environ['RUNNER_TEMP']).resolve(strict=True)
    out = args.out.absolute()
    require(temporary in out.parents and repo not in out.parents and '..' not in out.parts
            and not out.exists() and not any(p.is_symlink() for p in out.parents), 'new owned temporary output required')
    out.mkdir(mode=0o700)
    report = {'version': 'som.complete-source-fixture-file-run.v1', 'helper_revision': args.helper_revision,
              'files': [], 'all_files_ran': False, 'product_acceptance_passed': False,
              'result_scope': 'fixture-file command inventory; actual test counts and statuses remain in per-file output'}
    expected_sources = None
    try:
        root = repo / BASE
        expected_sources = source_inventory(repo, args.helper_revision)
        save(out / 'git-source-inventory.json', expected_sources)
        report['source_inventory_sha256'] = sha((out / 'git-source-inventory.json').read_bytes())
        names = sorted(name for name in expected_sources
                       if Path(name).parent == Path(BASE) and Path(name).name.startswith('test_') and name.endswith('.py'))
        require(names and names == sorted(p.relative_to(repo).as_posix() for p in root.glob('test_*.py')),
                'complete actual source fixture inventory required')
        for name in names:
            tree = ast.parse(source(repo, args.helper_revision, name))
            guard = ast.dump(ast.parse("__name__ == '__main__'", mode='eval').body)
            require(any(isinstance(n, ast.If) and ast.dump(n.test) == guard for n in tree.body),
                    'fixture must have an actual main entry: ' + name)
        report['complete_inventory'] = names
        site, site_inventory_sha = prepare_dependencies(repo, args.helper_revision, out)
        report['site_inventory_sha256'] = site_inventory_sha
        for index, name in enumerate(names):
            print('BEGIN SOURCE FIXTURE FILE ' + Path(name).name, flush=True)
            try:
                verify_sources(repo, args.helper_revision, expected_sources)
            except BaseException as error:
                report['files'].append({'path': name, 'command_invoked': False, 'wait_completed': False,
                    'exit_code': None, 'source_refusal': type(error).__name__ + ': ' + str(error)})
                print('REFUSED UNBOUND SOURCE FILE ' + name, flush=True)
                continue
            result = command([sys.executable, '-I', '-S', '-B', '-c', BOOTSTRAP,
                              str(site), site_inventory_sha, expected_sources[name]['sha256'], str(root),
                              str(repo / name), '-v'], out, 'file-' + str(index), timeout=600)
            result['command_invoked'] = True
            report['files'].append({'path': name, **result})
            print('END SOURCE FIXTURE FILE ' + Path(name).name + ' exit=' + str(result['exit_code']), flush=True)
        report['all_files_ran'] = len(report['files']) == len(names) and all(r['command_invoked'] for r in report['files'])
        report['all_files_completed'] = all(row['wait_completed'] for row in report['files'])
    except BaseException as error:
        report['error'] = type(error).__name__ + ': ' + str(error)
        raise
    finally:
        if expected_sources is not None:
            try:
                verify_sources(repo, args.helper_revision, expected_sources)
                report['source_stable_after'] = True
            except BaseException as error:
                report['source_stable_after'] = False
                report['source_postcheck_error'] = type(error).__name__ + ': ' + str(error)
        save(out / 'source-fixture-results.json', report)
    return 1 if not report.get('source_stable_after') or any(
        not row['wait_completed'] or row['exit_code'] != 0 for row in report['files']) else 0


if __name__ == '__main__':
    raise SystemExit(main())

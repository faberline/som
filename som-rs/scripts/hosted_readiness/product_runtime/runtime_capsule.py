#!/usr/bin/env python3
"""Transport one same-run qualified runtime without running installed code."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
import sys
import tarfile
from types import ModuleType
from urllib.request import HTTPRedirectHandler, Request, build_opener

BASE = 'som-rs/scripts/hosted_readiness/product_runtime/'
SELF = BASE + 'runtime_capsule.py'
REUSE, SUPPORT = BASE + 'reuse_runtime.py', BASE + 'qualify_runtime.py'
QUALIFICATION, AUDIT, PINS = (BASE + name for name in (
    'runtime-bf3-qualification.json', 'runtime-bf3-byte-audit.json', 'source-pins.json'))
SOURCE_PATHS = (SELF, REUSE, SUPPORT, QUALIFICATION, AUDIT, PINS)
VERSION = 'som.same-run-qualified-runtime-capsule.v1'
RESTORE_VERSION = 'som.same-run-qualified-runtime-capsule-restore.v1'
ARTIFACT_NAME = 'same-run-qualified-runtime-capsule'
SHA = re.compile(r'[a-f0-9]{64}\Z')
MAX_FILE, MAX_TOTAL, MAX_META, MAX_ENTRIES = 256 * 1024**2, 2 * 1024**3, 16 * 1024**2, 20000
EMPTY_SCRATCH = {'build', 'cache', 'pycache', 'tmp'}
TOP_FILES = {'receipt.json', 'artifact-origin.json', 'github.env'}
TOP_DIRS = {'install', 'licenses', 'qualification-evidence', 'logs'} | EMPTY_SCRATCH


def require(ok, message):
    if not ok:
        raise ValueError(message)


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def parse(raw):
    def pairs(rows):
        result = {}
        for key, value in rows:
            require(key not in result, 'duplicate JSON member')
            result[key] = value
        return result
    return json.loads(raw, object_pairs_hook=pairs,
        parse_constant=lambda _: (_ for _ in ()).throw(ValueError('nonfinite JSON number')))


def encoded(value):
    return (json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False) + '\n').encode()


def relative(name):
    require(type(name) is str and name and not name.startswith('/') and '\\' not in name
            and all(p not in {'', '.', '..'} for p in name.split('/'))
            and not any(ord(c) < 32 or ord(c) == 127 for c in name), 'unsafe capsule path')
    return name


def absolute(path):
    path = Path(path).absolute()
    require('..' not in path.parts and not any(ord(c) < 32 or ord(c) == 127 for c in str(path)),
            'unsafe absolute path')
    return path


def no_links(path, include_leaf=True):
    path = absolute(path)
    for item in ((path, *path.parents) if include_leaf else path.parents):
        require(not item.is_symlink(), 'symlink parent or input refused')
    return path


def read_file(path, maximum=MAX_FILE):
    path = no_links(path)
    with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK), 'rb') as stream:
        info = os.fstat(stream.fileno())
        require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and info.st_size <= maximum,
                'bounded regular single-link file required')
        raw = stream.read(maximum + 1)
        final = os.fstat(stream.fileno())
    require(len(raw) <= maximum and len(raw) == info.st_size
            and (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)
            == (final.st_dev, final.st_ino, final.st_size, final.st_mtime_ns, final.st_ctime_ns),
            'file changed during read')
    return raw


def write_file(path, raw, mode=0o644):
    no_links(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('xb') as stream:
        stream.write(raw)
        os.fchmod(stream.fileno(), mode)


def owned_new(path, repo, inputs=()):
    path, repo = no_links(path), absolute(repo)
    temporary = no_links(Path(os.environ['RUNNER_TEMP']).resolve(strict=True))
    require(temporary in path.parents and path.parent.is_dir() and not path.exists()
            and all(path != x and x not in path.parents and path not in x.parents for x in (repo, *inputs)),
            'new disjoint RUNNER_TEMP output required')
    return path


def run_identity():
    require(os.environ.get('GITHUB_ACTIONS') == 'true' and sys.platform == 'linux', 'GitHub Linux only')
    values = {name: os.environ.get(name) for name in ('GITHUB_REPOSITORY', 'GITHUB_RUN_ID', 'GITHUB_RUN_ATTEMPT')}
    require(values['GITHUB_REPOSITORY'] == 'faberline/som'
            and all(type(values[k]) is str and values[k].isdecimal() and int(values[k]) > 0
                    for k in ('GITHUB_RUN_ID', 'GITHUB_RUN_ATTEMPT')), 'actual GitHub run identity required')
    return {'repository': values['GITHUB_REPOSITORY'], 'run_id': int(values['GITHUB_RUN_ID']),
            'run_attempt': int(values['GITHUB_RUN_ATTEMPT'])}


class Sources:
    def __init__(self, repo, helper_revision):
        self.repo, self.revision, self.raw = no_links(repo), helper_revision, {}
        require(re.fullmatch('[a-f0-9]{40}', helper_revision) is not None, 'full helper revision required')
        require(Path(__file__).absolute() == self.repo / SELF, 'fixed capsule source path required')
        require(self.git('rev-parse', '--verify', 'HEAD^{commit}').strip().decode() == helper_revision
                and self.git('rev-parse', '--show-toplevel').strip().decode() == str(self.repo), 'helper source HEAD differs')
        for name in SOURCE_PATHS:
            tree = self.git('ls-tree', '-z', helper_revision, '--', name)
            require(tree.count(b'\0') == 1 and tree.endswith(b'\t' + name.encode() + b'\0')
                    and tree.startswith((b'100644 blob ', b'100755 blob ')), 'regular committed capsule source required')
            body = self.git('cat-file', 'blob', helper_revision + ':' + name)
            require(len(body) <= MAX_META and body == read_file(self.repo / name, MAX_META), 'capsule working source differs')
            self.raw[name] = body
        self.pins = {name: digest(body) for name, body in self.raw.items()}

    def git(self, *arguments):
        return subprocess.check_output(['/usr/bin/git', '--no-replace-objects', '-C', str(self.repo), *arguments],
            env={'PATH': '/usr/bin:/bin', 'LANG': 'C.UTF-8', 'LC_ALL': 'C.UTF-8',
                 'GIT_CONFIG_NOSYSTEM': '1', 'GIT_CONFIG_GLOBAL': '/dev/null'}, stderr=subprocess.DEVNULL, timeout=30)

    def verify(self):
        require(self.git('rev-parse', '--verify', 'HEAD^{commit}').strip().decode() == self.revision, 'helper HEAD changed')
        require(all(read_file(self.repo / name, MAX_META) == raw for name, raw in self.raw.items()), 'capsule sources changed')

    def validator(self):
        require('_som_reuse_support' not in sys.modules and '_som_capsule_reuse' not in sys.modules,
                'preloaded runtime validator refused')
        module = ModuleType('_som_capsule_reuse')
        module.__file__, module.__package__, module.__cached__ = str(self.repo / REUSE), '', None
        sys.modules[module.__name__] = module
        try:
            exec(compile(self.raw[REUSE], module.__file__, 'exec', dont_inherit=True), module.__dict__)
            require(module.SUPPORT_SHA256 == self.pins[SUPPORT], 'runtime support source pin differs')
            module.q.host()
            return module
        except BaseException:
            sys.modules.pop(module.__name__, None)
            raise


def environment_bytes(root):
    values = {'SOM_PRODUCT_PYTHON': root / 'install/python/bin/python3.12', 'SOM_PRODUCT_UV': root / 'install/uv/bin/uv',
              'SOM_PRODUCT_PG_BIN': root / 'install/postgresql/bin', 'SOM_PRODUCT_MONGOD': root / 'install/mongodb/bin/mongod',
              'SOM_PRODUCT_REDIS_SERVER': root / 'install/redis/bin/redis-server'}
    return ''.join(key + '=' + str(value) + '\n' for key, value in values.items()).encode()


def command_logs(root, commands):
    require(type(commands) is list and commands, 'complete runtime command records required')
    result = {}
    for command in commands:
        require(type(command.get('exit_code')) is int and command['exit_code'] == 0
                and 'interrupted' not in command and type(command.get('pid')) is int and command['pid'] > 0,
                'runtime command failed or wait outcome absent')
        for stream in ('stdout', 'stderr'):
            path = absolute(command[stream])
            require(root in path.parents, 'runtime command log escapes prefix')
            name = relative(path.relative_to(root).as_posix())
            require(name.startswith('logs/') and name not in result, 'runtime log is duplicate or outside logs')
            raw = read_file(path)
            require(digest(raw) == command.get(stream + '_sha256'), 'runtime command raw log changed')
            result[name] = digest(raw)
    return result


def scan_tree(root, aliases):
    root = no_links(root)
    require(root.is_dir() and stat.S_IMODE(root.stat().st_mode) == 0o700, 'qualified root mode differs')
    result, total = {}, 0
    for base, dirs, files in os.walk(root, followlinks=False):
        for name in sorted(dirs + files):
            path = Path(base) / name
            relative_name = relative(path.relative_to(root).as_posix())
            info = path.lstat()
            if stat.S_ISLNK(info.st_mode):
                require(relative_name in aliases and os.readlink(path) == aliases[relative_name]
                        and path.resolve(strict=True).is_file() and root / 'install' in path.resolve(strict=True).parents,
                        'unknown or escaping installed alias')
                require(name not in dirs, 'directory alias refused')
                result[relative_name] = {'kind': 'alias', 'target': aliases[relative_name], 'mode': 0o777}
            elif stat.S_ISDIR(info.st_mode):
                require(stat.S_IMODE(info.st_mode) in {0o700, 0o755}, 'unknown runtime directory mode')
                result[relative_name] = {'kind': 'directory', 'mode': stat.S_IMODE(info.st_mode)}
            else:
                raw = read_file(path)
                mode = stat.S_IMODE(info.st_mode)
                allowed_modes = {0o644, 0o755} if relative_name.startswith('install/') else {0o600, 0o644}
                require(mode in allowed_modes, 'unknown runtime regular-file mode')
                total += len(raw)
                require(total <= MAX_TOTAL, 'runtime capsule exceeds total limit')
                result[relative_name] = {'kind': 'file', 'mode': mode, 'sha256': digest(raw), 'size': len(raw)}
            require(len(result) <= MAX_ENTRIES, 'runtime capsule exceeds member limit')
    return dict(sorted(result.items()))


def validate_runtime(root, sources, reuse):
    root = no_links(root)
    qualification = reuse.qualification_data(sources.raw[QUALIFICATION], sources.raw[AUDIT], sources.raw[PINS])
    require(str(root) == qualification['install_prefix'], 'runtime relocation is not admitted')
    receipt_raw = read_file(root / 'receipt.json', MAX_META)
    receipt = parse(receipt_raw)
    require(receipt.get('version') == 'som.product-runtime-reuse.v1'
            and receipt.get('status') == 'audited_runtime_reinstalled_and_rechecked_for_hosted_tests'
            and receipt.get('tested_sha') == sources.revision and receipt.get('qualified_for') == 'later_GitHub_tests_only'
            and receipt.get('installed_tree_verified_before') is True and receipt.get('installed_tree_verified_after') is True
            and receipt.get('qualification_evidence_verified_after') is True and receipt.get('services') == 'not_started'
            and receipt.get('product_runtime_ready') is False and receipt.get('product_acceptance_passed') is False,
            'same-helper qualified runtime reuse receipt required')
    identity = run_identity()
    require(receipt['runner']['GITHUB_RUN_ID'] == str(identity['run_id'])
            and receipt['runner']['GITHUB_RUN_ATTEMPT'] == str(identity['run_attempt']), 'runtime was not qualified in this same run')
    binding = receipt.get('source_bindings', {})
    require(binding.get('revision') == sources.revision and set(binding.get('files', {})) == set(SOURCE_PATHS) - {SELF},
            'runtime qualification source group differs')
    for name, row in binding['files'].items():
        require(row.get('sha256') == row.get('working_sha256') == sources.pins[name]
                and row.get('working_bytes_match') is True, 'runtime qualifier source bytes differ')
    expected_binding = {'manifest_path': QUALIFICATION, 'manifest_sha256': sources.pins[QUALIFICATION],
        'audit_sha256': sources.pins[AUDIT], 'artifact_id': qualification['artifact_id'], 'run_id': qualification['run_id'],
        'artifact_sha256': qualification['artifact_sha256'], 'receipt_sha256': qualification['transport_files']['receipt.json'],
        'installed_inventory_sha256': qualification['transport_files']['installed-files.json'],
        'source_pins_sha256': qualification['transport_files']['source-pins.json']}
    require(receipt.get('qualification') == expected_binding, 'complete runtime qualification profile differs')
    inventory = reuse.read_inventory(root / 'qualification-evidence/installed-files.json', qualification)
    tree = reuse.verify_installed_tree(root, inventory, qualification)
    old_raw = read_file(root / 'qualification-evidence/receipt.json', MAX_META)
    old = parse(old_raw)
    pins = reuse.q.manifest_data(sources.raw[PINS])
    expected = reuse.transport_expectations(old, inventory, pins, qualification)
    reuse.verify_evidence(root, expected)
    logs = command_logs(root, receipt.get('commands'))
    actual_logs, actual_dirs = reuse.collect_files(root / 'logs')
    require(set(actual_logs) == {n.removeprefix('logs/') for n in logs}
            and actual_dirs == reuse.expected_directories(actual_logs), 'runtime recheck log universe differs')
    admitted = receipt.get('admitted_executables', {})
    require(set(admitted) == set(qualification['admitted_paths']), 'complete native recheck inventory missing')
    for name, row in admitted.items():
        old_row = old['admitted_executables'][name]
        require(row.get('path') == str(root / name) and row.get('sha256') == inventory[name]['sha256']
                and row.get('status') == 'hash_and_version_rechecked' and row.get('version_exit_code') == 0
                and row.get('authority') == 'controller_audited_saved_artifact'
                and row.get('qualified_for') == 'later_GitHub_tests_only' and row.get('version_stdout') == old_row['version_stdout'],
                'native tool source hash or version qualification differs')
        index = row.get('version_command_index')
        require(type(index) is int and 0 <= index < len(receipt['commands']), 'native version command index differs')
        command = receipt['commands'][index]
        argv = [str(root / name), '-B', '--version'] if name.startswith('install/python/') else [str(root / name), '--version']
        require(command['argv'] == argv and read_file(Path(command['stdout'])) == row['version_stdout'].encode()
                and command['stderr_sha256'] == old['commands'][old_row['version_command_index']]['stderr_sha256'],
                'native version raw command evidence differs')
    probe = receipt.get('python_modules', {})
    require(probe.get('stdout') == old['python_modules'] and probe.get('exit_code') == 0
            and type(probe.get('command_index')) is int and 0 <= probe['command_index'] < len(receipt['commands']),
            'Python core module qualification differs')
    command = receipt['commands'][probe['command_index']]
    require(command['argv'] == [str(root / 'install/python/bin/python3.12'), '-I', '-B', '-c', reuse.MODULE_PROBE]
            and read_file(Path(command['stdout'])) == old['python_modules'].encode()
            and read_file(Path(command['stderr'])) == b'', 'Python module probe raw log differs')
    origin_raw = read_file(root / 'artifact-origin.json', MAX_META)
    origin = parse(origin_raw)
    require(receipt.get('origin') == {'metadata_sha256': digest(origin_raw), 'artifact_id': qualification['artifact_id'],
            'run_id': qualification['run_id'], 'artifact_sha256': qualification['artifact_sha256']}
            and origin.get('id') == qualification['artifact_id'] and origin.get('name') == qualification['artifact_name']
            and origin.get('digest') == 'sha256:' + qualification['artifact_sha256']
            and origin.get('workflow_run', {}).get('id') == qualification['run_id']
            and origin.get('workflow_run', {}).get('head_sha') == qualification['source_revision'],
            'original audited-runtime artifact origin differs')
    require(read_file(root / 'github.env', MAX_META) == environment_bytes(root), 'fixed runtime environment exports differ')
    require({p.name for p in root.iterdir() if p.is_file()} == TOP_FILES
            and {p.name for p in root.iterdir() if p.is_dir()} == TOP_DIRS, 'qualified runtime top-level universe differs')
    require(all(not any((root / name).iterdir()) for name in EMPTY_SCRATCH), 'runtime scratch directory is not empty')
    entries = scan_tree(root, qualification['aliases'])
    return {'reuse_receipt_sha256': digest(receipt_raw), 'qualification_sha256': sources.pins[QUALIFICATION],
            'audit_sha256': sources.pins[AUDIT], 'source_pins_sha256': sources.pins[PINS],
            'inventory_sha256': qualification['transport_files']['installed-files.json'],
            'original_receipt_sha256': digest(old_raw), 'installed_tree': tree,
            'admitted_executables': {name: {'sha256': row['sha256'], 'version_stdout': row['version_stdout']}
                                     for name, row in sorted(admitted.items())},
            'recheck_log_sha256': logs, 'aliases': qualification['aliases']}, entries


def tar_header(name, row):
    header = tarfile.TarInfo(relative(name))
    header.mode, header.uid, header.gid, header.mtime = row['mode'], 0, 0, 0
    header.uname = header.gname = ''
    if row['kind'] == 'directory':
        header.type = tarfile.DIRTYPE
    elif row['kind'] == 'alias':
        header.type, header.linkname = tarfile.SYMTYPE, row['target']
    else:
        require(row['kind'] == 'file', 'unknown capsule member kind')
        header.type, header.size = tarfile.REGTYPE, row['size']
    header.tobuf(format=tarfile.USTAR_FORMAT, encoding='utf-8', errors='strict')
    return header


def pack_archive(runtime_root, archive_path, entries):
    with tarfile.open(archive_path, 'x:', format=tarfile.USTAR_FORMAT, encoding='utf-8', errors='strict') as archive:
        for name, row in entries.items():
            header = tar_header(name, row)
            if row['kind'] == 'file':
                body = read_file(runtime_root / name)
                require(digest(body) == row['sha256'] and len(body) == row['size'], 'runtime changed before packing')
                import io
                archive.addfile(header, io.BytesIO(body))
            else:
                archive.addfile(header)
    archive_path.chmod(0o644)


def validate_entries(entries):
    require(type(entries) is dict and 0 < len(entries) <= MAX_ENTRIES, 'complete bounded runtime member map required')
    for name, row in entries.items():
        relative(name)
        require(type(row) is dict and row.get('kind') in {'file', 'directory', 'alias'}, 'unknown capsule entry kind')
        fields = {'kind', 'mode'} | ({'size', 'sha256'} if row['kind'] == 'file' else {'target'} if row['kind'] == 'alias' else set())
        require(set(row) == fields and type(row['mode']) is int, 'unknown capsule entry fields or mode type')
        file_modes = {0o644, 0o755} if name.startswith('install/') else {0o600, 0o644}
        require(row['mode'] in (file_modes if row['kind'] == 'file' else {0o700, 0o755} if row['kind'] == 'directory' else {0o777}),
                'unadmitted capsule mode')
        for parent in PurePosixPath(name).parents:
            if str(parent) != '.':
                require(entries.get(str(parent), {}).get('kind') == 'directory', 'capsule parent is missing or not a directory')
        if row['kind'] == 'file':
            require(type(row['size']) is int and 0 <= row['size'] <= MAX_FILE
                    and type(row['sha256']) is str and SHA.fullmatch(row['sha256']) is not None, 'invalid capsule file hash or size')
        elif row['kind'] == 'alias':
            target = row['target']
            require(type(target) is str and relative(target) == target and '/' not in target, 'invalid capsule alias basename')
            resolved = str(PurePosixPath(name).parent / target)
            require(entries.get(resolved, {}).get('kind') == 'file', 'capsule alias must target its recorded regular sibling')


def validate_archive(archive_path, entries):
    validate_entries(entries)
    seen, total = set(), 0
    with tarfile.open(archive_path, 'r:', encoding='utf-8', errors='strict') as archive:
        for header in archive:
            name = relative(header.name.removesuffix('/') if header.isdir() else header.name)
            require(name in entries and name not in seen and not header.pax_headers
                    and header.uid == header.gid == header.mtime == header.devmajor == header.devminor == 0
                    and header.uname == header.gname == '' and header.offset_data == header.offset + 512,
                    'unknown, duplicate or noncanonical capsule header')
            seen.add(name)
            row = entries[name]
            wanted_header = tar_header(name, row)
            require(header.mode == row['mode'] and header.type == wanted_header.type
                    and header.linkname == wanted_header.linkname, 'capsule kind, mode or link metadata differs')
            if row['kind'] == 'file':
                require(header.size == row['size'] and 0 <= header.size <= MAX_FILE, 'capsule member size differs')
                body = archive.extractfile(header).read(MAX_FILE + 1)
                total += len(body)
                require(total <= MAX_TOTAL and len(body) == header.size and digest(body) == row['sha256'], 'capsule bytes differ')
            elif row['kind'] == 'alias':
                require(header.size == 0 and header.linkname == row['target'] and '/' not in row['target']
                        and row['target'] not in {'.', '..'}, 'capsule alias target differs')
            else:
                require(row['kind'] == 'directory' and header.size == 0 and not header.linkname, 'capsule directory differs')
    require(seen == set(entries), 'capsule archive member universe differs')


def export_capsule(repo, helper_revision, candidate_revision, runtime_root, out):
    identity = run_identity()
    require(helper_revision == candidate_revision, 'same-source capsule requires helper revision equal candidate revision')
    require(re.fullmatch('[a-f0-9]{40}', candidate_revision) is not None, 'full candidate revision required')
    sources = Sources(repo, helper_revision)
    reuse = sources.validator()
    runtime_root = no_links(runtime_root)
    require(Path(os.environ['RUNNER_TEMP']).resolve(strict=True) in runtime_root.parents, 'owned runtime input required')
    out = owned_new(out, sources.repo, (runtime_root,))
    profile, entries = validate_runtime(runtime_root, sources, reuse)
    out.mkdir(mode=0o700)
    archive_path = out / 'runtime.tar'
    pack_archive(runtime_root, archive_path, entries)
    validate_archive(archive_path, entries)
    require(validate_runtime(runtime_root, sources, reuse) == (profile, entries), 'qualified runtime changed during export')
    sources.verify()
    manifest = {'version': VERSION, 'role': 'same-run-qualified-runtime-transport', 'github': identity,
        'helper_revision': helper_revision, 'candidate_revision': candidate_revision, 'source_sha256': sources.pins,
        'runtime_prefix': str(runtime_root), 'profile': profile, 'entries': entries,
        'archive': {'name': 'runtime.tar', 'sha256': digest(read_file(archive_path, MAX_TOTAL + MAX_ENTRIES * 1024)),
                    'size': archive_path.stat().st_size},
        'installed_code_executed_by_capsule': False, 'product_runtime_ready': False, 'product_acceptance_passed': False}
    raw = encoded(manifest)
    write_file(out / 'capsule.json', raw)
    return {'runtime_capsule_manifest_sha256': digest(raw), 'runtime_capsule_archive_sha256': manifest['archive']['sha256'],
            'capsule_root': str(out), 'product_acceptance_passed': False}


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args):
        raise ValueError('capsule artifact metadata redirect refused')


def verify_artifact(artifact_id, artifact_digest, identity):
    require(type(artifact_id) is int and artifact_id > 0, 'same-run uploaded artifact ID required')
    artifact_digest = artifact_digest.removeprefix('sha256:')
    require(SHA.fullmatch(artifact_digest) is not None, 'exact uploaded artifact digest required')
    url = f'https://api.github.com/repos/faberline/som/actions/artifacts/{artifact_id}'
    headers = {'Accept': 'application/vnd.github+json', 'X-GitHub-Api-Version': '2026-03-10', 'User-Agent': 'SOM-runtime-capsule/1'}
    if os.environ.get('SOM_RUNTIME_ACTIONS_TOKEN'):
        headers['Authorization'] = 'Bearer ' + os.environ['SOM_RUNTIME_ACTIONS_TOKEN']
    with build_opener(NoRedirect()).open(Request(url, headers=headers), timeout=30) as response:
        raw = response.read(1024**2 + 1)
    require(len(raw) <= 1024**2, 'capsule artifact metadata exceeds bound')
    metadata = parse(raw)
    require(type(metadata.get('id')) is int and metadata.get('id') == artifact_id and metadata.get('name') == ARTIFACT_NAME
            and metadata.get('expired') is False and metadata.get('digest') == 'sha256:' + artifact_digest
            and type(metadata.get('workflow_run', {}).get('id')) is int
            and metadata.get('workflow_run', {}).get('id') == identity['run_id'] and metadata.get('url') == url,
            'capsule artifact is not the exact same-run upload')
    return raw


def unpack_archive(archive_path, out, entries):
    out.mkdir(mode=0o700)
    for name, row in sorted(entries.items(), key=lambda item: (item[0].count('/'), item[0])):
        if row['kind'] == 'directory':
            path = out / name
            path.mkdir(mode=row['mode'])
            path.chmod(row['mode'])
    with tarfile.open(archive_path, 'r:', encoding='utf-8', errors='strict') as archive:
        for header in archive:
            if header.isfile():
                name = relative(header.name)
                body = archive.extractfile(header).read(MAX_FILE + 1)
                require(len(body) == entries[name]['size'] and digest(body) == entries[name]['sha256'], 'capsule changed during restore')
                write_file(out / name, body, entries[name]['mode'])
    for name, row in entries.items():
        if row['kind'] == 'alias':
            no_links((out / name).parent)
            (out / name).symlink_to(row['target'])


def restore_capsule(repo, helper_revision, candidate_revision, capsule_root, out, evidence_out,
                    artifact_id, artifact_digest, manifest_sha256):
    identity = run_identity()
    require(helper_revision == candidate_revision, 'same-source capsule requires helper revision equal candidate revision')
    require(re.fullmatch('[a-f0-9]{40}', candidate_revision) is not None
            and SHA.fullmatch(manifest_sha256) is not None, 'exact candidate and export manifest identities required')
    sources = Sources(repo, helper_revision)
    reuse = sources.validator()
    capsule_root = no_links(capsule_root)
    temporary = Path(os.environ['RUNNER_TEMP']).resolve(strict=True)
    require(temporary in capsule_root.parents and capsule_root.is_dir()
            and {p.name for p in capsule_root.iterdir()} == {'capsule.json', 'runtime.tar'}, 'exact owned capsule root required')
    out = owned_new(out, sources.repo, (capsule_root,))
    evidence_out = owned_new(evidence_out, sources.repo, (capsule_root, out))
    evidence_out.mkdir(mode=0o700)
    receipt = {'version': RESTORE_VERSION, 'status': 'failed', 'github': identity,
        'helper_revision': helper_revision, 'candidate_revision': candidate_revision,
        'manifest_sha256': manifest_sha256, 'source_sha256': sources.pins,
        'installed_code_executed': False, 'product_runtime_ready': False, 'product_acceptance_passed': False}
    try:
        raw = read_file(capsule_root / 'capsule.json', MAX_META)
        require(digest(raw) == manifest_sha256, 'capsule manifest differs from this run export output')
        manifest = parse(raw)
        require(manifest.get('version') == VERSION and manifest.get('role') == 'same-run-qualified-runtime-transport'
                and manifest.get('github') == identity and manifest.get('helper_revision') == helper_revision
                and manifest.get('candidate_revision') == candidate_revision and manifest.get('source_sha256') == sources.pins
                and manifest.get('runtime_prefix') == str(out)
                and manifest.get('installed_code_executed_by_capsule') is False
                and manifest.get('product_runtime_ready') is False and manifest.get('product_acceptance_passed') is False,
                'capsule run, source, qualified prefix or role differs')
        qualification = reuse.qualification_data(sources.raw[QUALIFICATION], sources.raw[AUDIT], sources.raw[PINS])
        require(str(out) == qualification['install_prefix'], 'restored runtime relocation is not admitted')
        archive = manifest['archive']
        archive_raw = read_file(capsule_root / 'runtime.tar', MAX_TOTAL + MAX_ENTRIES * 1024)
        require(set(archive) == {'name', 'sha256', 'size'} and archive['name'] == 'runtime.tar'
                and len(archive_raw) == archive['size'] and digest(archive_raw) == archive['sha256'], 'capsule archive hash or size differs')
        origin_raw = verify_artifact(artifact_id, artifact_digest, identity)
        write_file(evidence_out / 'artifact-origin.json', origin_raw)
        receipt['artifact_origin_sha256'] = digest(origin_raw)
        receipt['artifact_id'], receipt['artifact_digest'] = artifact_id, artifact_digest.removeprefix('sha256:')
        require({name: row['target'] for name, row in manifest['entries'].items() if row.get('kind') == 'alias'}
                == qualification['aliases'], 'capsule aliases differ from committed qualification')
        validate_archive(capsule_root / 'runtime.tar', manifest['entries'])
        unpack_archive(capsule_root / 'runtime.tar', out, manifest['entries'])
        require(scan_tree(out, qualification['aliases']) == manifest['entries'], 'complete restored runtime universe differs')
        profile, entries = validate_runtime(out, sources, reuse)
        require(profile == manifest['profile'] and entries == manifest['entries'], 'restored runtime qualification authority differs')
        sources.verify()
        require(read_file(capsule_root / 'capsule.json', MAX_META) == raw
                and digest(read_file(capsule_root / 'runtime.tar', MAX_TOTAL + MAX_ENTRIES * 1024)) == archive['sha256'],
                'capsule input changed during restore')
        receipt.update(status='same_run_qualified_runtime_restored_without_execution',
            complete_runtime_tree_verified=True, qualification_profile=profile,
            runtime_root=str(out), github_env=str(out / 'github.env'), archive_sha256=archive['sha256'])
        return receipt
    except BaseException as error:
        receipt['failure'] = {'type': type(error).__name__, 'message': str(error)}
        raise
    finally:
        write_file(evidence_out / 'restore.json', encoded(receipt))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_subparsers(dest='mode', required=True)
    for mode in ('export', 'restore'):
        command = actions.add_parser(mode)
        command.add_argument('--repo', type=Path, required=True)
        command.add_argument('--helper-revision', required=True)
        command.add_argument('--candidate-revision', required=True)
        command.add_argument('--out', type=Path, required=True)
        if mode == 'export':
            command.add_argument('--runtime-root', type=Path, required=True)
        else:
            command.add_argument('--capsule-root', type=Path, required=True)
            command.add_argument('--evidence-out', type=Path, required=True)
            command.add_argument('--artifact-id', type=int, required=True)
            command.add_argument('--artifact-digest', required=True)
            command.add_argument('--manifest-sha256', required=True)
    args = parser.parse_args()
    if args.mode == 'export':
        result = export_capsule(args.repo, args.helper_revision, args.candidate_revision, args.runtime_root, args.out)
        if os.environ.get('GITHUB_OUTPUT'):
            path = no_links(Path(os.environ['GITHUB_OUTPUT']))
            read_file(path, MAX_META)
            with os.fdopen(os.open(path, os.O_WRONLY | os.O_APPEND | os.O_NOFOLLOW | os.O_NONBLOCK), 'ab') as stream:
                info = os.fstat(stream.fileno())
                require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1, 'GitHub output file changed type')
                for key in ('runtime_capsule_manifest_sha256', 'runtime_capsule_archive_sha256'):
                    stream.write((key + '=' + result[key] + '\n').encode())
    else:
        result = restore_capsule(args.repo, args.helper_revision, args.candidate_revision,
            args.capsule_root, args.out, args.evidence_out, args.artifact_id, args.artifact_digest, args.manifest_sha256)
    print(encoded(result).decode(), end='')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

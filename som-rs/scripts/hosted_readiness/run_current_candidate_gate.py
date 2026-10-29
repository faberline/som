#!/usr/bin/env python3
"""Own Linux current-candidate services for the actual M6 and TS gate adapters.

This runner restores an existing same-build candidate. It never builds one.
All project execution and fixtures belong on GitHub runners.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import hashlib
import inspect
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tempfile
from types import ModuleType

BASE = 'som-rs/scripts/hosted_readiness/'
SELF = BASE + 'run_current_candidate_gate.py'
CONTEXT = BASE + 'product_runtime/full_product_context.py'
AUTHORITY = BASE + 'native_qa_authority.py'
LAYOUT = BASE + 'product_runtime/current_candidate_layout.py'
CARRIER = BASE + 'product_runtime/candidate_carrier.py'
SEMANTIC = BASE + 'run_candidate_semantic_gates.py'
ORACLE = BASE + 'product_runtime/recorded_before_oracle.py'
BOUND = BASE + 'bound_source.py'
FULL_DRIVER = BASE + 'run_full_products.py'
VERSION = 'som.current-candidate-owned-gate.v1'
GATES = {'typescript-regression', 'm6-react003'}
PORTS = {'current': 18978, 'baseline': 18979}
PROTECTED = (8765, 18765)
ROLES = {'assembly_root', 'python_inputs_root', 'rust_inputs_root', 'carrier_root'}
TOOLS = {'SOM_RUFF', 'SOM_RUSTFMT', 'SOM_TS_NODE', 'SOM_TS_PRETTIER'}
PREPARATION_SOURCE = 'f59e28d910ff4adb16e3becfdc82bd2978ca84fa'


def require(value, message):
    if not value:
        raise ValueError(message)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def parse(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result, 'duplicate saved JSON key')
            result[key] = value
        return result
    return json.loads(raw, object_pairs_hook=pairs,
        parse_constant=lambda _: (_ for _ in ()).throw(ValueError('nonfinite JSON')))


def member(value):
    require(type(value) is str and value and not value.startswith('/') and '\\' not in value
            and all(x not in {'', '.', '..'} for x in value.split('/'))
            and not any(ord(c) < 32 or ord(c) == 127 for c in value), 'safe repository/member path required')
    return value


def regular_root(value):
    path = Path(value).absolute()
    require('..' not in path.parts and path.is_dir()
            and not any(p.is_symlink() for p in (path, *path.parents)), 'regular absolute directory required')
    return path


def read(value, limit=300 * 1024**2):
    path = Path(value).absolute()
    require('..' not in path.parts and not any(p.is_symlink() for p in (path, *path.parents)), 'linked raw input refused')
    with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK), 'rb') as stream:
        info = os.fstat(stream.fileno())
        require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and info.st_size <= limit,
                'bounded single-link regular raw file required')
        body = stream.read(limit + 1)
    require(len(body) == info.st_size and len(body) <= limit, 'raw file changed length')
    return body


def identity(path):
    raw = read(path)
    info = Path(path).stat()
    return {'sha256': sha(raw), 'size': len(raw), 'mode': stat.S_IMODE(info.st_mode)}


def tree(root):
    root = regular_root(root)
    result = {}
    for path in sorted(root.rglob('*')):
        require(not path.is_symlink() and (path.is_file() or path.is_dir()), 'linked or special tree member')
        if path.is_file():
            result[member(path.relative_to(root).as_posix())] = identity(path)
    return result


def tree_digest(values):
    return sha(''.join(name + '\0' + value['sha256'] + '\n' for name, value in sorted(values.items())).encode())


def portable(value):
    if type(value) is dict and {'sha256', 'size', 'mode'} <= set(value):
        return {key: value[key] for key in ('sha256', 'size', 'mode')}
    require(type(value) is dict, 'identity vector must be an object')
    return {key: portable(item) for key, item in value.items()}


def layout_binding(value):
    # The layout consumer rechecks original path-normalization facts in layout.json.
    # These seven output locations may move with an admitted artifact download.
    locations = {'candidate_dir', 'binary', 'assets', 'source_json', 'source_tar', 'build_json', 'original_build_json'}
    return {key: item for key, item in value.items() if key not in locations}


def save(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('xb') as stream:
        stream.write(canonical(value) + b'\n')


def copy_checked(source, destination, wanted):
    require(identity(source) == wanted, 'input bytes/mode changed before copy')
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open('xb') as stream:
        stream.write(read(source))
    destination.chmod(wanted['mode'])
    require(identity(source) == identity(destination) == wanted, 'input bytes/mode changed during copy')


class Sources:
    """Bind every helper body to both exact Git and raw working bytes."""
    def __init__(self, repo, revision, reader=None):
        self.root, self.revision, self.reader = regular_root(repo), revision, reader
        self.pins = {}
        require(type(revision) is str and re.fullmatch('[0-9a-f]{40}', revision), 'full source revision required')
        require(self.git('rev-parse', '--verify', 'HEAD^{commit}').decode().strip() == revision
                and self.git('rev-parse', '--show-toplevel').decode().strip() == str(self.root), 'exact current Git root/HEAD required')
        self.read(SELF)

    def git(self, *args):
        return subprocess.check_output(['/usr/bin/git', '--no-replace-objects', '-C', str(self.root), *args],
            env={'PATH': '/usr/bin:/bin', 'LANG': 'C.UTF-8', 'LC_ALL': 'C.UTF-8',
                 'GIT_CONFIG_NOSYSTEM': '1', 'GIT_CONFIG_GLOBAL': '/dev/null'}, timeout=60)

    def read(self, name):
        member(name)
        entry = self.git('ls-tree', '-z', self.revision, '--', name)
        require(entry.count(b'\0') == 1 and entry.endswith(b'\t' + name.encode() + b'\0')
                and entry.startswith((b'100644 blob ', b'100755 blob ')), 'regular committed source required')
        raw = self.git('cat-file', 'blob', self.revision + ':' + name)
        require(len(raw) <= 64 * 1024**2 and read(self.root / name) == raw
                and (self.reader is None or self.reader(name) == raw), 'helper raw bytes differ from current Git')
        require(name not in self.pins or self.pins[name] == sha(raw), 'helper source changed')
        self.pins[name] = sha(raw)
        return raw

    def paths(self):
        raw = self.git('ls-tree', '-r', '-z', '--name-only', self.revision)
        require(raw.endswith(b'\0'), 'complete tracked source inventory required')
        values = raw[:-1].decode('utf-8', 'strict').split('\0')
        require(len(values) == len(set(values)), 'duplicate source path')
        return set(values)

    def verify(self):
        require(self.git('rev-parse', '--verify', 'HEAD^{commit}').decode().strip() == self.revision, 'source HEAD changed')
        for name in list(self.pins):
            self.read(name)


def raw_module(sources, name, path):
    require(name not in sys.modules, 'preloaded launcher helper refused: ' + name)
    raw = sources.read(path)
    module = ModuleType(name)
    module.__file__, module.__package__, module.__cached__ = str(sources.root / path), '', None
    module.__som_raw_sha256__ = sha(raw)
    sys.modules[name] = module
    try:
        exec(compile(raw, module.__file__, 'exec', dont_inherit=True), module.__dict__)
    except BaseException:
        sys.modules.pop(name, None)
        raise
    return module


@contextmanager
def admitted_sources(repo, revision, reader=None, *, install=True, namespace='_som_current_gate_authority'):
    sources = Sources(repo, revision, reader)
    require(namespace in {'_som_current_gate_authority', '_som_owner_postclose_authority'}, 'fixed source authority namespace required')
    authority = raw_module(sources, namespace, AUTHORITY)
    lease = None
    try:
        admission = authority.verify(sources.read, 'native-qa-v1', sources.paths(), revision, revision)
        require(admission.get('source_authority_active') is True
                and all(admission.get(k) is False for k in ('product_acceptance_passed', 'compiler_acceptance_passed', 'whole_acceptance_complete')),
                'source authority cannot grant acceptance')
        if install:
            lease = authority.install_raw_finder(sources.read, sources.root, admission)
        yield sources, authority, admission
        if lease is not None:
            lease.verify_installed()
        require(authority.verify(sources.read, 'native-qa-v1', sources.paths(), revision, revision) == admission,
                'native source authority changed')
        sources.verify()
    finally:
        if lease is not None:
            lease.close()
        sys.modules.pop(namespace, None)


def roots_checked(values, saved, repo):
    require(type(values) is dict and set(values) == ROLES, 'all four trusted proof roots required')
    result = {name: regular_root(path) for name, path in values.items()}
    require(len(set(result.values())) == 4, 'proof roots must be distinct')
    for path in result.values():
        require(path != saved and saved not in path.parents and path not in saved.parents
                and path != repo and repo not in path.parents, 'proof input overlaps output or repository')
    return result


def provenance_root(assembly):
    require(assembly.name == 'som-unsealed-native-compile' and assembly.parent.name == '_temp',
            'actual assembly artifact member root required')
    return regular_root(assembly.parent / 'som-current-build-provenance')


def adapter_spec(data, gate, admission, sources):
    require(gate in GATES, 'unknown current candidate gate')
    value = data.get('gate_adapters', {}).get(gate)
    require(type(value) is dict and set(value) == {'path', 'sha256'}, 'actual explicit owner adapter path/hash required')
    path = member(value['path'])
    require(path.endswith('.py') and path in admission['admitted_repository_python_sources']
            and sha(sources.read(path)) == value['sha256'], 'owner adapter is not admitted exact raw source')
    return value


def owner_api(module):
    for name, args, keyword in [('run_gate', ('ctx', 'candidate_dir', 'services', 'out', 'gate'), ()),
                              ('verify_result', ('root', 'source_reader', 'helper_revision', 'candidate_revision', 'gate'), ('repo', 'proof_roots'))]:
        function = getattr(module, name, None)
        require(callable(function), 'actual owner adapter function missing: ' + name)
        parameters = inspect.signature(function).parameters
        require(tuple(parameters)[:len(args)] == args and all(k in parameters for k in keyword), 'owner hook API differs: ' + name)


def require_elf(path):
    require(read(path)[:4] == b'\x7fELF' and identity(path)['mode'] & 0o111,
            'Linux QA requires executable ELF bytes; a Mach-O binary cannot run here')


def profile_inputs(profile, root, *, current_revision=None):
    require(profile.get('version') == 'som.linux-qa-immutable-baseline.v1'
            and profile.get('role') == 'historical-immutable-baseline' and profile.get('host') == 'x86_64-unknown-linux-gnu'
            and re.fullmatch('[0-9a-f]{40}', profile.get('source_revision', ''))
            and re.fullmatch('[0-9a-f]{64}', profile.get('source_sha256', '')),
            'explicit immutable Linux baseline profile required')
    require(profile['source_revision'] != PREPARATION_SOURCE
            and (current_revision is None or profile['source_revision'] != current_revision),
            'historical baseline cannot relabel f59 preparation or the current candidate')
    root = regular_root(root)
    expected = {}
    def file(value):
        require(type(value) is dict and set(value) == {'path', 'sha256', 'size', 'mode'}, 'exact baseline file profile required')
        name = member(value['path'])
        wanted = {k: value[k] for k in ('sha256', 'size', 'mode')}
        require(type(wanted['sha256']) is str and re.fullmatch('[0-9a-f]{64}', wanted['sha256'])
                and type(wanted['size']) is int and wanted['size'] >= 0 and type(wanted['mode']) is int
                and wanted['mode'] in {0o644, 0o755}, 'baseline file identity differs')
        require(name not in expected and identity(root / name) == wanted, 'baseline profile file differs or overlaps')
        expected[name] = wanted
        return root / name
    def directory(value):
        require(type(value) is dict and set(value) == {'path', 'files'} and type(value['files']) is dict and value['files'],
                'exact baseline tree profile required')
        name = member(value['path'])
        observed = tree(root / name)
        require(observed == value['files'], 'baseline full tree differs from immutable profile')
        for relative, wanted in observed.items():
            key = name + '/' + member(relative)
            require(key not in expected, 'baseline profile tree overlaps another role')
            expected[key] = wanted
        return root / name
    binary = file(profile['binary']); require_elf(binary)
    assets = directory(profile['assets'])
    require(type(profile.get('tools')) is dict and set(profile['tools']) == TOOLS, 'all baseline native tool roles required')
    tools = {}
    for role, value in profile['tools'].items():
        tools[role] = directory(value) if role == 'SOM_TS_PRETTIER' else file(value)
        if role != 'SOM_TS_PRETTIER': require_elf(tools[role])
    require(tree(root) == expected, 'baseline root has extra or missing unprofiled bytes')
    return binary, assets, tools, expected


def owner_stamp(value, binary_inode):
    require(type(value) is dict and all(type(value.get(k)) is int and value[k] > 0
        for k in ('pid', 'pgrp', 'session', 'start_ticks', 'executable_inode'))
        and value['pid'] == value['pgrp'] == value['session']
        and value['executable_inode'] == binary_inode and value.get('state') != 'Z', 'one actual owned process identity required')
    listener = value.get('listener')
    require(type(listener) is dict and set(listener) == {'table', 'address', 'inode'}
            and listener['table'] == 'tcp' and listener['address'] == '0100007F'
            and type(listener['inode']) is str and listener['inode'].isdecimal() and int(listener['inode']) > 0,
            'exact owned loopback TCP socket required')
    return {key: value[key] for key in ('pid', 'pgrp', 'session', 'start_ticks', 'executable_inode', 'listener')}


def proc_snapshot(service, out):
    owner = service.ownership()
    proc = Path('/proc') / str(owner['pid'])
    (out / 'start.proc-stat.raw').write_bytes((proc / 'stat').read_bytes())
    for name in ('tcp', 'tcp6'):
        (out / ('start.' + name + '.raw')).write_bytes((Path('/proc/net') / name).read_bytes())
    links = {}
    for fd in sorted((proc / 'fd').iterdir()):
        try: links[fd.name] = os.readlink(fd)
        except FileNotFoundError: pass
    save(out / 'start.fd-links.json', links)
    executable = (proc / 'exe').stat()
    proof = {'owner': owner, 'executable': {'device': executable.st_dev, 'inode': executable.st_ino},
             'raw_paths': ['start.proc-stat.raw', 'start.tcp.raw', 'start.tcp6.raw', 'start.fd-links.json']}
    require(owner_stamp(service.ownership(), service.binary['inode']) == owner_stamp(owner, service.binary['inode']), 'service changed during proc snapshot')
    save(out / 'start.json', proof)
    verify_proc_snapshot(out, proof, service.port, service.binary['inode'])
    return proof


def verify_proc_snapshot(root, proof, port, inode):
    owner = owner_stamp(proof['owner'], inode)
    require(proof['raw_paths'] == ['start.proc-stat.raw', 'start.tcp.raw', 'start.tcp6.raw', 'start.fd-links.json'], 'raw process proof scope differs')
    raw = read(root / 'start.proc-stat.raw')
    fields = raw.rsplit(b')', 1)[1].split()
    require(int(raw.split(b' ', 1)[0]) == owner['pid'] and fields[0] != b'Z'
            and int(fields[2]) == int(fields[3]) == owner['pid'] and int(fields[19]) == owner['start_ticks'], 'raw proc owner/start differs')
    found = []
    for table in ('tcp', 'tcp6'):
        for line in read(root / ('start.' + table + '.raw')).splitlines()[1:]:
            values = line.split(); address, number = values[1].decode().split(':')
            if values[3] == b'0A' and int(number, 16) == port:
                found.append({'table': table, 'address': address, 'inode': values[9].decode()})
    links = parse(read(root / 'start.fd-links.json'))
    require(found == [owner['listener']] and 'socket:[' + owner['listener']['inode'] + ']' in links.values()
            and proof['executable']['inode'] == inode, 'raw socket/fd/executable evidence differs')
    return owner


class Services:
    """Own every service group. The adapter receives URLs, never foreign PIDs."""
    def __init__(self, ctx, driver, checker, proof, layout, restored, out):
        self.ctx, self.driver, self.checker, self.proof = ctx, driver, checker, proof
        self.layout, self.restored, self.out = layout, restored, Path(out)
        self.items, self.closed = {}, False
        self.out.mkdir()
        self.protected_before = {str(port): driver.listeners(port) for port in PROTECTED}
        require(all(not driver.listeners(port) for port in PORTS.values()), 'candidate or baseline QA port is occupied')
        save(self.out / 'protected-before.json', self.protected_before)

    def start_current(self):
        if 'current' in self.items:
            self.verify_all()
            return self.items['current']['handle']
        directory = Path(self.layout['candidate_dir'])
        return self._start('current', directory / 'som', directory / 'assets', self.restored['tools'],
            {'source_sha256': self.layout['source_sha256'], 'expected_raw': self.proof.read('health.response.raw')})

    def start_baseline(self, profile_path):
        require('baseline' not in self.items, 'only one explicit immutable baseline may be started')
        member(profile_path)
        require(profile_path.startswith(BASE) and profile_path.endswith('.json'), 'baseline profile must be in the sealed operational source map')
        profile_raw = self.ctx.sources.read(profile_path)
        profile = parse(profile_raw)
        selected = self.ctx.data.get('baseline_inputs', {}).get(profile_path)
        require(type(selected) is str and Path(selected).is_absolute(), 'explicit admitted baseline input root required')
        root = regular_root(selected)
        if profile.get('version') == 'som.linux-qa-recorded-before.v1':
            return self._start_recorded(profile_path, profile_raw, root)
        binary, assets, tools, expected = profile_inputs(profile, root, current_revision=self.ctx.candidate_revision)
        retained = self.out / 'baseline-inputs'; retained.mkdir()
        for name, wanted in expected.items(): copy_checked(root / name, retained / name, wanted)
        profile_inputs(profile, retained, current_revision=self.ctx.candidate_revision)
        (self.out / 'baseline-profile.raw').write_bytes(profile_raw)
        return self._start('baseline', binary, assets, tools,
            {'source_sha256': profile['source_sha256'], 'profile_path': profile_path, 'profile_sha256': sha(profile_raw),
             'profile': profile, 'input_root': str(root), 'retained_root': 'baseline-inputs'})

    def _start_recorded(self, profile_path, profile_raw, root):
        """Use the real qualified Python process. Old SOM files are data only."""
        require(not self.closed and 'baseline' not in self.items, 'baseline service already started or closed')
        oracle = self.ctx.module('recorded_before_oracle', ORACLE)
        table, metadata, proof, verify = oracle.prepare_data(self.ctx.sources.read, profile_path, root)
        require(proof['profile_sha256'] == sha(profile_raw) and proof['named_slots'] == 4572, 'recorded data source admission differs')
        native = self.ctx.runtime()
        python = Path(native['tools']['python']).resolve(strict=True)
        wanted_python = native['identity']['python_sha256']
        require_elf(python)
        require(identity(python)['sha256'] == wanted_python, 'actual qualified base Python differs')
        self.ctx.sources.read(FULL_DRIVER)
        self.ctx.sources.read(CONTEXT)
        directory = self.out / 'baseline'; directory.mkdir()
        service = self.driver.OwnedService(python, root, {}, PORTS['baseline'], directory)
        # Set the actual command before Popen. Never rewrite Popen.args or old receipts.
        requested, expected_actual = recorded_python_arguments(python, self.ctx.repo, self.ctx.helper_revision,
            self.ctx.candidate_revision, profile_path, root, directory / 'oracle', wanted_python)
        service.argv = requested
        frame = ('GITHUB_ACTIONS', 'RUNNER_ENVIRONMENT', 'RUNNER_OS', 'RUNNER_TEMP',
                 'GITHUB_SHA', 'GITHUB_RUN_ID', 'GITHUB_RUN_ATTEMPT', 'GITHUB_JOB')
        require(all(type(os.environ.get(name)) is str and os.environ[name] for name in frame)
                and os.environ['GITHUB_SHA'] == self.ctx.helper_revision == self.ctx.candidate_revision,
                'real GitHub source/job frame required for recorded service')
        service.environment = {'PATH': '/usr/bin:/bin', 'LANG': 'C.UTF-8', 'LC_ALL': 'C.UTF-8',
            'TMPDIR': str(directory / 'tmp'), 'SOM_FULL_PRODUCT_CONTEXT': str(self.ctx.path),
            **{name: os.environ[name] for name in frame}}
        require(read(self.ctx.path) == self.ctx.raw == read(self.out.parent / 'context.input.json')
                and os.environ.get('SOM_FULL_PRODUCT_CONTEXT') == str(self.ctx.path),
                'recorded child requires the exact active checked Context')
        initial = {'binary': self.driver.file_identity(python), 'assets': self.driver.tree_identity(root), 'tools': {}}
        extra = {'mode': 'recorded-same-request-before', 'profile_path': profile_path, 'profile_sha256': sha(profile_raw),
                 'profile': parse(profile_raw), 'input_root': str(root), 'oracle_out': 'oracle',
                 'data_proof_sha256': sha(canonical(proof) + b'\n')}
        record = {'role': 'baseline', 'port': PORTS['baseline'], 'argv': requested, 'requested_argv': requested,
                  'environment': service.environment,
                  'started_at': self.driver.utc(), 'initial_identities': initial, 'health_checks': [], 'extra': extra}
        item = {'service': service, 'record': record, 'binary': python, 'assets': root, 'tools': {},
                'handle': None, 'recorded_verify': verify, 'recorded_module': oracle}
        self.items['baseline'] = item
        (self.out / 'baseline-profile.raw').write_bytes(profile_raw)
        record['owned_start'] = service.start()
        require(list(service.process.args) == expected_actual
                and self.ctx.python_argv(requested, cwd=directory) == expected_actual,
                'actual native Python guard rewrite differs')
        startup = getattr(service.process, '__som_native_startup_receipt__', None)
        require(type(startup) is tuple and len(startup) == 2, 'actual native Python startup receipt missing')
        startup_path, startup_proof = startup
        startup_raw = read(startup_path)
        require(Path(startup_path) == self.ctx.work / 'python-child-startups' / f'{os.getpid()}-{service.process.pid}.json'
                and parse(startup_raw) == startup_proof, 'actual native startup source record differs')
        (directory / 'native-python-startup.json').write_bytes(startup_raw)
        extra['native_startup_source'] = str(startup_path)
        extra['native_startup_sha256'] = sha(startup_raw)
        extra['native_bootstrap_source_sha256'] = sha(self.ctx.sources.read(FULL_DRIVER))
        record['argv'] = list(service.process.args)
        record['proc_proof'] = proc_snapshot(service, directory)
        status, raw, owner = service.call('/healthz')
        (directory / 'health.response.raw').write_bytes(raw)
        body = parse(raw)
        compiler = {name: metadata[name] for name in ('binary_sha256', 'source_revision', 'health', 'health_role')}
        require(status == 200 and body['version'] == 'som.recorded-before-oracle-health.v1'
                and body['status'] == 'ok' and body['role'] == 'recorded-same-request-before'
                and body['actual_runtime'] == 'qualified-linux-python' and body['runtime_python_sha256'] == wanted_python
                and body['source_revision'] == body['candidate_revision'] == self.ctx.candidate_revision
                and body['profile_sha256'] == proof['profile_sha256'] and body['table_sha256'] == proof['table_sha256']
                and body['source_compiler'] == compiler and body['pid'] == owner['pid'], 'recorded data/Python/old compiler health roles differ')
        require(owner_stamp(owner, initial['binary']['inode']) == owner_stamp(record['owned_start'], initial['binary']['inode']),
                'recorded health has another executable/socket owner')
        record['health'] = {'status': status, 'raw_sha256': sha(raw), 'owned_socket': owner, 'body': body}
        runtime = {'version': 'som.recorded-before-oracle-runtime.v1', 'actual_runtime': 'qualified-linux-python',
            'pid': owner['pid'], 'argv': record['argv'], 'port': PORTS['baseline'], 'binary_sha256': wanted_python,
            'source_revision': self.ctx.helper_revision, 'candidate_revision': self.ctx.candidate_revision,
            'profile_path': profile_path, 'profile_sha256': sha(profile_raw), 'health': body,
            'source_compiler': compiler, 'accepted': False}
        save(directory / 'runtime.json', runtime)
        save(directory / 'started.json', record)
        baseline_dir = root / 'approved/restored/.local/m6-hosted/baseline'
        handle = {'url': f'http://127.0.0.1:{PORTS["baseline"]}', 'candidate_dir': str(baseline_dir), 'runtime': runtime}
        item['handle'] = handle
        return handle

    def _start(self, role, binary, assets, tools, extra):
        require(not self.closed and role in PORTS and role not in self.items, 'owned service role already started or closed')
        require_elf(binary)
        directory = self.out / role; directory.mkdir()
        service = self.driver.OwnedService(binary, assets, tools, PORTS[role], directory)
        initial = {'binary': self.driver.file_identity(binary), 'assets': self.driver.tree_identity(assets),
                   'tools': {key: self.driver.tree_identity(path) if key == 'SOM_TS_PRETTIER' else self.driver.file_identity(path)
                             for key, path in tools.items()}}
        record = {'role': role, 'port': PORTS[role], 'argv': service.argv, 'environment': service.environment,
                  'started_at': self.driver.utc(), 'initial_identities': initial, 'health_checks': [],
                  'extra': {k: v for k, v in extra.items() if k != 'expected_raw'}}
        item = {'service': service, 'record': record, 'binary': Path(binary), 'assets': Path(assets), 'tools': tools, 'handle': None}
        self.items[role] = item  # A failed start is still owned and must be stopped.
        record['owned_start'] = service.start()
        record['proc_proof'] = proc_snapshot(service, directory)
        status, raw, owner = service.call('/healthz')
        (directory / 'health.response.raw').write_bytes(raw)
        record['health'] = {'status': status, 'raw_sha256': sha(raw), 'owned_socket': owner, 'body': parse(raw)}
        if role == 'current':
            require(raw == extra['expected_raw'] and status == 200, 'current health differs from the actual typed candidate proof')
            value = record['health']['body']
            require(value['status'] == 'ok' and value['model_loaded'] is False and value['blocks_loaded'] is True
                    and value['backend_sha256'] == self.checker.after['python_backend']
                    and value['asset_manifest_sha256'] == self.driver.asset_manifest_canonical_sha(assets)
                    and value['formatter_sha256'] == initial['tools']['SOM_RUFF']['sha256'], 'current health source/factory/assets/tools differ')
        else:
            wanted = extra['profile']['health']
            require(status == 200 and sha(raw) == wanted['raw_sha256'] and parse(raw) == wanted['body'], 'baseline health differs from its immutable source profile')
        owner_stamp(owner, initial['binary']['inode'])
        require(owner_stamp(owner, initial['binary']['inode']) == owner_stamp(record['owned_start'], initial['binary']['inode']), 'health response has another owner')
        runtime = {'version': 'som.rust-output-candidate-runtime.v1', 'pid': owner['pid'], 'argv': service.argv,
            'port': PORTS[role], 'binary_sha256': initial['binary']['sha256'], 'source_sha256': extra['source_sha256'],
            'started_at': record['started_at'], 'health': record['health']['body'], 'som_ts_prettier': str(tools['SOM_TS_PRETTIER']),
            'prettier_tree_sha256': tree_digest(tree(tools['SOM_TS_PRETTIER'])), 'accepted': False}
        if role == 'current':
            require(runtime['prettier_tree_sha256'] == self.layout['prettier_tree_sha256'], 'same-build Prettier tree hash differs')
        if role == 'current':
            save(Path(self.layout['candidate_dir']) / 'runtime.json', runtime)
        save(directory / 'runtime.json', runtime)
        save(directory / 'started.json', record)
        handle = {'url': f'http://127.0.0.1:{PORTS[role]}',
                  'candidate_dir': self.layout['candidate_dir'] if role == 'current' else extra['input_root'], 'runtime': runtime}
        item['handle'] = handle
        return handle

    def verify_all(self):
        require(not self.closed, 'service manager is closed')
        for item in self.items.values():
            self.verify_item(item)
            role, record, service = item['record']['role'], item['record'], item['service']
            status, raw, owner = service.call('/healthz')
            require(status == 200 and raw == read(self.out / role / 'health.response.raw')
                    and owner_stamp(owner, service.binary['inode']) == owner_stamp(record['owned_start'], service.binary['inode']),
                    'service source/health/socket changed after the actual gate')
            directory = self.out / role / 'health-checks' / f"{len(record['health_checks']):04d}"
            directory.mkdir(parents=True)
            (directory / 'response.raw').write_bytes(raw)
            check = {'index': len(record['health_checks']), 'status': status, 'raw_sha256': sha(raw), 'owned_socket': owner}
            save(directory / 'record.json', check)
            record['health_checks'].append(check)
        require({str(port): self.driver.listeners(port) for port in PROTECTED} == self.protected_before, 'protected default listeners changed')

    def verify_item(self, item):
        record, service = item['record'], item['service']
        require(owner_stamp(service.ownership(), service.binary['inode']) == owner_stamp(record['owned_start'], service.binary['inode']), 'owned service identity changed')
        current = {'binary': self.driver.file_identity(item['binary']), 'assets': self.driver.tree_identity(item['assets']),
            'tools': {key: self.driver.tree_identity(path) if key == 'SOM_TS_PRETTIER' else self.driver.file_identity(path)
                      for key, path in item['tools'].items()}}
        require(current == record['initial_identities'], 'running source binary/assets/tools changed')
        if record['role'] == 'baseline' and record['extra'].get('mode') == 'recorded-same-request-before':
            item['recorded_verify']()
        elif record['role'] == 'baseline':
            profile_inputs(record['extra']['profile'], record['extra']['input_root'], current_revision=self.ctx.candidate_revision)

    def close_all(self):
        if self.closed: return
        errors = []
        def failed(role, stage, error):
            value = {'role': role, 'stage': stage, 'error': type(error).__name__, 'reason': str(error)}
            errors.append(value)
            if role in self.items:
                self.items[role]['record'].setdefault('cleanup_failures', []).append(value)
        def attempt(role, stage, function, fallback=None):
            try:
                return function()
            except BaseException as error:
                failed(role, stage, error)
                return fallback
        for role, item in reversed(list(self.items.items())):
            service, record = item['service'], item['record']
            process = attempt(role, 'process-reference', lambda: service.process)
            def verify_live():
                if process is not None and process.poll() is None:
                    self.verify_item(item)
            attempt(role, 'pre-close-identity', verify_live)
            cleanup = attempt(role, 'close', service.close)
            if type(cleanup) is not dict:
                if cleanup is not None:
                    failed(role, 'close-result', ValueError('owned close returned no cleanup object'))
                cleanup = {'owned_pid': getattr(process, 'pid', None), 'cleanup_passed': False,
                           'exit_code': None, 'remaining_owned_session_members': None,
                           'signal_target': 'owned new process group only', 'signals': []}
            cleanup['wait_completed'] = False
            if process is not None:
                def actual_wait():
                    code = process.wait(timeout=20)
                    require(type(code) is int, 'owned wait returned no terminal exit')
                    cleanup['wait_exit_code'] = code
                    cleanup['wait_completed'] = True
                    if cleanup.get('exit_code') is None:
                        cleanup['exit_code'] = code
                attempt(role, 'wait', actual_wait)
            record['cleanup'] = cleanup
            if role == 'baseline' and record.get('extra', {}).get('mode') == 'recorded-same-request-before':
                def recorded_postcheck():
                    require(process is not None and cleanup.get('wait_completed') is True
                            and cleanup.get('exit_code') == cleanup.get('wait_exit_code') == 0,
                            'recorded Python must finish its real native postchecks with exit zero')
                    source = self.ctx.work / 'python-startup-postchecks' / f'{process.pid}.json'
                    raw = read(source)
                    (self.out / role / 'native-python-postcheck.json').write_bytes(raw)
                    record['native_python_postcheck'] = {'source': str(source), 'sha256': sha(raw)}
                    verify_recorded_python_startup(self.out / role, record, self.ctx.sources, self.ctx.data)
                attempt(role, 'recorded-native-postcheck', recorded_postcheck)
            record['port_absent_after'] = attempt(role, 'listener-after', lambda: not self.driver.listeners(PORTS[role]))
            def final_inputs():
                return {'binary': self.driver.file_identity(item['binary']), 'assets': self.driver.tree_identity(item['assets']),
                    'tools': {key: self.driver.tree_identity(path) if key == 'SOM_TS_PRETTIER' else self.driver.file_identity(path)
                              for key, path in item['tools'].items()}}
            record['final_identities'] = attempt(role, 'final-input-identities', final_inputs)
            def verify_final():
                require(record['final_identities'] == record['initial_identities'], 'service input bytes changed')
                require(cleanup.get('cleanup_passed') is True and cleanup.get('wait_completed') is True
                        and cleanup.get('wait_exit_code') == cleanup.get('exit_code') and type(cleanup.get('exit_code')) is int
                        and cleanup.get('remaining_owned_session_members') == [] and record['port_absent_after'], 'owned wait/group/port cleanup incomplete')
            attempt(role, 'final-cleanup-check', verify_final)
            attempt(role, 'cleanup-ledger', lambda: save(self.out / role / 'cleanup.json', cleanup))
            record['server_log_sha256'] = attempt(role, 'server-log', lambda: sha(read(self.out / role / 'server.log')))
            attempt(role, 'role-ledger', lambda: save(self.out / role / 'result.json', record))
        after = {str(port): attempt('protected', 'listener-' + str(port), lambda port=port: self.driver.listeners(port)) for port in PROTECTED}
        attempt('protected', 'ledger', lambda: save(self.out / 'protected-after.json', after))
        if after != self.protected_before:
            failed('protected', 'identity-after', ValueError('protected default listeners changed'))
        self.closed = True
        self.receipt = {'version': 'som.current-candidate-services.v1', 'services': {k: v['record'] for k, v in self.items.items()},
                        'protected_before': self.protected_before, 'protected_after': after, 'errors': errors}
        attempt('manager', 'manifest-ledger', lambda: save(self.out / 'manifest.json', self.receipt))
        require(not errors, 'owned service verification/cleanup failed: ' + repr(errors))


def failure_record(error):
    return {'error': type(error).__name__, 'reason': str(error)}


@contextmanager
def owned_cleanup(services, cancellation, report):
    """Keep the first owner/cancel failure while every owned role is attempted."""
    primary = None
    try:
        yield
    except BaseException as error:
        primary = error
        report.setdefault('primary_failure', failure_record(error))
        raise
    finally:
        cancellation['cleanup'] = True
        try:
            services.close_all()
        except BaseException as error:
            report.setdefault('cleanup_failures', []).append(failure_record(error))
            receipt = getattr(services, 'receipt', None)
            if receipt is not None:
                report['owned_cleanup_receipt'] = receipt
            if primary is None:
                report.setdefault('primary_failure', failure_record(error))
                raise
        finally:
            # The source-bound handler can record its first signal during close/wait.
            report['interrupted_signal'] = cancellation.get('signal')


def layout_arguments(sources, roots, restored, catalog):
    return {'repo': sources.root, 'build_provenance_root': provenance_root(roots['assembly_root']),
            'assembly_root': roots['assembly_root'], 'carrier_root': roots['carrier_root'],
            'restored_carrier_root': restored, 'artifact_inputs': catalog}


def validate_owner_result(value, gate, revision):
    require(type(value) is dict and type(value.get('version')) is str and value.get('gate') == gate
            and value.get('helper_revision') == value.get('candidate_revision') == revision
            and value.get('product_acceptance_passed') is False
            and all(value.get(k) is True for k in ('complete', 'all_required_passed', 'owned_waits_complete', 'owned_cleanup_complete', 'raw_logs_complete')),
            'owner gate did not provide complete independently verified evidence')


def recorded_python_arguments(python, repo, helper, candidate, profile, inputs, output, python_sha256):
    """Request the admitted script; the existing native guard owns the rewrite."""
    tail = [str(Path(repo) / ORACLE), '--repo', str(repo), '--helper-revision', helper,
            '--candidate-revision', candidate, '--profile', profile, '--inputs-root', str(inputs),
            '--python-sha256', python_sha256, '--port', str(PORTS['baseline']), '--out', str(output)]
    requested = [str(python), '-I', '-S', '-B', *tail]
    actual = [str(python), '-I', '-S', '-B', str(Path(repo) / FULL_DRIVER), '--native-python-bootstrap', '--', *tail]
    return requested, actual


def validate_recorded_python_evidence(startup, post, *, requested, actual, pid, cwd, revision, source_reader, exit_code):
    """Check the existing guard's real startup and terminal bootstrap protocol."""
    require(type(exit_code) is int and exit_code == 0, 'recorded Python native bootstrap must exit zero')
    require(startup == {'version': 'som.native-python-child-startup.v1', 'pid': pid,
            'actual_argv': actual, 'original_requested_argv': requested, 'cwd': str(cwd),
            'helper_revision': revision, 'candidate_revision': revision, 'product_acceptance_passed': False},
            'recorded Python actual guarded startup differs')
    require(post.get('version') == 'som.native-python-child-postcheck.v1'
            and post.get('helper_revision') == post.get('candidate_revision') == revision
            and post.get('actual_argv') == actual and post.get('original_python_arguments') == requested[4:]
            and post.get('entry_executed') is True and post.get('postchecks_passed') is True
            and type(post.get('exit_code')) is int and post['exit_code'] == 0
            and post.get('product_acceptance_passed') is False
            and 'error' not in post and 'postcheck_error' not in post,
            'recorded Python native entry or final postcheck failed')
    require(post.get('payload') == {'version': 'som.native-python-startup-payload.v1',
            'role': 'qualified-base-no-site', 'python': actual[0], 'site': None,
            'pth_executed': False, 'product_acceptance_passed': False}, 'recorded Python payload differs')
    pins = post.get('source_sha256')
    require(type(pins) is dict and {CONTEXT, ORACLE} <= set(pins)
            and all(sha(source_reader(name)) == digest for name, digest in pins.items()),
            'recorded Python terminal source checks differ')


def verify_recorded_python_startup(directory, record, sources, data):
    """Read retained and actual same-job native records. Never synthesize receipts."""
    context_raw = read(directory.parent.parent / 'context.input.json')
    require(parse(context_raw) == data and data['repo'] == str(sources.root)
            and data['helper_revision'] == data['candidate_revision'] == sources.revision
            and data['qa_mode'] == 'native-qa-v1'
            and read(record['environment']['SOM_FULL_PRODUCT_CONTEXT']) == context_raw,
            'recorded native child Context differs from the actual checked Context')
    python = Path(data['runtime_root']) / 'install/python/bin/python3.12'
    requested, actual = recorded_python_arguments(python, sources.root, sources.revision, sources.revision,
        record['extra']['profile_path'], record['extra']['input_root'], directory / 'oracle', identity(python)['sha256'])
    require(record['requested_argv'] == requested and record['argv'] == actual
            and record['extra']['native_bootstrap_source_sha256'] == sha(sources.read(FULL_DRIVER)),
            'recorded actual bootstrap source or command differs')
    pid = record['owned_start']['pid']
    proc = read(directory / 'start.proc-stat.raw').rsplit(b')', 1)[1].split()
    startup_source = Path(data['work_root']) / 'python-child-startups' / f'{int(proc[1])}-{pid}.json'
    startup_raw = read(directory / 'native-python-startup.json')
    require(record['extra']['native_startup_source'] == str(startup_source)
            and read(startup_source) == startup_raw and sha(startup_raw) == record['extra']['native_startup_sha256'],
            'actual native startup receipt was not retained unchanged')
    post_source = Path(data['work_root']) / 'python-startup-postchecks' / f'{pid}.json'
    post_raw = read(directory / 'native-python-postcheck.json')
    require(record['native_python_postcheck'] == {'source': str(post_source), 'sha256': sha(post_raw)}
            and read(post_source) == post_raw and record['cleanup']['wait_exit_code'] == record['cleanup']['exit_code'],
            'actual terminal native Python postcheck was not retained unchanged')
    validate_recorded_python_evidence(parse(startup_raw), parse(post_raw), requested=requested, actual=actual,
        pid=pid, cwd=directory, revision=sources.revision, source_reader=sources.read, exit_code=record['cleanup']['exit_code'])


def qualified_recorded_python(sources, data):
    """Rederive the checked runtime tree without reimporting fixed bootstrap names."""
    base = BASE + 'product_runtime/'
    policy = parse(sources.read(BASE + 'product_python/wheel-policy.json'))
    qualification_raw = sources.read(base + 'runtime-bf3-qualification.json')
    audit_raw = sources.read(base + 'runtime-bf3-byte-audit.json')
    qualification, audit = parse(qualification_raw), parse(audit_raw)
    authority = policy['native_authority']
    require(sha(audit_raw) == authority['controller_byte_audit_sha256'] == qualification['audit']['sha256']
            and qualification['audit']['path'] == base + 'runtime-bf3-byte-audit.json'
            and qualification['version'] == 'som.product-runtime-artifact-qualification.v1'
            and qualification['qualified_for'] == 'later_GitHub_tests_only'
            and qualification['product_runtime_ready'] is False and qualification['product_acceptance_passed'] is False
            and audit['qualification_evidence_verified'] is True and audit['product_acceptance_passed'] is False
            and audit['tested_sha'] == qualification['source_revision']
            and audit['artifact_id'] == qualification['artifact_id']
            and audit['artifact_github_digest'] == qualification['artifact_sha256'], 'qualified runtime source/controller binding differs')
    root = regular_root(data['runtime_root'])
    require(str(root) == qualification['install_prefix'], 'recorded service runtime prefix differs')
    inventory_raw = read(root / 'qualification-evidence/installed-files.json')
    require(sha(inventory_raw) == authority['qualified_inventory_sha256']
            == qualification['transport_files']['installed-files.json'] == audit['installed_inventory_sha256'],
            'complete qualified runtime inventory differs')
    inventory = parse(inventory_raw)
    expected_dirs = {parent.as_posix() for name in inventory for parent in Path(member(name)).parents if parent.as_posix() != '.'}
    files, directories, aliases, regular = set(), {'install'}, {}, 0
    for path in (root / 'install').rglob('*'):
        name = path.relative_to(root).as_posix(); info = path.lstat()
        if stat.S_ISDIR(info.st_mode):
            directories.add(name)
            continue
        files.add(name)
        require(name in inventory, 'unexpected qualified runtime member')
        row = inventory[name]
        if row['kind'] == 'regular_file':
            require(set(row) == {'kind', 'sha256', 'executable_bit'} and type(row['executable_bit']) is bool
                    and stat.S_ISREG(info.st_mode) and not path.is_symlink()
                    and identity(path) == {'sha256': row['sha256'], 'size': info.st_size,
                                           'mode': 0o755 if row['executable_bit'] else 0o644}, 'qualified runtime byte/mode changed')
            regular += 1
        else:
            target = row.get('target')
            require(set(row) == {'kind', 'target', 'sha256', 'qualification_probe_executed', 'runtime_admitted'}
                    and row['kind'] == 'alias_not_admitted' and row['qualification_probe_executed'] is False
                    and row['runtime_admitted'] is False and type(target) is str and '/' not in target and target not in ('', '.', '..')
                    and stat.S_ISLNK(info.st_mode) and os.readlink(path) == target and sha(target.encode()) == row['sha256']
                    and qualification['aliases'].get(name) == target
                    and inventory[str(Path(name).parent / target)]['kind'] == 'regular_file', 'qualified internal alias changed or became executable authority')
            aliases[name] = target
    require(files == set(inventory) and directories == expected_dirs
            and regular == qualification['counts']['regular_installed_files'] and aliases == qualification['aliases'],
            'whole qualified runtime tree/count differs')
    receipt = parse(read(root / 'receipt.json'))
    require(receipt['version'] == 'som.product-runtime-reuse.v1'
            and receipt['status'] == 'audited_runtime_reinstalled_and_rechecked_for_hosted_tests'
            and receipt['tested_sha'] == sources.revision and receipt['qualified_for'] == 'later_GitHub_tests_only'
            and receipt['installed_tree_verified_before'] is True and receipt['installed_tree_verified_after'] is True
            and receipt['product_runtime_ready'] is False and receipt['product_acceptance_passed'] is False
            and receipt['qualification']['manifest_sha256'] == sha(qualification_raw)
            and receipt['qualification']['audit_sha256'] == sha(audit_raw)
            and receipt['qualification']['installed_inventory_sha256'] == sha(inventory_raw), 'runtime reuse source/evidence differs')
    admitted = receipt['admitted_executables']
    require(set(admitted) == set(qualification['admitted_paths']) and len(admitted) == 41, 'complete native runtime tool scope required')
    for name, item in admitted.items():
        require(inventory[name]['kind'] == 'regular_file' and inventory[name]['executable_bit'] is True
                and item['status'] == 'hash_and_version_rechecked' and item['version_exit_code'] == 0
                and item['qualified_for'] == 'later_GitHub_tests_only' and item['authority'] == 'controller_audited_saved_artifact'
                and item['sha256'] == inventory[name]['sha256'], 'runtime executable admission differs')
    python = root / 'install/python/bin/python3.12'
    require_elf(python)
    require(identity(python)['sha256'] == policy['native_sha256']['python']
            == admitted['install/python/bin/python3.12']['sha256']
            and admitted['install/python/bin/python3.12']['version_stdout'].strip() == 'Python 3.12.11', 'actual qualified Python differs')
    return python


def verify_service_cleanup(directory, record, stamp, purpose):
    cleanup = parse(read(directory / 'cleanup.json'))
    require(cleanup == record['cleanup'] and cleanup.get('owned_pid') == stamp['pid']
            and cleanup.get('cleanup_passed') is True and cleanup.get('wait_completed') is True
            and type(cleanup.get('exit_code')) is int and cleanup.get('wait_exit_code') == cleanup['exit_code']
            and cleanup.get('remaining_owned_session_members') == [] and cleanup.get('signal_target') == 'owned new process group only'
            and cleanup.get('signals') in ([], ['SIGTERM'], ['SIGTERM', 'SIGKILL']) and record['port_absent_after'] is True
            and sha(read(directory / 'server.log')) == record['server_log_sha256'], 'actual owned wait/log/group cleanup differs')
    events = [parse(line) for line in read(directory / 'server.log').splitlines() if line.startswith(b'{')]
    require(events and events[0] == {'stage': 'listening', 'address': f'127.0.0.1:{record["port"]}', 'purpose': purpose},
            'actual owning server listening event differs')


def verify_recorded_service(root, directory, record, stamp, sources):
    profile_path = member(record['extra']['profile_path'])
    require(profile_path.startswith(BASE) and profile_path.endswith('.json'), 'recorded profile source scope differs')
    profile_raw = sources.read(profile_path)
    require(read(root / 'baseline-profile.raw') == profile_raw and sha(profile_raw) == record['extra']['profile_sha256']
            and parse(profile_raw) == record['extra']['profile'], 'recorded profile differs from admitted raw Git')
    data = parse(read(root.parent / 'context.input.json'))
    selected = data.get('baseline_inputs', {}).get(profile_path)
    require(type(selected) is str and record['extra']['input_root'] == selected
            and record['extra']['oracle_out'] == 'oracle', 'actual recorded input source root differs')
    inputs = regular_root(selected)
    python = qualified_recorded_python(sources, data)
    runtime = parse(read(directory / 'runtime.json'))
    requested, argv = recorded_python_arguments(python, sources.root, sources.revision, sources.revision,
        profile_path, inputs, directory / 'oracle', identity(python)['sha256'])
    require(record['argv'] == runtime['argv'] == argv and record['port'] == runtime['port'] == PORTS['baseline']
            and runtime['pid'] == stamp['pid'] and runtime['binary_sha256'] == identity(python)['sha256']
            and record['initial_identities'] == record['final_identities']
            and portable(record['initial_identities']) == {'binary': identity(python), 'assets': tree(inputs), 'tools': {}},
            'actual recorded runtime executable/data identities differ')
    frame = {'GITHUB_ACTIONS', 'RUNNER_ENVIRONMENT', 'RUNNER_OS', 'RUNNER_TEMP',
             'GITHUB_SHA', 'GITHUB_RUN_ID', 'GITHUB_RUN_ATTEMPT', 'GITHUB_JOB'}
    environment = record['environment']
    require(set(environment) == {'PATH', 'LANG', 'LC_ALL', 'TMPDIR', 'SOM_FULL_PRODUCT_CONTEXT', *frame}
            and environment['PATH'] == '/usr/bin:/bin' and environment['LANG'] == environment['LC_ALL'] == 'C.UTF-8'
            and environment['TMPDIR'] == str(directory / 'tmp') and environment['GITHUB_ACTIONS'] == 'true'
            and environment['RUNNER_ENVIRONMENT'] == 'github-hosted' and environment['RUNNER_OS'] == 'Linux'
            and environment['GITHUB_SHA'] == sources.revision
            and all(type(environment[name]) is str and environment[name] for name in frame), 'recorded Python environment/frame differs')
    require(record['requested_argv'] == requested, 'recorded original script request differs')
    verify_recorded_python_startup(directory, record, sources, data)
    oracle = raw_module(sources, '_som_recorded_service_verify', ORACLE)
    try:
        verified = oracle.verify_recorded_run(directory / 'oracle', sources.read, sources.revision, sources.revision,
            profile_path, inputs, repo=sources.root, runtime=runtime)
        require(verified['profile_sha256'] == sha(profile_raw)
                and sha(read(directory / 'oracle/data-proof.json')) == record['extra']['data_proof_sha256'], 'recorded complete data proof differs')
    finally:
        sys.modules.pop('_som_recorded_service_verify', None)
    verify_service_cleanup(directory, record, stamp, 'recorded-same-request-before')


def verify_services(root, value, trusted, layout, sources):
    require(value.get('version') == 'som.current-candidate-services.v1' and value.get('errors') == []
            and value['protected_before'] == value['protected_after']
            and parse(read(root / 'protected-before.json')) == value['protected_before']
            and parse(read(root / 'protected-after.json')) == value['protected_after']
            and set(value['services']) in ({'current'}, {'current', 'baseline'}), 'complete service/protected listener scope differs')
    for role, record in value['services'].items():
        directory = root / role
        require(parse(read(directory / 'result.json')) == record and record['role'] == role and record['port'] == PORTS[role]
                and record['initial_identities'] == record['final_identities'], 'saved service source/identity record differs')
        inode = record['initial_identities']['binary']['inode']
        stamp = verify_proc_snapshot(directory, record['proc_proof'], PORTS[role], inode)
        require(parse(read(directory / 'start.json')) == record['proc_proof']
                and owner_stamp(record['owned_start'], inode) == stamp
                and owner_stamp(record['health']['owned_socket'], inode) == stamp
                and record['proc_proof']['executable']['device'] == record['initial_identities']['binary']['device'],
                'start/health raw socket/executable records differ')
        raw = read(directory / 'health.response.raw')
        require(record['health']['status'] == 200 and sha(raw) == record['health']['raw_sha256']
                and parse(raw) == record['health']['body'], 'raw health record differs')
        after_start_fields = {'cleanup', 'final_identities', 'port_absent_after', 'server_log_sha256'}
        if role == 'baseline' and record['extra'].get('mode') == 'recorded-same-request-before':
            after_start_fields.add('native_python_postcheck')
        initial_record = {key: item for key, item in record.items() if key not in after_start_fields}
        initial_record['health_checks'] = []
        require(parse(read(directory / 'started.json')) == initial_record and record['health_checks'], 'actual startup or post-gate health evidence missing')
        for index, check in enumerate(record['health_checks']):
            check_dir = directory / 'health-checks' / f'{index:04d}'
            require(check['index'] == index and check['status'] == 200 and check['raw_sha256'] == sha(raw)
                    and read(check_dir / 'response.raw') == raw and parse(read(check_dir / 'record.json')) == check
                    and owner_stamp(check['owned_socket'], inode) == stamp, 'post-gate raw health/owner evidence differs')
        require(set(tree(directory / 'health-checks')) == {f'{index:04d}/{name}' for index in range(len(record['health_checks']))
                    for name in ('response.raw', 'record.json')}, 'post-gate health raw member universe differs')
        if role == 'baseline' and record['extra'].get('mode') == 'recorded-same-request-before':
            verify_recorded_service(root, directory, record, stamp, sources)
            continue
        if role == 'current':
            require(raw == trusted.proof.read('health.response.raw')
                    and portable(record['initial_identities']) == trusted.carrier['identity']
                    and record['initial_identities']['binary']['sha256'] == layout['binary_sha256'], 'current service differs from fresh typed/layout proof')
            path_facts = parse(read(Path(layout['candidate_dir']) / 'layout.json'))
            launch_binary = str(Path(path_facts['original_candidate_dir']) / 'som')
            launch_assets = str(Path(path_facts['original_candidate_dir']) / 'assets')
            tool_root = Path(path_facts['original_restored_carrier_root'])
            tool_paths = {key: str(tool_root / name) for key, name in
                {'SOM_RUFF': 'native/bin/ruff', 'SOM_RUSTFMT': 'compiler-rustfmt',
                 'SOM_TS_NODE': 'native/bin/node', 'SOM_TS_PRETTIER': 'native/prettier'}.items()}
        else:
            profile_path = member(record['extra']['profile_path'])
            require(profile_path.startswith(BASE) and profile_path.endswith('.json'), 'baseline profile source scope differs')
            profile_raw = sources.read(profile_path)
            require(read(root / 'baseline-profile.raw') == profile_raw and sha(profile_raw) == record['extra']['profile_sha256']
                    and parse(profile_raw) == record['extra']['profile'], 'baseline profile differs from admitted current raw Git')
            profile = parse(profile_raw)
            profile_inputs(profile, root / 'baseline-inputs', current_revision=sources.revision)
            require(sha(raw) == profile['health']['raw_sha256'] and parse(raw) == profile['health']['body'], 'baseline raw health differs from immutable profile')
            expected_identity = {'binary': {k: profile['binary'][k] for k in ('sha256', 'size', 'mode')},
                'assets': profile['assets']['files'], 'tools': {key: value['files'] if key == 'SOM_TS_PRETTIER'
                    else {k: value[k] for k in ('sha256', 'size', 'mode')} for key, value in profile['tools'].items()}}
            require(portable(record['initial_identities']) == expected_identity, 'baseline running bytes differ from retained immutable profile')
            launch_root = Path(record['extra']['input_root'])
            require(launch_root.is_absolute() and '..' not in launch_root.parts, 'baseline launch root is unsafe')
            launch_binary, launch_assets = str(launch_root / profile['binary']['path']), str(launch_root / profile['assets']['path'])
            tool_paths = {key: str(launch_root / value['path']) for key, value in profile['tools'].items()}
        runtime = parse(read(directory / 'runtime.json'))
        require(runtime['accepted'] is False and runtime['pid'] == stamp['pid'] and runtime['argv'] == record['argv']
                and runtime['port'] == PORTS[role] and runtime['health'] == parse(raw)
                and runtime['binary_sha256'] == record['initial_identities']['binary']['sha256'], 'actual runtime receipt differs')
        require(record['argv'] == [launch_binary, 'serve', '--assets', launch_assets, '--port', str(PORTS[role])],
                'service launch is not the exact owned candidate command')
        environment = record['environment']
        require(set(environment) == {'PATH', 'LANG', 'LC_ALL', 'RUST_BACKTRACE', 'TMPDIR', *TOOLS}
                and environment['PATH'] == '/usr/bin:/bin' and environment['LANG'] == environment['LC_ALL'] == 'C.UTF-8'
                and environment['RUST_BACKTRACE'] == '1' and all(environment[key] == value for key, value in tool_paths.items())
                and Path(environment['TMPDIR']).name == 'tmp' and Path(environment['TMPDIR']).parent.name == role,
                'service environment did not use the checked native tools')
        wanted_source = layout['source_sha256'] if role == 'current' else profile['source_sha256']
        require(runtime['source_sha256'] == wanted_source
                and runtime['prettier_tree_sha256'] == tree_digest(portable(record['initial_identities'])['tools']['SOM_TS_PRETTIER'])
                and runtime['som_ts_prettier'] == tool_paths['SOM_TS_PRETTIER'] and runtime['started_at'] == record['started_at'],
                'runtime source/tool/start metadata differs')
        cleanup = parse(read(directory / 'cleanup.json'))
        require(cleanup == record['cleanup'] and cleanup.get('owned_pid') == stamp['pid']
                and cleanup.get('cleanup_passed') is True and cleanup.get('wait_completed') is True
                and type(cleanup.get('exit_code')) is int and cleanup.get('wait_exit_code') == cleanup['exit_code']
                and cleanup.get('remaining_owned_session_members') == [] and cleanup.get('signal_target') == 'owned new process group only'
                and cleanup.get('signals') in ([], ['SIGTERM'], ['SIGTERM', 'SIGKILL']) and record['port_absent_after'] is True
                and sha(read(directory / 'server.log')) == record['server_log_sha256'], 'actual owned wait/log/group cleanup differs')
        events = [parse(line) for line in read(directory / 'server.log').splitlines() if line.startswith(b'{')]
        require(events and events[0] == {'stage': 'listening', 'address': f'127.0.0.1:{PORTS[role]}',
                    'purpose': 'som.rust-compiler.v1'}, 'actual owning server listening event differs')
    require(read(Path(layout['candidate_dir']) / 'runtime.json') == read(root / 'current/runtime.json'), 'candidate runtime copy differs')


def verify_owner_postclose(root, source_reader, helper_revision, candidate_revision, gate, *, repo, proof_roots):
    """Rederive shared source/layout/process cleanup; owner gate checks remain separate."""
    require(os.environ.get('GITHUB_ACTIONS') == 'true' and sys.platform == 'linux', 'GitHub Linux post-close verifier only')
    root = regular_root(root)
    require(root.name == 'owner' and helper_revision == candidate_revision and gate in GATES,
            'exact shared owner source and directory required')
    shared = root.parent
    capture_raw = read(root / 'result.json')
    capture = parse(capture_raw)
    require(capture.get('gate') == gate and capture.get('helper_revision') == capture.get('candidate_revision') == candidate_revision
            and capture.get('product_acceptance_passed') is False, 'owner capture identity differs')
    names = ('_som_owner_postclose_layout', '_som_owner_postclose_semantic')
    with admitted_sources(repo, helper_revision, source_reader, namespace='_som_owner_postclose_authority') as (sources, authority, admission):
        roots = roots_checked(proof_roots, shared, sources.root)
        context_raw = read(shared / 'context.input.json')
        data = parse(context_raw)
        require(data.get('repo') == str(sources.root) and data.get('helper_revision') == data.get('candidate_revision') == candidate_revision
                and data.get('qa_mode') == 'native-qa-v1' and data.get('native_source_authority') == admission
                and all(data[name] == str(path) for name, path in roots.items()), 'shared Context/proof source differs')
        spec = adapter_spec(data, gate, admission, sources)
        catalog_raw = read(shared / 'artifact-inputs.json')
        catalog = parse(catalog_raw)
        try:
            layout_module = raw_module(sources, names[0], LAYOUT)
            semantic = raw_module(sources, names[1], SEMANTIC)
            layout = layout_module.verify_candidate_layout(shared / 'candidate', sources.read, helper_revision, candidate_revision,
                **layout_arguments(sources, roots, shared / 'restored-candidate', catalog))
            services_raw = read(shared / 'services/manifest.json')
            with semantic.trusted_proof_bundle(sources.root, sources.read, helper_revision, candidate_revision, roots, shared, admission) as trusted:
                verify_services(shared / 'services', parse(services_raw), trusted, layout, sources)
            proof = {'version': 'som.owner-shared-postclose-proof.v1', 'gate': gate,
                'helper_revision': helper_revision, 'candidate_revision': candidate_revision,
                'capture_result_sha256': sha(capture_raw), 'context_sha256': sha(context_raw),
                'artifact_inputs_sha256': sha(catalog_raw), 'services_manifest_sha256': sha(services_raw),
                'layout_binding_sha256': sha(canonical(layout_binding(layout))),
                'source_admission_sha256': sha(canonical(admission)), 'owner_adapter': spec,
                'proof_files_sha256': sha(canonical({name: tree(path) for name, path in roots.items()})),
                'bound_source_sha256': dict(sorted(sources.pins.items())),
                'owned_waits_complete': True, 'owned_cleanup_complete': True,
                'product_acceptance_passed': False}
            require(read(root / 'result.json') == capture_raw and read(shared / 'context.input.json') == context_raw
                    and read(shared / 'artifact-inputs.json') == catalog_raw
                    and read(shared / 'services/manifest.json') == services_raw, 'post-close raw inputs changed')
        finally:
            for name in names: sys.modules.pop(name, None)
    return proof


def verify_owner_phase_join(root, capture_raw, verified, shared_proof):
    """Bind the new verified result to immutable capture bytes and actual cleanup."""
    capture = parse(capture_raw)
    require(capture.get('owned_waits_complete') is False and capture.get('owned_cleanup_complete') is False,
            'capture must keep shared waits and cleanup pending')
    require(verified != capture and verified.get('capture_result_sha256') == sha(capture_raw)
            and verified.get('shared_postclose_proof') == shared_proof
            and shared_proof['capture_result_sha256'] == sha(capture_raw)
            and read(Path(root) / 'result.json') == capture_raw,
            'verified owner result does not bind unchanged capture and rederived shared cleanup')


def verify_result(root, source_reader, helper_revision, candidate_revision, gate, *, repo, proof_roots):
    require(os.environ.get('GITHUB_ACTIONS') == 'true' and sys.platform == 'linux', 'GitHub Linux verifier only')
    root = regular_root(root)
    require(helper_revision == candidate_revision and gate in GATES, 'one exact final source gate required')
    raw = read(root / 'result.json'); report = parse(raw)
    require(report.get('version') == VERSION and report.get('helper_revision') == report.get('candidate_revision') == candidate_revision
            and report.get('gate') == gate and report.get('product_acceptance_passed') is False, 'shared result identity differs')
    snapshot = tree(root); snapshot.pop('result.json')
    require(snapshot == report['saved_files'], 'shared raw file universe/bytes/modes differ')
    with admitted_sources(repo, helper_revision, source_reader) as (sources, authority, admission):
        require(SELF in report['bound_source_sha256'] and all(sha(sources.read(name)) == digest
                for name, digest in report['bound_source_sha256'].items()), 'captured source bindings differ')
        roots = roots_checked(proof_roots, root, sources.root)
        require({k: tree(p) for k, p in roots.items()} == report['proof_files'] and admission == report['source_admission'], 'trusted catalog proof roots/source authority differ')
        data = parse(read(root / 'context.input.json'))
        require(data.get('repo') == str(sources.root) and data.get('helper_revision') == data.get('candidate_revision') == candidate_revision
                and data.get('qa_mode') == 'native-qa-v1' and data.get('native_source_authority') == admission,
                'retained qualified Context source/identity differs')
        spec = adapter_spec(data, gate, admission, sources)
        require(spec == report['owner_adapter'], 'saved owner adapter differs')
        catalog = parse(read(root / 'artifact-inputs.json'))
        layout_module = raw_module(sources, '_som_current_gate_layout_verify', LAYOUT)
        semantic = raw_module(sources, '_som_current_gate_semantic_verify', SEMANTIC)
        owner = raw_module(sources, '_som_current_gate_owner_verify', spec['path']); owner_api(owner)
        try:
            layout = layout_module.verify_candidate_layout(root / 'candidate', sources.read, helper_revision, candidate_revision,
                **layout_arguments(sources, roots, root / 'restored-candidate', catalog))
            require(layout_binding(layout) == layout_binding(report['layout']), 'same-build layout/provenance proof differs')
            with semantic.trusted_proof_bundle(sources.root, sources.read, helper_revision, candidate_revision, roots, root, admission) as trusted:
                verify_services(root / 'services', parse(read(root / 'services/manifest.json')), trusted, layout, sources)
            verified = owner.verify_result(root / 'owner', sources.read, helper_revision, candidate_revision, gate,
                repo=sources.root, proof_roots=roots)
            validate_owner_result(verified, gate, candidate_revision)
            capture_raw = read(root / 'owner/result.json')
            shared_proof = verify_owner_postclose(root / 'owner', sources.read, helper_revision, candidate_revision, gate,
                repo=sources.root, proof_roots=roots)
            verify_owner_phase_join(root / 'owner', capture_raw, verified, shared_proof)
            require(verified == report['owner_result'] == parse(read(root / 'owner/verified-result.json'))
                    and sha(capture_raw) == report['owner_capture_result_sha256']
                    and sha(read(root / 'owner/verified-result.json')) == report['owner_verified_result_sha256'],
                    'actual owner verifier/capture/verified result join differs')
            require(all(report.get(k) is True for k in ('complete', 'all_required_passed', 'owned_waits_complete', 'owned_cleanup_complete', 'raw_logs_complete')),
                    'incomplete shared result')
            require(read(root / 'result.json') == raw and tree(root) == {'result.json': identity(root / 'result.json'), **snapshot}, 'shared raw files changed during join')
        finally:
            for name in ('_som_current_gate_layout_verify', '_som_current_gate_semantic_verify', '_som_current_gate_owner_verify'):
                sys.modules.pop(name, None)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('repo', 'product-context', 'out', 'assembly-root', 'carrier-root'):
        parser.add_argument('--' + name, type=Path, required=True)
    for name in ('helper-revision', 'candidate-revision'):
        parser.add_argument('--' + name, required=True)
    parser.add_argument('--gate', choices=sorted(GATES), required=True)
    parser.add_argument('--qa-mode', choices=['native-qa-v1'], required=True)
    parser.add_argument('--port', type=int, required=True)
    args = parser.parse_args(argv)
    require(os.environ.get('GITHUB_ACTIONS') == 'true' and sys.platform == 'linux', 'GitHub Linux launcher only')
    require(args.helper_revision == args.candidate_revision and args.candidate_revision != 'f59e28d910ff4adb16e3becfdc82bd2978ca84fa'
            and args.port == PORTS['current'], 'fresh current candidate and exact QA port18978 required')
    args.out = args.out.absolute()
    temporary = regular_root(os.environ['RUNNER_TEMP'])
    require(temporary in args.out.parents and not args.out.exists() and '..' not in args.out.parts
            and not any(p.is_symlink() for p in args.out.parents), 'new owned launcher output required')
    # Context will own its raw import controls. Do not stack a second owner above it.
    with admitted_sources(args.repo, args.helper_revision, install=False) as (sources, authority, admission):
        raw = read(args.product_context)
        data = parse(raw)
        require(data['repo'] == str(sources.root) and data['helper_revision'] == data['candidate_revision'] == args.candidate_revision
                and data['qa_mode'] == 'native-qa-v1' and data['native_source_authority'] == admission, 'qualified Context identity/source authority differs')
        roots = roots_checked({name: data[name] for name in ROLES}, args.out, sources.root)
        require(roots['assembly_root'] == args.assembly_root.absolute() and roots['carrier_root'] == args.carrier_root.absolute()
                and Path(data['build_provenance_root']).absolute() == provenance_root(roots['assembly_root']), 'trusted build/assembly/carrier context roots differ')
        protected = [sources.root, *roots.values(), provenance_root(roots['assembly_root']), args.product_context.absolute(),
                     *[Path(data[name]).absolute() for name in ('wheel_root', 'cargo_root', 'runtime_root', 'rust_tools_root')],
                     *[Path(value).absolute() for value in data.get('baseline_inputs', {}).values()]]
        require(all(args.out != path and path not in args.out.parents and args.out not in path.parents for path in protected),
                'launcher output overlaps a protected input')
        spec = adapter_spec(data, args.gate, admission, sources)  # Missing owner fails before any native command.
        module = raw_module(sources, 'full_product_context', CONTEXT)
        os.environ[module.ENV] = str(args.product_context.absolute())
        ctx = module.Context(args.product_context); module._ACTIVE = ctx
        require(Path(sys.executable).resolve() == module.observer_python().resolve(), 'actual qualified observer Python required')
        ctx.verify()
        args.out.mkdir(mode=0o700)
        (args.out / 'context.input.json').write_bytes(raw)
        catalog = parse(read(data['artifact_inputs'])) if type(data['artifact_inputs']) is str else data['artifact_inputs']
        save(args.out / 'artifact-inputs.json', catalog)
        report = {'version': VERSION, 'gate': args.gate, 'helper_revision': args.helper_revision, 'candidate_revision': args.candidate_revision,
            'source_admission': admission, 'owner_adapter': spec, 'proof_files': {k: tree(p) for k, p in roots.items()},
            'complete': False, 'all_required_passed': False, 'owned_waits_complete': False, 'owned_cleanup_complete': False,
            'raw_logs_complete': False, 'product_acceptance_passed': False}
        services = None
        try:
            carrier = ctx.module('current_gate_carrier', CARRIER)
            restored = carrier.restore(roots['carrier_root'], args.out / 'restored-candidate', roots['assembly_root'] / 'manifest.json', args.candidate_revision)
            layout_module = ctx.module('current_gate_layout', LAYOUT)
            report['layout'] = layout_module.restore_candidate_layout(sources.root, sources.read, args.helper_revision, args.candidate_revision,
                out=args.out / 'candidate', **{k: v for k, v in layout_arguments(sources, roots, restored['root'], catalog).items() if k != 'repo'})
            semantic = ctx.module('current_gate_semantic', SEMANTIC)
            owner = ctx.module('current_gate_owner', spec['path']); owner_api(owner)
            output = args.out / 'owner'; output.mkdir()
            with semantic.trusted_proof_bundle(sources.root, sources.read, args.helper_revision, args.candidate_revision, roots, args.out, admission) as trusted:
                services = Services(ctx, trusted.driver, trusted.checker, trusted.proof, report['layout'], restored, args.out / 'services')
                with trusted.driver.cancellation_handlers() as cancellation:
                    with owned_cleanup(services, cancellation, report):
                        services.start_current()
                        returned = owner.run_gate(ctx, Path(report['layout']['candidate_dir']), services, output, args.gate)
                        capture_raw = read(output / 'result.json')
                        require(type(returned) is dict and parse(capture_raw) == returned,
                                'actual owner hook must return its retained result object')
                        services.verify_all()
                    require(cancellation['signal'] is None, 'owned gate was interrupted')
            verified = owner.verify_result(output, sources.read, args.helper_revision, args.candidate_revision, args.gate,
                repo=sources.root, proof_roots=roots)
            validate_owner_result(verified, args.gate, args.candidate_revision)
            shared_proof = verify_owner_postclose(output, sources.read, args.helper_revision, args.candidate_revision, args.gate,
                repo=sources.root, proof_roots=roots)
            verify_owner_phase_join(output, capture_raw, verified, shared_proof)
            save(output / 'verified-result.json', verified)
            report['owner_result'] = verified
            report['owner_capture_result_sha256'] = sha(capture_raw)
            report['owner_verified_result_sha256'] = sha(read(output / 'verified-result.json'))
            require(layout_module.verify_candidate_layout(args.out / 'candidate', sources.read, args.helper_revision, args.candidate_revision,
                **layout_arguments(sources, roots, restored['root'], catalog)) == report['layout'], 'same-build candidate layout changed during gate')
            ctx.verify(); sources.verify()
            require({k: tree(p) for k, p in roots.items()} == report['proof_files'], 'protected proof/input files changed')
            report.update(complete=True, all_required_passed=True, owned_waits_complete=True, owned_cleanup_complete=True, raw_logs_complete=True)
        except BaseException as error:
            primary = report.setdefault('primary_failure', failure_record(error))
            report.update(error=primary['error'], reason=primary['reason'])
        finally:
            if services is not None and not services.closed:
                try: services.close_all()
                except BaseException as error: report['cleanup_error'] = type(error).__name__ + ': ' + str(error)
            report['bound_source_sha256'] = dict(sorted(sources.pins.items()))
            report['saved_files'] = tree(args.out)
            save(args.out / 'result.json', report)
    return 0 if report['all_required_passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())

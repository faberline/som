"""Exact source-owned current-gate service manager and minimal original helpers.

All definitions below originate unchanged from run_current_candidate_gate.py.
The ordinary Core role owns its own code and globals after the entry returns.
"""
from __future__ import annotations

from pathlib import Path
import hashlib
import json
import os
import re
import stat


BASE = 'som-rs/scripts/hosted_readiness/'


CONTEXT = BASE + 'product_runtime/full_product_context.py'


ORACLE = BASE + 'product_runtime/recorded_before_oracle.py'


FULL_DRIVER = BASE + 'run_full_products.py'


NATIVE_BOOTSTRAP = BASE + 'native_source_bootstrap.py'


RECORDED_FRAME = ('GITHUB_ACTIONS', 'RUNNER_ENVIRONMENT', 'RUNNER_OS', 'RUNNER_TEMP',
    'GITHUB_SHA', 'GITHUB_RUN_ID', 'GITHUB_RUN_ATTEMPT', 'GITHUB_JOB',
    'GITHUB_REPOSITORY', 'GITHUB_REPOSITORY_ID', 'GITHUB_SERVER_URL', 'GITHUB_API_URL',
    'GITHUB_WORKFLOW_SHA', 'GITHUB_EVENT_NAME', 'GITHUB_REF', 'GITHUB_WORKFLOW_REF', 'RUNNER_NAME')


RECORDED_TOKEN = 'SOM_DEPENDENCY_ACTIONS_TOKEN'


RECORDED_TOKEN_PUBLIC = '<present; value withheld>'


PORTS = {'current': 18978, 'baseline': 18979}


PROTECTED = (8765, 18765)


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
        self.ctx.sources.read(NATIVE_BOOTSTRAP)
        self.ctx.sources.read(FULL_DRIVER)
        self.ctx.sources.read(CONTEXT)
        directory = self.out / 'baseline'; directory.mkdir()
        service = self.driver.OwnedService(python, root, {}, PORTS['baseline'], directory)
        # Request the oracle. The real guard creates the fresh bootstrap argv.
        requested, expected_actual = recorded_python_arguments(python, self.ctx.repo, self.ctx.helper_revision,
            self.ctx.candidate_revision, profile_path, root, directory / 'oracle', wanted_python)
        service.argv = requested
        service.environment, public_environment = recorded_python_environment(self.ctx.child_environment(),
            context=self.ctx.path, directory=directory, revision=self.ctx.helper_revision)
        require(read(self.ctx.path) == self.ctx.raw == read(self.out.parent / 'context.input.json')
                and os.environ.get('SOM_FULL_PRODUCT_CONTEXT') == str(self.ctx.path),
                'recorded child requires the exact active checked Context')
        initial = {'binary': self.driver.file_identity(python), 'assets': self.driver.tree_identity(root), 'tools': {}}
        extra = {'mode': 'recorded-same-request-before', 'profile_path': profile_path, 'profile_sha256': sha(profile_raw),
                 'profile': parse(profile_raw), 'input_root': str(root), 'oracle_out': 'oracle',
                 'data_proof_sha256': sha(canonical(proof) + b'\n')}
        record = {'role': 'baseline', 'port': PORTS['baseline'], 'argv': requested, 'requested_argv': requested,
                  'environment': public_environment,
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
        extra['native_bootstrap_source'] = NATIVE_BOOTSTRAP
        extra['native_bootstrap_source_sha256'] = sha(self.ctx.sources.read(NATIVE_BOOTSTRAP))
        extra['native_entry_driver_source'] = FULL_DRIVER
        extra['native_entry_driver_source_sha256'] = sha(self.ctx.sources.read(FULL_DRIVER))
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


def recorded_python_arguments(python, repo, helper, candidate, profile, inputs, output, python_sha256):
    """Keep the oracle request distinct from the fresh source bootstrap argv."""
    tail = [str(Path(repo) / ORACLE), '--repo', str(repo), '--helper-revision', helper,
            '--candidate-revision', candidate, '--profile', profile, '--inputs-root', str(inputs),
            '--python-sha256', python_sha256, '--port', str(PORTS['baseline']), '--out', str(output)]
    requested = [str(python), '-I', '-S', '-B', *tail]
    actual = [str(python), '-I', '-S', '-B', str(Path(repo) / NATIVE_BOOTSTRAP),
              '--repo', str(repo), '--helper-revision', helper, '--candidate-revision', candidate,
              '--entry-role', 'entry.full-products', '--', '--native-python-bootstrap', '--', *tail]
    return requested, actual


def validate_recorded_python_environment(environment, *, context, directory, revision):
    """Admit an exact public frame. The token has only a fixed presence marker."""
    require(type(environment) is dict
            and set(environment) == {'PATH', 'LANG', 'LC_ALL', 'TMPDIR', 'SOM_FULL_PRODUCT_CONTEXT',
                                     RECORDED_TOKEN, *RECORDED_FRAME}
            and all(type(value) is str and value and not any(char in value for char in ('\0', '\n', '\r'))
                    for value in environment.values()), 'recorded Python exact public environment required')
    require(environment['PATH'] == '/usr/bin:/bin' and environment['LANG'] == environment['LC_ALL'] == 'C.UTF-8'
            and environment['TMPDIR'] == str(Path(directory) / 'tmp')
            and environment['SOM_FULL_PRODUCT_CONTEXT'] == str(context)
            and environment[RECORDED_TOKEN] == RECORDED_TOKEN_PUBLIC,
            'recorded Python minimal paths or public authentication presence differs')
    require(environment['GITHUB_ACTIONS'] == 'true' and environment['RUNNER_ENVIRONMENT'] == 'github-hosted'
            and environment['RUNNER_OS'] == 'Linux' and environment['GITHUB_REPOSITORY'] == 'faberline/som'
            and environment['GITHUB_REPOSITORY_ID'] == '1390374878'
            and environment['GITHUB_SERVER_URL'] == 'https://github.com'
            and environment['GITHUB_API_URL'] == 'https://api.github.com'
            and environment['GITHUB_SHA'] == environment['GITHUB_WORKFLOW_SHA'] == revision
            and re.fullmatch('[0-9a-f]{40}', revision or '') is not None,
            'recorded Python hosted repository/source frame differs')
    require(all(re.fullmatch('[1-9][0-9]*', environment[name]) is not None
                for name in ('GITHUB_RUN_ID', 'GITHUB_RUN_ATTEMPT'))
            and re.fullmatch('[A-Za-z_][A-Za-z0-9_-]*', environment['GITHUB_JOB']) is not None
            and environment['GITHUB_EVENT_NAME'] in {'push', 'workflow_dispatch'},
            'recorded Python actual run/attempt/job/event frame differs')
    ref, runner = environment['GITHUB_REF'], environment['RUNNER_NAME']
    require(len(ref) <= 1024 and re.fullmatch(r'refs/(?:heads|tags)/[^\s@]+', ref) is not None
            and environment['GITHUB_WORKFLOW_REF'] ==
                'faberline/som/.github/workflows/rust-hosted-readiness.yml@' + ref
            and 0 < len(runner) <= 256 and runner == runner.strip()
            and not any(ord(char) < 32 or ord(char) == 127 for char in ref + runner),
            'recorded Python workflow/ref/runner frame differs')
    temporary = Path(environment['RUNNER_TEMP'])
    require(temporary.is_absolute() and '..' not in temporary.parts and temporary in Path(directory).parents,
            'recorded Python directory must be inside its actual RUNNER_TEMP')


def recorded_python_environment(child_environment, *, context, directory, revision):
    """Select the existing checked Context frame; never save the real token."""
    require(type(child_environment) is dict and all(name in child_environment for name in RECORDED_FRAME),
            'checked Context complete child environment required')
    actual = {'PATH': '/usr/bin:/bin', 'LANG': 'C.UTF-8', 'LC_ALL': 'C.UTF-8',
              'TMPDIR': str(Path(directory) / 'tmp'), 'SOM_FULL_PRODUCT_CONTEXT': str(context),
              **{name: child_environment[name] for name in RECORDED_FRAME}}
    token = child_environment.get(RECORDED_TOKEN)
    require(type(token) is str and token and token != RECORDED_TOKEN_PUBLIC
            and not any(char in token for char in ('\0', '\n', '\r')),
            'actual existing Actions authentication is required for recorded fresh bootstrap')
    public = {**actual, RECORDED_TOKEN: RECORDED_TOKEN_PUBLIC}
    validate_recorded_python_environment(public, context=context, directory=directory, revision=revision)
    actual[RECORDED_TOKEN] = token
    return actual, public


def validate_recorded_python_evidence(startup, post, *, requested, actual, pid, cwd, revision, source_reader, exit_code):
    """Check the existing guard's real startup and terminal bootstrap protocol."""
    require(type(exit_code) is int and exit_code == 0, 'recorded Python native bootstrap must exit zero')
    require(type(requested) is list and len(requested) == 21 and all(type(value) is str for value in requested),
            'recorded original oracle request shape differs')
    expected_requested, expected_actual = recorded_python_arguments(requested[0], requested[6], revision, revision,
        requested[12], requested[14], requested[20], requested[16])
    require(requested == expected_requested and actual == expected_actual,
            'recorded fresh bootstrap/entry/oracle source command differs')
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
    validate_recorded_python_environment(record['environment'], context=record['environment']['SOM_FULL_PRODUCT_CONTEXT'],
        directory=directory, revision=sources.revision)
    require(record['requested_argv'] == requested and record['argv'] == actual
            and record['extra']['native_bootstrap_source'] == NATIVE_BOOTSTRAP
            and record['extra']['native_bootstrap_source_sha256'] == sha(sources.read(NATIVE_BOOTSTRAP))
            and record['extra']['native_entry_driver_source'] == FULL_DRIVER
            and record['extra']['native_entry_driver_source_sha256'] == sha(sources.read(FULL_DRIVER)),
            'recorded actual initial bootstrap, entry driver source or command differs')
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

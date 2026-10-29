"""Narrow native-only execution binding for the unchanged TypeScript product gates.

No frozen Mac host rule is changed. This adapter lives only in the new isolated
TypeScript owner child after exact native source and input-profile admission.
"""
from __future__ import annotations
from contextlib import contextmanager
import base64
import ast
import hashlib
import http.client
import json
import os
from pathlib import Path
import signal
import stat
import subprocess
import tempfile
import time
from types import SimpleNamespace
from urllib.parse import urlsplit

BASE = 'som-rs/scripts/hosted_readiness/'
SELF = BASE + 'typescript_native_host.py'
PROFILE = BASE + 'inputs/typescript-regression-inputs-v1.json'
OWNER = BASE + 'typescript_regression_owner.py'
ARTIFACTS = 'som-rs/contracts/native-tools-v1/artifacts.json'
SOCKETS = BASE + 'typescript_regression_nested_rust.py'


def require(value, message):
    if not value:
        raise ValueError(message)


def file_sha(path):
    path = Path(path)
    require(path.is_file() and not any(p.is_symlink() for p in (path, *path.parents)), 'regular native tool required')
    return hashlib.sha256(path.read_bytes()).hexdigest()


def tree(root):
    values = {}
    for path in sorted(root.rglob('*')):
        info = path.lstat()
        require(stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode), 'native TypeScript input link/special member')
        if stat.S_ISREG(info.st_mode):
            values[path.relative_to(root).as_posix()] = {'sha256': file_sha(path), 'size': info.st_size,
                                                      'mode': stat.S_IMODE(info.st_mode)}
    return values


def process_stamp(pid):
    root = Path('/proc') / str(pid)
    raw = (root / 'stat').read_bytes()
    fields = raw.rsplit(b')', 1)[1].split()
    exe = (root / 'exe').stat()
    cmdline = (root / 'cmdline').read_bytes()
    require(cmdline.endswith(b'\0') and not cmdline.startswith(b'\0'), 'complete actual process argv required')
    environment = {}
    for item in (root / 'environ').read_bytes().split(b'\0'):
        name, separator, value = item.partition(b'=')
        if separator and name in {b'SOM_APP_ADDR', b'DATABASE_URL', b'SOM_BEHAVIOR_DATABASE_URL', b'TEST_DATABASE_URL'}:
            key, value = name.decode(), value.decode('utf-8', 'strict')
            require(key not in environment, 'duplicate actual process environment key')
            if key != 'SOM_APP_ADDR':
                parsed = urlsplit(value.replace('postgresql+psycopg://', 'postgresql://', 1))
                require(parsed.scheme == 'postgresql' and parsed.hostname == '127.0.0.1'
                        and parsed.port is not None and parsed.port not in {8765, 18765, 18978, 18979}
                        and parsed.username == 'postgres' and parsed.password is None,
                        'only owned password-free loopback database environment may be retained')
            environment[key] = value
    return {'pid': pid, 'state': fields[0].decode(), 'pgrp': int(fields[2]), 'session': int(fields[3]),
            'start_ticks': int(fields[19]), 'executable_device': exe.st_dev, 'executable_inode': exe.st_ino,
            'cwd': os.readlink(root / 'cwd'), 'proc_stat_raw': raw.decode('utf-8', 'strict'),
            'cmdline_raw_b64': base64.b64encode(cmdline).decode(), 'environment': environment}


def port_listeners(port):
    result = []
    for protocol in ('tcp', 'tcp6'):
        for raw in (Path('/proc/net') / protocol).read_text().splitlines()[1:]:
            fields = raw.split()
            if fields[3] == '0A' and int(fields[1].split(':')[1], 16) == port:
                result.append({'protocol': protocol, 'raw': raw})
    return result


def configure(ctx, checker):
    require(ctx.qa_mode == 'native-qa-v1' and ctx.qa_source_authority['source_authority_active'] is True
            and {SELF, OWNER} <= set(ctx.qa_source_authority['admitted_repository_python_sources']),
            'exact native TypeScript host source admission required')
    ctx.sources.read(SELF)
    profile = json.loads(ctx.sources.read(PROFILE))
    roots = ctx.data['typescript_regression_inputs']['roots']
    native_root, cache = Path(roots['node_tools']), Path(roots['npm_cache'])
    native = profile['native_typescript_tools']
    owner = ctx.module('typescript_regression_input_inventory', OWNER)
    require(set(native) == {'node', 'npm_cli', 'npm_root'}, 'exact native Node/npm source paths required')
    for value in native.values():
        require(type(value) is str and value and not value.startswith('/')
                and all(p not in {'', '.', '..'} for p in value.split('/')), 'safe native tool member required')
    node, npm_cli, npm_root = (native_root / native[name] for name in ('node', 'npm_cli', 'npm_root'))
    artifacts = json.loads(ctx.sources.read(ARTIFACTS))
    admitted_nodes = [row for row in artifacts['tools'] if row['tool'] == 'node'
                      and row['host'] == 'x86_64-unknown-linux-gnu' and row['version'] == '26.5.0']
    require(len(admitted_nodes) == 1 and file_sha(node) == admitted_nodes[0]['executable_sha256']
            and node.read_bytes()[:4] == b'\x7fELF' and os.access(node, os.X_OK),
            'native Node differs from the exact committed artifact authority')
    require(file_sha(npm_cli) == profile['roles']['node_tools']['files'][native['npm_cli']]['sha256'],
            'native npm entry differs from the complete source-profile tree')
    host = checker.ts_host
    require(host.__file__.endswith('/som-rs/behavior/typescript/host.py'), 'original TypeScript host module required')
    pg = ctx.verify_postgres()
    original_python = host.python_product
    original_migrate = host.migrate_python_product
    retained_python = set()
    stamps = {}
    roles = {}
    created = {}
    entries = {}
    checker.__som_native_process_stamps__, checker.__som_native_process_roles__ = stamps, roles

    def verify_inputs():
        require(owner.tree(native_root) == profile['roles']['node_tools']['files']
                and owner.tree(cache) == profile['roles']['npm_cache']['files'], 'complete native TypeScript tool/cache bytes changed')
        require(host.tree_sha(npm_root) == (host.TOOLCHAIN['npm']['tree_sha256'],
                host.TOOLCHAIN['npm']['tree_files'], host.TOOLCHAIN['npm']['tree_symlinks']),
                'native npm JavaScript differs from the frozen TypeScript npm release')
        require(host.tree_sha(cache) == (host.TOOLCHAIN['vendored_cache']['tree_sha256'],
                host.TOOLCHAIN['vendored_cache']['tree_files'], host.TOOLCHAIN['vendored_cache']['tree_symlinks']),
                'the TypeScript cache differs from its own original frozen authority')
        ctx.verify_postgres()

    def native_toolchain(cache_dir=None):
        require(cache_dir is not None and Path(cache_dir) == cache, 'only the admitted TypeScript cache is allowed')
        verify_inputs()
        versions = []
        for command in ([str(node), '--version'], [str(node), str(npm_cli), '--version'],
                        [str(pg / 'postgres'), '--version']):
            result = subprocess.run(command, capture_output=True, text=True, timeout=30, check=False)
            require(result.returncode == 0 and not result.stderr, 'qualified native tool version command failed')
            versions.append(result.stdout.strip())
        require(versions[0] == host.TOOLCHAIN['node']['version']
                and versions[1] == host.TOOLCHAIN['npm']['version']
                and versions[2].startswith('postgres (PostgreSQL) ' + host.TOOLCHAIN['postgresql']['version']),
                'actual native tools changed a frozen TypeScript tool version')
        return {'mode': 'native-qa-v1', 'node_version': versions[0],
                'node_sha256': file_sha(node), 'libnode_sha256': None,
                'npm_version': versions[1], 'npm_tree_sha256': host.tree_sha(npm_root)[0],
                'postgresql_version': versions[2],
                'cache': {'path': str(cache), 'tree_sha256': host.tree_sha(cache)[0],
                          'tree_files': host.tree_sha(cache)[1], 'tree_symlinks': 0}}

    def cwd_verified(pid, expected):
        try:
            if pid in host.forbidden_pids():
                return False, None
            actual = process_stamp(pid)
            wanted = stamps.get(pid)
            same = (wanted is not None and all(actual[key] == wanted[key] for key in
                ('pid', 'pgrp', 'session', 'start_ticks', 'executable_device', 'executable_inode'))
                and actual['state'] != 'Z' and Path(actual['cwd']).resolve() == Path(expected).resolve())
            return same, actual['cwd']
        except (OSError, ValueError, IndexError):
            return False, None

    def verify_process(pid):
        require(pid in stamps and cwd_verified(pid, stamps[pid]['cwd'])[0],
                'actual owned process identity changed')
        return process_stamp(pid)
    checker.__som_native_verify_process__ = verify_process

    original_tracker = host.ProcessTracker
    class NativeTracker(original_tracker):
        def register_checked(self, pid, role, cwd, command):
            require(pid not in host.forbidden_pids(), 'protected process cannot enter the native tracker')
            stamp = process_stamp(pid)
            require(stamp['pgrp'] == stamp['session'] == pid and stamp['state'] != 'Z'
                    and Path(stamp['cwd']).resolve() == Path(cwd).resolve(), 'new owned native process group required')
            if role == 'postgres':
                expected = pg / 'postgres'
            elif role in {'typescript_product', 'typescript_startup_probe'}:
                expected = node
            elif role == 'python_product':
                expected = Path(ctx.runtime()['tools']['python'])
            else:
                raise ValueError('unadmitted native TypeScript process role: ' + role)
            info = expected.stat()
            require((stamp['executable_device'], stamp['executable_inode']) == (info.st_dev, info.st_ino),
                    'actual owned process executable differs from its qualified role')
            stamps[pid] = stamp
            roles[pid] = role
            record = super().register(pid, role, cwd, command)
            record['native_owned_start'] = stamp
            record['native_actual_argv'] = [part.decode('utf-8', 'strict') for part in
                base64.b64decode(stamp['cmdline_raw_b64'], validate=True)[:-1].split(b'\0')]
            record['native_executable_sha256'] = file_sha(expected)
            entries[pid] = record
            return record

        def register(self, pid, role, cwd, command):
            try:
                return self.register_checked(pid, role, cwd, command)
            except BaseException as primary:
                held = created.get(pid)
                cleanup = {'pid': pid, 'role': role, 'primary_error': type(primary).__name__,
                           'primary_reason': str(primary), 'wait_attempted': False, 'wait_completed': False,
                           'signals': [], 'cleanup_errors': []}
                if held is not None:
                    proc = held['process']
                    try:
                        require(held['new_session'] is True and held['cwd'] == str(Path(cwd).absolute())
                                and held['requested_argv'] == [str(v) for v in command]
                                and proc.pid == pid and proc.returncode is None,
                                'failed registration has no unreaped owned new-session handle')
                        # Before the first wait/poll, the direct child's PID is
                        # still reserved. Its constructor created this session.
                        # No unrelated process group can reuse that ID here.
                        try:
                            os.killpg(pid, signal.SIGTERM); cleanup['signals'].append('SIGTERM')
                        except ProcessLookupError:
                            pass
                    except BaseException as error:
                        cleanup['cleanup_errors'].append({'stage': 'owned-group-stop',
                            'error': type(error).__name__, 'reason': str(error)})
                    cleanup['wait_attempted'] = True
                    try:
                        code = proc.wait(timeout=10)
                        cleanup.update(wait_completed=True, exit_code=code)
                    except subprocess.TimeoutExpired:
                        try:
                            require(proc.returncode is None, 'owned child was reaped before escalation')
                            os.killpg(pid, signal.SIGKILL); cleanup['signals'].append('SIGKILL')
                        except BaseException as error:
                            cleanup['cleanup_errors'].append({'stage': 'owned-group-escalation',
                                'error': type(error).__name__, 'reason': str(error)})
                        try:
                            cleanup.update(wait_completed=True, exit_code=proc.wait(timeout=10))
                        except BaseException as error:
                            cleanup['cleanup_errors'].append({'stage': 'final-child-wait',
                                'error': type(error).__name__, 'reason': str(error)})
                    except BaseException as error:
                        cleanup['cleanup_errors'].append({'stage': 'actual-child-wait',
                            'error': type(error).__name__, 'reason': str(error)})
                directory = ctx.work / 'typescript-regression/native-registration-failures'
                try:
                    directory.mkdir(parents=True, exist_ok=True)
                    (directory / (str(pid) + '.json')).write_text(json.dumps(cleanup, sort_keys=True) + '\n')
                except BaseException as error:
                    cleanup['cleanup_errors'].append({'stage': 'failure-evidence',
                        'error': type(error).__name__, 'reason': str(error)})
                primary.__som_typescript_registration_cleanup__ = cleanup
                raise

        def stop(self, proc, cwd, entry, sig=signal.SIGTERM, timeout=10.0):
            primary, code, waited = None, None, None
            try:
                code = super().stop(proc, cwd, entry, sig=sig, timeout=timeout)
            except BaseException as error:
                primary = error
            try:
                waited = proc.wait(timeout=10)
                entry['wait_completed'], entry['wait_exit_code'] = True, waited
            except BaseException as error:
                entry['wait_completed'] = False
                entry.setdefault('native_cleanup_failures', []).append(
                    {'stage': 'actual-child-wait', 'error': type(error).__name__, 'reason': str(error)})
                if primary is None:
                    primary = error
            try:
                require('native_owned_start' in entry and entry.get('command'), 'actual native start metadata required')
                command = entry['command']
                if entry['role'].startswith('typescript_'):
                    port = urlsplit('http://' + entry['native_owned_start']['environment']['SOM_APP_ADDR']).port
                else:
                    flag = '-p' if entry['role'] == 'postgres' else '--port'
                    port = int(command[command.index(flag) + 1])
                require(type(port) is int and port not in {8765, 18765, 18978, 18979}, 'owned terminal port required')
                remaining = port_listeners(port)
                entry['native_terminal'] = {'pid': proc.pid, 'port': port, 'wait_exit_code': waited,
                                             'remaining_listeners': remaining}
                require(waited is not None and not remaining, 'actual owned product port remains after wait')
            except BaseException as error:
                entry.setdefault('native_cleanup_failures', []).append(
                    {'stage': 'terminal-listener-check', 'error': type(error).__name__, 'reason': str(error)})
                if primary is None:
                    primary = error
            if primary is not None:
                raise primary
            require(type(waited) is int and waited == code, 'actual owned native child wait differs')
            return waited

    @contextmanager
    def pg_cluster(work, port, tracker):
        """Keep the same fresh PG settings, with an actual owned child handle."""
        host.check_port_allowed(port)
        data, log = Path(work) / 'pgdata', Path(work) / 'postgres.log'
        env = {**os.environ, 'LC_ALL': 'C', 'TZ': 'UTC'}
        command = [str(pg / 'initdb'), '-D', str(data), '-U', 'postgres', '-A', 'trust',
                   '--encoding=UTF8', '--locale=C', '--no-instructions']
        initialized = subprocess.run(command, env=env, capture_output=True, text=True, check=False)
        require(initialized.returncode == 0, 'qualified PostgreSQL initdb failed: ' + initialized.stderr)
        command = [str(pg / 'postgres'), '-D', str(data), '-F', '-h', '127.0.0.1',
                   '-p', str(port), '-k', '', '-c', 'timezone=UTC']
        with log.open('xb') as stream:
            proc = registered_popen(command, cwd=data, env=env, stdin=subprocess.DEVNULL,
                                    stdout=stream, stderr=subprocess.STDOUT, start_new_session=True)
        record, primary = None, None
        try:
            record = tracker.register(proc.pid, 'postgres', data, command)
            deadline, last = time.monotonic() + 30, ''
            while True:
                require(proc.poll() is None, 'owned PostgreSQL exited before readiness')
                try:
                    with checker.rust_products.psycopg.connect(
                            f'postgresql://postgres@127.0.0.1:{port}/postgres', connect_timeout=1):
                        pass
                    require(checker.base_hosts.listening(proc.pid, port),
                            'PostgreSQL readiness socket is outside the owned child')
                    break
                except checker.rust_products.psycopg.OperationalError as error:
                    last = str(error)
                    require(time.monotonic() < deadline, 'owned PostgreSQL readiness timed out: ' + last)
                    time.sleep(0.05)
            yield f'127.0.0.1:{port}'
        except BaseException as error:
            primary = error
            raise
        finally:
            cleanup_errors = []
            try:
                if proc.returncode is not None:
                    pass
                elif record is not None:
                    tracker.stop(proc, data, record)
                else:
                    # Register failed. Kill only the new Popen handle after its
                    # exact process/executable/CWD stamp is rechecked.
                    stamp = process_stamp(proc.pid)
                    expected = (pg / 'postgres').stat()
                    require(stamp['pgrp'] == stamp['session'] == proc.pid
                            and Path(stamp['cwd']).resolve() == data.resolve()
                            and (stamp['executable_device'], stamp['executable_inode']) ==
                                (expected.st_dev, expected.st_ino),
                            'failed PostgreSQL registration has no owned cleanup proof')
                    proc.terminate(); proc.wait(timeout=10)
            except BaseException as cleanup:
                cleanup_errors.append({'stage': 'owned-stop', 'error': type(cleanup).__name__, 'reason': str(cleanup)})
                if primary is None:
                    primary = cleanup
            # A stamp or signaling failure must never skip the actual handle's
            # final wait attempt. Unknown ownership still forbids a blind kill.
            terminal = {'wait_attempted': True, 'wait_completed': False, 'exit_code': None}
            try:
                terminal.update(wait_completed=True, exit_code=proc.wait(timeout=10))
            except BaseException as cleanup:
                cleanup_errors.append({'stage': 'final-child-wait', 'error': type(cleanup).__name__, 'reason': str(cleanup)})
                if primary is None:
                    primary = cleanup
            terminal['cleanup_errors'] = cleanup_errors
            try:
                (Path(work) / 'native-postgres-cleanup.json').write_text(
                    json.dumps(terminal, sort_keys=True, separators=(',', ':')) + '\n')
            except BaseException as cleanup:
                cleanup_errors.append({'stage': 'cleanup-evidence', 'error': type(cleanup).__name__, 'reason': str(cleanup)})
                if primary is None:
                    primary = cleanup
            if primary is not None and cleanup_errors:
                primary.__som_typescript_pg_cleanup__ = terminal
                raise primary

    def migrate(work, root, db_url, python_exe):
        require(ctx.work in Path(root).absolute().parents, 'TypeScript reference Python root must belong to Context')
        python = ctx.prepare_python(root)
        passed = False
        try:
            original_migrate(work, root, db_url, str(python)); passed = True
        finally:
            if str(Path(root).absolute()) not in retained_python:
                ctx.release_python(root, passed)

    @contextmanager
    def python_product(work, name, prompt, files, db_url, port, python_exe, tracker):
        root = Path(work) / name
        retained_python.add(str(root.absolute()))
        passed = False
        try:
            with original_python(work, name, prompt, files, db_url, port,
                                 str(root / '.venv/bin/python'), tracker) as url:
                yield url
            passed = True
        finally:
            retained_python.discard(str(root.absolute()))
            if root.is_dir():
                ctx.release_python(root, passed)

    host.NODE_BIN, host.NPM_INVOCATION, host.PG_BIN_DIR = str(node), [str(node), str(npm_cli)], pg
    host.verify_toolchain, host.verify_process_cwd, host.ProcessTracker = native_toolchain, cwd_verified, NativeTracker
    host.migrate_python_product, host.python_product = migrate, python_product
    host.pg_cluster, host.find_runtime_root = pg_cluster, lambda: ctx.repo
    require(SOCKETS in ctx.qa_source_authority['admitted_repository_python_sources'],
            'owned listener source must be admitted')
    sockets = ctx.module('typescript_native_owned_listeners', SOCKETS)
    original_ready = host.wait_http_ready
    def wait_http_ready(proc, port, path='/healthz', log_path=None, timeout=20.0):
        original_ready(proc, port, path, log_path=log_path, timeout=timeout)
        verify_process(proc.pid)
        owned = sockets.owned_socket(proc.pid, port)
        connection = http.client.HTTPConnection('127.0.0.1', port, timeout=5)
        try:
            connection.request('GET', path)
            response = connection.getresponse(); raw = response.read(64 * 1024**2 + 1)
            require(len(raw) <= 64 * 1024**2 and response.status < 500,
                    'actual owned product readiness response refused')
            row = {'pid': proc.pid, 'method': 'GET', 'path': path,
                   'url': f'http://127.0.0.1:{port}' + path, 'status': response.status,
                   'response_headers': list(response.getheaders()),
                   'response_sha256': hashlib.sha256(raw).hexdigest(), 'owned_socket': owned}
        finally:
            connection.close()
        require(sockets.owned_socket(proc.pid, port) == owned, 'owned readiness listener changed')
        directory = ctx.work / 'typescript-regression/native-readiness' / str(proc.pid)
        directory.mkdir(parents=True)
        (directory / 'response.raw').write_bytes(raw)
        (directory / 'result.json').write_text(json.dumps(row, sort_keys=True, separators=(',', ':')) + '\n')
        entries[proc.pid]['native_readiness'] = row
    host.wait_http_ready = wait_http_ready
    # The old checker deletes its TemporaryDirectory. Native evidence must keep
    # all product bytes and failed database state below the checked work root.
    temporary_roots = []
    class RetainedTemporaryDirectory:
        def __init__(self, suffix=None, prefix=None, dir=None, ignore_cleanup_errors=False, **kwargs):
            require(dir is None and not kwargs, 'only the fixed native product work root is allowed')
            parent = ctx.work / 'typescript-regression/native-products'
            parent.mkdir(parents=True, exist_ok=True)
            self.name = tempfile.mkdtemp(suffix=suffix or '', prefix=prefix or 'tmp', dir=parent)
            temporary_roots.append(self.name)
        def __enter__(self):
            return self.name
        def __exit__(self, *exc):
            return False
    checker.tempfile = SimpleNamespace(**{key: value for key, value in vars(tempfile).items()
                                         if key != 'TemporaryDirectory'},
                                       TemporaryDirectory=RetainedTemporaryDirectory)
    def registered_popen(*args, **kwargs):
        require(len(args) == 1 and isinstance(args[0], (list, tuple))
                and kwargs.get('start_new_session') is True and not kwargs.get('shell')
                and kwargs.get('executable') is None and ctx.work in Path(kwargs['cwd']).absolute().parents,
                'only exact owned product child constructors may be registered')
        proc = subprocess.Popen(*args, **kwargs)
        created[proc.pid] = {'process': proc, 'requested_argv': [str(v) for v in args[0]],
                             'cwd': str(Path(kwargs['cwd']).absolute()), 'new_session': True}
        return proc
    def original_owned_function(path, name, namespace):
        require(path in ctx.qa_source_authority['admitted_repository_python_sources'],
                'original product constructor source must be admitted')
        raw = ctx.sources.read(path); tree = ast.parse(raw)
        nodes = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == name]
        require(len(nodes) == 1, 'exact original owned product constructor required')
        scope = dict(namespace)
        scope['subprocess'] = SimpleNamespace(**{key: value for key, value in vars(subprocess).items()
            if key != 'Popen'}, Popen=registered_popen)
        selected = ast.Module(body=[ast.ImportFrom(module='__future__', names=[ast.alias(name='annotations')], level=0),
                                    *nodes], type_ignores=[]); ast.fix_missing_locations(selected)
        exec(compile(selected, str(ctx.repo / path), 'exec', dont_inherit=True), scope)
        return scope[name]
    host_path = 'som-rs/behavior/typescript/host.py'
    host.typescript_product = original_owned_function(host_path, 'typescript_product', vars(host))
    original_python = original_owned_function(host_path, 'python_product', vars(host))
    checker.run_startup_gate = original_owned_function('som-rs/som-targets/qa/check_ts_products.py',
                                                       'run_startup_gate', vars(checker))
    verify_inputs()
    return {'profile_sha256': hashlib.sha256(ctx.sources.read(PROFILE)).hexdigest(),
            'node_sha256': file_sha(node), 'npm_tree_sha256': host.tree_sha(npm_root)[0],
            'typescript_cache_sha256': host.tree_sha(cache)[0], 'retained_temporary_roots': temporary_roots,
            'postgres_launch_role': 'qualified-owned-direct-child', 'product_acceptance_passed': False}

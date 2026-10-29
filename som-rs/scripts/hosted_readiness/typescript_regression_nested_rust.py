"""Keep the original Rust cluster gate, with owned PG and a raw child entry."""
from __future__ import annotations
import traceback
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

BASE = 'som-rs/scripts/hosted_readiness/'
SELF = BASE + 'typescript_regression_nested_rust.py'
WRAPPER = BASE + 'typescript_regression_checker.py'
RUST = 'som-rs/som-targets/qa/check_rust_products.py'
HOST = 'som-rs/behavior/hosts.py'
STAMP = BASE + 'typescript_native_host.py'
CONTEXT = BASE + 'product_runtime/full_product_context.py'
NATIVE_ROLE = 'ctx.typescript-regression-nested-rust'


def failure_record(error):
    return {'error': type(error).__name__, 'reason': str(error),
            'traceback': ''.join(traceback.format_exception(type(error), error, error.__traceback__))}


def remember_failure(state, role, error):
    record = {'role': role, **failure_record(error)}
    if state['primary'] is None:
        state.update(primary=error, traceback=error.__traceback__, failure=record)
        error.__som_typescript_failure_record__ = record
    else:
        state['secondary'].append(record)
    state['primary'].__som_typescript_secondary_failures__ = state['secondary']
    return record


def raise_primary(state):
    if state['primary'] is None:
        return
    try:
        sys.stderr.write(json.dumps({'typescript_primary_failure': state['failure'],
            'secondary_failures': state['secondary']}, ensure_ascii=False, sort_keys=True) + '\n')
    except BaseException as error:
        remember_failure(state, 'primary_traceback_stderr', error)
    raise state['primary'].with_traceback(state['traceback'])


def require(value, message):
    if not value:
        raise ValueError(message)


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x') as stream:
        stream.write(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')) + '\n')


def owned_socket(pid, port):
    links = {}
    for path in (Path('/proc') / str(pid) / 'fd').iterdir():
        try:
            links[os.readlink(path)] = path.name
        except FileNotFoundError:
            # An unrelated descriptor may close during enumeration. The exact
            # owned listener must still be present and unique below.
            continue
    found = []
    for name in ('tcp', 'tcp6'):
        for raw in (Path('/proc/net') / name).read_text().splitlines()[1:]:
            row = raw.split()
            if row[3] != '0A' or int(row[1].split(':')[1], 16) != port:
                continue
            link = 'socket:[' + row[9] + ']'
            if link in links:
                found.append({'protocol': name, 'row': raw, 'inode': row[9], 'fd': links[link], 'port': port})
    require(len(found) == 1, 'one actual owned PostgreSQL listener required')
    return found[0]


def _native_context(ctx):
    """Use the current checked Context object before the nested child route."""
    from _som_native_active_controls_binding import controls
    controls.verify_installed()
    require(controls.role_sources.get(NATIVE_ROLE) == SELF
            and controls.role_sources.get('runtime.context') == CONTEXT,
            'fixed nested Rust and Context source roles required')
    hosted = controls.load_module('runtime.context')
    context_type = globals().get('__som_native_Context__')
    source_type = globals().get('__som_native_SourceBytes__')
    require(context_type is hosted.Context and source_type is hosted.SourceBytes,
            'fixed native nested Context class exports required')
    require(type(ctx) is context_type, 'actual native nested Context object required')
    require(vars(hosted).get('_ACTIVE') is ctx,
            'actual active nested Context owner required')
    require(ctx.qa_mode == 'native-qa-v1' and ctx.repo == controls.root
            and ctx.helper_revision == controls.helper_revision
            == ctx.candidate_revision == controls.candidate_revision,
            'actual native nested Context source identities differ')
    require(type(ctx.sources) is source_type and ctx.sources.root == controls.root
            and ctx.sources.revision == controls.helper_revision,
            'actual native nested SourceBytes owner required')
    require(type(ctx.qa_source_authority) is dict
            and ctx.qa_source_authority.get('source_authority_active') is True
            and {SELF, CONTEXT} <= set(ctx.qa_source_authority['admitted_repository_python_sources']),
            'complete native nested Context source admission required')
    require(hosted.__file__ == str(controls.root / CONTEXT)
            and hosted.__som_raw_sha256__ == hashlib.sha256(ctx.sources.read(CONTEXT)).hexdigest()
            and ctx.sources.read(SELF) == Path(__file__).read_bytes(),
            'actual native nested Context module source differs')
    require(hosted.context() is ctx, 'current native nested Context getter differs')
    controls.verify_installed()
    return hosted


def install(ctx, target, capture_root):
    require(ctx.qa_mode == 'native-qa-v1' and {SELF, WRAPPER, RUST, HOST, STAMP} <=
            set(ctx.qa_source_authority['admitted_repository_python_sources']),
            'exact nested Rust/owned PostgreSQL source admission required')
    stamp_helper = ctx.module('typescript_nested_process_stamp', STAMP)
    hosts = ctx.module('typescript_nested_original_hosts', HOST)
    state = {'mode': 'native-qa-v1', 'original_cluster_scope': True, 'product_acceptance_passed': False}
    def cluster_gates(arguments):
        import psycopg  # The checked isolated observer payload is active first.
        work = ctx.work / 'typescript-regression/nested-rust-cluster'
        require(not work.exists(), 'fresh owned nested Rust cluster root required')
        work.mkdir(parents=True)
        pg = ctx.verify_postgres(); port = hosts.Ports().take()
        env = {**ctx.child_environment(), 'LC_ALL': 'C', 'TZ': 'UTC'}
        data, log = work / 'pgdata', work / 'postgres.log'
        initialize = [str(pg / 'initdb'), '-D', str(data), '-U', 'postgres', '-E', 'UTF8',
                      '--locale=C', '--auth=trust', '--no-sync']
        state['initialize'] = ctx.command(initialize, work, env, 'typescript-nested-rust-initdb')
        argv = [str(pg / 'postgres'), '-D', str(data), '-p', str(port), '-c', 'listen_addresses=127.0.0.1',
                '-c', "unix_socket_directories=", '-c', 'timezone=UTC', '-c', 'log_timezone=UTC',
                '-c', 'max_connections=300', '-c', 'fsync=off']
        proc, primary, started = None, None, None
        try:
            with log.open('xb') as stream:
                proc = subprocess.Popen(argv, cwd=data, env=env, stdin=subprocess.DEVNULL,
                    stdout=stream, stderr=subprocess.STDOUT, start_new_session=True)
            started = stamp_helper.process_stamp(proc.pid)
            expected = (pg / 'postgres').stat()
            require(started['pgrp'] == started['session'] == proc.pid and started['cwd'] == str(data)
                    and (started['executable_device'], started['executable_inode']) == (expected.st_dev, expected.st_ino),
                    'actual nested PostgreSQL process differs from its qualified source role')
            deadline = time.monotonic() + 30
            while True:
                require(proc.poll() is None, 'owned nested PostgreSQL exited before readiness')
                try:
                    with psycopg.connect(f'postgresql://postgres@127.0.0.1:{port}/postgres', connect_timeout=1):
                        pass
                    break
                except psycopg.OperationalError as error:
                    require(time.monotonic() < deadline, 'owned nested PostgreSQL readiness timed out: ' + str(error))
                    time.sleep(0.05)
            state['postgres'] = {'pid': proc.pid, 'argv': argv, 'cwd': str(data), 'start': started,
                                 'owned_socket': owned_socket(proc.pid, port)}
            save(work / 'pg-start.json', state['postgres'])
            hosted = _native_context(ctx)
            child_args = ['--tool', RUST, '--capture-root', str(capture_root / 'nested-rust-gates'), '--',
                          '--pg', f'127.0.0.1:{port}', *arguments]
            command = ctx.command(hosted.tool_command(ctx.repo / WRAPPER, child_args), ctx.repo / 'som-rs/som-targets/qa',
                                  ctx.child_environment(), 'typescript-nested-original-rust-products',
                                  timeout=18000, check=False)
            state['child_command'] = command
            # Retain inherited stream behavior, and also keep the command's full
            # independent files and actual PID/wait record in Context evidence.
            sys.stdout.buffer.write((ctx.work / command['stdout']).read_bytes()); sys.stdout.buffer.flush()
            sys.stderr.buffer.write((ctx.work / command['stderr']).read_bytes()); sys.stderr.buffer.flush()
            require(stamp_helper.process_stamp(proc.pid)['start_ticks'] == started['start_ticks'],
                    'nested PostgreSQL identity changed during original Rust gates')
            state['postgres']['final_owned_socket'] = owned_socket(proc.pid, port)
            return command['exit_code']
        except BaseException as error:
            primary = error
            raise
        finally:
            cleanup = {'child_started': proc is not None, 'wait_attempted': proc is not None, 'wait_completed': False,
                       'signals': [], 'cleanup_errors': []}
            if proc is not None:
                try:
                    if proc.returncode is None:
                        # This still-unreaped Popen handle owns the fresh session.
                        os.killpg(proc.pid, signal.SIGTERM); cleanup['signals'].append('SIGTERM')
                except ProcessLookupError:
                    pass
                except BaseException as error:
                    cleanup['cleanup_errors'].append({'stage': 'owned-stop', 'error': type(error).__name__, 'reason': str(error)})
                    if primary is None: primary = error
                try:
                    cleanup.update(wait_completed=True, exit_code=proc.wait(timeout=30))
                except subprocess.TimeoutExpired:
                    try:
                        require(proc.returncode is None, 'nested owned child was reaped before escalation')
                        os.killpg(proc.pid, signal.SIGKILL); cleanup['signals'].append('SIGKILL')
                    except BaseException as error:
                        cleanup['cleanup_errors'].append({'stage': 'owned-escalation', 'error': type(error).__name__, 'reason': str(error)})
                        if primary is None: primary = error
                    try:
                        cleanup.update(wait_completed=True, exit_code=proc.wait(timeout=10))
                    except BaseException as error:
                        cleanup['cleanup_errors'].append({'stage': 'final-wait', 'error': type(error).__name__, 'reason': str(error)})
                        if primary is None: primary = error
                except BaseException as error:
                    cleanup['cleanup_errors'].append({'stage': 'actual-wait', 'error': type(error).__name__, 'reason': str(error)})
                    if primary is None: primary = error
            state['cleanup'] = cleanup
            try:
                cleanup['remaining_listeners'] = stamp_helper.port_listeners(port)
            except BaseException as error:
                cleanup['remaining_listeners'] = None
                cleanup['cleanup_errors'].append({'stage': 'terminal-listener-check',
                    'error': type(error).__name__, 'reason': str(error)})
                if primary is None: primary = error
            if primary is not None:
                cleanup['primary_failure'] = failure_record(primary)
            try:
                save(work / 'pg-cleanup.json', cleanup); save(work / 'nested-rust-result.json', state)
            except BaseException as error:
                cleanup['cleanup_errors'].append({'stage': 'cleanup-evidence', 'error': type(error).__name__, 'reason': str(error)})
                if primary is None: primary = error
            if primary is not None:
                primary.__som_nested_rust_cleanup__ = cleanup
                failure = {'primary': None, 'traceback': None, 'failure': None, 'secondary': []}
                remember_failure(failure, 'nested_rust_cluster', primary)
                failure['secondary'].extend(cleanup['cleanup_errors'])
                raise_primary(failure)
            require(cleanup['wait_completed'] is True and not cleanup['cleanup_errors']
                    and not cleanup['remaining_listeners'], 'nested PostgreSQL actual wait/listener cleanup incomplete')
    target.cluster_gates = cluster_gates
    return state

"""Retain actual command, CLI and product HTTP bytes in an isolated QA child.

This adapter preserves returned objects and original assertions. It grants no
domain or product pass. Every process record comes from its actual Popen handle.
"""
from __future__ import annotations
import hashlib
import json
import locale
import os
from pathlib import Path
import subprocess
import sys
import traceback
import threading
from urllib.parse import urlsplit


def require(value, message):
    if not value:
        raise ValueError(message)


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


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('xb') as stream:
        stream.write((json.dumps(value, ensure_ascii=False, sort_keys=True,
                                 separators=(',', ':'), allow_nan=False) + '\n').encode())


class RawRuns:
    def __init__(self, root):
        self.root, self.rows, self.lock = root, [], threading.Lock()
        self.original = subprocess.run

    def install(self):
        owner = self
        def run(*popenargs, input=None, capture_output=False, timeout=None, check=False, **kwargs):
            # Commands with file streams retain their original implementation;
            # their complete files are retained by the owning source checker.
            if not capture_output and kwargs.get('stdout') != subprocess.PIPE and kwargs.get('stderr') != subprocess.PIPE:
                return owner.original(*popenargs, input=input, capture_output=capture_output,
                                      timeout=timeout, check=check, **kwargs)
            require(not kwargs.get('shell') and kwargs.get('executable') is None,
                    'raw command capture refuses shell/executable substitution')
            with owner.lock:
                index = len(owner.rows); owner.rows.append(None)
            directory = owner.root / f'{index:06d}'; directory.mkdir(parents=True)
            row = {'index': index, 'pid': None, 'wait_completed': False, 'exit_code': None,
                   'stdout_present': False, 'stderr_present': False, 'database_environment': {},
                   'application_environment': {}}
            if 'SOM_APP_ADDR' in kwargs.get('env', os.environ):
                row['application_environment']['SOM_APP_ADDR'] = kwargs.get('env', os.environ)['SOM_APP_ADDR']
            for key in ('DATABASE_URL', 'SOM_BEHAVIOR_DATABASE_URL', 'TEST_DATABASE_URL'):
                if key in kwargs.get('env', os.environ):
                    value = kwargs.get('env', os.environ)[key]
                    parsed = urlsplit(value.replace('postgresql+psycopg://', 'postgresql://', 1))
                    require(parsed.scheme == 'postgresql' and parsed.hostname == '127.0.0.1'
                            and parsed.port is not None and parsed.port not in {8765, 18765, 18978, 18979}
                            and parsed.username == 'postgres' and parsed.password is None
                            and not parsed.query and not parsed.fragment,
                            'only owned password-free loopback database environment may be retained')
                    row['database_environment'][key] = value
            text = bool(kwargs.pop('text', False) or kwargs.pop('universal_newlines', False))
            encoding, errors = kwargs.pop('encoding', None), kwargs.pop('errors', None)
            text = text or encoding is not None or errors is not None
            codec = encoding or ('utf-8' if sys.flags.utf8_mode else locale.getencoding())
            if capture_output:
                require('stdout' not in kwargs and 'stderr' not in kwargs, 'capture_output conflicts with explicit streams')
                kwargs['stdout'], kwargs['stderr'] = subprocess.PIPE, subprocess.PIPE
            if input is not None:
                require('stdin' not in kwargs, 'input conflicts with stdin')
                kwargs['stdin'] = subprocess.PIPE
                if text:
                    require(type(input) is str, 'text command input must be text')
                    input = input.encode(codec, errors or 'strict')
                require(type(input) is bytes, 'complete command input must be bytes')
                (directory / 'input.raw').write_bytes(input); row['input_sha256'] = sha(input)
            state = {'primary': None, 'traceback': None, 'failure': None, 'secondary': []}
            completed, stdout, stderr, proc = None, None, None, None
            row['cleanup_failures'], row['evidence_failures'] = [], []
            row['streams_complete'] = False
            try:
                proc = subprocess.Popen(*popenargs, **kwargs)
                row.update(pid=proc.pid, argv=[os.fspath(v) for v in proc.args],
                           cwd=os.fspath(kwargs.get('cwd') or Path.cwd()))
                try:
                    stdout, stderr = proc.communicate(input, timeout=timeout)
                    row['streams_complete'] = True
                except BaseException as error:
                    remember_failure(state, 'child_communicate', error)
                    if isinstance(error, subprocess.TimeoutExpired):
                        stdout = error.output if type(error.output) is bytes else stdout
                        stderr = error.stderr if type(error.stderr) is bytes else stderr
                    # Signal only the still-held, unreaped child handle. There is
                    # no implicit Popen context-manager exit or unbounded wait.
                    try:
                        if proc.returncode is None:
                            proc.kill()
                    except BaseException as cleanup:
                        row['cleanup_failures'].append(remember_failure(state, 'child_kill', cleanup))
                    try:
                        stdout, stderr = proc.communicate(timeout=10)
                        row['streams_complete'] = True
                    except BaseException as cleanup:
                        row['cleanup_failures'].append(remember_failure(state, 'child_cleanup_communicate', cleanup))
                        if isinstance(cleanup, subprocess.TimeoutExpired):
                            stdout = cleanup.output if type(cleanup.output) is bytes else stdout
                            stderr = cleanup.stderr if type(cleanup.stderr) is bytes else stderr
                try:
                    code = proc.wait(timeout=10)
                    row.update(exit_code=code, wait_completed=True)
                except BaseException as error:
                    row['cleanup_failures'].append(remember_failure(state, 'child_terminal_wait', error))
                if state['primary'] is None:
                    require(row['wait_completed'] is True and row['streams_complete'] is True,
                            'actual child stream completion and bounded wait required')
                    def returned(raw):
                        return (raw.decode(codec, errors or 'strict').replace('\r\n', '\n').replace('\r', '\n')
                                if text and raw is not None else raw)
                    completed = subprocess.CompletedProcess(proc.args, row['exit_code'], returned(stdout), returned(stderr))
                    if check:
                        completed.check_returncode()
            except BaseException as error:
                if state['primary'] is not error:
                    remember_failure(state, 'child_run', error)
            finally:
                if proc is not None:
                    seen = set()
                    for name in ('stdin', 'stdout', 'stderr'):
                        pipe = getattr(proc, name, None)
                        if pipe is not None and id(pipe) not in seen:
                            seen.add(id(pipe))
                            try:
                                pipe.close()
                            except BaseException as error:
                                row['cleanup_failures'].append(remember_failure(state, 'child_pipe_close_' + name, error))
                for name, raw in (('stdout', stdout), ('stderr', stderr)):
                    if raw is not None:
                        try:
                            require(type(raw) is bytes, 'raw command streams must stay bytes')
                            (directory / (name + '.raw')).write_bytes(raw)
                            row[name + '_present'], row[name + '_sha256'] = True, sha(raw)
                        except BaseException as error:
                            row['evidence_failures'].append(remember_failure(state, name + '_evidence', error))
                if state['primary'] is not None:
                    row['failure'] = state['failure']
                row['secondary_failures'] = state['secondary']
                try:
                    save(directory / 'result.json', row)
                except BaseException as error:
                    row['evidence_failures'].append(remember_failure(state, 'command_result_evidence', error))
                    row['failure'] = state['failure']
                with owner.lock:
                    owner.rows[index] = row
            raise_primary(state)
            return completed
        self.installed = run
        subprocess.run = run

    def close(self):
        if not hasattr(self, 'installed'):
            save(self.root / 'index.json', {'version': 'som.typescript-regression-raw-commands.v1',
                                          'rows': self.rows, 'product_acceptance_passed': False})
            return
        owned = subprocess.run is self.installed
        if owned:
            subprocess.run = self.original
        save(self.root / 'index.json', {'version': 'som.typescript-regression-raw-commands.v1',
                                      'rows': self.rows, 'product_acceptance_passed': False})
        require(owned and all(type(row) is dict for row in self.rows),
                'raw command capture changed or a worker did not finish')


def install_cli(target, root):
    original = target._run_cli_with_request_capture
    rows = []
    def capture(binary, base_url, cnl_path, selected):
        done, sent = original(binary, base_url, cnl_path, selected)
        index = len(rows); directory = root / f'{index:06d}'; directory.mkdir(parents=True)
        for name, raw in (('stdout', done.stdout), ('stderr', done.stderr), ('source', Path(cnl_path).read_bytes())):
            require(type(raw) is bytes, 'complete CLI byte streams required')
            (directory / (name + '.raw')).write_bytes(raw)
        requests = []
        for n, (route, body) in enumerate(sent):
            (directory / f'request-{n:03d}.raw').write_bytes(body)
            requests.append({'route': route, 'sha256': sha(body)})
        row = {'index': index, 'binary': binary, 'base_url': base_url, 'target': selected,
               'argv': [str(v) for v in done.args], 'exit_code': done.returncode,
               'source_sha256': sha(Path(cnl_path).read_bytes()),
               'stdout_sha256': sha(done.stdout), 'stderr_sha256': sha(done.stderr), 'requests': requests}
        save(directory / 'result.json', row); rows.append(row)
        save(root / f'checkpoint-{index:06d}.json', row)
        return done, sent
    target._run_cli_with_request_capture = capture
    return rows


def install_product_http(target, root):
    original = target.ts_suite.http_send
    rows = []
    def capture(base, method, path, body, content_type='application/json'):
        index = len(rows); directory = root / f'{index:06d}'; directory.mkdir(parents=True)
        require(body is None or type(body) is bytes, 'complete product request bytes required')
        if body is not None:
            (directory / 'request.raw').write_bytes(body)
        row = {'index': index, 'base': base, 'method': method, 'path': path,
               'content_type': content_type, 'body_is_null': body is None,
               'request_sha256': None if body is None else sha(body), 'complete': False}
        try:
            status, returned_type, raw = original(base, method, path, body, content_type)
            require(type(raw) is bytes, 'complete product response bytes required')
            (directory / 'response.raw').write_bytes(raw)
            row.update(status=status, response_content_type=returned_type, response_sha256=sha(raw), complete=True)
            return status, returned_type, raw
        finally:
            save(directory / 'result.json', row); rows.append(row)
    target.ts_suite.http_send = capture
    return rows

"""Capture the actual rows used by the unchanged TypeScript schema judge."""
from __future__ import annotations
import traceback
import sys
import json
from pathlib import Path


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


def typed(value):
    if value is None:
        return {'type': 'none'}
    for kind, cls in (('bool', bool), ('int', int), ('str', str)):
        if type(value) is cls:
            return {'type': kind, 'value': value}
    require(type(value) in {list, tuple}, 'schema result has an unadmitted SQL type')
    return {'type': 'list' if type(value) is list else 'tuple', 'value': [typed(item) for item in value]}


def install(checker, root):
    original_schema, original_psycopg = checker.rust_products.schema, checker.rust_products.psycopg
    rows, active = [], []
    class Connection:
        def __init__(self, connection, row):
            self.connection, self.row = connection, row
        def __enter__(self):
            self.connection.__enter__(); return self
        def __exit__(self, *args):
            return self.connection.__exit__(*args)
        def execute(self, sql, params=None):
            cursor = self.connection.execute(sql, params) if params is not None else self.connection.execute(sql)
            complete = cursor.fetchall()
            self.row['queries'].append({'sql': sql, 'params': typed(params), 'rows': typed(complete)})
            class Cursor:
                def __iter__(self): return iter(complete)
                def fetchall(self): return complete
            return Cursor()
    class Psycopg:
        def __getattr__(self, name): return getattr(original_psycopg, name)
        def connect(self, *args, **kwargs):
            if active:
                require(len(args) == 1 and not kwargs and args[0] == active[0]['url'],
                        'schema used an unexpected connection call')
            result = original_psycopg.connect(*args, **kwargs)
            return Connection(result, active[0]) if active else result
    def schema(url, excluded):
        require(not active, 'overlapping schema capture refused')
        roles = checker.__som_native_process_roles__
        pids = [pid for pid, role in roles.items() if role == 'postgres']
        require(len(pids) == 1, 'one actual owned PostgreSQL process required for schema capture')
        checker.__som_native_verify_process__(pids[0])
        row = {'index': len(rows), 'url': url, 'excluded': list(excluded), 'queries': [],
               'schema': None, 'complete': False, 'pg_owner_pid': pids[0]}
        active.append(row)
        failure = {'primary': None, 'traceback': None, 'failure': None, 'secondary': []}
        result = None
        try:
            result = original_schema(url, excluded)
            row.update(schema=result, complete=True)
        except BaseException as error:
            row['primary_failure'] = remember_failure(failure, 'original_schema_query', error)
        finally:
            active.clear()
            directory = Path(root) / f"{row['index']:06d}"
            try:
                directory.mkdir(parents=True)
            except BaseException as error:
                remember_failure(failure, 'schema_directory_evidence', error)
            row['secondary_failures'] = failure['secondary']
            try:
                with (directory / 'result.json').open('x') as stream:
                    stream.write(json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(',', ':')) + '\n')
            except BaseException as error:
                remember_failure(failure, 'schema_result_evidence', error)
            rows.append(row)
        raise_primary(failure)
        return result
    checker.rust_products.psycopg, checker.rust_products.schema = Psycopg(), schema
    return rows

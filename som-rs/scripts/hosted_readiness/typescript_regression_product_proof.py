"""Replay TypeScript schema, parity and strict product assertions from raw data.

This consumer does not run products, connect to a database, or grant acceptance.
Its only pass rows are the three named gaps in the archived TypeScript audit.
The caller must also verify the complete owner capture and all other gate rows.
"""
from __future__ import annotations
import traceback

import ast
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys
from types import ModuleType, SimpleNamespace
from urllib.parse import urlsplit

BASE = 'som-rs/scripts/hosted_readiness/'
SELF = BASE + 'typescript_regression_product_proof.py'
SUITE = 'som-rs/behavior/typescript/suite.py'
BASE_SUITE = 'som-rs/behavior/suite.py'
CHECKER = 'som-rs/som-targets/qa/check_ts_products.py'
RUST_CHECKER = 'som-rs/som-targets/qa/check_rust_products.py'
INVENTORY = 'som-rs/contracts/typescript-output-v1/case-inventory.json'
EXAMPLES = 'som-rs/contracts/typescript-output-v1/target-examples.json'
ARTIFACTS = 'som-rs/contracts/native-tools-v1/artifacts.json'
ROWS = ('ts.product_gates.5', 'ts.product_gates.6', 'ts.product_gates.7')
GATES = ('schema_equivalence', 'parity', 'strict_refusals')


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


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def parse(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result, 'duplicate product proof JSON member')
            result[key] = value
        return result
    return json.loads(raw, object_pairs_hook=pairs,
        parse_constant=lambda _: (_ for _ in ()).throw(ValueError('nonfinite product proof JSON')))


def read(path):
    path = Path(path).absolute()
    require('..' not in path.parts and not any(p.is_symlink() for p in (path, *path.parents)),
            'physical product proof path required')
    with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK), 'rb') as stream:
        before = os.fstat(stream.fileno())
        require(stat.S_ISREG(before.st_mode) and before.st_nlink == 1 and before.st_size <= 320 * 1024**2,
                'bounded single-link product proof file required')
        raw = stream.read(before.st_size + 1)
        after = os.fstat(stream.fileno())
    require(len(raw) == before.st_size and (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns,
            before.st_ctime_ns) == (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns),
            'product proof input changed during read')
    return raw


def members(root):
    root = Path(root).absolute()
    require(root.is_dir() and '..' not in root.parts
            and not any(path.is_symlink() for path in (root, *root.parents)), 'physical product proof directory required')
    result = set()
    for path in root.rglob('*'):
        info = path.lstat()
        require(stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode), 'product proof alias/special member refused')
        if stat.S_ISREG(info.st_mode):
            result.add(path.relative_to(root).as_posix())
    return result


class SourceModules:
    """Own fresh raw modules and the one fixed alias the original suite expects."""
    def __init__(self, ctx):
        self.ctx, self.owned, self.pins = ctx, [], {}
        self.owned_path_entries = []

    def raw(self, path):
        raw = self.ctx.sources.read(path)
        self.pins[path] = sha(raw)
        return raw

    def load(self, path, name):
        require(name not in sys.modules, 'preloaded product proof helper refused: ' + name)
        raw = self.raw(path)
        module = ModuleType(name)
        module.__file__, module.__package__, module.__cached__ = str(Path(self.ctx.repo) / path), '', None
        module.__som_raw_sha256__ = sha(raw)
        sys.modules[name] = module; self.owned.append((name, module))
        exec(compile(raw, module.__file__, 'exec', dont_inherit=True), module.__dict__)
        return module

    def original_functions(self, path, names, namespace):
        """Execute original AST function bodies with explicit data-only globals."""
        raw = self.raw(path); tree = ast.parse(raw)
        nodes = [node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                 and node.name in names]
        require({node.name for node in nodes} == set(names) and len(nodes) == len(names),
                'original product assertion function set changed')
        result = dict(namespace)
        source = ast.Module(body=[ast.ImportFrom(module='__future__', names=[ast.alias(name='annotations')], level=0),
                                  *nodes], type_ignores=[])
        ast.fix_missing_locations(source)
        exec(compile(source, str(Path(self.ctx.repo) / path), 'exec', dont_inherit=True), result)
        return SimpleNamespace(**result)

    def suite(self):
        base = self.load(BASE_SUITE, 'som_behavior_base_suite')
        path_list, previous = sys.path, tuple(sys.path)
        expected = str(Path(self.ctx.repo) / 'som-rs/behavior')
        try:
            suite = self.load(SUITE, '_som_typescript_product_proof_suite')
            require(suite.base_suite is base, 'original suite did not use its fresh bound base')
            return suite
        finally:
            if expected not in previous and sys.path is path_list:
                added = [value for value in sys.path if value == expected]
                if len(added) == 1:
                    self.owned_path_entries.append((path_list, added[0]))

    def constant(self, path, name):
        nodes = ast.parse(self.raw(path)).body
        values = [node.value for node in nodes if
                  (isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.target.id == name)
                  or (isinstance(node, ast.Assign) and len(node.targets) == 1
                      and isinstance(node.targets[0], ast.Name) and node.targets[0].id == name)]
        require(len(values) == 1, 'unique original product constant required')
        value = values[0]
        if isinstance(value, ast.Call):
            require(isinstance(value.func, ast.Name) and value.func.id == 'frozenset'
                    and len(value.args) == 1 and not value.keywords, 'unexpected original product constant expression')
            return frozenset(ast.literal_eval(value.args[0]))
        return ast.literal_eval(value)

    def close(self):
        failures = []
        for name, value in reversed(self.owned):
            if sys.modules.get(name) is value:
                del sys.modules[name]
            else:
                failures.append(name)
        self.owned.clear()
        for path_list, owned in reversed(self.owned_path_entries):
            positions = [index for index, value in enumerate(sys.path) if value is owned]
            if sys.path is path_list and len(positions) == 1:
                del sys.path[positions[0]]
            else:
                failures.append('suite sys.path entry replaced by later owner')
        self.owned_path_entries.clear()
        require(not failures, 'product proof module/path ownership changed: ' + ', '.join(failures))


def typed(value):
    """Decode the exact scalar/list/tuple types returned by the schema queries."""
    require(type(value) is dict and 'type' in value, 'typed SQL value required')
    kind = value['type']
    if kind == 'none':
        require(set(value) == {'type'}, 'SQL null encoding changed'); return None
    require(set(value) == {'type', 'value'}, 'typed SQL scalar/container value required')
    data = value['value']
    if kind in {'bool', 'int', 'str'}:
        require(type(data) is {'bool': bool, 'int': int, 'str': str}[kind], 'SQL scalar type changed')
        return data
    require(kind in {'list', 'tuple'} and type(data) is list, 'unsupported SQL result type')
    result = [typed(item) for item in data]
    return tuple(result) if kind == 'tuple' else result


class SchemaReplay:
    def __init__(self, row):
        require(row['complete'] is True and type(row['queries']) is list, 'complete schema query capture required')
        self.row, self.index, self.connections = row, 0, 0

    def connect(self, url):
        require(self.connections == 0 and url == self.row['url'], 'schema connection identity/order changed')
        self.connections += 1
        owner = self
        class Connection:
            def __enter__(self): return self
            def __exit__(self, *args): return False
            def execute(self, sql, params=None):
                require(owner.index < len(owner.row['queries']), 'schema issued an unrecorded query')
                item = owner.row['queries'][owner.index]; owner.index += 1
                require(set(item) == {'sql', 'params', 'rows'} and item['sql'] == sql
                        and typed(item['params']) == params, 'original schema SQL/parameters differ')
                rows = typed(item['rows'])
                require(type(rows) is list, 'complete schema cursor rows required')
                class Cursor:
                    def __iter__(self): return iter(rows)
                    def fetchall(self): return rows
                return Cursor()
        return Connection()

    def finish(self):
        require(self.connections == 1 and self.index == len(self.row['queries']), 'unconsumed schema connection/query')


class ProductReplay:
    def __init__(self, root, rows):
        self.root, self.rows, self.index = Path(root), rows, 0
        expected = set()
        for n, row in enumerate(rows):
            require(row['index'] == n and row['complete'] is True, 'complete ordered product HTTP rows required')
            directory = self.root / f'{n:06d}'
            require(parse(read(directory / 'result.json')) == row, 'product HTTP row changed')
            expected |= {f'{n:06d}/result.json', f'{n:06d}/response.raw'}
            require(sha(read(directory / 'response.raw')) == row['response_sha256'], 'product HTTP response bytes changed')
            if row['body_is_null']:
                require(row['request_sha256'] is None, 'null product request hash differs')
            else:
                require(sha(read(directory / 'request.raw')) == row['request_sha256'], 'product request bytes changed')
                expected.add(f'{n:06d}/request.raw')
            endpoint(row['base'])
        require(members(self.root) == expected, 'complete product HTTP member universe differs')

    def sender(self, base):
        endpoint(base)
        def send(method, path, body, content_type):
            require(self.index < len(self.rows), 'unrecorded product behavior request')
            row = self.rows[self.index]; directory = self.root / f'{self.index:06d}'
            request = None if row['body_is_null'] else read(directory / 'request.raw')
            require((row['base'], row['method'], row['path'], request, row['content_type']) ==
                    (base, method, path, body, content_type), 'source-derived product request/order differs')
            self.index += 1
            return row['status'], row['response_content_type'], read(directory / 'response.raw')
        return send

    def next_base(self):
        require(self.index < len(self.rows), 'missing product behavior request')
        return self.rows[self.index]['base']

    def finish(self):
        require(self.index == len(self.rows), 'unconsumed product behavior HTTP exchange')


def endpoint(base):
    url = urlsplit(base)
    require(url.scheme == 'http' and url.hostname == '127.0.0.1' and url.port is not None
            and url.port not in {8765, 18765, 18978, 18979} and url.path in {'', '/'}
            and not url.query and not url.fragment and not url.username and not url.password,
            'explicit isolated product loopback port required')
    return url.port


def database(url):
    value = urlsplit(url.replace('postgresql+psycopg://', 'postgresql://', 1))
    require(value.scheme == 'postgresql' and value.hostname == '127.0.0.1' and value.port is not None
            and value.port not in {8765, 18765, 18978, 18979} and value.username == 'postgres'
            and value.password is None and not value.query and not value.fragment
            and re.fullmatch('/[a-z0-9_]+', value.path), 'explicit owned password-free database endpoint required')
    return value.port, value.path[1:]


def commands(root):
    index = parse(read(root / 'index.json'))
    require(index['version'] == 'som.typescript-regression-raw-commands.v1'
            and index['product_acceptance_passed'] is False, 'actual raw command index required')
    expected = {'index.json'}
    for n, row in enumerate(index['rows']):
        directory = root / f'{n:06d}'
        require(row['index'] == n and row['pid'] > 0 and row['wait_completed'] is True
                and type(row['exit_code']) is int and parse(read(directory / 'result.json')) == row
                and not any(key in row for key in ('failure', 'cleanup_failure', 'secondary_failure'))
                and not row.get('cleanup_failures') and not row.get('evidence_failures') and not row.get('secondary_failures')
                and ('streams_complete' not in row or row['streams_complete'] is True),
                'actual nested command completion differs')
        expected.add(f'{n:06d}/result.json')
        for name in ('stdout', 'stderr', 'input'):
            if name + '_sha256' in row:
                require(sha(read(directory / (name + '.raw'))) == row[name + '_sha256'], 'nested command raw stream differs')
                expected.add(f'{n:06d}/{name}.raw')
        require(type(row.get('database_environment')) is dict
                and set(row['database_environment']) <=
                    {'DATABASE_URL', 'SOM_BEHAVIOR_DATABASE_URL', 'TEST_DATABASE_URL'},
                'exact narrow command database environment capture required')
        for value in row['database_environment'].values():
            database(value)
    require(members(root) == expected, 'complete nested migration command member universe differs')
    return index['rows']


def processes(report, capture, node_sha):
    by_pid = {}
    for row in report['processes']:
        pid, role, start = row['pid'], row['role'], row['native_owned_start']
        require(type(pid) is int and pid > 0 and pid not in by_pid
                and role in {'postgres', 'typescript_product', 'python_product', 'typescript_startup_probe'},
                'unique source-owned native process role required')
        raw = start['proc_stat_raw'].encode('utf-8')
        fields = raw.rsplit(b')', 1)[1].split()
        require(int(raw.split(b' ', 1)[0]) == pid == start['pid'] == start['pgrp'] == start['session']
                and start['state'] == fields[0].decode() != 'Z'
                and start['pgrp'] == int(fields[2]) and start['session'] == int(fields[3])
                and start['start_ticks'] == int(fields[19]), 'actual native process start identity differs')
        raw_argv = base64.b64decode(start['cmdline_raw_b64'], validate=True)
        require(raw_argv.endswith(b'\0') and not raw_argv.startswith(b'\0'),
                'complete kernel process argv bytes required')
        argv = [part.decode('utf-8', 'strict') for part in raw_argv[:-1].split(b'\0')]
        require(argv == row['native_actual_argv'] and argv and row['command'], 'actual native process argv differs')
        require(type(start.get('environment')) is dict and set(start['environment']) <=
                {'SOM_APP_ADDR', 'DATABASE_URL', 'SOM_BEHAVIOR_DATABASE_URL', 'TEST_DATABASE_URL'},
                'actual narrow native process environment required')
        for name, value in start['environment'].items():
            if name != 'SOM_APP_ADDR':
                database(value)
        require(Path(capture['original_run']) / 'native-products' in Path(row['expected_cwd']).parents
                and row['expected_cwd'] == row['initial_cwd'] == start['cwd']
                and row['initial_cwd_verified'] is True and row['stopped'] is True
                and row['wait_completed'] is True and type(row['wait_exit_code']) is int
                and row['wait_exit_code'] == row['exit_code'], 'owned process CWD/actual terminal wait differs')
        require(row['stop_cwd_verified'] is True or row['stop_cwd_verified'] is None,
                'owned process signaling failed CWD verification')
        require(re.fullmatch('[0-9a-f]{64}', row['native_executable_sha256'])
                and type(start['executable_device']) is int and type(start['executable_inode']) is int,
                'qualified executable identity is incomplete')
        if role == 'python_product':
            command = row['command']
            prefix = [command[0], '-I', '-S', '-B',
                      str(Path(capture['context']['repo']) / (BASE + 'run_full_products.py')),
                      '--native-python-bootstrap', '--']
            require(argv == prefix + command[1:] and command[1:3] == ['-m', 'uvicorn'],
                    'actual isolated Python product argv differs from the original app command')
        else:
            require(argv == row['command'], 'actual native Node/PostgreSQL argv differs')
            if role.startswith('typescript_'):
                require(row['native_executable_sha256'] == node_sha and argv[1:] == ['src/main.ts'],
                        'actual TypeScript product Node/source command differs')
        by_pid[pid] = row
    require(len([row for row in by_pid.values() if row['role'] == 'postgres']) == 1,
            'one owned qualified PostgreSQL parent required')
    return by_pid


def product_owner(by_pid, role, key, base):
    port = endpoint(base)
    cwd_name = ('py_parity_' if role == 'python_product' else 'ts_') + key
    matches = [row for row in by_pid.values() if row['role'] == role and Path(row['expected_cwd']).name == cwd_name]
    require(len(matches) == 1, 'unique actual product process role/CWD required')
    owner = matches[0]
    if role == 'python_product':
        argv = owner['native_actual_argv']
        require('--port' in argv and argv[argv.index('--port') + 1] == str(port),
                'Python behavior endpoint differs from the actual isolated app argv')
    else:
        require(owner['native_owned_start']['environment'].get('SOM_APP_ADDR') == '127.0.0.1:' + str(port),
                'TypeScript behavior endpoint differs from the actual owned process environment')
    return owner['pid']


def schema_migration_join(rows, product, key, all_commands, by_pid):
    used = []
    for role, row in zip(('ts', 'py'), rows):
        port, db = database(row['url'])
        require(re.fullmatch('ts76_schema_' + role + '_' + re.escape(key) + '_[0-9]+', db),
                'schema query database is outside its source-derived product role')
        pg = by_pid.get(row['pg_owner_pid'])
        require(pg is not None and pg['role'] == 'postgres' and '-p' in pg['command']
                and pg['command'][pg['command'].index('-p') + 1] == str(port),
                'schema query is outside the actual owned PostgreSQL endpoint')
        found = []
        for command in all_commands:
            environment = command['database_environment']
            if not any(database(value) == (port, db) for value in environment.values()):
                continue
            cwd = Path(command['cwd'])
            if role == 'ts':
                matches = command['argv'] == product['gates']['schema_equivalence']['command'] and cwd.name == 'ts_' + key
            else:
                argv = command['argv']
                matches = (cwd.name == 'py_schema_' + key and len(argv) > 8
                           and argv[1:4] == ['-I', '-S', '-B']
                           and '--native-python-bootstrap' in argv
                           and argv[-3].endswith('/_alembic/migrate.py')
                           and argv[-2].endswith('/_alembic') and argv[-1] == str(cwd / 'alembic/versions'))
            if matches:
                require(command['exit_code'] == 0 and command['stdout_present'] is True
                        and command['stderr_present'] is True, 'schema migration command failed or lacks complete streams')
                found.append(command['index'])
        require(len(found) == 1, 'unique actual source migration command/database join required')
        used.append(found[0])
    return used


def source_products(source, owner_root, capture):
    inventory = parse(source.raw(INVENTORY))
    wanted = [row['id'] for kind in ('public_examples', 'version_positive_fixtures', 'coverage_cases', 'name_cases')
              for row in inventory[kind] if row.get('typescript', 'in_scope') == 'in_scope']
    wanted += [row['id'] for row in inventory['legacy_in_scope']]
    require(len(wanted) == len(set(wanted)) == inventory['counts']['acceptance_total'] == 125,
            'complete frozen125 product scope required')
    entries = parse(read(owner_root / 'capture/bundles/products/manifest.json'))['products']
    require([entry['id'] for entry in entries] == wanted, 'product manifest order/universe differs from source')
    metadata = {row['id']: (kind, row) for kind in ('public_examples', 'version_positive_fixtures',
                'coverage_cases', 'name_cases', 'legacy_in_scope') for row in inventory[kind]}
    examples = parse(source.raw(EXAMPLES))
    names = {row['id']: row for row in examples['name_cases']}
    coverage = parse(source.raw('som-rs/tests/rust-output-v1/cases/index.json'))
    coverage = {row['id']: row for row in coverage['cases']}
    tape_root = owner_root / 'capture/raw-checkers/04'
    tape = parse(read(tape_root / 'index.json'))['rows']
    exchanges = []
    for n, row in enumerate(tape):
        require(row['index'] == n and row['complete'] is True, 'complete source product compiler tape required')
        request, response = read(tape_root / f'{n:06d}/request.raw'), read(tape_root / f'{n:06d}/response.raw')
        require(sha(request) == row['request_sha256'] and sha(response) == row['response_sha256'],
                'source product compiler tape bytes changed')
        exchanges.append((row, request, response))
    for entry in entries:
        cid, prompt = entry['id'], entry['prompt']
        require(re.fullmatch('[A-Za-z0-9_-]+', cid) and type(prompt) is dict, 'safe source product identity required')
        require(entry['product'] == str(Path(capture['original_run']) / 'bundles/products' / cid),
                'original product source path differs')
        prefix = f'bundle_checks__{cid}__'
        compiled = read(owner_root / 'capture/bundles/responses' / (prefix + 'compile_json_1.json'))
        bundle = parse(compiled)['bundle']
        require(bundle['target'] == 'typescript' and bundle['prompt'] == prompt
                and bundle['hashes']['prompt_sha256'] == sha(canonical(prompt)), 'source product prompt differs')
        require(any(row['status'] == 200 and raw == compiled and row['url'].endswith('/v1/compile')
                    and parse(request) == {'format': 'som.prompt-json.v1', 'target': 'typescript', 'prompt': prompt}
                    for row, request, raw in exchanges), 'product compile is not joined to exact current raw HTTP')
        assembled = read(owner_root / 'capture/bundles/responses' / (prefix + 'assemble_1.json'))
        files = parse(assembled)['files']
        require(any(row['status'] == 200 and raw == assembled and row['url'].endswith('/v1/assemble')
                    and parse(request) == {'bundle': bundle} for row, request, raw in exchanges),
                'product files are not joined to exact current raw assemble')
        product = owner_root / 'capture/bundles/products' / cid
        require(type(files) is dict and len(files) == 11 and members(product) == set(files)
                and all(type(text) is str and read(product / path) == text.encode() for path, text in files.items()),
                'full assembled product file universe/bytes differ')
        kind, row = metadata[cid]
        if kind == 'public_examples':
            raw = source.raw(row['prompt'])
            require(sha(raw) == row['prompt_file_sha256'] and parse(raw) == prompt, 'public source prompt differs')
        elif kind == 'coverage_cases':
            path = 'som-rs/tests/rust-output-v1/cases/' + coverage[cid]['prompt']
            raw = source.raw(path)
            require(sha(raw) == row['prompt_file_sha256'] and parse(raw) == prompt, 'coverage source prompt differs')
        elif kind in {'version_positive_fixtures', 'legacy_in_scope'}:
            require(sha(canonical(prompt)) == row['prompt_sha256'], 'source-pinned fixture/legacy prompt differs')
        else:
            body = names[cid]['body'].encode()
            require(sha(body) == row['body_sha256'], 'source name case body differs')
            parsed = parse(body)
            if 'prompt' in parsed:
                require(parsed['prompt'] == prompt, 'source typed name prompt differs')
            else:
                require(any(raw == compiled and request == body for _, request, raw in exchanges),
                        'source CNL name body is not joined to derived product prompt')
        entry['files_sha256'] = sha(canonical({path: sha(text.encode()) for path, text in files.items()}))
    return entries


def assert_gate(actual, expected):
    require(actual == expected and expected['passed'] is True and expected['status'] == 'passed'
            and expected['exit_code'] == 0, 'original source product gate assertion differs')


def verify(ctx, owner_root, capture):
    authority = ctx.qa_source_authority
    require(ctx.qa_mode == 'native-qa-v1' and authority['source_authority_active'] is True
            and authority['helper_revision'] == authority['candidate_revision'] ==
                ctx.helper_revision == ctx.candidate_revision, 'exact native source admission required')
    require({SELF, SUITE, BASE_SUITE, CHECKER, RUST_CHECKER} <=
            set(authority['admitted_repository_python_sources']), 'complete product proof source closure required')
    owner_root = Path(owner_root)
    data = parse(read(owner_root / 'context.input.json'))
    require(data == ctx.data and data['qa_mode'] == 'native-qa-v1'
            and data['helper_revision'] == data['candidate_revision'] == ctx.helper_revision,
            'actual checked Context copy differs from the product consumer Context')
    if 'context' in capture:
        require(capture['context'] == data, 'caller product Context view differs from captured original')
    capture = {**capture, 'context': data}
    source = SourceModules(ctx)
    primary = None
    try:
        source.raw(SELF)
        suite = source.suite()
        checker = source.original_functions(CHECKER,
            {'make_gate', 'schema_differences', 'evaluate_schema_equivalence', 'evaluate_parity', 'evaluate_strict_refusals'},
            {'json': json, 'ts_suite': suite,
             'EXCLUDED_SCHEMA_TABLES': source.constant(CHECKER, 'EXCLUDED_SCHEMA_TABLES'),
             'DIVERGENCE_IDS': source.constant(CHECKER, 'DIVERGENCE_IDS')})
        entries = source_products(source, owner_root, capture)
        report_raw = read(owner_root / 'capture/ts-products/report.json'); report = parse(report_raw)
        require([row['id'] for row in report['products']] == [entry['id'] for entry in entries],
                'all source product report identities/order required')
        root = owner_root / 'capture/raw-checkers/05'
        postcheck = parse(read(root / 'checker-postcheck.json'))
        require(postcheck['tool'] == CHECKER and postcheck['source_sha256'] == source.pins[CHECKER]
                and postcheck['entry_executed'] is True and postcheck['exit_code'] == 0
                and postcheck['postcheck_complete'] is True, 'actual source product entry/postcheck differs')
        replay = ProductReplay(root / 'product-http', postcheck['product_http_rows'])
        actual_commands = commands(root / 'raw-commands')
        artifacts = parse(source.raw(ARTIFACTS))
        nodes = [row for row in artifacts['tools'] if row['tool'] == 'node'
                 and row['host'] == 'x86_64-unknown-linux-gnu' and row['version'] == '26.5.0']
        require(len(nodes) == 1 and report['toolchain']['node_sha256'] == nodes[0]['executable_sha256'],
                'actual source-qualified native Node identity differs')
        owned_processes = processes(report, capture, nodes[0]['executable_sha256'])
        schema_rows = postcheck.get('schema_rows')
        require(type(schema_rows) is list and len(schema_rows) == 2 * len(entries), 'every actual schema query capture required')
        require(members(root / 'schema-raw') == {f'{n:06d}/result.json' for n in range(len(schema_rows))},
                'complete schema raw member universe differs')
        proofs = []
        for n, (entry, product) in enumerate(zip(entries, report['products'])):
            cid, prompt, gates = entry['id'], entry['prompt'], product['gates']
            require(product['files_sha256'] == entry['files_sha256'], 'product commands did not use exact assembled source files')
            key = re.sub('[^a-z0-9]+', '_', cid.lower()).strip('_')
            schemas = []
            for k, role in enumerate(('typescript', 'python')):
                index = 2 * n + k; row = schema_rows[index]
                require(row['index'] == index and parse(read(root / 'schema-raw' / f'{index:06d}/result.json')) == row
                        and row['excluded'] == ['alembic_version', '_som_ts_migrations'], 'schema raw identity/exclusions differ')
                sql = SchemaReplay(row)
                original = source.original_functions(RUST_CHECKER, {'schema'}, {'psycopg': sql})
                value = original.schema(row['url'], tuple(row['excluded'])); sql.finish()
                require(value == row['schema'] == gates['schema_equivalence'][role + '_schema'],
                        'original raw schema SQL result differs from reported migration schema')
                schemas.append(value)
            gate = gates['schema_equivalence']
            expected = checker.evaluate_schema_equivalence(*schemas, gate['command'], gate['duration_ms'])
            assert_gate(gate, expected)
            migrations = schema_migration_join(schema_rows[2 * n:2 * n + 2], product, key,
                                               actual_commands, owned_processes)
            py_base = replay.next_base(); python_run = suite.run(prompt, py_base, send=replay.sender(py_base))
            ts_base = replay.next_base(); ts_run = suite.run(prompt, ts_base, send=replay.sender(ts_base))
            require(py_base != ts_base, 'Python and TypeScript behavior endpoints must be separate owned processes')
            owners = [product_owner(owned_processes, role, key, base) for role, base in
                      (('python_product', py_base), ('typescript_product', ts_base))]
            script_steps, script_failures = len(ts_run.transcript), len(ts_run.failures)
            ts_run.strict()
            for language, result in (('py', python_run), ('ts', ts_run)):
                raw = read(owner_root / 'capture/ts-products/raw' / f'{language}_{key}.json')
                require(parse(raw) == {'raw': result.raw, 'transcript': result.transcript, 'failures': result.failures},
                        'source-replayed complete product raw/transcript/assertions differ')
                require(sha(raw) == gates['parity']['python_raw_sha256' if language == 'py' else 'typescript_raw_sha256'],
                        'product parity raw hash differs')
            parity = suite.TsRun(ts_run.prof, ts_run.send)
            parity.transcript, parity.raw, parity.failures = (ts_run.transcript[:script_steps],
                ts_run.raw[:script_steps], ts_run.failures[:script_failures])
            differences = suite.compare(python_run.transcript, parity.transcript)
            gate = gates['parity']
            expected = checker.evaluate_parity(python_run, parity, differences, gate['duration_ms'], gate['command'],
                gate['python_raw_sha256'], gate['typescript_raw_sha256'])
            assert_gate(gate, expected)
            gate = gates['strict_refusals']
            expected = checker.evaluate_strict_refusals(ts_run.transcript[script_steps:],
                ts_run.failures[script_failures:], gate['duration_ms'], gate['command'])
            assert_gate(gate, expected)
            proofs.append({'id': cid, 'files_sha256': entry['files_sha256'], 'schema_pair': [2 * n, 2 * n + 1],
                           'schema_migration_command_indices': migrations, 'product_owner_pids': owners,
                           'parity_steps': script_steps, 'strict_steps': len(ts_run.transcript) - script_steps})
        replay.finish()
        return {'version': 'som.typescript-regression-product-raw-proof.v1',
                'replacement_rows': {row: {'complete': True, 'all_required_passed': True} for row in ROWS},
                'helper_revision': ctx.helper_revision, 'candidate_revision': ctx.candidate_revision,
                'report_sha256': sha(report_raw), 'bound_source_sha256': dict(sorted(source.pins.items())),
                'products': proofs, 'raw_product_http_calls': replay.index,
                'process_and_migration_joins_complete': True,
                'required_external_proofs': ['common current Context runtime/source/tool postclose proof',
                                            'all remaining owner domain and whole-capture proofs'],
                'product_acceptance_passed': False}
    except BaseException as error:
        primary = error
        raise
    finally:
        try:
            source.close()
        except BaseException as error:
            if primary is None:
                raise
            primary.__som_typescript_product_proof_cleanup__ = failure_record(error)
            failure = {'primary': None, 'traceback': None, 'failure': None, 'secondary': []}
            remember_failure(failure, 'original_product_assertion', primary)
            remember_failure(failure, 'product_source_cleanup', error)
            raise_primary(failure)

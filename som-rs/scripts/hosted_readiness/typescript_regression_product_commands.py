"""Recompute all remaining TypeScript command/startup gates from complete raw."""
from __future__ import annotations
import traceback
import sys
import ast
import hashlib
import json
from pathlib import Path
import re
from types import SimpleNamespace
from urllib.parse import urlsplit

BASE = 'som-rs/scripts/hosted_readiness/'
SELF = BASE + 'typescript_regression_product_commands.py'
PRODUCT = BASE + 'typescript_regression_product_proof.py'
CHECKER = 'som-rs/som-targets/qa/check_ts_products.py'
HOST = 'som-rs/behavior/typescript/host.py'


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


def source_command_vectors(raw, product_root, node, npm_cli):
    """Evaluate only exact source-owned command assignment ASTs, never a gate."""
    tree = ast.parse(raw)
    functions = {item.name: item for item in tree.body if isinstance(item, ast.FunctionDef)}
    require({'check_one_product', 'run_startup_gate'} <= set(functions), 'original command constructors required')
    names = {'install_cmd', 'tsc_cmd', 'ts_files', 'extra_ts', 'prettier_cmd', 'test_cmd', 'migrate_cmd'}
    nodes = [item for item in functions['check_one_product'].body if isinstance(item, ast.Assign)
             and len(item.targets) == 1 and isinstance(item.targets[0], ast.Name) and item.targets[0].id in names]
    require(len(nodes) == len(names) and {item.targets[0].id for item in nodes} == names,
            'complete exact original product command assignments required')
    startup = [item for item in functions['run_startup_gate'].body if isinstance(item, ast.Assign)
               and len(item.targets) == 1 and isinstance(item.targets[0], ast.Name) and item.targets[0].id == 'command']
    require(len(startup) == 1, 'exact original startup command assignment required')
    selected = ast.Module(body=[*nodes, *startup], type_ignores=[]); ast.fix_missing_locations(selected)
    values = {'ts_host': SimpleNamespace(NODE_BIN=node, NPM_INVOCATION=[node, npm_cli]),
              'product_work': Path(product_root)}
    exec(compile(selected, CHECKER, 'exec', dont_inherit=True), values)
    return {name: values[key] for name, key in {'install': 'install_cmd', 'typecheck': 'tsc_cmd',
        'format_check': 'prettier_cmd', 'test': 'test_cmd', 'startup': 'command',
        'schema_equivalence': 'migrate_cmd'}.items()}


def command_records(commands, cwd, name, gate, expected):
    require(gate['command'] == expected[name], 'gate argv differs from independent original source command: ' + name)
    rows = [row for row in commands if row['cwd'] == cwd and row['argv'] == expected[name]]
    require(len(rows) == (2 if name in {'test', 'startup'} else 1),
            'actual command count/argv differs from independent original source: ' + name)
    return rows


def verify(ctx, root, capture, runtime):
    require({SELF, PRODUCT, CHECKER, HOST} <= set(ctx.qa_source_authority['admitted_repository_python_sources'])
            and ctx.qa_mode == 'native-qa-v1', 'exact product command assertion source admission required')
    product = ctx.module('typescript_full_product_command_data', PRODUCT)
    source = product.SourceModules(ctx)
    primary = None
    try:
        source.raw(SELF)
        entries = product.source_products(source, root, capture)
        report = product.parse(product.read(root / 'capture/ts-products/report.json'))
        commands_root = root / 'capture/raw-checkers/05/raw-commands'
        commands = product.commands(commands_root)
        nodes = product.parse(source.raw(product.ARTIFACTS))['tools']
        node = next(row['executable_sha256'] for row in nodes if row['tool'] == 'node'
                    and row['host'] == 'x86_64-unknown-linux-gnu' and row['version'] == '26.5.0')
        processes = product.processes(report, capture, node)
        for value in processes.values():
            terminal = value['native_terminal']
            require(terminal['pid'] == value['pid'] and terminal['wait_exit_code'] == value['wait_exit_code']
                    and terminal['remaining_listeners'] == [] and not value.get('native_cleanup_failures'),
                    'actual owned process wait/port cleanup incomplete')
            if value['role'] == 'postgres':
                require(value['native_executable_sha256'] == runtime['qualified_postgres_sha256'],
                        'actual PostgreSQL process differs from independently qualified runtime')
            if value['role'] == 'python_product':
                require(value['native_executable_sha256'] == runtime['qualified_python_sha256'],
                        'actual reference Python differs from independently qualified runtime')
        pattern = next(node.value for node in ast.parse(source.raw(CHECKER)).body
                       if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'NODE_TEST_STAT'
                                                              for t in node.targets))
        require(isinstance(pattern, ast.Call) and isinstance(pattern.func, ast.Attribute)
                and pattern.func.attr == 'compile' and len(pattern.args) == 2 and not pattern.keywords
                and isinstance(pattern.args[1], ast.Attribute) and isinstance(pattern.args[1].value, ast.Name)
                and pattern.args[1].value.id == 're' and pattern.args[1].attr == 'MULTILINE',
                'exact original Node result parser required')
        regex = re.compile(ast.literal_eval(pattern.args[0]), re.MULTILINE)
        names = {'make_gate', 'non_empty_lines', 'parse_node_test_counts', 'evaluate_install', 'evaluate_typecheck',
                 'evaluate_format_check', 'evaluate_test_gate', 'evaluate_lock_unchanged', 'evaluate_startup'}
        assertions = source.original_functions(CHECKER, names, {'re': re, 'NODE_TEST_STAT': regex})
        snapshots = source.original_functions(HOST, {'snapshot_outside_node_modules'}, {'hashlib': hashlib})
        references = {name: product.sha(source.raw('som-rs/contracts/typescript-output-v1/reference/' + name))
                      for name in ('package.json', 'package-lock.json', 'tsconfig.json')}
        profile = product.parse(source.raw(BASE + 'inputs/typescript-regression-inputs-v1.json'))
        locators = profile['native_typescript_tools']
        require(set(locators) == {'node', 'npm_cli', 'npm_root'}
                and all(type(value) is str and value and not value.startswith('/')
                        and all(part not in {'', '.', '..'} for part in value.split('/')) for value in locators.values()),
                'source-admitted exact native Node/npm tool locators required')
        tool_root = Path(capture['input_roots']['node_tools'])
        node_path, npm_cli = str(tool_root / locators['node']), str(tool_root / locators['npm_cli'])
        require(profile['roles']['node_tools']['files'][locators['node']]['sha256'] == node,
                'command Node identity differs from independent official source authority')
        used, proofs = set(), []
        def result(row, duration):
            used.add(row['index']); directory = commands_root / f"{row['index']:06d}"
            require(row['stdout_present'] is True and row['stderr_present'] is True, 'complete product command streams required')
            return {'command': row['argv'], 'exit_code': row['exit_code'], 'duration_ms': duration,
                'stdout': product.read(directory / 'stdout.raw').decode('utf-8', 'strict').replace('\r\n', '\n').replace('\r', '\n'),
                'stderr': product.read(directory / 'stderr.raw').decode('utf-8', 'strict').replace('\r\n', '\n').replace('\r', '\n')}
        for entry, row in zip(entries, report['products']):
            cid, gates = entry['id'], row['gates']
            key = re.sub('[^a-z0-9]+', '_', cid.lower()).strip('_')
            owned = [value for value in processes.values() if value['role'] == 'typescript_product'
                     and Path(value['expected_cwd']).name == 'ts_' + key]
            require(len(owned) == 1, 'one actual TypeScript product CWD required')
            cwd = owned[0]['expected_cwd']
            relative = Path(cwd).relative_to(capture['original_run'])
            actual_root = root / 'capture' / relative
            expected_argv = source_command_vectors(source.raw(CHECKER), actual_root, node_path, npm_cli)
            product_used = set()
            def matching(name):
                rows = command_records(commands, cwd, name, gates[name], expected_argv)
                product_used.update(value['index'] for value in rows)
                return rows
            for name, evaluator in (('install', assertions.evaluate_install), ('typecheck', assertions.evaluate_typecheck),
                                    ('format_check', assertions.evaluate_format_check)):
                records = matching(name); require(len(records) == 1, 'unique actual product command required: ' + name)
                require(records[0]['database_environment'] == {} and records[0]['application_environment'] == {},
                        'ordinary product command inherited a database or application endpoint')
                product.assert_gate(gates[name], evaluator(result(records[0], gates[name]['duration_ms'])))
            tests = matching('test')
            with_db = [value for value in tests if 'TEST_DATABASE_URL' in value['database_environment']]
            without_db = [value for value in tests if 'TEST_DATABASE_URL' not in value['database_environment']]
            require(len(with_db) == len(without_db) == 1
                    and set(with_db[0]['database_environment']) == {'TEST_DATABASE_URL'}
                    and without_db[0]['database_environment'] == {}
                    and with_db[0]['application_environment'] == without_db[0]['application_environment'] == {},
                    'exact original database/no-database test environment roles required')
            test_port, test_db = product.database(with_db[0]['database_environment']['TEST_DATABASE_URL'])
            require(re.fullmatch('ts76_test_' + re.escape(key) + '_[0-9]+', test_db)
                    and any(value['role'] == 'postgres' and '-p' in value['command']
                            and value['command'][value['command'].index('-p') + 1] == str(test_port)
                            for value in processes.values()), 'test database does not join actual owned PostgreSQL')
            expected = assertions.evaluate_test_gate(
                result(with_db[0], gates['test']['with_test_database_url']['duration_ms']),
                result(without_db[0], gates['test']['without_test_database_url']['duration_ms']))
            product.assert_gate(gates['test'], expected)
            before = {path.relative_to(root / 'capture/bundles/products' / cid).as_posix(): product.sha(product.read(path))
                      for path in (root / 'capture/bundles/products' / cid).rglob('*') if path.is_file()}
            after = snapshots.snapshot_outside_node_modules(actual_root)
            product.assert_gate(gates['lock_unchanged'], assertions.evaluate_lock_unchanged(
                before, after, references, gates['lock_unchanged']['duration_ms']))
            migrations = matching('schema_equivalence')
            require(set(migrations[0]['database_environment']) == {'DATABASE_URL'}
                    and migrations[0]['application_environment'] == {}, 'exact schema migration environment required')
            schema_port, schema_db = product.database(migrations[0]['database_environment']['DATABASE_URL'])
            require(re.fullmatch('ts76_schema_ts_' + re.escape(key) + '_[0-9]+', schema_db)
                    and any(value['role'] == 'postgres' and '-p' in value['command']
                            and value['command'][value['command'].index('-p') + 1] == str(schema_port)
                            for value in processes.values()), 'schema database does not join actual owned PostgreSQL')
            used.add(migrations[0]['index'])
            startup = matching('startup')
            missing = [value for value in startup if 'DATABASE_URL' not in value['database_environment']]
            invalid = [value for value in startup if value['application_environment'].get('SOM_APP_ADDR') == 'invalid-address']
            require(len(startup) == 2 and len(missing) == len(invalid) == 1 and missing[0] is not invalid[0]
                    and missing[0]['database_environment'] == {}
                    and set(invalid[0]['database_environment']) == {'DATABASE_URL'},
                    'both exact original missing-DB/invalid-address startup environment roles required')
            startup_port, startup_db = product.database(invalid[0]['database_environment']['DATABASE_URL'])
            require(re.fullmatch('ts76_start_' + re.escape(key) + '_[0-9]+', startup_db)
                    and any(value['role'] == 'postgres' and '-p' in value['command']
                            and value['command'][value['command'].index('-p') + 1] == str(startup_port)
                            for value in processes.values()), 'startup database does not join actual owned PostgreSQL')
            address = missing[0]['application_environment'].get('SOM_APP_ADDR')
            product.endpoint('http://' + address)
            live = [value for value in processes.values() if value['role'] == 'typescript_startup_probe'
                    and value['expected_cwd'] == cwd]
            require(len(live) == 1 and live[0]['wait_exit_code'] == 0, 'actual owned SIGTERM startup child/wait required')
            readiness = live[0]['native_readiness']
            directory = root / 'capture/native-readiness' / str(live[0]['pid'])
            require(product.parse(product.read(directory / 'result.json')) == readiness
                    and product.sha(product.read(directory / 'response.raw')) == readiness['response_sha256']
                    and readiness['pid'] == live[0]['pid'] and readiness['method'] == 'GET'
                    and readiness['path'] == '/healthz' and type(readiness['status']) is int and readiness['status'] < 500,
                    'actual owned startup readiness raw response required')
            require(live[0]['native_owned_start']['environment']['DATABASE_URL']
                    == invalid[0]['database_environment']['DATABASE_URL'], 'startup process used a different database role')
            require({value['index'] for value in commands if value['cwd'] == cwd} == product_used,
                    'extra or missing actual source-owned product command')
            actual_address = live[0]['native_owned_start']['environment']['SOM_APP_ADDR']
            require(readiness['url'] == 'http://' + actual_address + '/healthz'
                    and readiness['owned_socket']['port'] == product.endpoint('http://' + actual_address),
                    'startup raw readiness endpoint differs from actual owned process')
            sigterm = {'ready': True, 'cwd_verified': live[0]['stop_cwd_verified'] is True,
                       'exit_code': live[0]['wait_exit_code']}
            product.assert_gate(gates['startup'], assertions.evaluate_startup(result(missing[0], 0), result(invalid[0], 0),
                sigterm, gates['startup']['command'], gates['startup']['duration_ms']))
            proofs.append({'id': cid, 'command_startup_gates_rederived':
                           ['install', 'typecheck', 'format_check', 'test', 'lock_unchanged', 'startup'],
                           'schema_parity_strict_proof_required_separately': True,
                           'outside_node_modules_files_sha256': product.sha(product.canonical(after)),
                           'startup_pid': live[0]['pid']})
        return {'version': 'som.typescript-regression-product-command-proof.v1', 'products': proofs,
            'command_indices': sorted(used), 'exact_original_evaluators': sorted(names),
            'replaces': ['ts.product_gates.0', 'ts.product_gates.1', 'ts.product_gates.2',
                         'ts.product_gates.3', 'ts.product_gates.4', 'ts.product_gates.8'],
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
            failure = {'primary': None, 'traceback': None, 'failure': None, 'secondary': []}
            remember_failure(failure, 'original_product_command_assertion', primary)
            remember_failure(failure, 'product_command_source_cleanup', error)
            raise_primary(failure)

"""GitHub-only fixtures for exact original TypeScript product raw replay."""
from __future__ import annotations

import ast
import base64
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import sys
import tempfile
from types import ModuleType, SimpleNamespace
import unittest
from unittest import mock
import urllib.parse

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
# The transfer, Context and SQL/product inputs are explicit protocol doubles.
# Only actual Core-owned modules/fragments and its owning Lease supply lifecycle.
NAME = '_som_test_typescript_regression_product_proof'
SPEC = proof = _CASE = None
_PRODUCT_PINS = {'som-rs/scripts/hosted_readiness/typescript_regression_product_proof.py': {'bytes': 37578,
                                                                            'sha256': 'ddf320e7e14aacb1ee0a18008f290cf2fbfe4b84ad91d1bf996ff97a8ee3906b'},
 'som-rs/som-targets/qa/check_rust_products.py': {'bytes': 36051,
                                                  'sha256': '08d49d57aa77773df87c14c50b92afb0d543a59f4b05c44793f34cf185440ea0'},
 'som-rs/som-targets/qa/check_ts_products.py': {'bytes': 41606,
                                                'sha256': 'fb52e35d465779a31affad4078e9209050f55ade3803e6931881bc523e2271d1'},
 'som-rs/behavior/suite.py': {'bytes': 49360,
                              'sha256': '1f7fbd9bbd340aa4457fdbbbc256ebcc5add742bdf86cd1f390c28329a0c7d83'},
 'som-rs/behavior/typescript/suite.py': {'bytes': 28185,
                                         'sha256': 'e040c9e7af9394eb38ff15bc2ba471b72075f22a1b32b7479273e46133ca29fd'},
 'som-rs/scripts/hosted_readiness/native_active_controls.py': {'bytes': 81177,
                                                               'sha256': 'eead8e03186f8f55812566a11d5aa837fb5cdb542d2c3de9435da9bad6bb5b32'},
 'som-rs/scripts/hosted_readiness/test_native_active_controls.py': {'bytes': 61533,
                                                                    'sha256': 'f76335be9269618bcec26750895202a896bbe759cfc6ea490d7b8dffddc99e0d'},
 'som-rs/behavior/typescript/test_ts_gates.py': {'bytes': 76025,
                                                 'sha256': 'e5f66af8a397db6f56ebbab355cc8b9abd56cc6412cd00a98c838a247dcdce3c'},
 'som-rs/scripts/hosted_readiness/native_qa_authority.py': {'bytes': 31631,
                                                            'sha256': '024a8e96487db8455f4f57008967524e6ce81fe4a3c853baafb67969e56a0cc1'}}
_PRODUCT_ROWS = {'ts.product-proof': {'bindings': {'__som_native_active_controls__': {'kind': 'active-controls'}},
                      'callers': [{'role': 'caller', 'function': 'run'}],
                      'closure': ['ts.product-proof'],
                      'group': None,
                      'is_package': False,
                      'kind': 'module',
                      'module': '_som_ts_domain_full_typescript_product_raw_proof',
                      'package': '',
                      'source': 'som-rs/scripts/hosted_readiness/typescript_regression_product_proof.py'},
 'ts.product.schema': {'bindings': {'psycopg': {'fields': {'connections': 'int',
                                                           'index': 'int',
                                                           'row': 'json'},
                                                'kind': 'local-instance',
                                                'local': 'sql',
                                                'type_name': 'SchemaReplay',
                                                'type_role': 'ts.product-proof'}},
                       'callers': [{'role': 'ts.product-proof', 'function': 'verify'},
                                   {'role': 'caller', 'function': 'derive_schema'}],
                       'closure': ['ts.product-proof', 'ts.product.schema'],
                       'group': None,
                       'is_package': False,
                       'kind': 'fragment',
                       'module': '_som_ts_product_schema_assertions',
                       'package': '',
                       'selector': {'ast_sha256': '4d3fab2bd42cf762a310dd82b9c2be49ea4ac5a0847924e597796ad80044075c',
                                    'kind': 'top-level-functions',
                                    'names': ['schema']},
                       'source': 'som-rs/som-targets/qa/check_rust_products.py'},
 'ts.product.gate-assertions': {'bindings': {'DIVERGENCE_IDS': {'kind': 'source-constant',
                                                                'name': 'DIVERGENCE_IDS',
                                                                'source': 'som-rs/som-targets/qa/check_ts_products.py'},
                                             'EXCLUDED_SCHEMA_TABLES': {'kind': 'source-constant',
                                                                        'name': 'EXCLUDED_SCHEMA_TABLES',
                                                                        'source': 'som-rs/som-targets/qa/check_ts_products.py'},
                                             'json': {'kind': 'stdlib', 'module': 'json'},
                                             'ts_suite': {'kind': 'role-module', 'role': 'ts.product.suite'}},
                                'callers': [{'role': 'ts.product-proof', 'function': 'verify'},
                                            {'role': 'caller', 'function': 'strict_gate'}],
                                'closure': ['ts.product.gate-assertions',
                                            'ts.product.suite',
                                            'ts.product.suite-base'],
                                'group': None,
                                'is_package': False,
                                'kind': 'fragment',
                                'module': '_som_ts_product_gate_assertions',
                                'package': '',
                                'selector': {'ast_sha256': '3d3e8eea67c48a3be4813766eb60aa57a2e5471edc159da4de3edefd4c507615',
                                             'kind': 'top-level-functions',
                                             'names': ['make_gate',
                                                       'schema_differences',
                                                       'evaluate_schema_equivalence',
                                                       'evaluate_parity',
                                                       'evaluate_strict_refusals']},
                                'source': 'som-rs/som-targets/qa/check_ts_products.py'},
 'ts.product.suite': {'bindings': {'base_suite': {'kind': 'role-module', 'role': 'ts.product.suite-base'}},
                      'callers': [{'function': 'SourceModules.load', 'role': 'ts.product-proof'}],
                      'closure': ['ts.product.suite', 'ts.product.suite-base'],
                      'group': None,
                      'is_package': False,
                      'kind': 'module',
                      'module': '_som_typescript_product_proof_suite',
                      'package': '',
                      'source': 'som-rs/behavior/typescript/suite.py'},
 'ts.product.suite-base': {'bindings': {},
                           'callers': [{'function': 'SourceModules.load', 'role': 'ts.product-proof'}],
                           'closure': ['ts.product.suite-base'],
                           'group': None,
                           'is_package': False,
                           'kind': 'module',
                           'module': 'som_behavior_base_suite',
                           'package': '',
                           'source': 'som-rs/behavior/suite.py'},
 'caller': {'kind': 'module',
            'source': 'som-rs/scripts/hosted_readiness/synthetic_active_caller.py',
            'module': '_synthetic_active_caller',
            'package': '',
            'is_package': False,
            'callers': [],
            'bindings': {},
            'group': None,
            'closure': ['caller',
                        'ts.product-proof',
                        'ts.product.gate-assertions',
                        'ts.product.schema',
                        'ts.product.suite',
                        'ts.product.suite-base']}}
_PRODUCT_BRIDGES = [{'function': 'SourceModules.original_functions',
  'kind': 'fragment-wrapper',
  'names_local': 'names',
  'role': 'ts.product-proof',
  'source_local': 'path'}]
_PRODUCT_CALLER = '''def run(controls):
    return controls.load_module("ts.product-proof")

def derive_schema(controls, ctx, row):
    proof = run(controls)
    source = proof.SourceModules(ctx)
    sql = proof.SchemaReplay(row)
    primary = None
    try:
        with source.original_functions(proof.RUST_CHECKER, {"schema"}) as original:
            value = original.schema(row["url"], tuple(row["excluded"]))
            sql.finish()
            return value
    except BaseException as error:
        primary = error
        raise
    finally:
        try:
            source.close()
        except BaseException as error:
            if primary is None:
                raise
            primary.__som_typescript_product_proof_cleanup__ = proof.failure_record(error)
            failure = {"primary": None, "traceback": None, "failure": None, "secondary": []}
            proof.remember_failure(failure, "original_product_assertion", primary)
            proof.remember_failure(failure, "product_source_cleanup", error)
            proof.raise_primary(failure)

def strict_gate(controls, ctx, transcript, failures):
    proof = run(controls)
    source = proof.SourceModules(ctx)
    primary = None
    try:
        with source.original_functions(proof.CHECKER, {
                "make_gate", "schema_differences", "evaluate_schema_equivalence", "evaluate_parity",
                "evaluate_strict_refusals"}) as checker:
            return checker.evaluate_strict_refusals(transcript, failures, 1, ["node", "src/main.ts"])
    except BaseException as error:
        primary = error
        raise
    finally:
        try:
            source.close()
        except BaseException as error:
            if primary is None:
                raise
            primary.__som_typescript_product_proof_cleanup__ = proof.failure_record(error)
            failure = {"primary": None, "traceback": None, "failure": None, "secondary": []}
            proof.remember_failure(failure, "original_product_assertion", primary)
            proof.remember_failure(failure, "product_source_cleanup", error)
            proof.raise_primary(failure)
'''


def _protocol_module():
    path = HERE / 'test_native_active_controls.py'
    module = ModuleType('_som_product_proof_active_protocol_fixture')
    module.__file__, module.__cached__, module.__package__ = str(path), None, ''
    exec(compile(path.read_bytes(), str(path), 'exec', dont_inherit=True), module.__dict__)
    return module


def _start_product_case(case):
    global proof, SPEC, _CASE
    previous = {'proof': proof, 'SPEC': SPEC, '_CASE': _CASE}
    owned = {}
    def restore_aliases():
        for name, value in owned.items():
            if globals().get(name) is value:
                globals()[name] = previous[name]
    case.addCleanup(restore_aliases)
    selected = {}
    for relative, pin in _PRODUCT_PINS.items():
        raw = (REPO / relative).read_bytes()
        if len(raw) != pin['bytes'] or hashlib.sha256(raw).hexdigest() != pin['sha256']:
            raise RuntimeError('selected product fixture dependency bytes differ: ' + relative)
        selected[relative] = raw
    protocol = _protocol_module()
    class Transfer(protocol.ProtocolTransfer):
        def __init__(self, fixture):
            # Complete only fixed data before the existing Domain constructor.
            for role, row in _PRODUCT_ROWS.items():
                fixture.rows[role]['closure'] = list(row['closure'])
            fixture.policy['bridges'] = copy.deepcopy(_PRODUCT_BRIDGES)
            super().__init__(fixture)
    class Fixture(protocol.Fixture):
        def add_role(self, role, path, raw, **kwargs):
            result = super().add_role(role, path, raw, **kwargs)
            if role != 'm6.api.producer':
                return result
            for name, template in _PRODUCT_ROWS.items():
                if name == 'caller':
                    continue
                row = super().add_role(name, template['source'], selected[template['source']],
                    kind=template['kind'], module=template['module'], callers=copy.deepcopy(template['callers']),
                    bindings=copy.deepcopy(template['bindings']), group=template['group'])
                (self.root / template['source']).chmod(0o644)
                if 'selector' in template:
                    names = template['selector']['names']
                    nodes = [node for node in ast.parse(selected[template['source']]).body
                             if isinstance(node, ast.FunctionDef) and node.name in names]
                    if [node.name for node in nodes] != names:
                        raise RuntimeError('selected original fragment order differs')
                    selector = copy.deepcopy(template['selector'])
                    selector['ast_sha256'] = hashlib.sha256(ast.dump(
                        ast.Module(body=nodes, type_ignores=[]), include_attributes=False).encode()).hexdigest()
                    row['selector'] = selector
            return result
    with mock.patch.object(protocol, 'ProtocolTransfer', Transfer):
        fixture = Fixture(case, _PRODUCT_CALLER)
    fixture.controls.__enter__()
    case.addCleanup(fixture.controls.__exit__, None, None, None)
    module = fixture.run()
    state = SimpleNamespace(fixture=fixture, caller=fixture.caller(), context=SimpleNamespace(
        qa_mode='native-qa-v1', repo=fixture.controls.root,
        helper_revision=fixture.controls.helper_revision, candidate_revision=fixture.controls.candidate_revision,
        sources=SimpleNamespace(read=lambda path: fixture.raw[path])))
    proof, SPEC, _CASE = module, module.__spec__, state
    owned.update(proof=proof, SPEC=SPEC, _CASE=_CASE)
    assert SPEC is not None and SPEC.loader is not None


def encoded(value):
    if value is None:
        return {'type': 'none'}
    kind = {bool: 'bool', int: 'int', str: 'str', list: 'list', tuple: 'tuple'}[type(value)]
    return {'type': kind, 'value': [encoded(v) for v in value] if kind in {'list', 'tuple'} else value}


def source_context():
    return _CASE.context


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(proof.canonical(value))


def schema_fixture():
    raw = (REPO / proof.RUST_CHECKER).read_bytes()
    tree = ast.parse(raw)
    function = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == 'schema')
    executes = [node for node in ast.walk(function) if isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute) and node.func.attr == 'execute']
    sql = [ast.literal_eval(node.args[0]) for node in sorted(executes, key=lambda node: node.lineno)]
    return {'index': 0, 'url': 'postgresql://postgres@127.0.0.1:18990/ts76_schema_ts_case_123',
            'excluded': ['alembic_version', '_som_ts_migrations'], 'complete': True, 'pg_owner_pid': 400,
            'queries': [{'sql': sql[0], 'params': encoded(None), 'rows': encoded([('items',), ('alembic_version',)])},
                        {'sql': sql[1], 'params': encoded(('items',)),
                         'rows': encoded([('id', 'integer', None, 'NO', "nextval('old'::regclass)")])},
                        {'sql': sql[2], 'params': encoded(('public."items"',)),
                         'rows': encoded([('p', ['id'], '-', [], 'a')])},
                        {'sql': sql[3], 'params': encoded(('public."items"',)),
                         'rows': encoded([(True, ['id'])])}]}


def derive_schema(row):
    return _CASE.caller.derive_schema(_CASE.fixture.controls, source_context(), row)


class TypedSchemaTests(unittest.TestCase):
    setUp = _start_product_case
    def test_exact_null_container_types(self):
        value = [(None, True, 7, 'id', ['nested'])]
        self.assertEqual(proof.typed(encoded(value)), value)

    def test_null_with_value_refused(self):
        with self.assertRaises(ValueError): proof.typed({'type': 'none', 'value': None})

    def test_boolean_cannot_claim_integer_type(self):
        with self.assertRaises(ValueError): proof.typed({'type': 'int', 'value': True})

    def test_unsupported_or_lossy_sql_type_refused(self):
        with self.assertRaises(ValueError): proof.typed({'type': 'repr', 'value': 'Decimal(1)'})

    def test_actual_original_schema_query_derivation(self):
        result = derive_schema(schema_fixture())
        self.assertEqual(result, {'items': {'columns': [['id', 'integer', None, 'NO',
            "nextval('items_id_seq'::regclass)"]], 'primary_key': [['id']], 'unique': [],
            'foreign_keys': [], 'indexes': [[['id'], True]]}})

    def test_actual_original_schema_changed_query_refused(self):
        row = schema_fixture(); row['queries'][1]['sql'] += ' LIMIT 1'
        with self.assertRaises(ValueError): derive_schema(row)

    def test_actual_original_schema_changed_table_parameter_refused(self):
        row = schema_fixture(); row['queries'][1]['params'] = encoded(('other',))
        with self.assertRaises(ValueError): derive_schema(row)

    def test_actual_original_schema_missing_query_refused(self):
        row = schema_fixture(); row['queries'].pop()
        with self.assertRaises(ValueError): derive_schema(row)

    def test_actual_original_schema_extra_query_refused(self):
        row = schema_fixture(); row['queries'].append(copy.deepcopy(row['queries'][-1]))
        with self.assertRaises(ValueError): derive_schema(row)

    def test_missing_schema_complete_refused(self):
        row = schema_fixture(); row['complete'] = False
        with self.assertRaises(ValueError): proof.SchemaReplay(row)


class OriginalBehaviorReplayTests(unittest.TestCase):
    def setUp(self):
        _start_product_case(self)
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.source = proof.SourceModules(source_context())
        self.suite = self.source.suite()
        raw = (REPO / 'som-rs/behavior/typescript/test_ts_gates.py').read_bytes()
        tree = ast.parse(raw)
        nodes = [node for node in tree.body if
                 isinstance(node, ast.ClassDef) and node.name == 'InMemoryProduct'
                 or isinstance(node, ast.FunctionDef) and node.name == 'RichV4Prompt']
        module = ast.Module(body=[ast.ImportFrom(module='__future__', names=[ast.alias(name='annotations')], level=0),
                                  *nodes], type_ignores=[])
        ast.fix_missing_locations(module)
        namespace = {'json': json, 'urllib': urllib, 're': re, 'ts_suite': self.suite}
        exec(compile(module, 'source-original-in-memory-product-fixture', 'exec', dont_inherit=True), namespace)
        self.simulator, self.prompt = namespace['InMemoryProduct'], namespace['RichV4Prompt']()
        self.rows = []

    def tearDown(self):
        self.source.close()
        self.temporary.cleanup()

    def collect(self, **flags):
        simulator = self.simulator(self.prompt, **flags)
        base = 'http://127.0.0.1:18990'
        def send(method, path, body, content_type):
            status, media, raw = simulator.send(method, path, body, content_type)
            n = len(self.rows); directory = self.root / f'{n:06d}'; directory.mkdir()
            if body is not None: (directory / 'request.raw').write_bytes(body)
            (directory / 'response.raw').write_bytes(raw)
            row = {'index': n, 'base': base, 'method': method, 'path': path, 'content_type': content_type,
                   'body_is_null': body is None, 'request_sha256': None if body is None else proof.sha(body),
                   'status': status, 'response_content_type': media, 'response_sha256': proof.sha(raw), 'complete': True}
            save(directory / 'result.json', row); self.rows.append(row)
            return status, media, raw
        run = self.suite.run(self.prompt, base, send=send)
        before = len(run.transcript); run.strict()
        return run, before

    def replay(self):
        tape = proof.ProductReplay(self.root, self.rows)
        result = self.suite.run(self.prompt, tape.next_base(), send=tape.sender(tape.next_base()))
        before = len(result.transcript); result.strict(); tape.finish()
        return result, before

    def test_actual_original_parity_and_all_strict_assertions_replayed(self):
        original, before = self.collect()
        result, actual_before = self.replay()
        self.assertEqual((result.raw, result.transcript, result.failures, actual_before),
                         (original.raw, original.transcript, [], before))
        categories = {row['category'] for row in result.transcript[before:]}
        self.assertTrue({'D1', 'D2', 'D3', 'D4', 'D5', 'D7', 'prototype_members', 'strict_no_row_change'} <= categories)

    def test_original_prototype_bug_stays_a_failure_after_replay(self):
        original, before = self.collect(allow_prototype_keys=True)
        result, _ = self.replay()
        self.assertEqual(result.failures, original.failures)
        self.assertTrue(any(row['category'] == 'prototype_members' for row in result.failures))
        gate = _CASE.caller.strict_gate(_CASE.fixture.controls, source_context(),
                                       result.transcript[before:], result.failures)
        with self.assertRaises(ValueError): proof.assert_gate(gate, gate)

    def test_extra_product_http_member_refused(self):
        self.collect(); (self.root / 'extra.raw').write_bytes(b'first failure must not hide extra data')
        with self.assertRaises(ValueError): proof.ProductReplay(self.root, self.rows)

    def test_response_tamper_refused_before_original_replay(self):
        self.collect(); (self.root / '000000/response.raw').write_bytes(b'changed')
        with self.assertRaises(ValueError): proof.ProductReplay(self.root, self.rows)

    def test_original_request_order_tamper_refused(self):
        self.collect(); self.rows[0]['method'] = 'DELETE'; save(self.root / '000000/result.json', self.rows[0])
        with self.assertRaises(ValueError): self.replay()

    def test_unconsumed_real_product_exchange_refused(self):
        self.collect(); tape = proof.ProductReplay(self.root, self.rows)
        with self.assertRaises(ValueError): tape.finish()

    def test_protected_compiler_port_cannot_label_product_tape(self):
        self.collect(); self.rows[0]['base'] = 'http://127.0.0.1:18765'
        save(self.root / '000000/result.json', self.rows[0])
        with self.assertRaises(ValueError): proof.ProductReplay(self.root, self.rows)


class OwnershipBoundaryTests(unittest.TestCase):
    setUp = _start_product_case
    def process_fixture(self):
        def record(pid, role, cwd, command, environment, digest):
            fields = ['S', '1', str(pid), str(pid)] + ['0'] * 15 + ['12345']
            raw = str(pid) + ' (owned)' + ' ' + ' '.join(fields)
            stamp = {'pid': pid, 'state': 'S', 'pgrp': pid, 'session': pid, 'start_ticks': 12345,
                     'proc_stat_raw': raw, 'cwd': cwd, 'executable_device': 7, 'executable_inode': pid,
                     'cmdline_raw_b64': base64.b64encode(b'\0'.join(v.encode() for v in command) + b'\0').decode(),
                     'environment': environment}
            return {'pid': pid, 'role': role, 'command': command, 'native_actual_argv': command,
                    'expected_cwd': cwd, 'initial_cwd': cwd, 'initial_cwd_verified': True,
                    'native_owned_start': stamp, 'native_executable_sha256': digest,
                    'stopped': True, 'stop_cwd_verified': True, 'exit_code': 0,
                    'wait_completed': True, 'wait_exit_code': 0}
        pg = record(900, 'postgres', '/run/native-products/tmp/pgdata',
                    ['/pg/postgres', '-p', '18991', '-k', ''], {}, 'b' * 64)
        node = record(901, 'typescript_product', '/run/native-products/tmp/ts_demo',
                      ['/node', 'src/main.ts'], {'SOM_APP_ADDR': '127.0.0.1:18990'}, 'a' * 64)
        context = {'repo': '/repo', 'helper_revision': 'a' * 40, 'candidate_revision': 'a' * 40,
                   'observer_python': '/runtime/python'}
        command = [context['observer_python'], '-m', 'uvicorn', 'fixture.app:app']
        python = record(902, 'python_product', '/run/native-products/tmp/py_parity_demo',
                        command, {'SOM_APP_ADDR': '127.0.0.1:18992'}, 'c' * 64)
        # Logical app command and complete kernel argv are distinct synthetic data.
        actual = [context['observer_python'], '-I', '-S', '-B',
                  '/repo/som-rs/scripts/hosted_readiness/native_source_bootstrap.py',
                  '--repo', context['repo'], '--helper-revision', context['helper_revision'],
                  '--candidate-revision', context['candidate_revision'], '--entry-role', 'entry.full-products', '--',
                  '--native-python-bootstrap', '--', *command[1:]]
        python['native_actual_argv'] = actual
        python['native_owned_start']['cmdline_raw_b64'] = base64.b64encode(
            b'\0'.join(value.encode() for value in actual) + b'\0').decode()
        return {'processes': [pg, node, python]}, {'original_run': '/run', 'context': context}

    def test_obsolete_direct_python_driver_is_refused_with_matching_kernel_argv(self):
        report, capture = self.process_fixture()
        self.assertEqual(proof.processes(report, capture, 'a' * 64)[902]['role'], 'python_product')
        row = report['processes'][2]; command = row['command']
        actual = [command[0], '-I', '-S', '-B',
                  str(Path(capture['context']['repo']) / (proof.BASE + 'run_full_products.py')),
                  '--native-python-bootstrap', '--', *command[1:]]
        row['native_actual_argv'] = actual
        row['native_owned_start']['cmdline_raw_b64'] = base64.b64encode(
            b'\0'.join(value.encode() for value in actual) + b'\0').decode()
        with self.assertRaisesRegex(ValueError, 'actual isolated Python product argv'):
            proof.processes(report, capture, 'a' * 64)

    def test_actual_argv_and_owned_wait_records_join_with_empty_pg_socket_argument(self):
        report, capture = self.process_fixture()
        result = proof.processes(report, capture, 'a' * 64)
        self.assertEqual(proof.product_owner(result, 'typescript_product', 'demo', 'http://127.0.0.1:18990'), 901)

    def test_kernel_argv_cannot_be_relabelled_by_logical_command(self):
        report, capture = self.process_fixture()
        report['processes'][1]['native_actual_argv'] = ['/other', 'src/main.ts']
        with self.assertRaises(ValueError): proof.processes(report, capture, 'a' * 64)

    def test_owned_product_port_cannot_be_relabelled(self):
        report, capture = self.process_fixture()
        result = proof.processes(report, capture, 'a' * 64)
        with self.assertRaises(ValueError):
            proof.product_owner(result, 'typescript_product', 'demo', 'http://127.0.0.1:18992')

    def test_missing_actual_wait_refuses_product_ownership(self):
        report, capture = self.process_fixture(); report['processes'][1]['wait_completed'] = False
        with self.assertRaises(ValueError): proof.processes(report, capture, 'a' * 64)

    def test_unapproved_process_environment_member_refused(self):
        report, capture = self.process_fixture(); report['processes'][1]['native_owned_start']['environment']['TOKEN'] = 'not saved'
        with self.assertRaises(ValueError): proof.processes(report, capture, 'a' * 64)

    def test_preloaded_suite_alias_refused_before_source_read(self):
        current = source_context()
        ctx = SimpleNamespace(repo=current.repo, qa_mode=current.qa_mode,
            helper_revision=current.helper_revision, candidate_revision=current.candidate_revision,
            sources=SimpleNamespace(read=lambda _: self.fail('preload read source')))
        modules = proof.SourceModules(ctx)
        name = 'som_behavior_base_suite'; old = sys.modules.get(name)
        sentinel = ModuleType(name); sys.modules[name] = sentinel
        try:
            with self.assertRaises(ValueError): modules.suite()
            self.assertIs(sys.modules[name], sentinel)
        finally:
            if sys.modules.get(name) is sentinel:
                if old is None: sys.modules.pop(name, None)
                else: sys.modules[name] = old

    def test_close_preserves_unowned_replacement_and_cleans_other_owned_modules(self):
        modules = proof.SourceModules(source_context())
        b = modules.suite(); a = b.base_suite
        replacement = ModuleType(a.__name__)
        try:
            with self.assertRaises(ValueError):
                with _CASE.fixture.controls:
                    sys.modules[a.__name__] = replacement
                    modules.close()
            self.assertIs(sys.modules[a.__name__], replacement)
            self.assertNotIn(b.__name__, sys.modules)
        finally:
            if sys.modules.get(a.__name__) is replacement:
                sys.modules.pop(a.__name__, None)

    def test_native_missing_source_admission_refused_before_source_read(self):
        ctx = SimpleNamespace(qa_mode='native-qa-v1', helper_revision='a' * 40, candidate_revision='a' * 40,
            qa_source_authority={'source_authority_active': True, 'helper_revision': 'a' * 40,
                'candidate_revision': 'a' * 40, 'admitted_repository_python_sources': []})
        with self.assertRaises(ValueError): proof.verify(ctx, Path('/not-read'), {})

    def test_database_password_or_protected_port_refused(self):
        for url in ('postgresql://postgres:secret@127.0.0.1:18990/db',
                    'postgresql://postgres@127.0.0.1:8765/db'):
            with self.assertRaises(ValueError): proof.database(url)


if __name__ == '__main__':
    unittest.main()

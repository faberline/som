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
import urllib.parse

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
NAME = '_som_test_typescript_regression_product_proof'
SPEC = importlib.util.spec_from_file_location(NAME, HERE / 'typescript_regression_product_proof.py')
assert SPEC is not None and SPEC.loader is not None
proof = importlib.util.module_from_spec(SPEC)
sys.modules[NAME] = proof
SPEC.loader.exec_module(proof)


def encoded(value):
    if value is None:
        return {'type': 'none'}
    kind = {bool: 'bool', int: 'int', str: 'str', list: 'list', tuple: 'tuple'}[type(value)]
    return {'type': kind, 'value': [encoded(v) for v in value] if kind in {'list', 'tuple'} else value}


def source_context():
    return SimpleNamespace(repo=REPO, sources=SimpleNamespace(read=lambda path: (REPO / path).read_bytes()))


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
    source = proof.SourceModules(source_context())
    replay = proof.SchemaReplay(row)
    original = source.original_functions(proof.RUST_CHECKER, {'schema'}, {'psycopg': replay})
    value = original.schema(row['url'], tuple(row['excluded']))
    replay.finish()
    return value


class TypedSchemaTests(unittest.TestCase):
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
        checker = self.source.original_functions(proof.CHECKER, {'make_gate', 'evaluate_strict_refusals'},
            {'json': json, 'DIVERGENCE_IDS': self.source.constant(proof.CHECKER, 'DIVERGENCE_IDS')})
        gate = checker.evaluate_strict_refusals(result.transcript[before:], result.failures, 1, ['node', 'src/main.ts'])
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
        return {'processes': [pg, node]}, {'original_run': '/run', 'context': {'repo': '/repo'}}

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
        name = 'som_behavior_base_suite'; old = sys.modules.get(name)
        sentinel = ModuleType(name); sys.modules[name] = sentinel
        ctx = SimpleNamespace(repo=REPO, sources=SimpleNamespace(read=lambda _: self.fail('preload read source')))
        modules = proof.SourceModules(ctx)
        try:
            with self.assertRaises(ValueError): modules.suite()
            self.assertIs(sys.modules[name], sentinel)
        finally:
            if old is None: sys.modules.pop(name, None)
            else: sys.modules[name] = old

    def test_close_preserves_unowned_replacement_and_cleans_other_owned_modules(self):
        modules = proof.SourceModules(source_context())
        a, b = ModuleType('_som_proof_fixture_a'), ModuleType('_som_proof_fixture_b')
        replacement = ModuleType(a.__name__)
        sys.modules[a.__name__], sys.modules[b.__name__] = replacement, b
        modules.owned = [(a.__name__, a), (b.__name__, b)]
        try:
            with self.assertRaises(ValueError): modules.close()
            self.assertIs(sys.modules[a.__name__], replacement)
            self.assertNotIn(b.__name__, sys.modules)
        finally:
            sys.modules.pop(a.__name__, None); sys.modules.pop(b.__name__, None)

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

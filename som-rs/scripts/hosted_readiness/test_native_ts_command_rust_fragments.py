"""GitHub-only command/Rust-key fragment protocol fixtures.

Real Core and exact production/fragment source execute on synthetic physical
roots. The source authority transfer, Context, source reader and input profile
are explicit data doubles. No actual Context, transport, tool or product passes.
"""
from __future__ import annotations
import ast
import copy
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest import mock

if os.environ.get('GITHUB_ACTIONS') != 'true':
    raise RuntimeError('GitHub tests only')
protocol_path = Path(__file__).absolute().with_name('test_native_active_controls.py')
protocol = ModuleType('_som_ts_command_rust_active_protocol_fixture')
protocol.__file__, protocol.__cached__, protocol.__package__ = str(protocol_path), None, ''
exec(compile(protocol_path.read_bytes(), str(protocol_path), 'exec', dont_inherit=True), protocol.__dict__)
REPO, BASE = protocol.REPO, 'som-rs/scripts/hosted_readiness/'
CMD, CLI = 'ctx.full-original-product-command-proof', 'ctx.full-original-cli-rust-raw-proof'
PROFILE = BASE + 'inputs/typescript-regression-inputs-v1.json'
ROLES = {'ctx.full-original-cli-rust-raw-proof': {'bindings': {'__som_native_active_controls__': {'kind': 'active-controls'},
                                                       '_native_original_target_sha': {'attribute': 'sha',
                                                                                       'kind': 'role-export',
                                                                                       'role': 'ts.cli-rust.target-sha'}},
                                          'callers': [{'function': 'verify',
                                                       'role': 'ts.owner.domain-verifier'}],
                                          'closure_pending_root_complete_policy': True,
                                          'group': None,
                                          'is_package': False,
                                          'kind': 'module',
                                          'module': '_som_full_full_original_cli_rust_raw_proof',
                                          'package': '',
                                          'source': 'som-rs/scripts/hosted_readiness/typescript_regression_cli_rust_proof.py'},
 'ctx.full-original-product-command-proof': {'bindings': {'__som_native_active_controls__': {'kind': 'active-controls'}},
                                             'callers': [{'function': 'verify',
                                                          'role': 'ts.owner.domain-verifier'}],
                                             'closure_pending_root_complete_policy': True,
                                             'group': None,
                                             'is_package': False,
                                             'kind': 'module',
                                             'module': '_som_full_full_original_product_command_proof',
                                             'package': '',
                                             'source': 'som-rs/scripts/hosted_readiness/typescript_regression_product_commands.py'},
 'ts.cli-rust.input-source-key': {'bindings': {'hosted_products': {'fields': {'bindings': 'json',
                                                                              'profile': 'str',
                                                                              'repo': 'path'},
                                                                   'kind': 'local-instance',
                                                                   'local': 'metadata',
                                                                   'type_name': '_InputMetadata',
                                                                   'type_role': 'ctx.full-original-cli-rust-raw-proof'},
                                               'json': {'kind': 'stdlib', 'module': 'json'},
                                               'os': {'fields': {'environ': 'json'},
                                                      'kind': 'local-instance',
                                                      'local': 'environment',
                                                      'type_name': '_InputEnvironment',
                                                      'type_role': 'ctx.full-original-cli-rust-raw-proof'},
                                               'pathlib': {'kind': 'stdlib', 'module': 'pathlib'},
                                               'targets': {'fields': {'repo': 'path'},
                                                           'kind': 'local-instance',
                                                           'local': 'targets',
                                                           'type_name': '_InputTargets',
                                                           'type_role': 'ctx.full-original-cli-rust-raw-proof'}},
                                  'callers': [{'function': '_native_input_key',
                                               'role': 'ctx.full-original-cli-rust-raw-proof'}],
                                  'closure_pending_root_complete_policy': True,
                                  'group': None,
                                  'is_package': False,
                                  'kind': 'fragment',
                                  'module': '_som_ts_cli_rust_input_key',
                                  'package': '',
                                  'selector': {'ast_sha256': '3a71b5df7bde1d7649b88888122fe951231e31872cd7e2ee92390946bd6323a0',
                                               'kind': 'top-level-functions',
                                               'names': ['input_source_key']},
                                  'source': 'som-rs/som-targets/qa/check_rust_products.py'},
 'ts.cli-rust.target-sha': {'bindings': {'hashlib': {'kind': 'stdlib', 'module': 'hashlib'}},
                            'callers': [],
                            'closure_pending_root_complete_policy': True,
                            'group': None,
                            'is_package': False,
                            'kind': 'fragment',
                            'module': '_som_ts_cli_rust_target_sha',
                            'package': '',
                            'selector': {'ast_sha256': 'ac4c4cec2be63defe9d22ea87560a1084e09a6714d3d071e8997229eafaceb96',
                                         'kind': 'top-level-functions',
                                         'names': ['sha']},
                            'source': 'som-rs/som-targets/qa/check_targets.py'},
 'ts.commands.command-vectors': {'bindings': {'product_work': {'kind': 'local-data',
                                                               'local': 'product_work',
                                                               'type': 'path'},
                                              'ts_host': {'fields': {'NODE_BIN': 'str',
                                                                     'NPM_INVOCATION': 'json'},
                                                          'kind': 'local-instance',
                                                          'local': 'tools',
                                                          'type_name': '_CommandTools',
                                                          'type_role': 'ctx.full-original-product-command-proof'}},
                                 'callers': [{'function': 'source_command_vectors',
                                              'role': 'ctx.full-original-product-command-proof'}],
                                 'closure_pending_root_complete_policy': True,
                                 'group': None,
                                 'is_package': False,
                                 'kind': 'fragment',
                                 'module': '_som_ts_commands_vectors',
                                 'package': '',
                                 'selector': {'ast_sha256': '2c052d8600e22364a57f9fad695e5c0be6d2ded8bce4cb9b24d3ccc0c004fc8d',
                                              'kind': 'qualified-assignments',
                                              'names': ['run_startup_gate.command',
                                                        'check_one_product.install_cmd',
                                                        'check_one_product.tsc_cmd',
                                                        'check_one_product.ts_files',
                                                        'check_one_product.extra_ts',
                                                        'check_one_product.prettier_cmd',
                                                        'check_one_product.test_cmd',
                                                        'check_one_product.migrate_cmd']},
                                 'source': 'som-rs/som-targets/qa/check_ts_products.py'},
 'ts.commands.file-snapshot': {'bindings': {'hashlib': {'kind': 'stdlib', 'module': 'hashlib'}},
                               'callers': [{'function': '_native_command_snapshot',
                                            'role': 'ctx.full-original-product-command-proof'}],
                               'closure_pending_root_complete_policy': True,
                               'group': None,
                               'is_package': False,
                               'kind': 'fragment',
                               'module': '_som_ts_commands_file_snapshot',
                               'package': '',
                               'selector': {'ast_sha256': '61f6b4ba3a6313fb4ccdd0f6e8d3610f177222018b22497345a9038a66e0080c',
                                            'kind': 'top-level-functions',
                                            'names': ['snapshot_outside_node_modules']},
                               'source': 'som-rs/behavior/typescript/host.py'},
 'ts.commands.gate-assertions': {'bindings': {'NODE_TEST_STAT': {'fields': {'flags': 'int', 'pattern': 'str'},
                                                                 'kind': 'local-instance',
                                                                 'local': 'stats',
                                                                 'type_name': '_NodeTestPattern',
                                                                 'type_role': 'ctx.full-original-product-command-proof'},
                                              'REFERENCE_FILE_HASHES': {'kind': 'local-data',
                                                                        'local': 'references',
                                                                        'type': 'json'},
                                              're': {'kind': 'stdlib', 'module': 're'}},
                                 'callers': [{'function': '_native_command_assertions',
                                              'role': 'ctx.full-original-product-command-proof'}],
                                 'closure_pending_root_complete_policy': True,
                                 'group': None,
                                 'is_package': False,
                                 'kind': 'fragment',
                                 'module': '_som_ts_commands_gate_assertions',
                                 'package': '',
                                 'selector': {'ast_sha256': 'b53441c5683d72f9c6c87de5d86379e38255c824b4f74f097482feb053c4ea4e',
                                              'kind': 'top-level-functions',
                                              'names': ['non_empty_lines',
                                                        'make_gate',
                                                        'parse_node_test_counts',
                                                        'evaluate_install',
                                                        'evaluate_typecheck',
                                                        'evaluate_format_check',
                                                        'evaluate_test_gate',
                                                        'evaluate_lock_unchanged',
                                                        'evaluate_startup']},
                                 'source': 'som-rs/som-targets/qa/check_ts_products.py'},
 'ts.commands.node-test-stat': {'bindings': {'TS_REFERENCE': {'kind': 'local-data',
                                                              'local': 'reference_root',
                                                              'type': 'path'},
                                             'hashlib': {'kind': 'stdlib', 'module': 'hashlib'},
                                             're': {'kind': 'stdlib', 'module': 're'}},
                                'callers': [{'function': '_native_command_assertions',
                                             'role': 'ctx.full-original-product-command-proof'}],
                                'closure_pending_root_complete_policy': True,
                                'group': None,
                                'is_package': False,
                                'kind': 'fragment',
                                'module': '_som_ts_commands_node_parser',
                                'package': '',
                                'selector': {'ast_sha256': '4629fc3894d7eb7bc43f65b6c22ce449146187d87954a86eab8a8257a53bcb74',
                                             'kind': 'top-level-assignments',
                                             'names': ['REFERENCE_FILE_HASHES', 'NODE_TEST_STAT']},
                                'source': 'som-rs/som-targets/qa/check_ts_products.py'}}


def actual_runner_selector(case, raw, template):
    """Hash full unchanged original nodes with this executing interpreter."""
    wanted, kind = list(template['names']), template['kind']
    case.assertIn(kind, {'top-level-functions', 'top-level-assignments', 'qualified-assignments'})
    case.assertEqual(len(wanted), len(set(wanted)))
    selected, found = [], []
    def visit(body, scope):
        for node in body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                name = '.'.join((*scope, node.name))
                if (kind == 'top-level-functions' and not scope
                        and isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and name in wanted):
                    selected.append(node); found.append(name)
                visit(node.body, (*scope, node.name))
            elif isinstance(node, (ast.Assign, ast.AnnAssign)):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                names = ['.'.join((*scope, target.id)) for target in targets if isinstance(target, ast.Name)]
                if kind in {'top-level-assignments', 'qualified-assignments'} and (not scope or kind == 'qualified-assignments'):
                    hits = [name for name in names if name in wanted]
                    if hits:
                        case.assertEqual(len(hits), 1)
                        case.assertEqual(len(names), 1)
                        case.assertEqual(len(targets), 1)
                        selected.append(node); found.extend(hits)
    visit(ast.parse(raw).body, ())
    case.assertEqual(found, wanted, 'fixed names and actual original source order changed')
    result = copy.deepcopy(template)
    result['ast_sha256'] = hashlib.sha256(ast.dump(
        ast.Module(body=selected, type_ignores=[]), include_attributes=False).encode()).hexdigest()
    return result


class SourceData:
    def __init__(self, fixture): self.fixture = fixture
    def raw(self, path): return self.read(path)
    def read(self, path): return self.fixture.raw[path]


class FragmentFixture(protocol.Fixture):
    def __init__(self, case):
        caller = ('def run(controls, target="commands"):\n'
                  '    if target == "cli":\n'
                  '        return controls.load_module("' + CLI + '")\n'
                  '    return controls.load_module("' + CMD + '")\n')
        super().__init__(case, caller)
        self.reader = SourceData(self)
        self.ctx = SimpleNamespace(repo=self.root, qa_mode='native-qa-v1', helper_revision='a'*40,
                                   candidate_revision='a'*40, sources=self.reader)
        self.product = self.root / 'synthetic-product'
        for name in ('src/main.ts', 'src/app.ts', 'test/api.test.ts', 'extra.ts', 'node_modules/ignore.ts'):
            path = self.product / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b'fixed product data')

    def add_role(self, role, path, raw, **kwargs):
        row = super().add_role(role, path, raw, **kwargs)
        if role != 'm6.api.producer': return row
        for target, data in ROLES.items():
            callers = [{'role': 'caller', 'function': 'run'}] if target in {CMD, CLI} else data['callers']
            entry = super().add_role(target, data['source'], (REPO / data['source']).read_bytes(),
                kind=data['kind'], module=data['module'], bindings=copy.deepcopy(data['bindings']), callers=copy.deepcopy(callers))
            if 'selector' in data:
                entry['selector'] = actual_runner_selector(self.case, self.raw[data['source']], data['selector'])
        for name in ('package.json', 'package-lock.json', 'tsconfig.json'):
            path = 'som-rs/contracts/typescript-output-v1/reference/' + name
            target = self.root / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes((REPO / path).read_bytes())
        # This finite fixture input profile is not the actual admitted profile.
        self.legacy = self.root / 'explicit-inputs/legacy_run'
        self.legacy.mkdir(parents=True)
        inventory = self.legacy / 'inventory-003.json'
        inventory.write_bytes(b'{"recorded":true}\n'); inventory.chmod(0o644)
        body = json.dumps({'roles': {'legacy_run': {'files': {'inventory-003.json': {
            'sha256': hashlib.sha256(inventory.read_bytes()).hexdigest(),
            'size': inventory.stat().st_size, 'mode': inventory.stat().st_mode & 0o7777}}}}}).encode()
        self.raw[PROFILE] = body
        profile = self.root / PROFILE
        profile.parent.mkdir(parents=True, exist_ok=True); profile.write_bytes(body)
        return row

    def context_view(self):
        return SimpleNamespace(cache={}, data={'typescript_regression_inputs': {
            'roots': {'legacy_run': str(self.legacy)}}})

    def key_snapshot(self, module):
        return {module.RUST: self.raw[module.RUST], PROFILE: self.raw[PROFILE]}

    def parser(self, module):
        tree = ast.parse(self.raw[module.CHECKER])
        value = next(node.value for node in tree.body if isinstance(node, ast.Assign)
            and any(isinstance(target, ast.Name) and target.id == 'NODE_TEST_STAT' for target in node.targets))
        return re.compile(ast.literal_eval(value.args[0]), re.MULTILINE)


@contextmanager
def active(case):
    with mock.patch.dict(sys.modules):
        fixture = FragmentFixture(case)
        try:
            yield fixture
        finally:
            try: fixture.finish()
            finally: fixture.stack.close()


class NativeTsCommandRustFragmentTests(unittest.TestCase):
    def test_all_eight_original_assignments_keep_exact_command_vectors(self):
        with active(self) as f:
            module = f.run()
            vectors = module.source_command_vectors(f.raw[module.CHECKER], f.product,
                                                     '/qualified/node', '/qualified/npm-cli.js')
            self.assertEqual(vectors['install'], ['/qualified/node','/qualified/npm-cli.js','ci','--offline',
                '--ignore-scripts','--no-audit','--no-fund'])
            self.assertEqual(vectors['typecheck'], ['/qualified/node','node_modules/typescript/bin/tsc','-p','tsconfig.json'])
            self.assertEqual(vectors['test'], ['/qualified/node','--test','--test-concurrency=1','test/api.test.ts'])
            self.assertEqual(vectors['startup'], ['/qualified/node','src/main.ts'])
            self.assertEqual(vectors['format_check'], ['/qualified/node','node_modules/prettier/bin/prettier.cjs',
                '--check','--no-config','--no-editorconfig','src/main.ts','src/app.ts','test/api.test.ts','extra.ts'])
            self.assertEqual(vectors['schema_equivalence'][:3], ['/qualified/node','--input-type=module','-e'])
            self.assertFalse(any(record.role == 'ts.commands.command-vectors'
                                 for record in f.domain.records.values()))
            self.assertFalse(f.domain.closed)

    def test_bin_true_and_missing_required_argument_do_not_replace_source_vectors(self):
        with active(self) as f:
            module = f.run()
            vectors = module.source_command_vectors(f.raw[module.CHECKER], f.product, '/node', '/npm-cli')
            for argv in (['/bin/true'], vectors['typecheck'][:-2]):
                with self.assertRaisesRegex(ValueError, 'independent original source command'):
                    module.command_records([{'cwd':str(f.product),'argv':argv}], str(f.product),
                                           'typecheck', {'command':argv}, vectors)

    def test_real_evaluators_replay_node_counts_and_refuse_skipped_tests(self):
        with active(self) as f:
            module = f.run()
            with module._native_command_assertions(f.ctx, f.reader, f.parser(module)) as assertions:
                with_db = {'command':['/node','--test'],'exit_code':0,'duration_ms':1,'stderr':'',
                           'stdout':'# tests 2\n# pass 2\n# fail 0\n# skipped 0\n'}
                without_db = {'command':['/node','--test'],'exit_code':1,'duration_ms':1,'stderr':'missing db','stdout':''}
                self.assertTrue(assertions.evaluate_test_gate(with_db, without_db)['passed'])
                changed = dict(with_db, stdout='# tests 2\n# pass 1\n# skipped 1\n')
                self.assertFalse(assertions.evaluate_test_gate(changed, without_db)['passed'])
                refs = {name:hashlib.sha256((f.root/'som-rs/contracts/typescript-output-v1/reference'/name).read_bytes()).hexdigest()
                        for name in ('package.json','package-lock.json','tsconfig.json')}
                self.assertTrue(assertions.evaluate_lock_unchanged(refs, refs)['passed'])
            with self.assertRaisesRegex(ValueError, 'fragment scope is closed'):
                assertions.evaluate_install
            self.assertFalse(f.domain.closed)

    def test_real_snapshot_stays_open_for_use_and_ignores_only_node_modules(self):
        with active(self) as f:
            module = f.run()
            with module._native_command_snapshot(f.ctx, f.reader) as snapshots:
                result = snapshots.snapshot_outside_node_modules(f.product)
                self.assertEqual(set(result), {'src/main.ts','src/app.ts','test/api.test.ts','extra.ts'})
                self.assertTrue(all(value == hashlib.sha256(b'fixed product data').hexdigest() for value in result.values()))
            with self.assertRaisesRegex(ValueError, 'fragment scope is closed'):
                snapshots.snapshot_outside_node_modules

    def test_exact_original_input_key_checks_native_profile_then_repo_fallback(self):
        with active(self) as f:
            module = f.caller().run(f.controls, 'cli')
            with module._native_input_key(f.ctx, f.context_view(), f.key_snapshot(module)) as key:
                self.assertEqual(key(f.legacy/'inventory-003.json'),
                                 'typescript_regression_inputs/legacy_run/inventory-003.json')
                self.assertEqual(key(f.root/'source-relative.json'), 'source-relative.json')
                path = f.legacy/'inventory-003.json'; original = path.read_bytes()
                try:
                    path.write_bytes(b'changed captured inventory')
                    with self.assertRaisesRegex(ValueError, 'explicit legacy inventory differs'):
                        key(path)
                finally: path.write_bytes(original)
            self.assertFalse(f.domain.closed)
            self.assertIn('ts.cli-rust.target-sha', f.domain.records)
            self.assertFalse(any(record.role == 'ts.cli-rust.input-source-key' for record in f.domain.records.values()))

    def test_foreign_tool_hook_is_rejected_before_original_assignment_reads_it(self):
        with active(self) as f:
            module, calls = f.run(), []
            def foreign(self, name):
                calls.append(name)
                raise AssertionError('foreign tool hook executed')
            module._CommandTools.__getattribute__ = foreign
            try:
                with self.assertRaisesRegex(ValueError, 'source-owned typed class namespace changed'):
                    module.source_command_vectors(f.raw[module.CHECKER], f.product, '/node', '/npm')
                self.assertEqual(calls, [])
            finally: del module._CommandTools.__getattribute__

    def test_missing_actual_controls_refuses_before_any_fragment(self):
        with active(self) as f:
            f.rows[CMD]['bindings'].pop('__som_native_active_controls__')
            module = f.run()
            with self.assertRaisesRegex(ValueError, 'actual fixed Context control binding was replaced'):
                module.source_command_vectors(f.raw[module.CHECKER], f.product, '/node', '/npm')
            self.assertFalse(any(record.role.startswith('ts.commands.') for record in f.domain.records.values()))

    def test_body_failure_keeps_exact_primary_and_closes_borrowed_fragment_scopes(self):
        with active(self) as f:
            module = f.run(); marker = RuntimeError('original command assertion')
            finder, spec = f.domain.finder, f.domain.spec
            with self.assertRaises(RuntimeError) as raised:
                with module._native_command_assertions(f.ctx, f.reader, f.parser(module)):
                    raise marker
            self.assertIs(raised.exception, marker)
            self.assertFalse(f.domain.closed)
            self.assertIs(f.domain.finder, finder); self.assertIs(f.domain.spec, spec)
            self.assertFalse(any(record.role.startswith('ts.commands.') for record in f.domain.records.values()))


if __name__ == '__main__':
    unittest.main()

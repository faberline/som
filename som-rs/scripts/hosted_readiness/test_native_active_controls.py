"""GitHub-only loader protocol fixtures; never native or product admission.

The actual core and actual RawQaFinder run on physical synthetic sources.
The transfer, source authority result, entry, wheel runtime and private payload
are explicit protocol doubles. Context tests execute the whole tracked Context
module and its real getter/module/runtime methods, but bypass its constructor.
No fixture qualifies the actual Context factory, M6 API, SQL or product gates.
"""
from __future__ import annotations

import ast
from contextlib import ExitStack
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import threading
from types import FunctionType, ModuleType, SimpleNamespace
import unittest
from unittest import mock

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
BASE = 'som-rs/scripts/hosted_readiness/'
CORE = BASE + 'native_active_controls.py'
BOOTSTRAP = BASE + 'native_source_bootstrap.py'
AUTHORITY = BASE + 'native_qa_authority.py'
CONTEXT = BASE + 'product_runtime/full_product_context.py'
WHEELS = BASE + 'product_python/produce_wheels.py'
CALLER = BASE + 'synthetic_active_caller.py'
FRAGMENT = 'som-rs/tests/rust-output-v1/synthetic_active_fragment.py'
REPLAY = BASE + 'synthetic_schema_replay.py'


class ProtocolTransfer:
    """Explicit authority/source double. Production cannot construct from it."""
    def __init__(self, fixture):
        self.fixture, self.root, self.revision = fixture, fixture.root, 'a' * 40
        self.authority = fixture.authority
        self.sources, self.admission = self, {
            'mode': 'native-qa-v1', 'source_authority_active': True,
            'admitted_repository_python_sources': sorted(fixture.raw),
            'manual_source_loads': [], 'product_acceptance_passed': False,
            'compiler_acceptance_passed': False, 'whole_acceptance_complete': False}
        self.source_paths = tuple(fixture.raw)
        self.initial_main = sys.modules['__main__']
        self.verify_count = 0
        self.manual = None
    def read(self, path): return self.fixture.raw[path]
    def mode(self, path): return 0o644
    def seed(self, name): return self.fixture.seeds[name]
    def verify(self): self.verify_count += 1
    def verify_sources(self): self.verify()
    def verify_entry_source(self, path):
        self.fixture.case.assertEqual(path, CALLER)
    def secondary(self, primary, phase, error):
        notes = getattr(primary, '__protocol_secondary__', [])
        primary.__protocol_secondary__ = [*notes, (phase, error)]
    def install_manual_adapter(self, function, domain):
        self.fixture.case.assertIs(function.__globals__, self.fixture.core.__dict__)
        self.fixture.case.assertTrue(any(function.__code__ is code for code in self.fixture.core.code_tree(self.fixture.core_code)))
        self.manual = self.authority.BoundManualSourceLoader.exec_module
        self.installed = function
        self.authority.BoundManualSourceLoader.exec_module = function
    def verify_manual_adapter(self, function, domain):
        self.fixture.case.assertIs(function, self.installed)
        self.fixture.case.assertIs(self.authority.BoundManualSourceLoader.exec_module, function)
    def restore_manual_adapter(self, function, domain):
        if self.manual is not None:
            self.verify_manual_adapter(function, domain)
            self.authority.BoundManualSourceLoader.exec_module = self.manual
            self.manual = None


class Fixture:
    def __init__(self, case, caller=None, *, context=False, entry=False, fragment=False):
        self.case, self.stack = case, ExitStack()
        case.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory(prefix='som-active-protocol-')))
        self.original_finders = tuple(sys.meta_path)
        self.original_spec = __import__('importlib.util', fromlist=['util']).spec_from_file_location
        self.raw, self.seeds = {}, {}
        self.rows = {}
        self.core, self.core_code = self.source_module('_som_native_active_controls', CORE)
        self.authority, authority_code = self.source_module('_som_same_candidate_native_authority', AUTHORITY)
        graph, graph_code = self.synthetic_seed('_som_bootstrap_same_candidate_graph', BASE + 'same_candidate_graph.py', 'VALUE = 1\n')
        initial, initial_code = self.synthetic_seed('_som_native_trusted_bootstrap_entry', BOOTSTRAP,
            'def _trusted_run(controls, role, argv):\n    return controls.execute_entry(role, argv)\n')
        for role, module, code, path in (
                ('bootstrap.core', self.core, self.core_code, CORE),
                ('bootstrap.authority', self.authority, authority_code, AUTHORITY),
                ('bootstrap.graph', graph, graph_code, BASE + 'same_candidate_graph.py'),
                ('bootstrap.entry', initial, initial_code, BOOTSTRAP)):
            self.seeds[module.__name__] = {'module': module, 'path': path, 'globals': module.__dict__, 'code': code}
            self.add_role(role, path, self.raw[path], kind='seed', module=module.__name__)
        default = ('def run(controls):\n'
                   '    return controls.load_module("target")\n')
        self.add_role('caller', CALLER, caller or default, kind='entry' if entry else 'module',
                      module='__main__' if entry else '_synthetic_active_caller',
                      callers=[{'role': 'bootstrap.entry', 'function': '_trusted_run'}] if entry else [])
        self.add_role('target', BASE + 'synthetic_target.py', 'VALUE = 41\n',
                      callers=[{'role': 'caller', 'function': 'run'}])
        self.add_role('ts.replay', REPLAY,
            'class SchemaReplay:\n    def __init__(self, row):\n        self.row = row\n        self.index = 0\n        self.connections = 0\n')
        for key, (role, path, name) in self.core.PRIVATE.items():
            body = 'VALUE = ' + repr(key) + '\n'
            if key in {'api', 'live'}:
                body += 'import sys\nimport retained_evidence\nRETAINED = sys.modules["retained_evidence"]\n'
            self.add_role(role, path, body, kind='private', module=name, group='m6.api.payload')
        self.rows['m6.api.payload.retained']['callers'] = [
            {'role': 'm6.api.payload.api', 'function': '<module>'},
            {'role': 'm6.api.payload.live', 'function': '<module>'}]
        self.add_role('m6.api.producer', BASE + 'synthetic_producer.py', 'VALUE = "producer"\n')
        if fragment:
            self.rows['ts.replay']['callers'] = [{'role': 'caller', 'function': 'run'}]
            raw = b'from __future__ import annotations\ndef unrelated():\n    raise RuntimeError("excluded original body")\ndef schema():\n    return psycopg.row\n'
            tree = ast.parse(raw)
            original = ast.Module(body=[node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == 'schema'], type_ignores=[])
            row = self.add_role('ts.schema', FRAGMENT, raw, kind='fragment', callers=[{'role': 'caller', 'function': 'run'}],
                bindings={'psycopg': {'kind': 'local-instance', 'local': 'sql', 'type_role': 'ts.replay',
                    'type_name': 'SchemaReplay', 'fields': {'row': 'json', 'index': 'int', 'connections': 'int'}}})
            row['selector'] = {'kind': 'top-level-functions', 'names': ['schema'],
                'ast_sha256': hashlib.sha256(ast.dump(original, include_attributes=False).encode()).hexdigest()}
        if context:
            self.add_role('context', CONTEXT, (REPO / CONTEXT).read_bytes(), module='_synthetic_actual_context',
                bindings={'__som_native_active_controls__': {'kind': 'active-controls'}},
                callers=[{'role': 'caller', 'function': 'run'}])
            self.add_role('runtime.wheels', WHEELS,
                'class Sources:\n    def __init__(self, repo, revision): pass\n'
                '    def read(self, path): return b"{}"\n'
                'POLICY = "synthetic-policy"\n'
                'def admitted_runtime(root, sources, policy, revision):\n'
                '    return ("synthetic-reuse", "synthetic-qualification", {}, {}, "synthetic-native")\n'
                'def admitted_native_runtime(root, sources, policy, revision):\n'
                '    return ("synthetic-reuse", "synthetic-qualification", {}, {}, "synthetic-native")\n',
                module='_synthetic_wheel_runtime', callers=[{'role': 'context', 'function': 'Context.runtime'}])
        for role, row in self.rows.items(): row['closure'] = sorted(self.rows)
        self.policy = {'roles': self.rows, 'sources': {path: {'sha256': self.core.sha(raw), 'mode': 0o644} for path, raw in self.raw.items()},
                       'static_edges': [], 'imports': [], 'bridges': [], 'context_keys': {},
                       'groups': {'m6.api.payload': {'callers': [{'role': 'caller', 'function': 'run'}]}}}
        if context:
            self.policy['context_keys']['wheels'] = 'runtime.wheels'
            self.policy['bridges'].append({'kind': 'context-module', 'role': 'context', 'function': 'Context.module'})
        self.transfer = ProtocolTransfer(self)
        self.domain = self.core._Domain(self.core._KEY, self.transfer, SimpleNamespace(data=self.policy))
        self.controls = self.core._Lease(self.core._KEY, self.domain, True)
        self.stack.callback(self.finish)
    def source_module(self, name, path):
        raw = (REPO / path).read_bytes()
        return self.synthetic_seed(name, path, raw)
    def synthetic_seed(self, name, path, raw):
        raw = raw.encode() if type(raw) is str else raw
        self.raw[path] = raw
        module = ModuleType(name)
        module.__file__, module.__package__ = str(REPO / path), ''
        code = compile(raw, module.__file__, 'exec', dont_inherit=True)
        self.stack.enter_context(mock.patch.dict(sys.modules, {name: module}))
        exec(code, module.__dict__)
        return module, code
    def add_role(self, role, path, raw, *, kind='module', module=None, callers=None, group=None, bindings=None):
        raw = raw.encode() if type(raw) is str else raw
        self.raw[path] = raw
        target = self.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)
        self.rows[role] = {'kind': kind, 'source': path, 'module': module or '_synthetic_' + role.replace('.', '_'),
            'package': '', 'is_package': False, 'callers': callers or [], 'closure': [role],
            'bindings': bindings or {}, 'group': group}
        return self.rows[role]
    def caller(self):
        return self.domain.load_known('caller', self.domain.prepare('caller'), None)
    def run(self): return self.caller().run(self.controls)
    def finish(self):
        self.controls.close()
        self.case.assertEqual(len(sys.meta_path), len(self.original_finders))
        self.case.assertTrue(all(a is b for a, b in zip(sys.meta_path, self.original_finders)))
        self.case.assertIs(__import__('importlib.util', fromlist=['util']).spec_from_file_location, self.original_spec)


class ActiveProtocolTests(unittest.TestCase):
    def test_actual_registered_caller_loads_same_target_module_and_code_before_exec(self):
        f = Fixture(self)
        target = f.run()
        self.assertEqual(target.VALUE, 41)
        self.assertIs(f.run(), target)
        record = f.domain.records['target']
        self.assertIs(record.module, target)
        self.assertTrue(record.codes)
        self.assertGreater(f.transfer.verify_count, 0)

    def test_same_filename_foreign_code_is_refused_before_target_exec(self):
        f = Fixture(self)
        namespace = {'controls': f.controls}
        with self.assertRaisesRegex(ValueError, 'CodeType/globals'):
            exec(compile('controls.load_module("target")', str(f.root / CALLER), 'exec'), namespace)
        self.assertNotIn('target', f.domain.records)

    def test_same_registered_code_with_foreign_globals_is_refused(self):
        f = Fixture(self)
        function = f.caller().run
        clone = FunctionType(function.__code__, {'__builtins__': function.__builtins__})
        with self.assertRaisesRegex(ValueError, 'CodeType/globals'):
            clone(f.controls)
        self.assertNotIn('target', f.domain.records)

    def test_unknown_intermediate_frame_cannot_launder_a_registered_caller(self):
        f = Fixture(self, 'def run(controls):\n    return foreign(controls)\n')
        def foreign(controls): return controls.load_module('target')
        f.caller().foreign = foreign
        with self.assertRaisesRegex(ValueError, 'CodeType/globals'):
            f.run()
        self.assertNotIn('target', f.domain.records)

    def test_missing_transitive_source_closure_refuses_before_body(self):
        f = Fixture(self)
        f.rows['target']['closure'] = ['target']
        f.policy['static_edges'].append({'caller': f.rows['target']['source'], 'import': 'other', 'source': CALLER})
        with self.assertRaisesRegex(ValueError, 'closure is missing before exec'):
            f.run()
        self.assertNotIn('target', f.domain.records)

    def test_preloaded_target_namespace_is_refused_without_replacement(self):
        f = Fixture(self)
        foreign = ModuleType(f.rows['target']['module'])
        f.caller()  # Keep the owned caller in patch.dict's saved module dictionary.
        with mock.patch.dict(sys.modules, {foreign.__name__: foreign}):
            with self.assertRaisesRegex(ValueError, 'preloaded'):
                f.run()
            self.assertIs(sys.modules[foreign.__name__], foreign)

    def test_fixed_import_executes_same_registered_target_with_exact_origin(self):
        f = Fixture(self, 'import _synthetic_target\ndef run(controls):\n    return _synthetic_target\n')
        f.policy['imports'] = [{'caller': 'caller', 'name': '_synthetic_target', 'level': 0,
            'fromlist': [], 'result': 'target', 'from_roles': {}}]
        f.rows['target']['callers'] = [{'role': 'caller', 'function': '<module>'}]
        self.assertIs(f.run(), f.domain.records['target'].module)
        self.assertEqual(f.run().VALUE, 41)

    def test_fixed_import_refuses_an_unlisted_qualified_origin_before_target_exec(self):
        f = Fixture(self, 'def run(controls):\n    import _synthetic_target\n    return _synthetic_target\n')
        f.policy['imports'] = [{'caller': 'caller', 'name': '_synthetic_target', 'level': 0,
            'fromlist': [], 'result': 'target', 'from_roles': {}}]
        f.rows['target']['callers'] = [{'role': 'caller', 'function': '<module>'}]
        with self.assertRaisesRegex(ValueError, 'caller-to-target'):
            f.run()
        self.assertNotIn('target', f.domain.records)

    def test_borrowed_close_keeps_original_finder_and_spec_same_objects(self):
        f = Fixture(self)
        finder, spec = f.domain.finder, f.domain.spec
        lease = f.controls.borrow()
        lease.close()
        self.assertIn(finder, sys.meta_path)
        self.assertIs(__import__('importlib.util', fromlist=['util']).spec_from_file_location, spec)
        self.assertEqual(f.run().VALUE, 41)

    def test_actual_guard_rules_mutation_refuses_before_target_body(self):
        f = Fixture(self)
        f.domain.finder.allowed.add('foreign.py')
        with self.assertRaisesRegex(ValueError, 'original finder source admission'):
            f.run()
        f.domain.finder.allowed.remove('foreign.py')
        self.assertNotIn('target', f.domain.records)

    def test_actual_source_function_defaults_change_is_refused_before_use(self):
        f = Fixture(self, 'def run(controls, reader=None):\n    return controls.load_module("target")\n')
        caller = f.caller()
        original_defaults = caller.run.__defaults__
        try:
            caller.run.__defaults__ = (object(),)
            with self.assertRaisesRegex(ValueError, 'invocation bindings changed'):
                f.run()
        finally:
            caller.run.__defaults__ = original_defaults
        self.assertIs(caller.run.__defaults__, original_defaults)
        self.assertNotIn('target', f.domain.records)

    def test_body_error_survives_foreign_namespace_cleanup_refusal(self):
        f = Fixture(self)
        raw = b'import sys\nfrom types import ModuleType\nsys.modules[__name__] = ModuleType("foreign-replacement")\nraise RuntimeError("first actual body failure")\n'
        path = f.rows['target']['source']
        f.raw[path] = raw
        f.policy['sources'][path]['sha256'] = f.core.sha(raw)
        name = f.rows['target']['module']
        f.stack.callback(lambda: sys.modules.pop(name, None))
        with self.assertRaisesRegex(RuntimeError, 'first actual body failure') as caught:
            f.run()
        self.assertEqual(caught.exception.__protocol_secondary__[0][0], 'role-exec-cleanup')
        self.assertEqual(sys.modules[name].__name__, 'foreign-replacement')

    def test_group_load_uses_private_aliases_and_keeps_collector_module_unchanged(self):
        f = Fixture(self, 'def run(controls):\n    return controls.load_private_group("m6.api.payload")\n')
        collector = ModuleType('retained_evidence')
        f.caller()  # Keep the owned caller in patch.dict's saved module dictionary.
        with mock.patch.dict(sys.modules, {'retained_evidence': collector}):
            with f.run() as group:
                self.assertEqual(set(group), {'retained', 'api', 'live', 'node'})
                self.assertIs(group['api'].RETAINED, group['retained'])
                self.assertIs(group['live'].retained_evidence, group['retained'])
                self.assertIs(sys.modules['retained_evidence'], collector)
            self.assertIs(sys.modules['retained_evidence'], collector)
        self.assertIsNone(f.domain.group)

    def test_group_collision_refuses_before_any_member_body(self):
        f = Fixture(self, 'def run(controls):\n    return controls.load_private_group("m6.api.payload")\n')
        name = f.core.PRIVATE['node'][2]
        collision = ModuleType(name)
        f.caller()  # Keep the owned caller in patch.dict's saved module dictionary.
        with mock.patch.dict(sys.modules, {name: collision}):
            with self.assertRaisesRegex(ValueError, 'namespace collision'):
                f.run()
            self.assertIs(sys.modules[name], collision)
        self.assertFalse(any(role in f.domain.records for role, _, _ in f.core.PRIVATE.values()))

    def test_actual_trusted_entry_is_registered_before_its_body(self):
        body = ('from __future__ import annotations\n'
                'def run(controls):\n    return controls.load_module("target")\n'
                'from _som_native_active_controls_binding import controls\n'
                'target = controls.load_module("target")\n'
                'if target.VALUE != 41: raise RuntimeError("wrong target")\n'
                'import sys\n'
                'if sys.modules["__main__"] is not sys.modules[__name__]: raise RuntimeError("wrong main")\n'
                'raise SystemExit(7)\n')
        f = Fixture(self, body, entry=True)
        f.rows['caller']['bindings'] = {'__som_native_active_controls__': {'kind': 'active-controls'}}
        f.rows['target']['callers'] = [{'role': 'caller', 'function': '<module>'}]
        old_main, old_argv = sys.modules['__main__'], sys.argv
        result = f.seeds['_som_native_trusted_bootstrap_entry']['module']._trusted_run(f.controls, 'caller', ['data-only'])
        self.assertEqual(result, 7)
        self.assertNotIn('caller', f.domain.records)
        self.assertIs(sys.modules['__main__'], old_main)
        self.assertIs(sys.argv, old_argv)

    def test_declared_general_binding_keeps_actual_role_origin(self):
        f = Fixture(self, 'def run(ignored):\n    from _som_native_active_controls_binding import controls\n    return controls.load_module("target")\n')
        f.rows['caller']['bindings'] = {'__som_native_active_controls__': {'kind': 'active-controls'}}
        self.assertEqual(f.run().VALUE, 41)

    def test_general_binding_replacement_cannot_call_foreign_handle_methods(self):
        f = Fixture(self, 'def run(ignored):\n    from _som_native_active_controls_binding import controls\n    return controls.load_module("target")\n')
        f.rows['caller']['bindings'] = {'__som_native_active_controls__': {'kind': 'active-controls'}}
        caller = f.caller()
        original = caller.__som_native_active_controls__
        calls = []
        class Foreign:
            def load_module(self, *args): calls.append('foreign')
        caller.__som_native_active_controls__ = Foreign()
        try:
            with self.assertRaisesRegex(ValueError, 'binding was replaced'):
                f.run()
            self.assertEqual(calls, [])
        finally:
            caller.__som_native_active_controls__ = original

    def test_unregistered_initial_entry_never_runs_qa_body(self):
        f = Fixture(self, 'raise RuntimeError("QA body must not run")\n', entry=True)
        with self.assertRaisesRegex(ValueError, 'CodeType/globals'):
            f.controls.execute_entry('caller', [])
        self.assertNotIn('caller', f.domain.records)

    def test_fake_sysmodule_transfer_class_cannot_run_a_foreign_verifier(self):
        f = Fixture(self)
        calls = []
        class ForeignTransfer:
            def verify_factory_call(self, *args): calls.append('foreign')
        foreign = ModuleType(f.core.ENTRY_MODULE)
        foreign._ActiveTransfer = ForeignTransfer
        with mock.patch.dict(sys.modules, {f.core.ENTRY_MODULE: foreign}):
            with self.assertRaisesRegex(ValueError, 'actual fresh trusted bootstrap'):
                f.core._activate_from_bootstrap(ForeignTransfer())
        self.assertEqual(calls, [])

    def test_pending_complete_policy_is_an_explicit_refusal(self):
        f = Fixture(self)
        schema = (REPO / BASE / 'native_active_roles.schema.json').read_bytes()
        design_raw = (HERE / 'native_active_policy_design_fixture.json').read_bytes()
        self.assertEqual(hashlib.sha256(design_raw).hexdigest(),
                         '35bbd6403930ad790d6a6bccac176138f1fd541f5b07a103479c8546f146a875')
        design_fixture = json.loads(design_raw)
        pending = {**f.policy, 'version': 'som.native-active-role-policy.v1',
                   'complete': False, 'pending': ['explicit protocol pending source map'],
                   'product_acceptance_passed': False, 'compiler_acceptance_passed': False,
                   'whole_acceptance_complete': False, 'design': design_fixture['design'],
                   'groups': {'m6.api.payload': {
                       **f.policy['groups']['m6.api.payload'],
                       'members': {name: role for name, (role, _, _) in f.core.PRIVATE.items()},
                       'producer_role': 'm6.api.producer'}}}
        schema_value = json.loads(schema)
        f.core.validate(pending, schema_value, schema_value)
        examples = json.dumps(pending).encode()
        original = f.transfer.read
        def read(path):
            if path == f.core.SCHEMA: return schema
            if path == f.core.POLICY: return examples
            return original(path)
        f.transfer.read = read
        with self.assertRaisesRegex(ValueError, 'policy remains pending'):
            f.core._Policy(f.transfer)


class ActualContextProtocolTests(unittest.TestCase):
    CALLER = ('from pathlib import Path\nimport threading\n'
        'def run(controls):\n'
        '    module = controls.load_module("context")\n'
        '    ctx = module.Context.__new__(module.Context)\n'
        '    ctx.qa_mode = "native-qa-v1"\n'
        '    ctx.repo, ctx.helper_revision, ctx.candidate_revision = controls.root, controls.helper_revision, controls.candidate_revision\n'
        '    ctx.modules, ctx.cache, ctx.module_lock = {}, {}, threading.RLock()\n'
        '    ctx.data = {"runtime_root": str(controls.root / "synthetic-runtime")}\n'
        '    return ctx, ctx.runtime()\n')

    def test_whole_actual_context_module_and_runtime_bridge_use_actual_origin(self):
        f = Fixture(self, self.CALLER, context=True)
        ctx, runtime = f.run()
        self.assertEqual(runtime['reuse'], 'synthetic-reuse')
        self.assertIs(ctx.runtime(), runtime)
        self.assertIs(ctx.modules['wheels'], f.domain.records['runtime.wheels'].module)
        self.assertIn('_native_source_controls', vars(type(ctx)))

    def test_actual_context_rejects_replaced_global_handle_before_fake_methods(self):
        f = Fixture(self, self.CALLER, context=True)
        ctx, _ = f.run()
        module = f.domain.records['context'].module
        original = module.__som_native_active_controls__
        calls = []
        class Fake:
            def borrow(self): calls.append('borrow'); return self
            def verify_installed(self): calls.append('verify')
        module.__som_native_active_controls__ = Fake()
        try:
            with self.assertRaisesRegex(ValueError, 'binding was replaced'):
                ctx._native_source_controls()
            self.assertEqual(calls, [])
        finally:
            module.__som_native_active_controls__ = original

    def test_context_bridge_cannot_launder_a_foreign_caller(self):
        f = Fixture(self, self.CALLER, context=True)
        ctx, _ = f.run()
        with self.assertRaisesRegex(ValueError, 'CodeType/globals'):
            ctx.module('wheels', WHEELS)

    def test_context_module_cache_replacement_is_refused(self):
        f = Fixture(self, self.CALLER, context=True)
        ctx, _ = f.run()
        # Invoke the actual registered Context.runtime origin again.
        ctx.cache.clear()
        ctx.modules['wheels'] = ModuleType('foreign-cache')
        with self.assertRaisesRegex(ValueError, 'cache changed identity'):
            ctx.runtime()
        ctx.modules['wheels'] = f.domain.records['runtime.wheels'].module


class FragmentProtocolTests(unittest.TestCase):
    def fixture(self):
        caller = ('def run(controls, row=None):\n'
                  '    replay = controls.load_module("ts.replay")\n'
                  '    sql = replay.SchemaReplay({"value": 1} if row is None else row)\n'
                  '    return controls.load_fragment("ts.schema")\n')
        return Fixture(self, caller, fragment=True)
    def test_original_whole_function_and_typed_local_are_fresh_for_every_call(self):
        f = self.fixture()
        caller = f.caller()
        with caller.run(f.controls, {'value': 11}) as first:
            self.assertEqual(first.schema(), {'value': 11})
            with caller.run(f.controls, {'value': 22}) as second:
                self.assertEqual(second.schema(), {'value': 22})
                self.assertIsNot(first.schema, second.schema)
                self.assertEqual(first.schema(), {'value': 11})
        self.assertFalse(any(record.role == 'ts.schema' for record in f.domain.records.values()))
    def test_changed_original_fragment_ast_refuses_before_exec(self):
        f = self.fixture()
        f.rows['ts.schema']['selector']['ast_sha256'] = '0' * 64
        with self.assertRaisesRegex(ValueError, 'original fragment AST differs'):
            f.run()
        self.assertFalse(any(record.role == 'ts.schema' for record in f.domain.records.values()))
    def test_typed_local_json_rejects_foreign_objects_without_calling_hooks(self):
        f = self.fixture()
        class Foreign:
            def __iter__(self): raise AssertionError('foreign iterator must not run')
            def __hash__(self): raise AssertionError('foreign hash must not run')
        with self.assertRaisesRegex(ValueError, 'foreign local JSON'):
            f.caller().run(f.controls, {'value': Foreign()})
    def test_closed_fragment_cannot_supply_exports(self):
        f = self.fixture()
        scope = f.run()
        scope.close()
        with self.assertRaisesRegex(ValueError, 'fragment scope is closed'):
            scope.schema


    def test_replacement_typed_class_with_original_methods_refuses_before_foreign_hook(self):
        f = self.fixture()
        replay = f.domain.load_known('ts.replay', f.domain.prepare('ts.replay'), None)
        original, calls = replay.SchemaReplay, []
        def foreign_getattribute(value, name):
            calls.append(name)
            return object.__getattribute__(value, name)
        attributes = dict(type.__getattribute__(original, '__dict__'))
        attributes.pop('__dict__', None)
        attributes.pop('__weakref__', None)
        attributes['__getattribute__'] = foreign_getattribute
        replacement = type('SchemaReplay', original.__bases__, attributes)
        self.assertIs(replacement.__init__, original.__init__)
        replay.SchemaReplay = replacement
        try:
            with self.assertRaisesRegex(ValueError, 'source-owned typed class export changed'):
                f.caller().run(f.controls)
            self.assertEqual(calls, [])
            self.assertFalse(any(record.role == 'ts.schema' for record in f.domain.records.values()))
        finally:
            replay.SchemaReplay = original

    def test_original_typed_class_added_hook_refuses_without_invoking_it(self):
        f = self.fixture()
        replay = f.domain.load_known('ts.replay', f.domain.prepare('ts.replay'), None)
        original, calls = replay.SchemaReplay, []
        def foreign_getattribute(value, name):
            calls.append(name)
            return object.__getattribute__(value, name)
        original.__getattribute__ = foreign_getattribute
        try:
            with self.assertRaisesRegex(ValueError, 'source-owned typed class namespace changed'):
                f.caller().run(f.controls)
            self.assertEqual(calls, [])
            self.assertFalse(any(record.role == 'ts.schema' for record in f.domain.records.values()))
        finally:
            del original.__getattribute__

    def test_already_bound_fragment_rechecks_typed_class_before_export_use(self):
        f = self.fixture()
        scope = f.run()
        original, calls = f.domain.records['ts.replay'].module.SchemaReplay, []
        def foreign_getattribute(value, name):
            calls.append(name)
            return object.__getattribute__(value, name)
        original.__getattribute__ = foreign_getattribute
        try:
            with self.assertRaisesRegex(ValueError, 'source-owned typed class namespace changed'):
                scope.schema()
            self.assertEqual(calls, [])
        finally:
            del original.__getattribute__
            scope.close()

    def test_typed_class_inherited_hook_change_refuses_before_foreign_hook(self):
        f = self.fixture()
        path = f.rows['ts.replay']['source']
        raw = (b'class ReplayBase:\n    pass\n'
               b'class SchemaReplay(ReplayBase):\n    def __init__(self, row):\n'
               b'        self.row, self.index, self.connections = row, 0, 0\n')
        f.raw[path] = raw
        (f.root / path).write_bytes(raw)
        f.policy['sources'][path]['sha256'] = f.core.sha(raw)
        replay = f.domain.load_known('ts.replay', f.domain.prepare('ts.replay'), None)
        base, calls = replay.ReplayBase, []
        def foreign_getattribute(value, name):
            calls.append(name)
            return object.__getattribute__(value, name)
        base.__getattribute__ = foreign_getattribute
        try:
            with self.assertRaisesRegex(ValueError, 'source-owned typed class namespace changed'):
                f.caller().run(f.controls)
            self.assertEqual(calls, [])
        finally:
            del base.__getattribute__

    def test_typed_class_added_property_refuses_before_foreign_descriptor(self):
        f = self.fixture()
        replay = f.domain.load_known('ts.replay', f.domain.prepare('ts.replay'), None)
        original, calls = replay.SchemaReplay, []
        def foreign_getter(value):
            calls.append('descriptor')
            return None
        original.foreign = property(foreign_getter)
        try:
            with self.assertRaisesRegex(ValueError, 'source-owned typed class namespace changed'):
                f.caller().run(f.controls)
            self.assertEqual(calls, [])
        finally:
            del original.foreign

    def test_unrelated_source_owned_class_adapter_remains_allowed(self):
        f = self.fixture()
        path = f.rows['target']['source']
        raw = (b'class Adapter:\n    def result(self): return 41\n'
               b'    def replacement(self): return 42\n')
        f.raw[path] = raw
        (f.root / path).write_bytes(raw)
        f.policy['sources'][path]['sha256'] = f.core.sha(raw)
        target = f.domain.load_known('target', f.domain.prepare('target'), None)
        target.Adapter.result = target.Adapter.replacement
        f.controls.verify_installed()
        self.assertEqual(target.Adapter().result(), 42)
        with f.run() as scope:
            self.assertEqual(scope.schema(), {'value': 1})



class FixedBindingProtocolTests(unittest.TestCase):
    def fixture(self, caller=None):
        body = ('def run(controls):\n'
                '    controls.load_module("target")\n'
                '    q.require(True, "module export")\n'
                '    require(True, "fixed export")\n'
                '    if q.SHA.fullmatch("a" * 64) is None: raise AssertionError("pattern")\n'
                '    marker = Refusal("source-owned refusal")\n'
                '    return q, Refusal, require, marker\n')
        f = Fixture(self, caller or body)
        # Reuse an already admitted, unused synthetic source slot. The shared
        # fixture setup and its original finder source universe stay unchanged.
        path = f.rows['ts.replay']['source']
        raw = ('import re\n'
               'CALLS = []\n'
               'SHA = re.compile("[0-9a-f]{64}")\n'
               'class Refusal(ValueError):\n    pass\n'
               'def require(value, message):\n    if not value: raise Refusal(message)\n'
               'def alternate(value, message):\n    CALLS.append("alternate")\n'
               'def unique_keys(value): return value\n'
               'def manifest_data(value): return value\n'
               'def clean_path(value): return value\n'
               'def no_links(value): return value\n'
               'def sha(value): return value\n')
        f.add_role('runtime.qualifier', path, raw, module='_synthetic_runtime_qualifier')
        f.policy['sources'][path] = {'sha256': f.core.sha(f.raw[path]), 'mode': 0o644}
        bindings = {'q': {'kind': 'role-module', 'role': 'runtime.qualifier'}}
        for name in ('Refusal', 'require', 'SHA', 'unique_keys', 'manifest_data', 'clean_path', 'no_links', 'sha'):
            bindings[name] = {'kind': 'role-export', 'role': 'runtime.qualifier', 'attribute': name}
        f.rows['caller']['bindings'] = bindings
        for row in f.rows.values():
            row['closure'] = sorted(f.rows)
        return f

    def test_fixed_module_alias_refuses_before_foreign_module_hook(self):
        f = self.fixture()
        caller, calls = f.caller(), []
        original = caller.q
        foreign = ModuleType('foreign-qualifier')
        def foreign_getattr(name):
            calls.append(name)
            raise AssertionError('foreign module hook must not run')
        foreign.__getattr__ = foreign_getattr
        caller.q = foreign
        try:
            with self.assertRaisesRegex(ValueError, 'declared fixed role binding was replaced'):
                caller.run(f.controls)
            self.assertEqual(calls, [])
            self.assertNotIn('target', f.domain.records)
        finally:
            caller.q = original

    def test_fixed_class_alias_refuses_before_foreign_class_call(self):
        f = self.fixture()
        caller, calls = f.caller(), []
        original = caller.Refusal
        class ForeignMeta(type):
            def __call__(cls, *args, **kwargs):
                calls.append('construct')
                raise AssertionError('foreign class hook must not run')
        class ForeignRefusal(ValueError, metaclass=ForeignMeta):
            pass
        caller.Refusal = ForeignRefusal
        try:
            with self.assertRaisesRegex(ValueError, 'declared fixed role binding was replaced'):
                caller.run(f.controls)
            self.assertEqual(calls, [])
            self.assertNotIn('target', f.domain.records)
        finally:
            caller.Refusal = original

    def test_fixed_function_alias_refuses_another_registered_source_function(self):
        f = self.fixture()
        caller = f.caller()
        qualifier, original = caller.q, caller.require
        self.assertTrue(f.domain.source_callable(qualifier.alternate))
        caller.require = qualifier.alternate
        try:
            with self.assertRaisesRegex(ValueError, 'declared fixed role binding was replaced'):
                caller.run(f.controls)
            self.assertEqual(qualifier.CALLS, [])
            self.assertNotIn('target', f.domain.records)
        finally:
            caller.require = original

    def test_provider_export_change_refuses_even_when_caller_alias_is_original(self):
        f = self.fixture()
        caller = f.caller()
        qualifier, original = caller.q, caller.require
        qualifier.require = qualifier.alternate
        try:
            self.assertIs(caller.require, original)
            with self.assertRaisesRegex(ValueError, 'declared fixed role target export changed'):
                caller.run(f.controls)
            self.assertEqual(qualifier.CALLS, [])
        finally:
            qualifier.require = original

    def test_provider_module_alias_replacement_refuses_before_foreign_hook(self):
        f = self.fixture()
        caller, calls = f.caller(), []
        qualifier = caller.q
        foreign = ModuleType(qualifier.__name__)
        def foreign_getattr(name):
            calls.append(name)
            raise AssertionError('foreign provider hook must not run')
        foreign.__getattr__ = foreign_getattr
        sys.modules[qualifier.__name__] = foreign
        try:
            with self.assertRaisesRegex(ValueError, 'fixed binding target module ownership changed'):
                caller.run(f.controls)
            self.assertEqual(calls, [])
        finally:
            sys.modules[qualifier.__name__] = qualifier

    def test_executing_body_cannot_skip_fixed_binding_check(self):
        body = ('from _som_native_active_controls_binding import controls\n'
                'q = None\n'
                'controls.load_module("target")\n')
        f = self.fixture(body)
        f.rows['caller']['bindings']['__som_native_active_controls__'] = {'kind': 'active-controls'}
        f.rows['target']['callers'] = [{'role': 'caller', 'function': '<module>'}]
        with self.assertRaisesRegex(ValueError, 'declared fixed role binding was replaced'):
            f.caller()
        self.assertNotIn('caller', f.domain.records)
        self.assertNotIn('target', f.domain.records)
        self.assertEqual(f.domain.records['runtime.qualifier'].module.CALLS, [])

    def test_missing_provider_export_refuses_without_module_getattr(self):
        f = self.fixture()
        caller, calls = f.caller(), []
        qualifier, original = caller.q, caller.require
        def foreign_getattr(name):
            calls.append(name)
            raise AssertionError('provider fallback must not run')
        qualifier.__getattr__ = foreign_getattr
        del qualifier.require
        try:
            with self.assertRaisesRegex(ValueError, 'fixed role export is absent'):
                caller.run(f.controls)
            self.assertEqual(calls, [])
        finally:
            qualifier.require = original
            del qualifier.__getattr__

    def test_provider_pattern_change_refuses_before_foreign_fullmatch_hook(self):
        f = self.fixture()
        caller, calls = f.caller(), []
        qualifier, original = caller.q, caller.SHA
        class ForeignPattern:
            def __getattribute__(self, name):
                calls.append(name)
                raise AssertionError('foreign pattern hook must not run')
        qualifier.SHA = ForeignPattern()
        try:
            self.assertIs(caller.SHA, original)
            with self.assertRaisesRegex(ValueError, 'declared fixed role target export changed'):
                caller.run(f.controls)
            self.assertEqual(calls, [])
        finally:
            qualifier.SHA = original

    def test_fixed_exports_keep_unrelated_source_adapter_and_data_mutable(self):
        f = self.fixture()
        path = f.rows['target']['source']
        raw = (b'class Adapter:\n    def result(self): return 41\n'
               b'    def replacement(self): return 42\n')
        f.raw[path] = raw
        (f.root / path).write_bytes(raw)
        f.policy['sources'][path]['sha256'] = f.core.sha(raw)
        caller = f.caller()
        target = f.domain.load_known('target', f.domain.prepare('target'), None)
        target.Adapter.result = target.Adapter.replacement
        caller.q.CALLS.append('ordinary mutable QA data')
        f.controls.verify_installed()
        module, refusal, requirement, marker = caller.run(f.controls)
        self.assertIs(module, f.domain.records['runtime.qualifier'].module)
        self.assertIs(refusal, module.Refusal)
        self.assertIs(requirement, module.require)
        self.assertIs(type(marker), refusal)
        self.assertEqual(target.Adapter().result(), 42)
        self.assertEqual(module.CALLS, ['ordinary mutable QA data'])


class FixedExportClassStateTests(unittest.TestCase):
    def fixture(self, caller=None, base_source=None):
        f = FixedBindingProtocolTests.fixture(self, caller)
        if base_source is not None:
            path = f.rows['runtime.qualifier']['source']
            raw = f.raw[path].replace(b'class Refusal(ValueError):\n    pass\n', base_source)
            self.assertNotEqual(raw, f.raw[path])
            f.raw[path] = raw
            (f.root / path).write_bytes(raw)
            f.policy['sources'][path]['sha256'] = f.core.sha(raw)
        return f

    def test_fixed_same_class_new_hook_refuses_before_construction(self):
        f = self.fixture()
        caller, calls = f.caller(), []
        refusal = caller.Refusal
        def foreign_new(cls, *args):
            calls.append('new')
            return ValueError.__new__(cls, *args)
        refusal.__new__ = foreign_new
        try:
            self.assertIs(caller.Refusal, caller.q.Refusal)
            with self.assertRaisesRegex(ValueError, 'fixed exported class namespace changed'):
                caller.run(f.controls)
            self.assertEqual(calls, [])
            self.assertNotIn('target', f.domain.records)
        finally:
            del refusal.__new__

    def test_fixed_same_class_init_hook_refuses_before_construction(self):
        f = self.fixture()
        caller, calls = f.caller(), []
        refusal = caller.Refusal
        def foreign_init(value, *args):
            calls.append('init')
        refusal.__init__ = foreign_init
        try:
            with self.assertRaisesRegex(ValueError, 'fixed exported class namespace changed'):
                caller.run(f.controls)
            self.assertEqual(calls, [])
        finally:
            del refusal.__init__

    def test_fixed_class_added_descriptor_refuses_before_getter(self):
        body = ('def run(controls):\n    controls.load_module("target")\n'
                '    return Refusal("marker").foreign\n')
        f = self.fixture(body)
        caller, calls = f.caller(), []
        refusal = caller.Refusal
        def foreign_getter(value):
            calls.append('descriptor')
            return 'changed'
        refusal.foreign = property(foreign_getter)
        try:
            with self.assertRaisesRegex(ValueError, 'fixed exported class namespace changed'):
                caller.run(f.controls)
            self.assertEqual(calls, [])
        finally:
            del refusal.foreign

    def test_fixed_class_inherited_new_hook_refuses_before_construction(self):
        base = (b'class RefusalBase(ValueError):\n    pass\n'
                b'class Refusal(RefusalBase):\n    pass\n')
        f = self.fixture(base_source=base)
        caller, calls = f.caller(), []
        parent = caller.q.RefusalBase
        def foreign_new(cls, *args):
            calls.append('inherited-new')
            return ValueError.__new__(cls, *args)
        parent.__new__ = foreign_new
        try:
            with self.assertRaisesRegex(ValueError, 'fixed exported class namespace changed'):
                caller.run(f.controls)
            self.assertEqual(calls, [])
        finally:
            del parent.__new__

    def test_fixed_source_base_registered_adapter_change_refuses(self):
        base = (b'class RefusalBase(ValueError):\n'
                b'    def message(self): return "original"\n'
                b'    def replacement(self): CALLS.append("replacement"); return "changed"\n'
                b'class Refusal(RefusalBase):\n    pass\n')
        body = ('def run(controls):\n    controls.load_module("target")\n'
                '    return Refusal("marker").message()\n')
        f = self.fixture(body, base_source=base)
        caller = f.caller()
        parent, original = caller.q.RefusalBase, caller.q.RefusalBase.message
        self.assertTrue(f.domain.source_callable(parent.replacement))
        parent.message = parent.replacement
        try:
            with self.assertRaisesRegex(ValueError, 'fixed exported class namespace changed'):
                caller.run(f.controls)
            self.assertEqual(caller.q.CALLS, [])
        finally:
            parent.message = original

    def test_fixed_class_MRO_change_refuses_before_use(self):
        base = (b'class OriginalBase(ValueError):\n    pass\n'
                b'class AlternateBase(ValueError):\n    pass\n'
                b'class Refusal(OriginalBase):\n    pass\n')
        f = self.fixture(base_source=base)
        caller = f.caller()
        original = caller.Refusal.__bases__
        caller.Refusal.__bases__ = (caller.q.AlternateBase,)
        try:
            with self.assertRaisesRegex(ValueError, 'fixed exported class MRO changed'):
                caller.run(f.controls)
        finally:
            caller.Refusal.__bases__ = original

    def test_fixed_class_is_pinned_before_the_first_consumer_load(self):
        f = self.fixture()
        qualifier = f.domain.load_known('runtime.qualifier', f.domain.prepare('runtime.qualifier'), None)
        self.assertIn(('Refusal',), f.domain.records['runtime.qualifier'].fixed_export_classes)
        calls = []
        def foreign_new(cls, *args):
            calls.append('before-consumer')
            return ValueError.__new__(cls, *args)
        qualifier.Refusal.__new__ = foreign_new
        try:
            with self.assertRaisesRegex(ValueError, 'fixed exported class namespace changed'):
                f.caller()
            self.assertEqual(calls, [])
            self.assertNotIn('caller', f.domain.records)
        finally:
            del qualifier.Refusal.__new__

    def test_fixed_builtin_base_keeps_unrelated_same_provider_class_mutable(self):
        f = self.fixture()
        path = f.rows['runtime.qualifier']['source']
        raw = f.raw[path] + (b'class UnrelatedAdapter:\n    def result(self): return 41\n'
                             b'    def replacement(self): return 42\n')
        f.raw[path] = raw
        (f.root / path).write_bytes(raw)
        f.policy['sources'][path]['sha256'] = f.core.sha(raw)
        caller = f.caller()
        self.assertIn(ValueError, caller.Refusal.__mro__)
        self.assertIn(Exception, caller.Refusal.__mro__)
        caller.q.UnrelatedAdapter.result = caller.q.UnrelatedAdapter.replacement
        caller.q.CALLS.append('ordinary mutable QA data')
        f.controls.verify_installed()
        module, refusal, requirement, marker = caller.run(f.controls)
        self.assertIs(refusal, module.Refusal)
        self.assertIs(type(marker), refusal)
        self.assertEqual(module.UnrelatedAdapter().result(), 42)
        self.assertEqual(module.CALLS, ['ordinary mutable QA data'])
        self.assertEqual(set(f.domain.records['runtime.qualifier'].fixed_export_classes), {('Refusal',)})


if __name__ == '__main__':
    unittest.main()

"""GitHub-only source loader protocol fixtures.

The real Core/finder and exact checker, recipe and public runtime sources run
on physical synthetic paths. Admission/source authority are explicit doubles.
The public Sources constructor is bypassed; its real verify_runtime runs.
No generator, compiler, formatter, model, service or product gate runs here.
"""
from __future__ import annotations

import ast
import os
from pathlib import Path
import sys
from types import FunctionType, ModuleType
import unittest

if os.environ.get('GITHUB_ACTIONS') != 'true':
    raise RuntimeError('GitHub tests only')

# Load the exact protocol fixture source under the existing isolated runner.
protocol_path = Path(__file__).absolute().with_name('test_native_active_controls.py')
protocol = ModuleType('_som_shared_helper_active_protocol_fixture')
protocol.__file__, protocol.__cached__, protocol.__package__ = str(protocol_path), None, ''
exec(compile(protocol_path.read_bytes(), str(protocol_path), 'exec', dont_inherit=True), protocol.__dict__)

REPO = protocol.REPO
BASE = 'som-rs/scripts/hosted_readiness/'
CHECKER_PATH = BASE + 'experimental_native_compile.py'
REPLAY_PATH = BASE + 'reconstruct_public_inputs.py'
RAW_PATH = BASE + 'raw_digest_compat.py'
EXPORT_PATH = BASE + 'export_goldens.py'
CHECKER = 'companions.entry.capture.experimental-native-compile'
REPLAY = 'companions.entry.capture.reconstruct-public-inputs'
RAW = CHECKER + '.raw-compat'
EXPORT = 'test.shared.export'
NATIVE_CHECKER = '_som_active_companions_entry_capture_experimental_native_compile'
NATIVE_REPLAY = '_som_active_companions_entry_capture_reconstruct_public_inputs'
RECIPE_PATHS = {
    'probe_flow_maximum_matrix': 'som-core/scripts/probe_flow_maximum_matrix.py',
    'freeze_flow_cases': 'som-core/scripts/freeze_flow_cases.py',
    'som_hosted_serializer': 'som-rs/tests/compiler-v1/serializer.py',
    **{'som_core' + ('.' + name if name != '__init__' else ''):
       'som-core/src/som_core/' + name + '.py'
       for name in ('__init__', 'flow', 'composed', 'related', 'v1', 'business',
                    'flow_compact', 'flow_codec', 'quality', 'compact_names')},
}


class SharedFixture(protocol.Fixture):
    def __init__(self, case):
        caller = ('def run(controls, target="checker"):\n'
                  '    if target == "replay":\n'
                  '        return controls.load_module("' + REPLAY + '")\n'
                  '    return controls.load_module("' + CHECKER + '")\n')
        super().__init__(case, caller)
        self.policy['imports'] = self.imports
        self.policy['static_edges'] = self.edges

    def add_role(self, role, path, raw, **kwargs):
        row = super().add_role(role, path, raw, **kwargs)
        if role != 'm6.api.producer':
            return row
        self.logical, self.imports, self.edges = {}, [], []
        bindings = {'__som_native_active_controls__': {'kind': 'active-controls'},
                    'raw_compat': {'kind': 'role-module', 'role': RAW}}
        for name in ('mask_raw', 'verify_frozen', 'RawDigestError'):
            bindings['_native_' + name] = {'kind': 'role-export', 'role': RAW, 'attribute': name}
        super().add_role(RAW, RAW_PATH, (REPO / RAW_PATH).read_bytes(),
                         module=NATIVE_CHECKER + '_reviewed_raw_compat',
                         callers=[{'role': CHECKER, 'function': '_native_raw_compat'}])
        super().add_role(CHECKER, CHECKER_PATH, (REPO / CHECKER_PATH).read_bytes(),
                         module=NATIVE_CHECKER, bindings=bindings,
                         callers=[{'role': 'caller', 'function': 'run'}])
        super().add_role(EXPORT, EXPORT_PATH, (REPO / EXPORT_PATH).read_bytes(),
                         callers=[{'role': REPLAY, 'function': '<module>'}])
        super().add_role(REPLAY, REPLAY_PATH, (REPO / REPLAY_PATH).read_bytes(),
                         module=NATIVE_REPLAY,
                         bindings={'__som_native_active_controls__': {'kind': 'active-controls'}},
                         callers=[{'role': 'caller', 'function': 'run'}])
        # A real consumer binding makes Core pin the original Sources class.
        super().add_role('test.shared.sources-consumer', BASE + 'synthetic_recipe_consumer.py',
                         'VALUE = 1\n', bindings={'Sources': {'kind': 'role-export',
                            'role': REPLAY, 'attribute': 'Sources'}})
        prefix = REPLAY + '.recipe'
        package = NATIVE_REPLAY + '_recipe_som_core'
        for name, source in RECIPE_PATHS.items():
            if name == 'probe_flow_maximum_matrix':
                target, module = prefix + '.probe', NATIVE_REPLAY + '_recipe_probe'
            elif name == 'som_hosted_serializer':
                target, module = prefix + '.serializer', NATIVE_REPLAY + '_recipe_serializer'
            elif name == 'freeze_flow_cases':
                target, module = prefix + '.freeze', NATIVE_REPLAY + '_recipe_freeze'
            elif name == 'som_core':
                target, module = prefix + '.som-core', package
            else:
                target, module = prefix + '.' + name.replace('_', '-'), package + name[len('som_core'):]
            entry = super().add_role(target, source, (REPO / source).read_bytes(), module=module,
                                     callers=[{'role': REPLAY, 'function': '_native_runtime'}]
                                     if name in {'probe_flow_maximum_matrix', 'som_hosted_serializer'} else [])
            entry['package'] = module if name == 'som_core' else package if name.startswith('som_core.') else ''
            entry['is_package'] = name == 'som_core'
            self.logical[name] = target
        self.recipe_imports()
        self.imports.append({'caller': REPLAY, 'name': 'export_goldens', 'level': 0,
            'fromlist': ['DIRECT_PATHS', 'EXAMPLES', 'FIXTURES', 'PUBLIC_REVISION', 'Refusal',
                         'case_id', 'digest_text', 'no_symlinks', 'parse', 'relative',
                         'require', 'sha', 'value_sha'], 'result': EXPORT, 'from_roles': {}})
        self.edges.append({'caller': REPLAY_PATH, 'import': 'export_goldens', 'source': EXPORT_PATH})
        # Materialize all fixed source/document pins. Do not fake their bytes.
        for source in (*RECIPE_PATHS.values(), 'som-rs/contracts/related-flow.v1.md',
                       'som-rs/contracts/related-business.v1.md',
                       'som-rs/contracts/compiler-v1/field-statement-map.json'):
            destination = self.root / source
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes((REPO / source).read_bytes())
        return row

    def recipe_imports(self):
        def sites(node, scope='<module>', in_function=False):
            for child in ast.iter_child_nodes(node):
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    prefix = scope + '.<locals>.' if in_function else scope + '.' if scope != '<module>' else ''
                    yield from sites(child, prefix + child.name,
                                     isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)))
                else:
                    if isinstance(child, ast.ImportFrom):
                        yield child, scope
                    yield from sites(child, scope, in_function)
        for name, caller in self.logical.items():
            source = self.rows[caller]['source']
            package = name if name == 'som_core' else name.rpartition('.')[0]
            for node, function in sites(ast.parse(self.raw[source])):
                stem = node.module or ''
                if node.level:
                    parts = package.split('.')
                    absolute = '.'.join(parts[:len(parts) - node.level + 1] + ([stem] if stem else []))
                else:
                    absolute = stem
                if absolute not in self.logical:
                    self.case.assertFalse(absolute.startswith(('som_core', 'freeze_flow_cases')),
                                          'unbound repository recipe import')
                    continue
                children = {item.name: self.logical[absolute + '.' + item.name]
                            for item in node.names if absolute + '.' + item.name in self.logical}
                rule = {'caller': caller, 'name': stem, 'level': node.level,
                    'fromlist': [item.name for item in node.names],
                    'result': self.logical[absolute], 'from_roles': children}
                if rule not in self.imports:
                    self.imports.append(rule)
                for target in {self.logical[absolute], *children.values()}:
                    self.edges.append({'caller': source, 'import': stem, 'source': self.rows[target]['source']})
                    rule = {'role': caller, 'function': function}
                    if rule not in self.rows[target]['callers']:
                        self.rows[target]['callers'].append(rule)

    def source_inputs(self, replay):
        sources = object.__new__(replay.Sources)
        sources.repo, sources.pins, sources.cache = self.root, {}, {}
        return sources


class NativeSharedHelperTests(unittest.TestCase):
    def test_checker_uses_actual_core_raw_module_and_original_parser(self):
        f = SharedFixture(self)
        checker = f.run()
        self.assertIs(checker.raw_compat, f.domain.records[RAW].module)
        self.assertEqual(checker.raw_compat.__file__, str(f.root / RAW_PATH))
        self.assertNotEqual(checker.raw_compat.__name__, '_som_reviewed_raw_digest_compat')
        self.assertTrue(f.domain.records[RAW].codes)
        self.assertEqual(checker.parse(b'{"value":1}'), {'value': 1})

    def test_declared_native_checker_missing_handle_refuses_without_legacy_fallback(self):
        f = SharedFixture(self)
        f.rows[CHECKER]['bindings'].pop('__som_native_active_controls__')
        with self.assertRaisesRegex(ValueError, 'actual fixed Context control binding was replaced'):
            f.run()
        self.assertNotIn(CHECKER, f.domain.records)

    def test_foreign_handle_is_refused_before_its_methods(self):
        f = SharedFixture(self)
        checker, calls = f.run(), []
        class ForeignHandle:
            def verify_installed(self):
                calls.append('verify')
                raise AssertionError('foreign handle must not run')
        original = checker.__som_native_active_controls__
        checker.__som_native_active_controls__ = ForeignHandle()
        try:
            with self.assertRaisesRegex(ValueError, 'fixed active-controls binding was replaced'):
                f.run()
            self.assertEqual(calls, [])
        finally:
            checker.__som_native_active_controls__ = original

    def test_public_recipe_uses_real_pins_and_keeps_owner_finder_and_namespaces(self):
        f = SharedFixture(self)
        replay = f.caller().run(f.controls, 'replay')
        sources = f.source_inputs(replay)
        before = {name: module for name, module in sys.modules.items()
                  if name == 'som_core' or name.startswith('som_core.')}
        finder, spec = f.domain.finder, f.domain.spec
        with replay._recipe_runtime(sources) as (maximum, serialize):
            self.assertIs(type(maximum), FunctionType)
            self.assertIs(type(serialize), FunctionType)
            self.assertTrue(f.domain.source_callable(maximum))
            self.assertTrue(f.domain.source_callable(serialize))
        self.assertIs(f.domain.finder, finder)
        self.assertIs(f.domain.spec, spec)
        self.assertFalse(f.domain.closed)
        self.assertEqual({name: module for name, module in sys.modules.items()
                          if name == 'som_core' or name.startswith('som_core.')}, before)
        self.assertIn(REPLAY + '.recipe.probe', f.domain.records)

    def test_changed_public_dependency_refuses_before_any_recipe_exec(self):
        f = SharedFixture(self)
        replay = f.caller().run(f.controls, 'replay')
        sources = f.source_inputs(replay)
        path = f.root / RECIPE_PATHS['som_core.flow']
        original = path.read_bytes()
        try:
            path.write_bytes(original + b'\n# changed pinned dependency\n')
            with self.assertRaisesRegex(replay.Refusal, 'public recipe dependency digest differs'):
                with replay._recipe_runtime(sources):
                    self.fail('changed pinned dependency reached recipe body')
            self.assertFalse(any(role.startswith(REPLAY + '.recipe.') for role in f.domain.records))
        finally:
            path.write_bytes(original)


if __name__ == '__main__':
    unittest.main()

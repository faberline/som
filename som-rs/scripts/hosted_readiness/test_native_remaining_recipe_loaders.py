"""GitHub-only finite Core recipe-loader protocol tests.

These run actual Core and exact helper/runtime bytes on synthetic physical roots.
Authority transfer and source admission are explicit doubles. Constructors are
bypassed; the original Sources.verify/ProofSources.verify are still executed.
No generator, compiler, model, service or product gate runs. No NativeContext or
SourceSeal is qualified by these fixtures.
"""
from __future__ import annotations

import ast
from contextlib import contextmanager
import json
import os
from pathlib import Path
import sys
from types import FunctionType, ModuleType
import unittest
from unittest import mock

if os.environ.get('GITHUB_ACTIONS') != 'true':
    raise RuntimeError('GitHub tests only')

# Use the exact sibling source under the existing isolated runner, not sys.path.
protocol_path = Path(__file__).absolute().with_name('test_native_active_controls.py')
protocol = ModuleType('_som_remaining_recipe_active_protocol_fixture')
protocol.__file__, protocol.__cached__, protocol.__package__ = str(protocol_path), None, ''
exec(compile(protocol_path.read_bytes(), str(protocol_path), 'exec', dont_inherit=True), protocol.__dict__)
REPO = protocol.REPO
BASE = 'som-rs/scripts/hosted_readiness/'
COMPLETE = 'companions.entry.capture.complete-rust-inputs'
PROOF = 'semantic.common-postclose.join.prove-checked-reference'
ROLES = {'companions.entry.capture.complete-rust-inputs': {'bindings': {'__som_native_active_controls__': {'kind': 'active-controls'}},
                                                   'is_package': False,
                                                   'module': '_som_active_companions_entry_capture_complete_rust_inputs',
                                                   'package': '',
                                                   'source': 'som-rs/scripts/hosted_readiness/complete_rust_inputs.py'},
 'companions.entry.capture.complete-rust-inputs.recipe.serializer': {'bindings': {'__native_recipe_inputs': {'fields': {'pins': 'json',
                                                                                                                        'repo': 'path'},
                                                                                                             'kind': 'local-instance',
                                                                                                             'local': 'sources',
                                                                                                             'type_name': 'Sources',
                                                                                                             'type_role': 'companions.entry.capture.complete-rust-inputs'}},
                                                                     'is_package': False,
                                                                     'module': '_som_active_companions_entry_capture_complete_rust_inputs_recipe_serializer',
                                                                     'package': '',
                                                                     'source': 'som-rs/tests/compiler-v1/serializer.py'},
 'companions.entry.capture.export-goldens': {'bindings': {},
                                             'is_package': False,
                                             'module': '_som_active_companions_entry_capture_export_goldens',
                                             'package': '',
                                             'source': 'som-rs/scripts/hosted_readiness/export_goldens.py'},
 'companions.entry.capture.map-rust-inputs': {'bindings': {'__som_native_public_sources_type__': {'attribute': 'Sources',
                                                                                                  'kind': 'role-export',
                                                                                                  'role': 'companions.entry.capture.reconstruct-public-inputs'}},
                                              'is_package': False,
                                              'module': '_som_active_companions_entry_capture_map_rust_inputs',
                                              'package': '',
                                              'source': 'som-rs/scripts/hosted_readiness/map_rust_inputs.py'},
 'companions.entry.capture.reconstruct-public-inputs': {'bindings': {'__som_native_active_controls__': {'kind': 'active-controls'}},
                                                        'is_package': False,
                                                        'module': '_som_active_companions_entry_capture_reconstruct_public_inputs',
                                                        'package': '',
                                                        'source': 'som-rs/scripts/hosted_readiness/reconstruct_public_inputs.py'},
 'semantic.common-postclose.join.export-goldens': {'bindings': {},
                                                   'is_package': False,
                                                   'module': '_som_active_semantic_common_postclose_join_export_goldens',
                                                   'package': '',
                                                   'source': 'som-rs/scripts/hosted_readiness/export_goldens.py'},
 'semantic.common-postclose.join.map-rust-inputs': {'bindings': {'__som_native_public_sources_type__': {'attribute': 'Sources',
                                                                                                        'kind': 'role-export',
                                                                                                        'role': 'semantic.common-postclose.join.reconstruct-public-inputs'}},
                                                    'is_package': False,
                                                    'module': '_som_active_semantic_common_postclose_join_map_rust_inputs',
                                                    'package': '',
                                                    'source': 'som-rs/scripts/hosted_readiness/map_rust_inputs.py'},
 'semantic.common-postclose.join.prove-checked-reference': {'bindings': {'__som_native_active_controls__': {'kind': 'active-controls'}},
                                                            'is_package': False,
                                                            'module': '_som_active_semantic_common_postclose_join_prove_checked_reference',
                                                            'package': '',
                                                            'source': 'som-rs/scripts/hosted_readiness/prove_checked_reference.py'},
 'semantic.common-postclose.join.prove-checked-reference.recipe.som-core': {'bindings': {},
                                                                            'is_package': True,
                                                                            'module': '_som_active_semantic_common_postclose_join_prove_checked_reference_recipe_som_core',
                                                                            'package': '_som_active_semantic_common_postclose_join_prove_checked_reference_recipe_som_core',
                                                                            'source': 'som-core/src/som_core/__init__.py'},
 'semantic.common-postclose.join.prove-checked-reference.recipe.som-core.business': {'bindings': {},
                                                                                     'is_package': False,
                                                                                     'module': '_som_active_semantic_common_postclose_join_prove_checked_reference_recipe_som_core.business',
                                                                                     'package': '_som_active_semantic_common_postclose_join_prove_checked_reference_recipe_som_core',
                                                                                     'source': 'som-core/src/som_core/business.py'},
 'semantic.common-postclose.join.prove-checked-reference.recipe.som-core.compact-names': {'bindings': {},
                                                                                          'is_package': False,
                                                                                          'module': '_som_active_semantic_common_postclose_join_prove_checked_reference_recipe_som_core.compact_names',
                                                                                          'package': '_som_active_semantic_common_postclose_join_prove_checked_reference_recipe_som_core',
                                                                                          'source': 'som-core/src/som_core/compact_names.py'},
 'semantic.common-postclose.join.prove-checked-reference.recipe.som-core.composed': {'bindings': {},
                                                                                     'is_package': False,
                                                                                     'module': '_som_active_semantic_common_postclose_join_prove_checked_reference_recipe_som_core.composed',
                                                                                     'package': '_som_active_semantic_common_postclose_join_prove_checked_reference_recipe_som_core',
                                                                                     'source': 'som-core/src/som_core/composed.py'},
 'semantic.common-postclose.join.prove-checked-reference.recipe.som-core.flow': {'bindings': {},
                                                                                 'is_package': False,
                                                                                 'module': '_som_active_semantic_common_postclose_join_prove_checked_reference_recipe_som_core.flow',
                                                                                 'package': '_som_active_semantic_common_postclose_join_prove_checked_reference_recipe_som_core',
                                                                                 'source': 'som-core/src/som_core/flow.py'},
 'semantic.common-postclose.join.prove-checked-reference.recipe.som-core.flow-codec': {'bindings': {},
                                                                                       'is_package': False,
                                                                                       'module': '_som_active_semantic_common_postclose_join_prove_checked_reference_recipe_som_core.flow_codec',
                                                                                       'package': '_som_active_semantic_common_postclose_join_prove_checked_reference_recipe_som_core',
                                                                                       'source': 'som-core/src/som_core/flow_codec.py'},
 'semantic.common-postclose.join.prove-checked-reference.recipe.som-core.flow-compact': {'bindings': {'__native_proof_source_type': {'attribute': 'ProofSources',
                                                                                                                                     'kind': 'role-export',
                                                                                                                                     'role': 'semantic.common-postclose.join.prove-checked-reference'},
                                                                                                      '__native_recipe_inputs': {'fields': {'pins': 'json',
                                                                                                                                            'repo': 'path'},
                                                                                                                                 'kind': 'local-instance',
                                                                                                                                 'local': 'typed_sources',
                                                                                                                                 'type_name': '_NativeCheckedInputs',
                                                                                                                                 'type_role': 'semantic.common-postclose.join.prove-checked-reference'}},
                                                                                         'is_package': False,
                                                                                         'module': '_som_active_semantic_common_postclose_join_prove_checked_reference_recipe_som_core.flow_compact',
                                                                                         'package': '_som_active_semantic_common_postclose_join_prove_checked_reference_recipe_som_core',
                                                                                         'source': 'som-core/src/som_core/flow_compact.py'},
 'semantic.common-postclose.join.prove-checked-reference.recipe.som-core.quality': {'bindings': {},
                                                                                    'is_package': False,
                                                                                    'module': '_som_active_semantic_common_postclose_join_prove_checked_reference_recipe_som_core.quality',
                                                                                    'package': '_som_active_semantic_common_postclose_join_prove_checked_reference_recipe_som_core',
                                                                                    'source': 'som-core/src/som_core/quality.py'},
 'semantic.common-postclose.join.prove-checked-reference.recipe.som-core.related': {'bindings': {},
                                                                                    'is_package': False,
                                                                                    'module': '_som_active_semantic_common_postclose_join_prove_checked_reference_recipe_som_core.related',
                                                                                    'package': '_som_active_semantic_common_postclose_join_prove_checked_reference_recipe_som_core',
                                                                                    'source': 'som-core/src/som_core/related.py'},
 'semantic.common-postclose.join.prove-checked-reference.recipe.som-core.v1': {'bindings': {},
                                                                               'is_package': False,
                                                                               'module': '_som_active_semantic_common_postclose_join_prove_checked_reference_recipe_som_core.v1',
                                                                               'package': '_som_active_semantic_common_postclose_join_prove_checked_reference_recipe_som_core',
                                                                               'source': 'som-core/src/som_core/v1.py'},
 'semantic.common-postclose.join.reconstruct-public-inputs': {'bindings': {'__som_native_active_controls__': {'kind': 'active-controls'}},
                                                              'is_package': False,
                                                              'module': '_som_active_semantic_common_postclose_join_reconstruct_public_inputs',
                                                              'package': '',
                                                              'source': 'som-rs/scripts/hosted_readiness/reconstruct_public_inputs.py'}}


def import_sites(node, scope='<module>', in_function=False):
    for child in ast.iter_child_nodes(node):
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            prefix = scope + '.<locals>.' if in_function else scope + '.' if scope != '<module>' else ''
            yield from import_sites(child, prefix + child.name,
                                    isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)))
        else:
            if isinstance(child, (ast.Import, ast.ImportFrom)):
                yield child, scope
            yield from import_sites(child, scope, in_function)


class RemainingFixture(protocol.Fixture):
    def __init__(self, case):
        caller = ('def run(controls, target="complete"):\n'
                  '    if target == "proof":\n'
                  '        return controls.load_module("' + PROOF + '")\n'
                  '    return controls.load_module("' + COMPLETE + '")\n')
        super().__init__(case, caller)
        self.policy['imports'], self.policy['static_edges'] = self.imports, self.edges

    def add_role(self, role, path, raw, **kwargs):
        row = super().add_role(role, path, raw, **kwargs)
        if role != 'm6.api.producer':
            return row
        self.imports, self.edges = [], []
        for target, data in ROLES.items():
            callers = [{'role': 'caller', 'function': 'run'}] if target in {COMPLETE, PROOF} else []
            if '.recipe.' in target:
                parent = COMPLETE if target.startswith(COMPLETE + '.') else PROOF
                callers.append({'role': parent, 'function': '_native_serializer_runtime'
                                if parent == COMPLETE else '_native_checked_runtime'})
            entry = super().add_role(target, data['source'], (REPO / data['source']).read_bytes(),
                    module=data['module'], bindings=data['bindings'], callers=callers)
            entry['package'], entry['is_package'] = data['package'], data['is_package']
        self.import_group({
            'complete_rust_inputs': COMPLETE,
            'map_rust_inputs': 'companions.entry.capture.map-rust-inputs',
            'reconstruct_public_inputs': 'companions.entry.capture.reconstruct-public-inputs',
            'export_goldens': 'companions.entry.capture.export-goldens'})
        self.import_group({
            'prove_checked_reference': PROOF,
            'reconstruct_public_inputs': 'semantic.common-postclose.join.reconstruct-public-inputs',
            'export_goldens': 'semantic.common-postclose.join.export-goldens'})
        self.import_group({'som_core' + ('.' + name if name != '__init__' else ''):
            PROOF + '.recipe.som-core' + ('.' + name.replace('_', '-') if name != '__init__' else '')
            for name in ('__init__', 'flow', 'composed', 'related', 'v1', 'business',
                         'flow_compact', 'flow_codec', 'quality', 'compact_names')})
        # Materialize genuine data/anchor pins for original ProofSources.verify.
        recipe_path = BASE + 'inputs/quality-checked-recipe-v1.json'
        recipe = json.loads((REPO / recipe_path).read_bytes())
        paths = [recipe_path, 'som-rs/contracts/compiler-v1/field-statement-map.json']
        paths += [item['path'] for key in ('public_runtime_module_pins', 'public_data_pins') for item in recipe[key]]
        paths += [item['path'] for item in recipe['accepted_hash_chain']['public_anchors']]
        for source in paths:
            destination = self.root / source
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes((REPO / source).read_bytes())
        return row

    def import_group(self, logical):
        for name, role in logical.items():
            source = self.rows[role]['source']
            package = name if self.rows[role]['is_package'] else name.rpartition('.')[0]
            for node, function in import_sites(ast.parse(self.raw[source])):
                if isinstance(node, ast.Import):
                    targets = [(alias.name, [], logical[alias.name], {}) for alias in node.names if alias.name in logical]
                    level = 0
                else:
                    stem = node.module or ''
                    absolute = '.'.join(package.split('.')[:len(package.split('.')) - node.level + 1]
                                        + ([stem] if stem else [])) if node.level else stem
                    if absolute not in logical:
                        self.case.assertFalse(absolute.startswith('som_core'), 'unlisted repository import')
                        continue
                    children = {item.name: logical[absolute + '.' + item.name]
                                for item in node.names if absolute + '.' + item.name in logical}
                    targets = [(stem, [item.name for item in node.names], logical[absolute], children)]
                    level = node.level
                for stem, fromlist, result, children in targets:
                    rule = {'caller': role, 'name': stem, 'level': level,
                            'fromlist': fromlist, 'result': result, 'from_roles': children}
                    if rule not in self.imports:
                        self.imports.append(rule)
                    for target in {result, *children.values()}:
                        edge = {'caller': source, 'import': stem, 'source': self.rows[target]['source']}
                        if edge not in self.edges:
                            self.edges.append(edge)
                        caller = {'role': role, 'function': function}
                        if caller not in self.rows[target]['callers']:
                            self.rows[target]['callers'].append(caller)

    def complete_inputs(self, module):
        # Finite data double; run the exact inherited verify method on real pins.
        sources = object.__new__(module.Sources)
        sources.repo = self.root
        sources.pins = {path: module.replay.RUNTIME_PINS[path]
                       for path in (module.replay.SERIALIZER, module.replay.STATEMENT_MAP)}
        return sources

    def proof_inputs(self, module):
        # Bypass constructor only. Never bypass its real source/data rechecks.
        sources = object.__new__(module.ProofSources)
        sources.repo, sources.raw = self.root, (self.root / module.RECIPE_FILE).read_bytes()
        sources.recipe = json.loads(sources.raw)
        sources.pins = {row['path']: row['raw_sha256'] for key in
                        ('public_runtime_module_pins', 'public_data_pins') for row in sources.recipe[key]}
        sources.anchors = {row['path']: row['raw_sha256']
                           for row in sources.recipe['accepted_hash_chain']['public_anchors']}
        return sources


@contextmanager
def active(case):
    # Snapshot before Core exists. Core closes before whole sys.modules restore.
    with mock.patch.dict(sys.modules):
        fixture = RemainingFixture(case)
        try:
            yield fixture
        finally:
            try:
                fixture.finish()
            finally:
                fixture.stack.close()


class NativeRemainingRecipeTests(unittest.TestCase):
    def test_serializer_loads_exact_source_and_keeps_owner_and_legacy_names(self):
        with active(self) as f:
            module = f.run()
            sources = f.complete_inputs(module)
            before = dict(sys.modules)
            finder, spec, bytecode = f.domain.finder, f.domain.spec, sys.dont_write_bytecode
            with module._serializer_recipe(sources) as serialize:
                self.assertIs(type(serialize), FunctionType)
                self.assertTrue(f.domain.source_callable(serialize))
                target = f.domain.records[COMPLETE + '.recipe.serializer'].module
                self.assertIs(serialize, target.serialize_prompt)
                self.assertEqual(target.__som_raw_sha256__, sources.pins[module.replay.SERIALIZER])
            self.assertIs(f.domain.finder, finder)
            self.assertIs(f.domain.spec, spec)
            self.assertIs(sys.dont_write_bytecode, bytecode)
            self.assertFalse(f.domain.closed)
            self.assertIs(sys.modules.get('som_hosted_serializer'), before.get('som_hosted_serializer'))

    def test_checked_recipe_loads_all_ten_exact_modules_in_its_own_package(self):
        with active(self) as f:
            module = f.caller().run(f.controls, 'proof')
            sources = f.proof_inputs(module)
            before = {name: value for name, value in sys.modules.items()
                      if name == 'som_core' or name.startswith('som_core.')}
            finder, spec = f.domain.finder, f.domain.spec
            with module._checked_recipe(sources) as (compact, flow, v1, check_loaded):
                expected = sorted('som_core' + ('.' + name if name != '__init__' else '') for name in
                    ('__init__', 'flow', 'composed', 'related', 'v1', 'business',
                     'flow_compact', 'flow_codec', 'quality', 'compact_names'))
                self.assertEqual(check_loaded(), expected)
                self.assertIs(compact, f.domain.records[PROOF + '.recipe.som-core.flow-compact'].module)
                self.assertIs(flow, f.domain.records[PROOF + '.recipe.som-core.flow'].module)
                self.assertIs(v1, f.domain.records[PROOF + '.recipe.som-core.v1'].module)
            self.assertIs(f.domain.finder, finder)
            self.assertIs(f.domain.spec, spec)
            self.assertFalse(f.domain.closed)
            self.assertEqual({name: value for name, value in sys.modules.items()
                              if name == 'som_core' or name.startswith('som_core.')}, before)

    def test_known_native_namespace_without_binding_refuses_before_recipe_load(self):
        with active(self) as f:
            f.rows[COMPLETE]['bindings'].pop('__som_native_active_controls__')
            module = f.run()
            with self.assertRaisesRegex(module.Refusal, 'controls binding is absent'):
                with module._serializer_recipe(f.complete_inputs(module)):
                    self.fail('missing binding reached serializer body')
            self.assertNotIn(COMPLETE + '.recipe.serializer', f.domain.records)

    def test_unlisted_native_namespace_refuses_instead_of_legacy_loader(self):
        with active(self) as f:
            f.rows[COMPLETE]['module'] = '_som_unlisted_native_complete'
            module = f.run()
            with self.assertRaisesRegex(module.Refusal, 'unlisted native companion serializer namespace'):
                with module._serializer_recipe(f.complete_inputs(module)):
                    self.fail('unlisted namespace reached serializer body')
            self.assertNotIn(COMPLETE + '.recipe.serializer', f.domain.records)

    def test_foreign_handle_is_rejected_before_its_methods(self):
        with active(self) as f:
            module, calls = f.run(), []
            class Foreign:
                def verify_installed(self):
                    calls.append('foreign')
                    raise AssertionError('foreign handle executed')
            original = module.__som_native_active_controls__
            try:
                module.__som_native_active_controls__ = Foreign()
                with self.assertRaisesRegex(ValueError, 'fixed active-controls binding was replaced'):
                    with module._serializer_recipe(f.complete_inputs(module)):
                        self.fail('foreign handle reached serializer body')
                self.assertEqual(calls, [])
            finally:
                module.__som_native_active_controls__ = original

    def test_changed_serializer_pin_refuses_before_source_execution(self):
        with active(self) as f:
            module = f.run()
            sources = f.complete_inputs(module)
            path = f.root / module.replay.SERIALIZER
            original = path.read_bytes()
            try:
                path.write_bytes(original + b'\n# changed actual dependency\n')
                with self.assertRaisesRegex(module.Refusal, 'Rust source digest differs'):
                    with module._serializer_recipe(sources):
                        self.fail('changed source reached serializer body')
                self.assertNotIn(COMPLETE + '.recipe.serializer', f.domain.records)
            finally:
                path.write_bytes(original)

    def test_wrong_proof_source_type_refuses_before_runtime_modules(self):
        with active(self) as f:
            module = f.caller().run(f.controls, 'proof')
            with self.assertRaisesRegex(module.Refusal, 'actual source-owned native checked inputs required'):
                with module._checked_recipe(object()):
                    self.fail('foreign source object reached checked body')
            self.assertFalse(any(role.startswith(PROOF + '.recipe.') for role in f.domain.records))

    def test_primary_body_failure_survives_pinned_source_postcheck_failure(self):
        with active(self) as f:
            module = f.run()
            sources = f.complete_inputs(module)
            path = f.root / module.replay.SERIALIZER
            original, marker = path.read_bytes(), RuntimeError('first body error')
            try:
                try:
                    with module._serializer_recipe(sources):
                        path.write_bytes(original + b'\n# changed during body\n')
                        raise marker
                except RuntimeError as actual:
                    self.assertIs(actual, marker)
                else:
                    self.fail('original body error was lost')
                self.assertIsInstance(marker.__som_native_serializer_postcheck_error__, module.Refusal)
                self.assertFalse(f.domain.closed)
            finally:
                path.write_bytes(original)


if __name__ == '__main__':
    unittest.main()

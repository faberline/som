"""Integrity fixtures. Run only on GitHub runners."""
import copy
import hashlib
import importlib.util
import json
import marshal
import os
from pathlib import Path
import struct
import sys
import tempfile
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch

if os.environ.get('GITHUB_ACTIONS') != 'true':
    raise RuntimeError('GitHub fixtures only')

source = Path(__file__).with_name('run_candidate_semantic_gates.py')
p = ModuleType('_semantic_subject')
p.__file__ = str(source)
exec(compile(source.read_bytes(), str(source), 'exec', dont_inherit=True), p.__dict__)


def protocol_source(relative):
    """Use tracked raw fixture/source bytes; never a Python import loader."""
    repo = Path(__file__).resolve().parents[3]
    raw = (repo / relative).read_bytes()
    assert raw == p.subprocess.check_output(['/usr/bin/git', '--no-replace-objects', '-C', str(repo),
        'cat-file', 'blob', os.environ['HELPER_REVISION'] + ':' + relative])
    return raw


def core_native_fixture(case, kind='semantic', authority_body=None, entry=False, variant=None, manual_variant='typescript'):
    """Actual Core and production caller. Transfer/data/helpers are protocol doubles."""
    import ast
    from unittest import mock
    base = 'som-rs/scripts/hosted_readiness/'
    protocol = ModuleType('_semantic_companion_actual_core_fixture')
    protocol.__file__ = str(Path(__file__).with_name('test_native_active_controls.py'))
    exec(compile(protocol_source(base + 'test_native_active_controls.py'), protocol.__file__, 'exec', dont_inherit=True), protocol.__dict__)
    relative = base + ('run_candidate_semantic_gates.py' if kind == 'semantic' else 'run_current_python_companions.py')
    production = protocol_source(relative)
    expected = {'semantic': '372e9e9d8b6a8d3d346a3ddc0abb447baa69e3aa3934249a22c7ae088e36fa08',
                'companions': '93147d315bf22d9b0a99ddeff290cfab8fa4e6b8deb659cd81f2880243ddcadc'}
    case.assertEqual(hashlib.sha256(production).hexdigest(), expected[kind])
    if variant is None: variant = '_som_graph_semantic_gate_data' if kind == 'semantic' else '_som_full_current_capture'
    prefix = 'semantic.graph' if kind == 'semantic' else 'companions.context'
    authority_path = base + 'native_qa_authority.py'
    if authority_body is None:
        authority_body = '''def verify(source_reader, mode, source_paths, helper_revision=None, candidate_revision=None):
    return {'mode': mode, 'source_authority_active': True,
        'helper_revision': helper_revision, 'candidate_revision': candidate_revision,
        'product_acceptance_passed': False, 'compiler_acceptance_passed': False,
        'whole_acceptance_complete': False}
'''
    suffix = ('\n# Explicit protocol admission body; not production activation.\n' + authority_body).encode()
    required = ('run_experimental_native_cnl', 'export_goldens', 'raw_digest_compat', 'reconstruct_public_inputs',
                'map_rust_inputs', 'complete_rust_inputs', 'experimental_native_compile', 'run_experimental_native_compile',
                'run_experimental_native_assembly', 'prove_checked_reference', '_som_semantic_join_carrier',
                'rust_profile_phase2', '_som_semantic_original_cli') if kind == 'semantic' else (
                'export_goldens', 'raw_digest_compat', 'reconstruct_public_inputs', 'map_rust_inputs', 'complete_rust_inputs',
                'experimental_native_compile', 'run_experimental_native_compile', 'dependency_candidate', 'candidate_carrier')
    paths = {name: base + 'synthetic-native-group/' + name + '.py' for name in required}
    if kind == 'companions':
        # Use the real MODULES data by parsing its fixed source-owned module once
        # later; these are the exact declared production paths, not body imports.
        paths = {name: base + name + '.py' for name in required}
        paths['candidate_carrier'] = base + 'product_runtime/candidate_carrier.py'
    target_source = p.TARGET_ASSERTION_SOURCE
    recipe_source = p.RUST_RECIPE_SOURCE
    target_names, recipe_names = p.TARGET_ASSERTION_FUNCTIONS, p.RUST_RECIPE_FUNCTIONS
    selected_pins = {'targets': '6107ee5270305e019cb7556f0c46afb855a3c5fa1e5963051a6af39ed06ce178',
                     'recipes': '828a9e815de0039bcac3d47daad0ce815f2413a2eb58d79c58953432413bb26e'}
    class Transfer(protocol.ProtocolTransfer):
        def __init__(self, fixture):
            super().__init__(fixture)
            caller, target = fixture.manual_caller, fixture.manual_target
            self.admission['manual_source_loads'] = [{'caller': caller, 'module': 'rust_build_negative_cases', 'path': target,
                'caller_sha256': fixture.core.sha(fixture.raw[caller]), 'sha256': fixture.core.sha(fixture.raw[target])}]
    class Fixture(protocol.Fixture):
        def synthetic_seed(self, name, path, raw):
            if path == authority_path: raw = (raw.encode() if type(raw) is str else raw) + suffix
            module, code = super().synthetic_seed(name, path, raw)
            module.__file__ = str(self.root / path)
            module.__som_raw_sha256__ = hashlib.sha256(self.raw[path]).hexdigest()
            return module, code
        def add_role(self, role, path, raw, **kwargs):
            if role == 'bootstrap.authority': kwargs['callers'] = [{'role': 'caller', 'function': 'native_source_authority'}]
            if role == 'caller':
                path, raw = relative, production
                kwargs.update(module='__main__' if entry else variant,
                    bindings={'__som_native_active_controls__': {'kind': 'active-controls'}})
            result = super().add_role(role, path, raw, **kwargs)
            if role != 'caller': return result
            self.group_paths, self.group_roles = paths, {}
            self.body_markers = self.root / 'helper-body-markers'; self.body_markers.mkdir()
            for name, member in paths.items():
                target_role = (prefix + '.join.' + name.strip('_').replace('_', '-') if kind == 'semantic'
                               else 'companions.entry.capture.' + name.replace('_', '-'))
                self.group_roles[name] = target_role
                marker = self.body_markers / name
                body = 'from pathlib import Path\nPath(' + repr(str(marker)) + ').write_text("BODY")\nVALUE = 41\n'
                super().add_role(target_role, member, body,
                    callers=[{'role': 'caller', 'function': '_native_join_modules' if kind == 'semantic' else 'main'}])
            if kind == 'semantic':
                for label, member, names in (('targets', target_source, target_names), ('recipes', recipe_source, recipe_names)):
                    original = protocol_source(member)
                    nodes = [n for n in ast.parse(original).body if isinstance(n, ast.FunctionDef) and n.name in names]
                    selector = {'kind': 'top-level-functions', 'names': list(names),
                        'ast_sha256': hashlib.sha256(ast.dump(ast.Module(body=nodes, type_ignores=[]), include_attributes=False).encode()).hexdigest()}
                    case.assertEqual(selector['ast_sha256'], selected_pins[label])
                    bindings = {'json': {'kind': 'stdlib', 'module': 'json'}, 'hashlib': {'kind': 'stdlib', 'module': 'hashlib'}}
                    if label == 'recipes': bindings.update(copy={'kind': 'stdlib', 'module': 'copy'},
                        CONTRACT={'kind': 'local-data', 'local': 'contract', 'type': 'path'},
                        PLAN={'kind': 'local-data', 'local': 'plan', 'type': 'json'},
                        RUST_ASSETS={'kind': 'source-constant', 'source': recipe_source, 'name': 'RUST_ASSETS'},
                        targets={'kind': 'local-instance', 'local': 'targets', 'type_role': 'caller', 'type_name': 'NativeRefusalTargets',
                            'fields': {'root': 'path', 'rows': 'json', 'base': 'str', 'cursor': 'int'}})
                    callers = ([{'role': 'caller', 'function': 'NativeRefusalTargets.' + name} for name in target_names]
                               if label == 'targets' else [{'role': 'caller', 'function': '_native_original_refusal_assertions'}])
                    row = super().add_role(prefix + '.original.' + label, member, original, kind='fragment', callers=callers, bindings=bindings)
                    row['selector'] = selector
                for member in ['som-rs/contracts/compiler-v1/examples/' + name + '.prompt.json' for name in ('v1', 'v3', 'v6')] + [
                        'som-rs/contracts/rust-output-v1/acceptance-plan.json', 'som-rs/contracts/rust-output-v1/negative-cases.json']:
                    original = protocol_source(member); self.raw[member] = original
                    target = self.root / member; target.parent.mkdir(parents=True, exist_ok=True); target.write_bytes(original)
            case.assertIn(manual_variant, {'typescript', 'react'})
            self.manual_caller = 'som-rs/tests/' + manual_variant + '-output-v1/common.py'
            self.manual_target = 'som-rs/tests/rust-output-v1/build_negative_cases.py'
            manual = '''import importlib.util
import pathlib
RUST_TOOLS = pathlib.Path(__file__).resolve().parents[1] / 'rust-output-v1'
_spec = importlib.util.spec_from_file_location('rust_build_negative_cases', RUST_TOOLS / 'build_negative_cases.py')
assert _spec and _spec.loader
rust_cases = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rust_cases)
def run(): return rust_cases.VALUE
'''
            original_manual = ast.parse(protocol_source(self.manual_caller))
            synthetic_manual = ast.parse(manual)
            for attribute in ('spec_from_file_location', 'exec_module'):
                def calls(tree):
                    return [ast.dump(node, include_attributes=False) for node in ast.walk(tree)
                            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                            and node.func.attr == attribute]
                case.assertEqual(calls(original_manual), calls(synthetic_manual))
            super().add_role('fixture.manual', self.manual_caller, manual,
                callers=[{'role': 'fixture.probe', 'function': 'capture'}])
            super().add_role('fixture.builder', self.manual_target, "VALUE = 'checked raw source'\n",
                module='rust_build_negative_cases', callers=[{'role': 'fixture.manual', 'function': '<module>'}])
            pins = {member: hashlib.sha256(value).hexdigest() for member, value in self.raw.items()}
            probe = '''from pathlib import Path
import hashlib
ROOT = Path(ROOT_LITERAL)
PINS = PINS_LITERAL
BORROWED = []
class Sources:
    def __init__(self):
        self.root, self.revision, self.seen, self.verify_calls, self.failure = ROOT, '1' * 40, {}, 0, None
    def read(self, name):
        raw = (self.root / name).read_bytes()
        if hashlib.sha256(raw).hexdigest() != PINS[name]: raise ValueError('protocol original source bytes differ')
        self.seen[name] = PINS[name]
        return raw
    def verify(self):
        self.verify_calls += 1
        if self.failure is not None: raise self.failure
        for name in list(self.seen): self.read(name)
def capture(sources, fail=False):
    with borrowed(sources) as lease:
        BORROWED.append(lease)
        value = lease.load_module('fixture.manual').run()
        if fail: raise RuntimeError('capture failed after import')
        return value
'''.replace('ROOT_LITERAL', repr(str(self.root))).replace('PINS_LITERAL', repr(pins))
            super().add_role('fixture.probe', base + 'synthetic-native-probe.py', probe,
                bindings={'borrowed': {'kind': 'role-export', 'role': 'caller', 'attribute': '_borrowed_native'}})
            return result
    with mock.patch.object(protocol, 'ProtocolTransfer', Transfer):
        f = Fixture(case, entry=entry)
    f.transfer.revision = f.domain.revision = '1' * 40
    f.production_path, f.kind = relative, kind
    f.controller = lambda: f.domain.load_known('caller', f.domain.prepare('caller'), None)
    def probe_module():
        f.controller()
        return f.domain.load_known('fixture.probe', f.domain.prepare('fixture.probe'), None)
    f.probe = probe_module
    f.sources = lambda: f.probe().Sources()
    f.marker = f.root / 'pyc-executed'
    target = f.root / f.manual_target; info = target.stat()
    cache = Path(importlib.util.cache_from_source(str(target))); cache.parent.mkdir()
    cache.write_bytes(importlib.util.MAGIC_NUMBER + struct.pack('<III', 0, int(info.st_mtime), info.st_size)
        + marshal.dumps(compile('from pathlib import Path\nPath(' + repr(str(f.marker)) + ').write_text("BAD")\nVALUE="untrusted PYC"\n', str(target), 'exec')))
    return f


class SemanticIntegrity(unittest.TestCase):
    def bundle(self):
        original = {'version': 'som.dsl-bundle.v2', 'target': 'rust', 'prompt': {'name': 'frozen'},
                    'dsl': {'files': []}, 'assets': {'backend': {'id': 'backend', 'sha256': '1' * 64},
                    'target_contract': {'id': 'contract', 'sha256': '2' * 64},
                    'compiler_contract': {'id': 'compiler', 'sha256': '3' * 64}},
                    'hashes': {'prompt_sha256': '4' * 64, 'dsl_sha256': '5' * 64, 'payload_sha256': ''}}
        digest = lambda v: hashlib.sha256(p.frozen_text(v).encode()).hexdigest()
        original['hashes']['payload_sha256'] = digest({k: v for k, v in original.items() if k != 'hashes'})
        current = copy.deepcopy(original)
        current['assets']['backend']['sha256'] = '6' * 64
        current['assets']['target_contract']['sha256'] = '7' * 64
        current['hashes']['payload_sha256'] = digest({k: v for k, v in current.items() if k != 'hashes'})
        return original, current, digest

    def test_descriptor_only_rebase_keeps_original_immutable(self):
        old, new, digest = self.bundle()
        before = copy.deepcopy(old)
        self.assertEqual(p.rebase(old, new, digest), new)
        self.assertEqual(old, before)

    def test_rebase_refuses_prompt_change(self):
        old, new, digest = self.bundle()
        new['prompt']['name'] = 'changed'
        with self.assertRaises(ValueError): p.rebase(old, new, digest)

    def test_rebase_refuses_dsl_change(self):
        old, new, digest = self.bundle()
        new['dsl']['files'] = ['unexpected']
        with self.assertRaises(ValueError): p.rebase(old, new, digest)

    def test_rebase_refuses_payload_hash_change(self):
        old, new, digest = self.bundle()
        new['hashes']['payload_sha256'] = '0' * 64
        with self.assertRaises(ValueError): p.rebase(old, new, digest)

    def test_rebase_refuses_immutable_contract_change(self):
        old, new, digest = self.bundle()
        new['assets']['compiler_contract']['sha256'] = '8' * 64
        with self.assertRaises(ValueError): p.rebase(old, new, digest)

    def test_rebase_refuses_descriptor_id_change(self):
        old, new, digest = self.bundle()
        new['assets']['backend']['id'] = 'other'
        with self.assertRaises(ValueError): p.rebase(old, new, digest)

    def test_rebase_refuses_extra_descriptor(self):
        old, new, digest = self.bundle()
        new['assets']['unexpected'] = {'id': 'x', 'sha256': '0' * 64}
        with self.assertRaises(ValueError): p.rebase(old, new, digest)

    def test_raw_reader_refuses_symlink(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); (root / 'data').write_bytes(b'raw'); (root / 'alias').symlink_to('data')
            with self.assertRaises(ValueError): p.read(root / 'alias')

    def test_raw_reader_refuses_hardlink(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); (root / 'data').write_bytes(b'raw'); os.link(root / 'data', root / 'alias')
            with self.assertRaises(ValueError): p.read(root / 'data')

    def test_raw_reader_refuses_size_overrun(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / 'data'; path.write_bytes(b'1234')
            with self.assertRaises(ValueError): p.read(path, 3)

    def test_raw_reader_preserves_exact_bytes(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / 'data'; path.write_bytes(b'\x00\xff\n')
            self.assertEqual(p.read(path), b'\x00\xff\n')

    def events(self, root, request, status, reply, event):
        directory = root / 'http/00000'; directory.mkdir(parents=True)
        (directory / 'request.raw').write_bytes(p.canonical(request))
        (directory / 'response.raw').write_bytes(p.canonical(reply))
        listening = {'stage': 'listening', 'address': '127.0.0.1:18979', 'purpose': 'som.rust-compiler.v1'}
        (root / 'server.log').write_bytes(p.canonical(listening) + b'\n' + p.canonical(event) + b'\n')
        return [{'index': 0, 'route': '/v1/assemble' if 'bundle' in request else '/v1/compile', 'status': status}]

    def test_python_assembly_event_uses_utf8_byte_count(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); rows = self.events(root, {'bundle': {'version': 'som.dsl-bundle.v1'}}, 200,
                {'files': {'a': '字'}}, {'stage': 'assemble', 'formatted_bytes': 3, 'total_ms': 0})
            self.assertEqual(p.verify_server_events(root / 'server.log', root, rows, 18979)['events'], 2)

    def test_rust_refusal_event_keeps_target_and_status(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); rows = self.events(root, {'bundle': {'version': 'som.dsl-bundle.v2', 'target': 'python'}}, 400,
                {'code': 'INVALID_SCHEMA'}, {'stage': 'assemble', 'target': 'python', 'http_status': 400, 'total_ms': 1.5})
            self.assertEqual(p.verify_server_events(root / 'server.log', root, rows, 18979)['captured_http_exchanges'], 1)

    def test_event_refuses_wrong_byte_count(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); rows = self.events(root, {'bundle': {'version': 'som.dsl-bundle.v1'}}, 200,
                {'files': {'a': '字'}}, {'stage': 'assemble', 'formatted_bytes': 1, 'total_ms': 0})
            with self.assertRaises(ValueError): p.verify_server_events(root / 'server.log', root, rows, 18979)

    def test_event_refuses_unknown_extra_field(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); rows = self.events(root, {'format': 'test'}, 400, {'code': 'INVALID_VALUE'},
                {'stage': 'compile', 'http_status': 400, 'bundle_bytes': None, 'total_ms': 0, 'extra': True})
            with self.assertRaises(ValueError): p.verify_server_events(root / 'server.log', root, rows, 18979)

    def test_event_refuses_missing_raw_exchange(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); self.events(root, {'format': 'test'}, 400, {'code': 'INVALID_VALUE'},
                {'stage': 'compile', 'http_status': 400, 'bundle_bytes': None, 'total_ms': 0})
            with self.assertRaises(ValueError): p.verify_server_events(root / 'server.log', root, [], 18979)

    def test_frozen_canonical_keeps_exponents_unicode_and_negative_zero(self):
        self.assertEqual(p.frozen_text({'z': -0.0, '字': 1e-7}), '{"z":-0.0,"字":1e-07}')
        with self.assertRaises(ValueError): p.frozen_text(float('inf'))

    def test_file_inventory_refuses_extra_special_alias(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); (root / 'data').write_bytes(b'a'); (root / 'alias').symlink_to('data')
            with self.assertRaises(ValueError): p.file_inventory(root)

    def test_verifier_refuses_false_gate_even_with_acceptance_false(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); p.save(root / 'result.json', {'version': p.VERSION, 'gate': 'cnl',
                'helper_revision': '1' * 40, 'candidate_revision': '1' * 40,
                'product_acceptance_passed': False, 'complete': False})
            with self.assertRaises(ValueError): p.verify_result(root, lambda name: b'', '1' * 40, '1' * 40, 'cnl')

    def test_verifier_refuses_mixed_candidate_before_source_execution(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); p.save(root / 'result.json', {'version': p.VERSION})
            def forbidden(name): raise AssertionError('must not read source')
            with self.assertRaises(ValueError): p.verify_result(root, forbidden, '1' * 40, '2' * 40, 'cnl')

    def test_bootstrap_ignores_valid_timestamp_pyc(self):
        with tempfile.TemporaryDirectory() as d, patch.dict(sys.modules):
            root = Path(d); revision = '1' * 40
            bodies = {p.SELF: b'# bound entry\n', p.CONTEXT: b'VALUE = "raw"\n'}
            for name, raw in bodies.items():
                path = root / name; path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(raw)
            path = root / p.CONTEXT; info = path.stat(); cached = Path(importlib.util.cache_from_source(str(path)))
            cached.parent.mkdir(); cached.write_bytes(importlib.util.MAGIC_NUMBER + struct.pack('<III', 0, int(info.st_mtime), info.st_size)
                + marshal.dumps(compile('VALUE = "pyc"\n', str(path), 'exec')))
            sys.modules.pop('full_product_context', None)
            def git(argv, **kwargs):
                args = argv[4:]
                if args[0] == 'rev-parse': return (revision + '\n').encode()
                name = args[-1]
                if args[0] == 'ls-tree': return b'100644 blob ' + b'0' * 40 + b'\t' + name.encode() + b'\0'
                if args[0] == 'cat-file': return bodies[name.split(':', 1)[1]]
                raise AssertionError(argv)
            with patch.object(p.subprocess, 'check_output', side_effect=git):
                self.assertEqual(p.bootstrap(root, revision).VALUE, 'raw')


class SemanticTrustedJoin(unittest.TestCase):
    """Independent tiny oracles exercise full named loops and forged local hashes."""
    def owner(self):
        return {'pid': 731, 'pgrp': 731, 'session': 731, 'start_ticks': 919,
                'state': 'S', 'executable_inode': 3001,
                'listener': {'table': 'tcp', 'address': '0100007F', 'inode': '4431'}}

    def cnl_fixture(self, base):
        root = base / 'cnl'; root.mkdir()
        owner = self.owner(); jobs, rows, typed = [], [], {}
        def identity_key(target, identity): return target + '/' + p.frozen_text(identity)
        def reference(target, identity):
            path = identity_key(target, identity)
            response = p.canonical({'accepted_typed_case': path})
            typed[path] = response
            return {'path': path, 'sha256': p.sha(response), 'status': 200, 'index': len(typed) - 1}
        identities = [('python_cnl', 'python', {'source': 'public', 'id': f'P{i//2}', 'variant': 'none' if i % 2 == 0 else 'python'}, 'cnl')
                      for i in range(1342)]
        rust = ['RS12_CNL_V3_FILTER_LIMIT', 'RS13_JSON_V3_FILTER_OFFSET', *[f'R{i}' for i in range(2, 699)]]
        identities += [('rust_cnl', 'rust', {'phase': 'phase2', 'id': name}, 'cnl') for name in rust]
        identities += [('rust_native_json', 'rust', {'phase': 'phase2', 'id': name}, 'json') for name in rust[1:10]]
        refs = {}
        def stable_reference(target, identity):
            key = identity_key(target, identity)
            if key not in refs: refs[key] = reference(target, identity)
            return dict(refs[key])
        def oracle(job):
            diagnostic = {'cause': job['identity']['id'], 'form': job['form'], 'span': [2, 8]}
            return 400, p.canonical({'independent_current_400': diagnostic}), diagnostic
        for index, (group, target, identity, form) in enumerate(identities):
            raw = p.canonical({'frozen_source_slot': index, 'form': form, 'target': target})
            job = {'group': group, 'target': target, 'identity': identity, 'form': form, 'raw': raw,
                   'request_sha256': p.sha(raw), 'input': {'path': f'frozen/{index:04d}'},
                   'typed_reference': stable_reference(target, identity)}
            row = {k: copy.deepcopy(v) for k, v in job.items() if k != 'raw'}
            row.update(index=index, state='passed', owned_socket=copy.deepcopy(owner))
            if target == 'rust' and identity['id'].startswith(('RS12_', 'RS13_')):
                status, response, diagnostic = oracle(job)
                row.update(expected_diagnostic=diagnostic, comparison='independent frozen compiler collision rule and raw span')
            else:
                status, response = 200, typed[job['typed_reference']['path']]
                row['comparison'] = 'exact complete current typed reply bytes'
            row.update(http_status=status, expected_status=status, response_sha256=p.sha(response), expected_response_sha256=p.sha(response))
            directory = root / 'rows' / f'{index:04d}'; directory.mkdir(parents=True)
            (directory / 'request.raw').write_bytes(raw); (directory / 'response.raw').write_bytes(response)
            (directory / 'result.json').write_bytes(p.canonical(row))
            jobs.append(job); rows.append(row)
        proof = SimpleNamespace(reference=stable_reference, read=lambda name: typed[name])
        return root, {'rows': rows}, jobs, proof, SimpleNamespace(expected=oracle), owner

    def call_cnl(self, data):
        root, value, jobs, proof, oracle, owner = data
        return p.verify_cnl_saved_rows(root, value, jobs, proof, oracle, None, owner, owner['executable_inode'])

    def change_cnl_row(self, data, index, change):
        root, value, *_ = data
        row = value['rows'][index]
        change(row)
        (root / 'rows' / f'{index:04d}' / 'result.json').write_bytes(p.canonical(row))

    def test_full2050_saved_join_uses_the_external_ordered_plan(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(self.call_cnl(self.cnl_fixture(Path(d))), {'python_cnl': 1342, 'rust_cnl': 699, 'rust_native_json': 9})

    def test_self_consistent_named_form_and_typed_reference_mutations_are_refused(self):
        with tempfile.TemporaryDirectory() as d:
            data = self.cnl_fixture(Path(d)); original = copy.deepcopy(data[1]['rows'][8])
            mutations = [lambda r: r['identity'].update(id='P0'), lambda r: r.update(group='rust_cnl'),
                         lambda r: r.update(form='json'), lambda r: r['typed_reference'].update(path='foreign-trusted-row')]
            for mutate in mutations:
                self.change_cnl_row(data, 8, mutate)
                with self.subTest(mutation=mutate), self.assertRaises(ValueError): self.call_cnl(data)
                data[1]['rows'][8] = copy.deepcopy(original)
                self.change_cnl_row(data, 8, lambda r: None)

    def test_self_consistent_foreign_CNL_request_bytes_are_refused(self):
        with tempfile.TemporaryDirectory() as d:
            data = self.cnl_fixture(Path(d)); directory = data[0] / 'rows/0008'
            raw = b'{"foreign":"whole-request"}'
            (directory / 'request.raw').write_bytes(raw)
            self.change_cnl_row(data, 8, lambda row: row.update(request_sha256=p.sha(raw)))
            with self.assertRaises(ValueError): self.call_cnl(data)

    def test_current400_cannot_reuse_a_rehashed_wrong_envelope_or_span(self):
        with tempfile.TemporaryDirectory() as d:
            data = self.cnl_fixture(Path(d)); index = 1342
            raw = p.canonical({'code': 'UNSUPPORTED_TARGET', 'status': 422})
            (data[0] / 'rows' / f'{index:04d}' / 'response.raw').write_bytes(raw)
            self.change_cnl_row(data, index, lambda row: row.update(response_sha256=p.sha(raw), expected_response_sha256=p.sha(raw)))
            with self.assertRaises(ValueError): self.call_cnl(data)

    def test_CNL_HTTP_owner_cannot_change_with_rehashed_metadata(self):
        with tempfile.TemporaryDirectory() as d:
            data = self.cnl_fixture(Path(d))
            self.change_cnl_row(data, 8, lambda row: row['owned_socket'].update(start_ticks=920))
            with self.assertRaises(ValueError): self.call_cnl(data)

    def rebase_fixture(self, base):
        root, authority = base / 'saved', base / 'trusted'; root.mkdir(); authority.mkdir()
        proof_bytes, original_bundles, normal, jobs, http, rows = {}, {}, {}, [], [], []
        digest = lambda v: p.sha(p.frozen_text(v).encode())
        for index in range(2039):
            target = 'python' if index < 1342 else 'rust'
            identity = {'source': 'public', 'id': f'P{index//2}', 'variant': 'none' if index % 2 == 0 else 'python'} if target == 'python' else {'phase': 'phase2', 'id': f'R{index}'}
            old, current, _ = SemanticIntegrity().bundle()
            old['prompt'] = {'name': f'case{index}'}; current['prompt'] = copy.deepcopy(old['prompt'])
            if target == 'python':
                for bundle in (old, current):
                    bundle['version'] = 'som.dsl-bundle.v1'; bundle.pop('target'); bundle['assets'].pop('target_contract')
            for bundle in (old, current):
                bundle['hashes']['payload_sha256'] = digest({k: v for k, v in bundle.items() if k != 'hashes'})
            original_bundles[(target, p.frozen_text(identity))] = old
            proof_bytes[f'rows/{index:04d}/response.raw'] = p.canonical({'bundle': current})
            jobs.append({'target': target, 'identity': identity, 'expected_http_status': 200})
            response = p.canonical({'status': 'ok', 'files': {'case.txt': str(index)}})
            response_path = f'assembly/{index:04d}.raw'; path = authority / response_path
            path.parent.mkdir(exist_ok=True); path.write_bytes(response)
            normal[index] = {'target': target, 'identity': identity, 'response_path': response_path,
                             'response_sha256': p.sha(response), 'actual_files_map_sha256': digest({'case.txt': str(index)})}
            for bundle, status, reply in ((old, 400, b'{"code":"ASSET_HASH_MISMATCH"}'), (current, 200, response)):
                http_index = len(http); directory = root / 'http' / f'{http_index:05d}'; directory.mkdir(parents=True)
                request = p.canonical({'bundle': bundle}); (directory / 'request.raw').write_bytes(request)
                (directory / 'response.raw').write_bytes(reply)
                http.append({'index': http_index, 'route': '/v1/assemble', 'status': status,
                             'request_sha256': p.sha(request), 'response_sha256': p.sha(reply)})
            rows.append({'compile_row_index': index, 'old_http_index': 2 * index, 'rebased_http_index': 2 * index + 1,
                         'target': target, 'identity': identity, 'passed': True, 'old_bundle_refused': True,
                         'rebased_response_sha256': p.sha(response), 'files_map_sha256': normal[index]['actual_files_map_sha256']})
        checker = SimpleNamespace(verify_response=lambda target, identity, status, raw: SimpleNamespace(
            original_bundle=copy.deepcopy(original_bundles[(target, p.frozen_text(identity))])))
        proof = SimpleNamespace(root=authority, read=lambda name: proof_bytes[name])
        driver = SimpleNamespace(compact=p.canonical, experiment=SimpleNamespace(value_sha=digest))
        details = {'old_bundle_rebase': {'complete': True, 'assembly_manifest_sha256': 'a' * 64, 'rows': rows}}
        return root, details, http, jobs, proof, checker, driver, normal, 'a' * 64

    def test_full2039_rebase_join_uses_trusted_typed_and_assembly_bytes(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(len(p.verify_rebase_saved_rows(*self.rebase_fixture(Path(d)))), 4078)

    def test_rebase_wrong_typed_index_and_wrong_assembly_digest_are_refused(self):
        with tempfile.TemporaryDirectory() as d:
            data = self.rebase_fixture(Path(d)); details = data[1]
            details['old_bundle_rebase']['assembly_manifest_sha256'] = '0' * 64
            with self.assertRaises(ValueError): p.verify_rebase_saved_rows(*data)
            details['old_bundle_rebase']['assembly_manifest_sha256'] = 'a' * 64
            details['old_bundle_rebase']['rows'][0]['compile_row_index'] = 1
            with self.assertRaises(ValueError): p.verify_rebase_saved_rows(*data)

    def test_locally_consistent_foreign_bundle_pair_is_refused(self):
        with tempfile.TemporaryDirectory() as d:
            data = self.rebase_fixture(Path(d)); root, details, http = data[:3]
            for first, foreign in ((0, 2), (1, 3)):
                raw = (root / 'http' / f'{foreign:05d}' / 'request.raw').read_bytes()
                (root / 'http' / f'{first:05d}' / 'request.raw').write_bytes(raw)
                http[first]['request_sha256'] = p.sha(raw)
            with self.assertRaises(ValueError): p.verify_rebase_saved_rows(*data)

    def test_rehashed_foreign_whole_product_response_is_refused(self):
        with tempfile.TemporaryDirectory() as d:
            data = self.rebase_fixture(Path(d)); root, details, http = data[:3]
            raw = (root / 'http/00003/response.raw').read_bytes()
            (root / 'http/00001/response.raw').write_bytes(raw); http[1]['response_sha256'] = p.sha(raw)
            details['old_bundle_rebase']['rows'][0].update(rebased_response_sha256=p.sha(raw),
                files_map_sha256=p.sha(p.frozen_text(json.loads(raw)['files']).encode()))
            with self.assertRaises(ValueError): p.verify_rebase_saved_rows(*data)

    def test_owner_requires_actual_same_PID_session_start_socket_and_executable(self):
        owner = self.owner(); p.verify_owner(owner, owner, 3001)
        for changes in ({'pid': 732, 'pgrp': 732, 'session': 732}, {'start_ticks': 920}, {'executable_inode': 3002},
                        {'listener': {'table': 'tcp', 'address': '0100007F', 'inode': '4432'}}, {'state': 'Z'}):
            with self.subTest(changes=changes), self.assertRaises(ValueError): p.verify_owner({**owner, **changes}, owner, 3001)

    def test_rehashed_raw_health_must_still_equal_the_trusted_fresh_proof(self):
        raw = p.canonical({'status': 'ok', 'model_loaded': False, 'blocks_loaded': True,
                          'backend_sha256': 'b', 'asset_manifest_sha256': 'a', 'formatter_sha256': 'r'})
        owner = self.owner(); record = {'status': 200, 'sha256': p.sha(raw), 'owner': owner}
        p.verify_health(raw, record, raw, {'python_backend': 'b'}, 'a', 'r', owner, 3001)
        changed = p.canonical({**json.loads(raw), 'backend_sha256': 'foreign'})
        with self.assertRaises(ValueError): p.verify_health(changed, {**record, 'sha256': p.sha(changed)}, raw,
            {'python_backend': 'b'}, 'a', 'r', owner, 3001)

    def test_formal_carrier_refuses_rebuilt_stale_attempt_and_foreign_portable_identity(self):
        expected = {'binary': {'sha256': 'b' * 64, 'size': 42, 'mode': 493}, 'assets': {}, 'tools': {}}
        record = {'version': 'som.tested-candidate-carrier.v1', 'candidate_revision': 'c' * 40, 'compile_manifest_sha256': 'd' * 64,
                  'tested_identities': expected, 'compiler_rebuilt': False, 'origin_role': 'original already tested candidate bytes',
                  'product_acceptance_passed': False, 'github': {'GITHUB_RUN_ID': '12', 'GITHUB_RUN_ATTEMPT': '2'}}
        p.verify_carrier_role(record, 'c' * 40, 'd' * 64, expected, '12', '2')
        for changed in ({**record, 'compiler_rebuilt': True}, {**record, 'github': {'GITHUB_RUN_ID': '12', 'GITHUB_RUN_ATTEMPT': '1'}},
                        {**record, 'tested_identities': {**expected, 'binary': {'sha256': 'f' * 64, 'size': 42, 'mode': 493}}}):
            with self.assertRaises(ValueError): p.verify_carrier_role(changed, 'c' * 40, 'd' * 64, expected, '12', '2')

    def test_full_command_wait_cleanup_and_streams_cannot_disagree_with_summary(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); stdout, stderr = b'whole stdout', b''
            (root / 'stdout.raw').write_bytes(stdout); (root / 'stderr.raw').write_bytes(stderr)
            full = {'argv': ['/fixed/binary', 'compile', '/input'], 'cwd': '/owned/cli/0000', 'exit_code': 0,
                    'pid': 700, 'wait_completed': True, 'owned_group_cleanup_passed': True,
                    'started_monotonic': 10.0, 'elapsed_seconds': 0.2,
                    'environment': {'PATH': '/usr/bin:/bin', 'LANG': 'C.UTF-8', 'LC_ALL': 'C.UTF-8'},
                    'stdout': 'environment-commands/prefix.stdout', 'stderr': 'environment-commands/prefix.stderr',
                    'stdout_sha256': p.sha(stdout), 'stderr_sha256': p.sha(stderr)}
            summary = {key: full[key] for key in ('argv', 'exit_code', 'wait_completed', 'owned_group_cleanup_passed', 'stdout_sha256', 'stderr_sha256')}
            row = {key: full[key] for key in ('exit_code', 'stdout_sha256', 'stderr_sha256')}
            (root / 'command.json').write_bytes(p.canonical(full))
            p.verify_full_command(root, summary, row, '/fixed/binary', '/owned/cli/0000')
            for change in ({'wait_completed': False}, {'owned_group_cleanup_passed': False}, {'stdout_sha256': '0' * 64}, {'pid': None}):
                (root / 'command.json').write_bytes(p.canonical({**full, **change}))
                with self.subTest(change=change), self.assertRaises(ValueError):
                    p.verify_full_command(root, summary, row, '/fixed/binary', '/owned/cli/0000')

    def test_missing_catalog_proof_roles_are_refused_without_source_execution(self):
        with tempfile.TemporaryDirectory() as d:
            base = Path(d); saved = base / 'saved'; saved.mkdir(); repo = base / 'repo'; repo.mkdir()
            roots = {}
            for name in ('assembly_root', 'python_inputs_root', 'rust_inputs_root', 'carrier_root'):
                path = base / name; path.mkdir(); roots[name] = path
            self.assertEqual(set(p.proof_roots_data(roots, saved, repo)), set(roots))
            with self.assertRaises(ValueError): p.proof_roots_data({k: v for k, v in roots.items() if k != 'carrier_root'}, saved, repo)
            with self.assertRaises(ValueError): p.proof_roots_data({**roots, 'carrier_root': saved / 'untrusted'}, saved, repo)


class OriginalAssertionReplay(unittest.TestCase):
    def actual_sources(self):
        repo = source.resolve().parents[3]
        class Sources:
            root = repo
            def read(self, name):
                raw = (self.root / name).read_bytes()
                revision = p.subprocess.check_output(['/usr/bin/git', '--no-replace-objects', '-C', str(self.root), 'rev-parse', 'HEAD']).decode().strip()
                expected = p.subprocess.check_output(['/usr/bin/git', '--no-replace-objects', '-C', str(self.root), 'cat-file', 'blob', revision + ':' + name])
                if raw != expected: raise AssertionError('original fixture assertion source is not exact Git')
                return raw
        return Sources()

    def original_cli(self):
        sources = self.actual_sources()
        profile_name = 'rust_profile_phase2'
        previous = sys.modules.get(profile_name)
        profile = ModuleType(profile_name)
        profile.__file__ = str(sources.root / 'som-rs/tests/rust-output-v1/rust_profile_phase2.py')
        profile.__cached__ = None
        exec(compile(sources.read('som-rs/tests/rust-output-v1/rust_profile_phase2.py'), profile.__file__, 'exec', dont_inherit=True), profile.__dict__)
        sys.modules[profile_name] = profile
        try:
            cli = ModuleType('_fixture_exact_original_cli')
            cli.__file__, cli.__cached__ = str(sources.root / p.CLI_SOURCE), None
            exec(compile(sources.read(p.CLI_SOURCE), cli.__file__, 'exec', dont_inherit=True), cli.__dict__)
            return cli
        finally:
            if previous is None: sys.modules.pop(profile_name, None)
            else: sys.modules[profile_name] = previous

    def write_frames(self, root, frames):
        http = root / 'http'
        if http.exists(): p.shutil.rmtree(http)
        rows = []
        for index, frame in enumerate(frames):
            directory = http / f'{index:05d}'; directory.mkdir(parents=True)
            row = {'index': index, 'route': frame['route'], 'status': frame['status'],
                   'request_sha256': p.sha(frame['request']), 'response_sha256': p.sha(frame['response'])}
            (directory / 'request.raw').write_bytes(frame['request']); (directory / 'response.raw').write_bytes(frame['response'])
            (directory / 'result.json').write_bytes(p.canonical(row)); rows.append(row)
        return rows

    def cli_fixture(self, base):
        cli = self.original_cli(); root = base / 'saved'; root.mkdir()
        repo = base / 'synthetic-inputs'; repo.mkdir()
        cli.REPO, cli.CASES, cli.CONTRACT, cli.TS_CONTRACT = repo, repo / 'cases', repo / 'rust', repo / 'ts'
        for directory in (cli.CASES, cli.CONTRACT, cli.TS_CONTRACT): directory.mkdir()
        (cli.CASES / 'coverage.cnl').write_text('coverage source\n')
        (cli.CASES / 'index.json').write_text(json.dumps({'cases': [{'id': 'COV', 'cnl': 'coverage.cnl'}]}))
        (repo / 'outside.cnl').write_text('outside typescript\n')
        (cli.TS_CONTRACT / 'case-inventory.json').write_text(json.dumps({'public_examples': [
            {'id': 'TS_OUT', 'typescript': 'out_of_scope', 'cnl': 'outside.cnl'}]}))
        negatives = []
        for identity, text in [('NEG_BAD', 'negative source\n'), ('RS12_TEST', 'rs refusal\n'), ('RS_OK', 'coverage source\n')]:
            negatives.append({'id': identity, 'form': 'cnl', 'body': json.dumps({'format': 'som.cnl.v1', 'source': text, 'target': 'rust'}),
                              'expected': {'code': 'INVALID_VALUE'}, 'python_control_expected': 'the same diagnostic for every target'})
        (cli.CONTRACT / 'negative-cases.json').write_text(json.dumps({'cases': negatives}))
        binary = root / 'restored-candidate/binary'; binary.parent.mkdir(); binary.write_bytes(b'never execute this fixture binary')
        commands, frames = [], []
        base_url = 'http://127.0.0.1:18979'
        def answer(raw):
            body = json.loads(raw); target, text = body.get('target'), body['source']
            if text in ('negative source\n', 'rs refusal\n'):
                return 400, p.canonical({'status': 'error', 'code': 'INVALID_VALUE'})
            if target not in (None, 'python', 'rust', 'typescript') or (target == 'typescript' and text == 'outside typescript\n'):
                return 422, p.canonical({'status': 'error', 'code': 'UNSUPPORTED_TARGET'})
            bundle = {'version': 'som.dsl-bundle.v1' if target in (None, 'python') else 'som.dsl-bundle.v2', 'prompt': {'text': text}}
            if target not in (None, 'python'): bundle['target'] = target
            return 200, p.canonical({'status': 'ok', 'bundle': bundle})
        def post(base_url, body):
            raw = body.encode() if isinstance(body, str) else body
            status, response = answer(raw); frames.append({'route': '/v1/compile', 'request': raw, 'status': status, 'response': response})
            return status, response
        def capture(binary_name, base_url, cnl_path, target):
            index, http_index = len(commands), len(frames)
            raw = cli.cli_body(cnl_path.read_text(), target); status, response = post(base_url, raw)
            stdout = p.canonical(json.loads(response)['bundle']) + b'\n' if status == 200 else b''
            stderr = b'' if status == 200 else response + b'\n'
            argv = [binary_name, 'compile', str(cnl_path), '--url', base_url] + ([] if target is None else ['--target', target])
            directory = root / 'cli' / f'{index:04d}'; directory.mkdir(parents=True)
            (directory / 'source.cnl.raw').write_bytes(cnl_path.read_bytes())
            (directory / 'stdout.raw').write_bytes(stdout); (directory / 'stderr.raw').write_bytes(stderr)
            full = {'argv': argv, 'exit_code': 0 if status == 200 else 2, 'wait_completed': True, 'owned_group_cleanup_passed': True,
                    'stdout_sha256': p.sha(stdout), 'stderr_sha256': p.sha(stderr)}
            (directory / 'command.json').write_bytes(p.canonical(full))
            commands.append({**full, 'http_request_index': http_index})
            return p.subprocess.CompletedProcess(argv, full['exit_code'], stdout, stderr), [('/v1/compile', raw)]
        original = cli.check
        def checked(binary, base_url, scratch, label, text, target, expect=None, verify_request=False):
            return original(binary, base_url, scratch, label, text, target, expect, verify_request=True)
        with patch.object(cli, 'post', side_effect=post), patch.object(cli, '_run_cli_with_request_capture', side_effect=capture), \
                patch.object(cli, 'check', side_effect=checked), patch.object(cli, 'subprocess', SimpleNamespace(run=p.no_replay_process)):
            self.assertEqual(cli.main(['--binary', str(binary), '--base-url', base_url, '--out', str(base / 'original.json'), '--refusals', 'phase2']), 0)
        saved = json.loads((base / 'original.json').read_bytes())
        rows = self.write_frames(root, frames)
        return SimpleNamespace(cli=cli, root=root, saved=saved, commands=commands, frames=frames, rows=rows, binary=binary, base=base)

    def replay_cli(self, data):
        output = data.base / ('replay-' + str(len(list(data.base.glob('replay-*')))) + '.json')
        transcript = p.OfflineTranscript(data.root, data.rows, 'http://127.0.0.1:18979')
        result = p.replay_cli_assertions(data.cli, data.root, data.saved, data.commands, transcript, output, data.binary)
        self.assertEqual(transcript.cursor, len(data.rows))
        return result

    def rehash_cli_mutation(self, data, index, status, response, stdout, stderr):
        command = data.commands[index]; capture = command['http_request_index']
        for frame in data.frames[capture:capture + 2]: frame.update(status=status, response=response)
        data.rows = self.write_frames(data.root, data.frames)
        directory = data.root / 'cli' / f'{index:04d}'
        (directory / 'stdout.raw').write_bytes(stdout); (directory / 'stderr.raw').write_bytes(stderr)
        command.update(exit_code=0 if status == 200 else 2, stdout_sha256=p.sha(stdout), stderr_sha256=p.sha(stderr))
        full = json.loads((directory / 'command.json').read_bytes()); full.update({k: command[k] for k in
            ('exit_code', 'stdout_sha256', 'stderr_sha256')}); (directory / 'command.json').write_bytes(p.canonical(full))
        row = data.saved['rows'][index]
        row.update(http_status=status, exit_code=command['exit_code'], stdout_sha256=p.sha(stdout), stderr_sha256=p.sha(stderr), passed=True, problems=[])
        if status == 200:
            row.pop('code', None); row['bundle_version'] = json.loads(response)['bundle']['version']
        else:
            row.pop('bundle_version', None); row['code'] = json.loads(response)['code']
        if row['target'] == 'typescript':
            key = 'typescript_refusal' if row['input'].endswith(' refusal') else 'typescript'
            data.saved[key] = copy.deepcopy(row)
        data.saved['refusals'] = sum(r['http_status'] != 200 for r in data.saved['rows'])
        data.saved['refusals_exit_2'] = sum(r['http_status'] != 200 and r['exit_code'] == 2 for r in data.saved['rows'])
        data.saved['passed'], data.saved['failed'] = len(data.saved['rows']), []

    def test_original_CLI_main_replay_consumes_all_named_transports_without_native_execution(self):
        with tempfile.TemporaryDirectory() as d:
            data = self.cli_fixture(Path(d))
            import socket
            with patch.object(p.subprocess, 'Popen', side_effect=AssertionError('no native child')), \
                    patch.object(socket, 'socket', side_effect=AssertionError('no live socket')):
                self.assertEqual(self.replay_cli(data), data.saved)

    def test_rehashed_TypeScript_refusal_cannot_become_a_success_bundle(self):
        with tempfile.TemporaryDirectory() as d:
            data = self.cli_fixture(Path(d)); index = len(data.commands) - 1
            bundle = {'version': 'som.dsl-bundle.v2', 'target': 'typescript', 'prompt': {'foreign': True}}
            self.rehash_cli_mutation(data, index, 200, p.canonical({'status': 'ok', 'bundle': bundle}), p.canonical(bundle), b'')
            with self.assertRaises(ValueError): self.replay_cli(data)

    def test_plain_and_explicit_Python_stdout_must_be_identical_bytes(self):
        with tempfile.TemporaryDirectory() as d:
            data = self.cli_fixture(Path(d)); index = 1; frame = data.frames[data.commands[index]['http_request_index']]
            raw = json.dumps(json.loads(frame['response'])['bundle'], indent=3).encode() + b'\n'
            self.rehash_cli_mutation(data, index, 200, frame['response'], raw, b'')
            with self.assertRaises(ValueError): self.replay_cli(data)

    def test_rehashed_negative_CLI_code_must_match_the_original_expected_code(self):
        with tempfile.TemporaryDirectory() as d:
            data = self.cli_fixture(Path(d)); index = next(i for i, row in enumerate(data.saved['rows']) if row['input'] == 'NEG_BAD')
            error = p.canonical({'status': 'error', 'code': 'INVALID_SCHEMA'})
            self.rehash_cli_mutation(data, index, 400, error, b'', error)
            with self.assertRaises(ValueError): self.replay_cli(data)

    def recipe_fixture(self, base):
        sources = self.actual_sources(); root = base / 'saved'; root.mkdir()
        frames, reply_specs = [], []
        recipes, targets, provenance = p.original_refusal_assertions(sources, SimpleNamespace(post=None))
        plan = json.loads(sources.read('som-rs/contracts/rust-output-v1/acceptance-plan.json'))
        by_id = {row['id']: row for row in plan['assemble_refusals']['recipes']}
        for identity in ('A1', 'A2', 'A3', 'A4', 'A5', 'A7', 'A9'):
            row = by_id[identity]; reply_specs.append((row['code'], row['http_status'], row['prompt_pointer']))
        negatives = json.loads(sources.read('som-rs/contracts/rust-output-v1/negative-cases.json'))['cases']
        rs13 = next(row for row in negatives if row['id'].startswith('RS13'))
        reply_specs.append((rs13['expected']['code'], rs13['http_status'], '/bundle' + rs13['expected']['prompt_pointer']))
        for identity in ('A1', 'A2', 'A4', 'A5', 'A7', 'A9'):
            row = by_id[identity]; reply_specs.append((row['code'], row['http_status'], row['prompt_pointer']))
        error_index = 0
        def bundle(prompt, target):
            result = {'version': 'som.dsl-bundle.v2' if target == 'rust' else 'som.dsl-bundle.v1', 'prompt': prompt,
                'dsl': {'files': [{'path': 'README.md', 'blocks': [{'id': 'rust.base' if target == 'rust' else 'orm.base'}]}]},
                'assets': {name: {'id': ('r.' if target == 'rust' else 'p.') + name, 'sha256': 'a' * 64}
                           for name in ('catalog', 'block_library', 'backend', 'formatter')}}
            if target == 'rust': result['target'] = target
            return recipes.rehash(result)
        def post(base_url, body, route='/v1/compile'):
            nonlocal error_index
            raw = body.encode() if isinstance(body, str) else body; value = json.loads(raw)
            if route == '/v1/compile':
                status, response = 200, p.canonical({'status': 'ok', 'bundle': bundle(value['prompt'], value.get('target'))})
            elif value['bundle']['prompt'].get('resource', {}).get('fields', [{}])[0].get('name') == 'Title':
                status, response = 200, b'{"status":"ok","files":{"renamed.txt":"Title"}}'
            else:
                code, status, pointer = reply_specs[error_index]; error_index += 1
                leaf = targets.dump(targets.resolve(value, pointer)).encode(); offset = raw.index(leaf)
                response = p.canonical({'status': 'error', 'code': code, 'diagnostics': [
                    {'code': code, 'prompt_pointer': pointer, 'byte_start': offset, 'byte_end': offset + len(leaf)}]})
            frames.append({'route': route, 'request': raw, 'response': response, 'status': status})
            return status, response
        targets.post = post
        prompt = json.loads(sources.read('som-rs/contracts/compiler-v1/examples/v1.prompt.json'))
        status, raw = post('http://127.0.0.1:18979', targets.dump({'format': 'som.prompt-json.v1', 'target': 'rust', 'prompt': prompt}))
        self.assertEqual(status, 200)
        saved = recipes.phase2_refusals('http://127.0.0.1:18979', json.loads(raw)['bundle'])
        self.assertTrue(all(row['passed'] for row in saved)); self.assertEqual(error_index, 14)
        rows = self.write_frames(root, frames)
        return SimpleNamespace(root=root, rows=rows, frames=frames, saved=saved, prompt=prompt, sources=sources, provenance=provenance)

    def replay_recipes(self, data):
        transcript = p.OfflineTranscript(data.root, data.rows, 'http://127.0.0.1:18979')
        recipes, targets, _ = p.original_refusal_assertions(data.sources, transcript)
        rows = p.replay_refusal_assertions(recipes, targets, transcript, data.prompt, data.saved)
        self.assertEqual(transcript.cursor, len(data.rows))
        return rows

    def test_original_fifteen_recipe_assertions_replay_the_complete_positive_transcript(self):
        with tempfile.TemporaryDirectory() as d:
            data = self.recipe_fixture(Path(d)); self.assertEqual(self.replay_recipes(data), data.saved)
            self.assertEqual(set(data.provenance['recipes']['function_AST_sha256']), set(p.RUST_RECIPE_FUNCTIONS))

    def test_missing_or_substituted_recipe_HTTP_calls_cannot_keep_passing_rows(self):
        with tempfile.TemporaryDirectory() as d:
            data = self.recipe_fixture(Path(d)); original = copy.deepcopy(data.frames)
            for changed in (original[:2] + original[3:], [*original[:2], copy.deepcopy(original[3]), *original[3:]]):
                data.rows = self.write_frames(data.root, changed)
                with self.assertRaises(ValueError): self.replay_recipes(data)

    def test_recipe_status_code_pointer_and_span_are_rechecked_from_raw(self):
        with tempfile.TemporaryDirectory() as d:
            data = self.recipe_fixture(Path(d)); original = copy.deepcopy(data.frames)
            index = next(i for i, frame in enumerate(original) if frame['route'] == '/v1/assemble' and frame['status'] != 200)
            for field in ('status', 'code', 'pointer', 'span'):
                changed = copy.deepcopy(original); frame = changed[index]; reply = json.loads(frame['response'])
                if field == 'status': frame['status'] = 422
                elif field == 'code': reply['code'] = 'INVALID_SCHEMA'
                elif field == 'pointer': reply['diagnostics'][0]['prompt_pointer'] = '/bundle/target'
                else: reply['diagnostics'][0].update(byte_start=0, byte_end=1)
                frame['response'] = p.canonical(reply); data.rows = self.write_frames(data.root, changed)
                with self.subTest(field=field), self.assertRaises(ValueError): self.replay_recipes(data)

    def test_A6_current_collision_and_A8_positive_rename_comparison_are_mandatory(self):
        with tempfile.TemporaryDirectory() as d:
            data = self.recipe_fixture(Path(d)); original = copy.deepcopy(data.frames)
            a6 = next(i for i, frame in enumerate(original) if frame['route'] == '/v1/assemble'
                      and json.loads(frame['response']).get('code') == 'INVALID_VALUE')
            changed = copy.deepcopy(original); changed[a6].update(status=422, response=b'{"code":"UNSUPPORTED_TARGET"}')
            data.rows = self.write_frames(data.root, changed)
            with self.assertRaises(ValueError): self.replay_recipes(data)
            changed = copy.deepcopy(original); a8 = next(i for i, frame in enumerate(changed) if frame['route'] == '/v1/assemble' and frame['status'] == 200)
            changed[a8]['response'] = b'{"status":"ok","files":{"foreign.txt":"wrong"}}'
            data.rows = self.write_frames(data.root, changed)
            with self.assertRaises(ValueError): self.replay_recipes(data)


class NativeSemanticProtocolTests(unittest.TestCase):
    # Reuse the untouched original transcript factory and raw wire writer.
    actual_sources = OriginalAssertionReplay.actual_sources
    write_frames = OriginalAssertionReplay.write_frames
    recipe_fixture = OriginalAssertionReplay.recipe_fixture

    def native_replay(self, data, fixture, prefix=0):
        module = fixture.controller(); sources = fixture.sources()
        transcript = module.OfflineTranscript(data.root, data.rows, 'http://127.0.0.1:18979')
        transcript.cursor = prefix
        with module._native_original_refusal_assertions(sources, transcript) as (recipes, targets, provenance):
            self.assertIs(type(targets), module.NativeRefusalTargets)
            self.assertEqual(set(vars(targets)), {'root', 'rows', 'base', 'cursor'})
            self.assertEqual(recipes.RUST_ASSETS, ('catalog', 'block_library', 'backend', 'formatter'))
            self.assertEqual(provenance, data.provenance)
            self.assertEqual(targets.cursor, prefix)
            rows = module.replay_refusal_assertions(recipes, targets, targets, data.prompt, data.saved)
        self.assertEqual(transcript.cursor, len(data.rows))
        self.assertEqual(targets.cursor, transcript.cursor)
        return rows

    def test_native_full_fifteen_refusals_use_actual_typed_fragments_and_one_cursor(self):
        with tempfile.TemporaryDirectory() as d:
            data = self.recipe_fixture(Path(d)); fixture = core_native_fixture(self)
            try:
                self.assertEqual(self.native_replay(data, fixture), data.saved)
                self.assertEqual(len(data.saved), 15)
                self.assertGreater(fixture.domain.fragment_counter, 3)
                self.assertFalse(any('.original.' in key for key in fixture.domain.records))
                # Model an already consumed prefix, then consume base compile plus
                # every refusal on the single real NativeRefusalTargets cursor.
                data.rows = self.write_frames(data.root, [copy.deepcopy(data.frames[0]), *data.frames])
                self.assertEqual(self.native_replay(data, fixture, prefix=1), data.saved)
            finally:
                fixture.stack.close()

    def test_native_status_code_pointer_and_span_mutations_reach_original_assertions(self):
        with tempfile.TemporaryDirectory() as d:
            data = self.recipe_fixture(Path(d)); original = copy.deepcopy(data.frames); fixture = core_native_fixture(self)
            try:
                self.assertEqual(self.native_replay(data, fixture), data.saved)
                index = next(i for i, frame in enumerate(original) if frame['route'] == '/v1/assemble' and frame['status'] != 200)
                for field in ('status', 'code', 'pointer', 'span'):
                    changed = copy.deepcopy(original); frame = changed[index]; reply = json.loads(frame['response'])
                    if field == 'status': frame['status'] = 422
                    elif field == 'code': reply['code'] = 'INVALID_SCHEMA'
                    elif field == 'pointer': reply['diagnostics'][0]['prompt_pointer'] = '/bundle/target'
                    else: reply['diagnostics'][0].update(byte_start=0, byte_end=1)
                    frame['response'] = p.canonical(reply); data.rows = self.write_frames(data.root, changed)
                    with self.subTest(field=field), self.assertRaises(ValueError): self.native_replay(data, fixture)
            finally:
                fixture.stack.close()

    def test_native_A6_A8_missing_reordered_and_raw_hash_controls_remain_mandatory(self):
        with tempfile.TemporaryDirectory() as d:
            data = self.recipe_fixture(Path(d)); original = copy.deepcopy(data.frames); fixture = core_native_fixture(self)
            try:
                self.assertEqual(self.native_replay(data, fixture), data.saved)
                a6 = next(i for i, frame in enumerate(original) if frame['route'] == '/v1/assemble'
                          and json.loads(frame['response']).get('code') == 'INVALID_VALUE')
                a8 = next(i for i, frame in enumerate(original) if frame['route'] == '/v1/assemble' and frame['status'] == 200)
                changed_a6 = copy.deepcopy(original); changed_a6[a6].update(status=422, response=b'{"code":"UNSUPPORTED_TARGET"}')
                changed_a8 = copy.deepcopy(original); changed_a8[a8]['response'] = b'{"status":"ok","files":{"foreign.txt":"wrong"}}'
                reordered = copy.deepcopy(original); reordered[2], reordered[3] = reordered[3], reordered[2]
                for label, frames in [('A6', changed_a6), ('A8', changed_a8), ('missing', original[:2] + original[3:]),
                                      ('reordered', reordered)]:
                    data.rows = self.write_frames(data.root, frames)
                    with self.subTest(label=label), self.assertRaises(ValueError): self.native_replay(data, fixture)
                data.rows = self.write_frames(data.root, original)
                raw = data.root / 'http/00001/response.raw'; raw.write_bytes(raw.read_bytes() + b' ')
                with self.assertRaisesRegex(ValueError, 'raw HTTP record differs'): self.native_replay(data, fixture)
            finally:
                fixture.stack.close()

    def test_whole_thirteen_role_preflight_runs_before_the_first_helper_body(self):
        fixture = core_native_fixture(self)
        try:
            module, sources = fixture.controller(), fixture.sources()
            self.assertEqual(len(fixture.group_roles), 13)
            last = fixture.group_roles[next(reversed(fixture.group_roles))]
            row = fixture.rows[last]; original = row['source']
            row['source'] = fixture.production_path
            try:
                with self.assertRaisesRegex(ValueError, 'fixed semantic join helper source differs'):
                    with module._native_join_modules(sources, fixture.group_paths): pass
                self.assertEqual(list(fixture.body_markers.iterdir()), [])
            finally:
                row['source'] = original
            del fixture.rows[last]
            try:
                with self.assertRaises(KeyError) as missing:
                    with module._native_join_modules(sources, fixture.group_paths): pass
                self.assertEqual(missing.exception.args, (last,))
                self.assertEqual(list(fixture.body_markers.iterdir()), [])
            finally:
                fixture.rows[last] = row
            with module._native_join_modules(sources, fixture.group_paths) as modules:
                self.assertEqual(set(modules), set(fixture.group_paths))
                self.assertTrue(all(value.VALUE == 41 for value in modules.values()))
            self.assertEqual(len(list(fixture.body_markers.iterdir())), 13)
            fixture.controls.verify_installed()
        finally:
            fixture.stack.close()

    def test_unknown_prefix_missing_binding_and_wrong_identity_refuse_before_helper_body(self):
        with self.assertRaises(ModuleNotFoundError) as missing: p._native_controls()
        self.assertEqual(missing.exception.name, '_som_native_active_controls_binding')
        fixture = core_native_fixture(self, variant='_unlisted_semantic_protocol')
        try:
            module, sources = fixture.controller(), fixture.sources()
            with self.assertRaisesRegex(ValueError, 'unlisted native semantic namespace'):
                with module._native_join_modules(sources, fixture.group_paths): pass
            with self.assertRaisesRegex(ValueError, 'helper/candidate identity differs'):
                module._native_controls(revision='2' * 40, candidate='2' * 40)
            with self.assertRaisesRegex(ValueError, 'repository differs'):
                module._native_controls(fixture.root.parent)
            self.assertEqual(list(fixture.body_markers.iterdir()), [])
        finally:
            fixture.stack.close()

    def test_primary_identity_and_all_secondary_steps_survive_real_guard_and_close_faults(self):
        fixture = core_native_fixture(self)
        primary, source_error, close_error = RuntimeError('original body failure'), ValueError('source-postcheck failure'), OSError('borrow-close fault')
        previous_trace = sys.gettrace(); observed = []
        try:
            module, sources = fixture.controller(), fixture.sources()
            sources.failure = source_error
            lease = None
            close_code = fixture.core._Lease.close.__code__
            def trace(frame, event, argument):
                if event == 'call' and frame.f_code is close_code and frame.f_globals is fixture.core.__dict__:
                    actual = frame.f_locals['self']
                    if not actual._owns:
                        observed.append('actual-borrow-close-entered')
                        raise close_error
                return trace
            try:
                caught, body_traceback = None, None
                try:
                    with module._borrowed_native(sources) as lease:
                        fixture.domain.finder.allowed.add('unlisted-fault.py')
                        sys.settrace(trace)
                        raise primary
                except RuntimeError as error:
                    caught, body_traceback = error, error.__traceback__
                self.assertIs(caught, primary)
                traceback_codes = []
                original_traceback = body_traceback
                while original_traceback is not None:
                    traceback_codes.append(original_traceback.tb_frame.f_code)
                    original_traceback = original_traceback.tb_next
                self.assertIn(self.test_primary_identity_and_all_secondary_steps_survive_real_guard_and_close_faults.__func__.__code__,
                              traceback_codes)
                secondary = primary.__som_native_caller_secondary_errors__
                self.assertEqual([item['phase'] for item in secondary], ['source-postcheck', 'controls-postcheck', 'borrow-close'])
                self.assertIs(secondary[0]['exception'], source_error)
                self.assertIs(secondary[2]['exception'], close_error)
                self.assertIn('original finder source admission', str(secondary[1]['exception']))
                self.assertEqual(observed, ['actual-borrow-close-entered'])
                self.assertGreaterEqual(sources.verify_calls, 1)
                self.assertFalse(fixture.controls._closed)
            finally:
                sys.settrace(previous_trace)
                fixture.domain.finder.allowed.discard('unlisted-fault.py')
                sources.failure = None
                if lease is not None: lease.close()
            fixture.controls.verify_installed()
        finally:
            sys.settrace(previous_trace)
            fixture.stack.close()

    def test_original_fragment_names_order_raw_hash_and_constant_are_source_bound(self):
        with tempfile.TemporaryDirectory() as d:
            data = self.recipe_fixture(Path(d)); fixture = core_native_fixture(self)
            try:
                self.assertEqual(self.native_replay(data, fixture), data.saved)
                for suffix in ('targets', 'recipes'):
                    role = 'semantic.graph.original.' + suffix; selector = fixture.rows[role]['selector']
                    original = list(selector['names']); selector['names'] = original[:-1]
                    try:
                        with self.subTest(selector=suffix), self.assertRaisesRegex(ValueError, 'original fragment AST differs'):
                            self.native_replay(data, fixture)
                    finally:
                        selector['names'] = original
                # Even a forged protocol source-hash update cannot replace the
                # separately pinned original function-node order.
                import ast
                member = p.RUST_RECIPE_SOURCE
                original_raw = fixture.raw[member]
                original_pin = fixture.policy['sources'][member]['sha256']
                tree = ast.parse(original_raw)
                positions = [i for i, node in enumerate(tree.body) if isinstance(node, ast.FunctionDef)
                             and node.name in p.RUST_RECIPE_FUNCTIONS]
                tree.body[positions[0]], tree.body[positions[1]] = tree.body[positions[1]], tree.body[positions[0]]
                reordered = ast.unparse(tree).encode()
                fixture.raw[member] = reordered
                fixture.policy['sources'][member]['sha256'] = p.sha(reordered)
                try:
                    with self.assertRaisesRegex(ValueError, 'exact original fragment AST differs'):
                        self.native_replay(data, fixture)
                finally:
                    fixture.raw[member] = original_raw
                    fixture.policy['sources'][member]['sha256'] = original_pin
                for member, mutation in ((p.TARGET_ASSERTION_SOURCE, lambda raw: raw + b'\n# foreign raw bytes\n'),
                        (p.RUST_RECIPE_SOURCE, lambda raw: raw.replace(b'"catalog", "block_library", "backend", "formatter"',
                                                                       b'"foreign", "block_library", "backend", "formatter"', 1))):
                    original = fixture.raw[member]; changed = mutation(original)
                    self.assertNotEqual(changed, original)
                    fixture.raw[member] = changed
                    try:
                        with self.subTest(source=member), self.assertRaisesRegex(ValueError, 'role source bytes changed'):
                            self.native_replay(data, fixture)
                    finally:
                        fixture.raw[member] = original
                recipe = fixture.root / p.RUST_RECIPE_SOURCE; original = recipe.read_bytes()
                recipe.write_bytes(original + b'\n# physical changed recipe\n')
                try:
                    with self.assertRaisesRegex(ValueError, 'protocol original source bytes differ'):
                        self.native_replay(data, fixture)
                finally:
                    recipe.write_bytes(original)
            finally:
                fixture.stack.close()

    def test_typed_target_foreign_hook_and_extra_field_are_refused_before_fragment_use(self):
        with tempfile.TemporaryDirectory() as d:
            data = self.recipe_fixture(Path(d)); fixture = core_native_fixture(self); hooks = []
            try:
                self.assertEqual(self.native_replay(data, fixture), data.saved)
                module = fixture.controller(); owner = module.NativeRefusalTargets
                def foreign(self, name):
                    hooks.append(name)
                    raise AssertionError('foreign hook executed')
                owner.__getattribute__ = foreign
                try:
                    with self.assertRaisesRegex(ValueError, 'source-owned typed class namespace changed'):
                        self.native_replay(data, fixture)
                    self.assertEqual(hooks, [])
                finally:
                    del owner.__getattribute__
                previous = sys.gettrace()
                def extra(frame, event, argument):
                    if event == 'line' and frame.f_globals is module.__dict__ and frame.f_code.co_qualname == '_native_original_refusal_assertions':
                        target = frame.f_locals.get('targets')
                        if type(target) is owner: object.__getattribute__(target, '__dict__')['unexpected'] = None
                    return extra
                try:
                    sys.settrace(extra)
                    with self.assertRaisesRegex(ValueError, 'local object fields differ'): self.native_replay(data, fixture)
                finally:
                    sys.settrace(previous)
            finally:
                fixture.stack.close()


if __name__ == '__main__':
    unittest.main()

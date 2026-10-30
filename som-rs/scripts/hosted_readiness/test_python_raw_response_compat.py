"""Whole retained-data fixtures. Run only on GitHub; no native acceptance claim."""
from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
FIXTURE = HERE / 'fixtures/python-raw-compat-v1'
PINS = {
    'python_backend_source_data.py': '144c662a236b51e953827de8a0ca4ea4078c2eab57d2d5d9c037bc5d572a961f',
    'raw_digest_compat.py': 'a025bd99b56dafc4de65c268318f583e117adfe16988d61bb9180cb5e8da0896',
    'python_raw_response_compat.py': '5d0a69ab90acc46475944851aa81e11110458345a7fe4a87228cf157d12adc30',
}


def load(name, filename):
    if name in sys.modules:
        raise RuntimeError('preloaded data fixture dependency refused')
    path = HERE / filename
    if hashlib.sha256(path.read_bytes()).hexdigest() != PINS[filename]:
        raise RuntimeError('exact data fixture dependency differs')
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


class PythonRawResponseDataTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if os.environ.get('GITHUB_ACTIONS') != 'true' or os.environ.get('RUNNER_ENVIRONMENT') != 'github-hosted':
            raise RuntimeError('this whole data fixture must run on a GitHub-hosted runner')
        load('python_backend_source_data', 'python_backend_source_data.py')
        load('raw_digest_compat', 'raw_digest_compat.py')
        cls.module = load('_som_python_raw_response_data_fixture', 'python_raw_response_compat.py')
        # Genuine authenticated members and actual Git-derived records precede every inner negative.
        cls.members = cls.module._authenticate_fixture(FIXTURE)
        cls.sources = cls.module._derive_sources(REPO)
        cls.canonical_records = cls.module._qualify_canonical(REPO, cls.sources)
        cls.good = {form: cls.module._pair_from_members(cls.members, form) for form in ('omitted', 'python')}
        for pair in cls.good.values():
            cls.module._compare_pair_data(pair, cls.sources)

    def pair(self, form='omitted'):
        return copy.deepcopy(self.good[form])

    def transport(self, pair, side, phase, mutate):
        key = phase + '_transport'
        value = json.loads(pair[side][key]); mutate(value)
        pair[side][key] = self.module.compact(value)

    def response(self, pair, side, phase, raw):
        pair[side][phase + '_response'] = raw
        def update(value):
            value['bytes'], value['sha256'] = len(raw), self.module.sha(raw)
            value['headers'] = [[k, str(len(raw)) if k.lower() == 'content-length' else v]
                                for k, v in value['headers']]
        self.transport(pair, side, phase, update)

    def compile(self, pair, mutate, *, rehash=True, sync_own=True):
        value = json.loads(pair['candidate']['compile_response'])
        mutate(value['bundle'])
        bundle = value['bundle']
        if rehash:
            bundle['hashes']['payload_sha256'] = self.module.value_sha({k: v for k, v in bundle.items() if k != 'hashes'})
        self.response(pair, 'candidate', 'compile', self.module.compact(value))
        if sync_own:
            pair['candidate']['assemble_request'] = self.module.compact({'bundle': bundle})

    def copied_fixture(self):
        temp = tempfile.TemporaryDirectory(prefix='som-raw-data-')
        self.addCleanup(temp.cleanup)
        root = Path(temp.name) / 'fixture'
        shutil.copytree(FIXTURE, root)
        return root

    def test_public_both_forms_preserve_original_strict_failures_and_own_request_hashes(self):
        result = self.module.analyze_retained_fixture_pair(REPO, FIXTURE)
        self.assertEqual([row['form'] for row in result['pairs']], ['omitted', 'python'])
        self.assertEqual(len(result['canonical_vectors_revalidated']), 11)
        self.assertEqual(result['original_strict_compile_failures'], 2)
        self.assertEqual(result['original_strict_assemble_passes'], 2)
        self.assertEqual(result['selected_current_source_revision'], self.sources[1]['revision'])
        self.assertEqual(result['retained_candidate_origin_revision'], '9763e9b86f36a99229b70d8c571734e9a740e81c')
        self.assertFalse(result['retained_data_is_fresh_current_candidate_proof'])
        for row in result['pairs']:
            self.assertFalse(row['strict_compile_byte_equal'])
            self.assertTrue(row['strict_assemble_byte_equal'])
            self.assertNotEqual(row['before_assemble_request_sha256'], row['candidate_assemble_request_sha256'])
            self.assertEqual([pin[0] for pin in row['independently_derived_digest_pins']],
                             ['/bundle/assets/backend/sha256', '/bundle/hashes/payload_sha256'])
            self.assertFalse(row['native_authority']); self.assertFalse(row['SourceSeal'])
            self.assertFalse(row['live_process_ownership_verified']); self.assertFalse(row['product_acceptance_passed'])

    def test_private_primitive_uses_genuine_complete_source_vectors_for_both_saved_forms(self):
        self.assertEqual([len(row['entries']) for row in self.sources], [27, 29])
        for form in ('omitted', 'python'):
            result = self.module._compare_pair_data(self.pair(form), self.sources)
            self.assertEqual(result['form'], form)
            pin = result['independently_derived_digest_pins'][0]
            self.assertEqual(pin[1:], (self.sources[0]['backend_sha256'], self.sources[1]['backend_sha256']))

    def test_complete_eleven_frozen_canonical_vectors_execute_new_implementation(self):
        vectors = json.loads((REPO / self.module.VECTORS).read_bytes())
        self.assertEqual(len(vectors['vectors']), 11)
        self.assertEqual([row['id'] for row in self.canonical_records], [row['id'] for row in vectors['vectors']])
        for row in vectors['vectors']:
            with self.subTest(vector=row['id']):
                actual = self.module.canonical(self.module._parse(row['input_json'].encode()))
                self.assertEqual(actual, row['canonical'])
                self.assertEqual(self.module.sha(actual.encode()), row['sha256'])

    def test_public_has_no_expected_digest_or_native_authority_parameter_and_result_is_readonly(self):
        with self.assertRaises(TypeError):
            self.module.analyze_retained_fixture_pair(REPO, FIXTURE, expected_digest='0' * 64)
        with self.assertRaises(TypeError):
            self.module.analyze_retained_fixture_pair(REPO, FIXTURE, approved=True)
        result = self.module._compare_pair_data(self.pair(), self.sources)
        with self.assertRaises(TypeError): result['native_authority'] = True
        with self.assertRaises(TypeError): result['generated_file_sha256']['new'] = '0' * 64
        self.assertFalse(result['SourceSeal']); self.assertFalse(result['product_acceptance_passed'])

    def test_outer_authentication_missing_extra_symlink_and_changed_member_are_separate(self):
        name = 'omitted/before/compile/request.raw'
        for mode in ('missing', 'extra', 'symlink', 'hash'):
            with self.subTest(mode=mode):
                root = self.copied_fixture(); path = root / name
                if mode == 'missing': path.unlink()
                elif mode == 'extra': (root / 'extra.raw').write_bytes(b'not in the frozen set')
                elif mode == 'symlink':
                    path.unlink(); path.symlink_to(FIXTURE / name)
                else: path.write_bytes(path.read_bytes() + b' ')
                with self.assertRaisesRegex(ValueError, 'missing or extra|symbolic link|fingerprint'):
                    self.module.analyze_retained_fixture_pair(REPO, root)

    def test_inner_same_form_compile_request_and_target_members_are_exact(self):
        pair = self.pair(); pair['candidate']['compile_request'] += b' '
        with self.assertRaisesRegex(ValueError, 'same-form compile request bytes'):
            self.module._compare_pair_data(pair, self.sources)
        pair = self.pair(); pair['form'] = 'python'
        with self.assertRaisesRegex(ValueError, 'compile request form'):
            self.module._compare_pair_data(pair, self.sources)

    def test_inner_transport_status_method_route_read_complete_hash_and_length_refuse(self):
        cases = [('status', 500, 'status'), ('method', 'GET', 'method or route'),
                 ('route', '/v1/other', 'method or route'), ('read_complete', False, 'read is incomplete'),
                 ('sha256', '0' * 64, 'length or fingerprint'), ('bytes', 0, 'length or fingerprint'),
                 ('owned_pid', False, 'PID or port'), ('port', 0, 'PID or port')]
        for key, changed, error in cases:
            with self.subTest(key=key):
                pair = self.pair()
                self.transport(pair, 'candidate', 'compile', lambda t: t.__setitem__(key, changed))
                with self.assertRaisesRegex(ValueError, error):
                    self.module._compare_pair_data(pair, self.sources)

    def test_inner_transport_headers_and_same_role_observations_are_checked(self):
        pair = self.pair()
        self.transport(pair, 'candidate', 'compile', lambda t: t.__setitem__('headers', [['content-length', '0']]))
        with self.assertRaisesRegex(ValueError, 'content length'):
            self.module._compare_pair_data(pair, self.sources)
        pair = self.pair()
        self.transport(pair, 'candidate', 'assemble', lambda t: t.__setitem__('owned_pid', t['owned_pid'] + 1))
        with self.assertRaisesRegex(ValueError, 'same-role retained transport'):
            self.module._compare_pair_data(pair, self.sources)

    def test_inner_prompt_dsl_and_full_payload_hashes_cannot_be_self_reported(self):
        for name, message in [('prompt_sha256', 'complete prompt hash'),
                              ('dsl_sha256', 'complete DSL hash'), ('payload_sha256', 'complete payload hash')]:
            with self.subTest(name=name):
                pair = self.pair()
                self.compile(pair, lambda b: b['hashes'].__setitem__(name, '0' * 64), rehash=False)
                with self.assertRaisesRegex(ValueError, message):
                    self.module._compare_pair_data(pair, self.sources)

    def test_inner_payload_hash_covers_every_member_not_only_prompt_or_changed_leaves(self):
        pair = self.pair()
        def subset(bundle): bundle['hashes']['payload_sha256'] = self.module.value_sha({'prompt': bundle['prompt']})
        self.compile(pair, subset, rehash=False)
        with self.assertRaisesRegex(ValueError, 'complete payload hash'):
            self.module._compare_pair_data(pair, self.sources)

    def test_inner_backend_rejects_forged_descriptor_after_valid_payload_recomputation(self):
        pair = self.pair()
        self.compile(pair, lambda b: b['assets']['backend'].__setitem__('sha256', '0' * 64))
        with self.assertRaisesRegex(ValueError, 'own complete source vector'):
            self.module._compare_pair_data(pair, self.sources)

    def test_inner_unlisted_semantic_change_refuses_after_valid_full_payload_and_own_bundle(self):
        pair = self.pair()
        self.compile(pair, lambda b: b['assets']['block_library'].__setitem__('sha256', '0' * 64))
        with self.assertRaisesRegex(ValueError, 'outside the pinned digest leaves'):
            self.module._compare_pair_data(pair, self.sources)

    def test_inner_render_only_formatter_change_is_never_an_allowed_leaf(self):
        pair = self.pair()
        self.compile(pair, lambda b: b['assets']['formatter'].__setitem__('sha256', '0' * 64))
        with self.assertRaisesRegex(ValueError, 'formatter bytes must remain exact'):
            self.module._compare_pair_data(pair, self.sources)

    def test_inner_v6_v7_missing_real_native_formatter_authority_refuses(self):
        for version in ('som.prompt.v6', 'som.prompt.v7'):
            with self.subTest(version=version):
                pair = self.pair(); request = json.loads(pair['before']['compile_request'])
                request['prompt']['version'] = version
                for side in ('before', 'candidate'): pair[side]['compile_request'] = self.module.compact(request)
                with self.assertRaisesRegex(ValueError, 'separately reviewed real native formatter authority'):
                    self.module._compare_pair_data(pair, self.sources)

    def test_inner_raw_whitespace_order_and_escaped_digest_are_not_normalized(self):
        for mode in ('space', 'order', 'escaped'):
            with self.subTest(mode=mode):
                pair = self.pair(); raw = pair['candidate']['compile_response']
                if mode == 'space': raw = b' ' + raw
                elif mode == 'order':
                    value = json.loads(raw); raw = self.module.compact({'status': value['status'], 'bundle': value['bundle']})
                else:
                    digest = self.sources[1]['backend_sha256'].encode()
                    escaped = ('\\u%04x' % digest[0]).encode() + digest[1:]
                    self.assertEqual(raw.count(digest), 1)
                    raw = raw.replace(digest, escaped)
                self.response(pair, 'candidate', 'compile', raw)
                with self.assertRaisesRegex(ValueError, 'outside the pinned digest leaves|exact unescaped digest'):
                    self.module._compare_pair_data(pair, self.sources)

    def test_inner_complete_json_scanner_refuses_malformed_duplicate_nonfinite_and_utf8(self):
        good = self.good['omitted']['candidate']['compile_response']
        values = [b'{', b'{"status":"ok",' + good[1:],
                  b'{"\\u0073tatus":"ok",' + good[1:],
                  b'{"bundle":NaN,"status":"ok"}', b'{"bundle":1e9999,"status":"ok"}', b'\xff']
        for raw in values:
            with self.subTest(raw_hash=hashlib.sha256(raw).hexdigest()):
                pair = self.pair(); self.response(pair, 'candidate', 'compile', raw)
                with self.assertRaises(ValueError):
                    self.module._compare_pair_data(pair, self.sources)

    def test_inner_complete_json_scanner_refuses_depth_and_size(self):
        for raw in (b'[' * 258 + b'0' + b']' * 258, b' ' * (self.module.raw_compat.MAX_BYTES + 1)):
            with self.subTest(length=len(raw)):
                pair = self.pair(); self.response(pair, 'candidate', 'compile', raw)
                with self.assertRaisesRegex(ValueError, 'depth|size limit'):
                    self.module._compare_pair_data(pair, self.sources)

    def test_inner_extra_compile_envelope_member_refuses_before_raw_masking(self):
        pair = self.pair(); value = json.loads(pair['candidate']['compile_response'])
        value['extra'] = 'not an allowed envelope member'
        self.response(pair, 'candidate', 'compile', self.module.compact(value))
        with self.assertRaisesRegex(ValueError, 'complete successful compile envelope'):
            self.module._compare_pair_data(pair, self.sources)

    def test_inner_changed_complete_dsl_refuses_after_valid_dsl_and_payload_hashes(self):
        pair = self.pair()
        def changed(bundle):
            bundle['dsl']['unlisted_change'] = True
            bundle['hashes']['dsl_sha256'] = self.module.value_sha(bundle['dsl'])
        self.compile(pair, changed)
        with self.assertRaisesRegex(ValueError, 'outside the pinned digest leaves'):
            self.module._compare_pair_data(pair, self.sources)

    def test_inner_transplanted_other_role_bundle_is_not_its_own_returned_bundle(self):
        pair = self.pair(); pair['before']['assemble_request'] = pair['candidate']['assemble_request']
        with self.assertRaisesRegex(ValueError, 'complete own returned bundle'):
            self.module._compare_pair_data(pair, self.sources)

    def test_inner_own_assembly_request_preserves_original_compact_spelling(self):
        pair = self.pair(); pair['candidate']['assemble_request'] = b' ' + pair['candidate']['assemble_request']
        with self.assertRaisesRegex(ValueError, 'original compact spelling'):
            self.module._compare_pair_data(pair, self.sources)

    def test_inner_complete_assembly_response_change_refuses(self):
        pair = self.pair(); value = json.loads(pair['candidate']['assemble_response'])
        name = next(iter(value['files'])); value['files'][name] += '\n# changed product\n'
        self.response(pair, 'candidate', 'assemble', self.module.compact(value))
        with self.assertRaisesRegex(ValueError, 'complete assembly response bytes differ'):
            self.module._compare_pair_data(pair, self.sources)

    def test_inner_equal_but_incomplete_assembly_file_map_refuses(self):
        pair = self.pair()
        for side in ('before', 'candidate'):
            self.response(pair, side, 'assemble', b'{"files":{},"status":"ok"}')
        with self.assertRaisesRegex(ValueError, 'complete successful assembly file map'):
            self.module._compare_pair_data(pair, self.sources)

    def test_inner_same_endpoint_and_missing_phase_are_not_accepted(self):
        pair = self.pair(); port = json.loads(pair['before']['compile_transport'])['port']
        for phase in ('compile', 'assemble'):
            self.transport(pair, 'candidate', phase, lambda t: t.__setitem__('port', port))
        with self.assertRaisesRegex(ValueError, 'endpoints are the same'):
            self.module._compare_pair_data(pair, self.sources)
        pair = self.pair(); del pair['candidate']['assemble_transport']
        with self.assertRaisesRegex(ValueError, 'complete raw data side'):
            self.module._compare_pair_data(pair, self.sources)

    def test_canonical_source_join_keeps_historical_origin_and_rejects_changed_actual_source_records(self):
        vectors = json.loads((REPO / self.module.VECTORS).read_bytes())
        self.assertEqual(vectors['source_sha256'],
                         '8df232e307baa785c696869f89f4f44b3a6eec2a621dfea0e5c054165fa483f6')
        self.assertEqual(len(self.module._qualify_canonical(REPO, self.sources)), 11)
        provenance = self.module.analyze_retained_fixture_pair(REPO, FIXTURE)['canonical_source_provenance']
        self.assertEqual(provenance['historical_vector_origin_flow_sha256'], vectors['source_sha256'])
        self.assertEqual(provenance['complete_canonical_region_sha256'],
                         'ae240afdfe50a0da4bae400f2d83747c0db19a95f5e8bd7417e4484bccbf39de')
        self.assertFalse(provenance['actual_Rust_build_features_or_runtime_caller_verified'])
        self.assertFalse(provenance['historical_Rust_code_executed'])
        for source in self.sources:
            rows = {row['logical']: row for row in source['entries']}
            self.assertEqual(rows['src/flow.rs']['sha256'],
                             'fc8b7ea7d591026aed9380a07641ca206391555e95df15b317b879e0e9a31b75')
            self.assertNotEqual(rows['src/flow.rs']['sha256'], vectors['source_sha256'])
        for side in (0, 1):
            for logical in ('src/flow.rs', 'Cargo.toml', 'Cargo.lock'):
                with self.subTest(side=side, logical=logical):
                    # Alter finite source data only after genuine source setup has passed.
                    changed = dict(self.sources[side])
                    changed['entries'] = [dict(row) for row in self.sources[side]['entries']]
                    for row in changed['entries']:
                        if row['logical'] == logical: row['sha256'] = '0' * 64
                    pair = list(self.sources); pair[side] = self.module._freeze(changed)
                    with self.assertRaisesRegex(ValueError, 'canonical algorithm source provenance differs'):
                        self.module._qualify_canonical(REPO, tuple(pair))


if __name__ == '__main__':
    unittest.main()

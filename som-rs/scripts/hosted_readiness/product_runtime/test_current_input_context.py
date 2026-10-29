"""Current input role fixtures. Execute only on GitHub."""
import copy
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
from types import ModuleType, SimpleNamespace
import unittest

if os.environ.get('GITHUB_ACTIONS') != 'true':
    raise RuntimeError('GitHub fixtures only')

path = Path(__file__).with_name('current_input_context.py')
p = ModuleType('_current_input_context_test_subject')
p.__file__ = str(path)
exec(compile(path.read_bytes(), str(path), 'exec', dont_inherit=True), p.__dict__)


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


class CurrentInputRoles(unittest.TestCase):
    def context(self):
        helper = '1' * 40
        rs_ids = [f'RS{i:02d}_POSITIVE' for i in range(1, 21)] + sorted(p.REFUSALS)
        result = []
        for cohort, ids in [('rs22', rs_ids), ('coverage6', [f'C{i}' for i in range(6)])]:
            for identity in ids:
                prompt = {'version': 'som.prompt.v3', 'id': identity}
                files = {'app.py': identity}
                negative = identity in p.REFUSALS
                forms = [{'form': form, 'compile_status': 400, 'refusal_verified': True,
                          'request_path': f'{identity}/{form}.raw', 'original_target_role': form}
                         for form in ('native', 'typed')]
                result.append({'cohort': cohort, 'id': identity, 'prompt': prompt,
                    'prompt_sha256': p.sha(canonical(prompt).encode()),
                    'state': 'current-refusal' if negative else 'current-product',
                    'files': None if negative else files, 'forms': forms,
                    'files_map_sha256': None if negative else p.sha(canonical(files).encode()),
                    'helper_revision': helper, 'candidate_revision': helper,
                    'current_capture_manifest_sha256': '2' * 64})
        self.result = result
        self.calls = []
        def verify(*args, **kwargs):
            self.calls.append((args, kwargs))
            return copy.deepcopy(self.result)
        export = SimpleNamespace(canonical=canonical)
        capture = SimpleNamespace(verify_capture=verify, sha=p.sha, REPAIRED={},
                                  plain_module=lambda reader, name: export)
        ctx = SimpleNamespace(data={'qa_mode': 'native-qa-v1', 'current_companions_root': '/tmp/current',
                'assembly_root': '/tmp/assembly'}, cache={}, helper_revision=helper, candidate_revision=helper,
                candidate_admission={'mode': 'same-candidate'},
                sources=SimpleNamespace(git=lambda *args: b'a.py\0b.json\0', read=lambda name: b''),
                module=lambda name, source: capture,
                native_prompt=lambda expected: {'version': 'som.prompt.v3', 'id': expected['id']})
        return ctx, capture

    def expected(self, row):
        return {'family': 'rs', 'id': row['id'], 'prompt_sha256': row['prompt_sha256'],
                'product_sha256': row['files_map_sha256'] or '3' * 64,
                'phase2_expected': {'result': 'refusal' if row['id'] in p.REFUSALS else 'product'}}

    def test_complete28_forwards_exact_native_authority_inputs(self):
        ctx, _ = self.context()
        got = p.rows(ctx)
        self.assertEqual(len(got), 28)
        args, kwargs = self.calls[0]
        self.assertEqual(args[2:4], (ctx.helper_revision, ctx.candidate_revision))
        self.assertEqual(args[4], ctx.candidate_admission)
        self.assertEqual(kwargs, {'qa_mode': 'native-qa-v1', 'source_paths': {'a.py', 'b.json'}})

    def test_non_native_mode_refuses_before_capture_load(self):
        ctx, _ = self.context(); ctx.data['qa_mode'] = 'experimental-preparation'
        ctx.module = lambda *args: self.fail('must not load capture')
        with self.assertRaises(ValueError): p.rows(ctx)

    def test_mixed_candidate_refuses_before_capture_load(self):
        ctx, _ = self.context(); ctx.candidate_revision = '2' * 40
        ctx.module = lambda *args: self.fail('must not load capture')
        with self.assertRaises(ValueError): p.rows(ctx)

    def test_missing_named_slot_refused(self):
        ctx, _ = self.context(); self.result.pop()
        with self.assertRaises(ValueError): p.rows(ctx)

    def test_duplicate_slot_cannot_replace_missing_slot(self):
        ctx, _ = self.context(); self.result[-1] = copy.deepcopy(self.result[0])
        with self.assertRaises(ValueError): p.rows(ctx)

    def test_positive_row_cannot_hide_negative_slot(self):
        ctx, _ = self.context(); self.result[-7]['state'] = 'current-product'
        with self.assertRaises(ValueError): p.rows(ctx)

    def test_refresh_rechecks_capture_after_cache(self):
        ctx, _ = self.context(); p.rows(ctx); p.rows(ctx)
        self.assertEqual(len(self.calls), 1)
        self.result.pop()
        with self.assertRaises(ValueError): p.rows(ctx, refresh=True)
        self.assertEqual(len(self.calls), 2)

    def test_current_files_are_copied_without_mutating_saved_input(self):
        ctx, _ = self.context(); row = self.result[0]
        got = p.lookup_files(ctx, row['prompt'], row['id'])
        got['files']['app.py'] = 'changed by checker'
        self.assertEqual(p.lookup_files(ctx, row['prompt'], row['id'])['files'], row['files'])

    def test_unknown_prompt_never_borrows_another_product(self):
        ctx, _ = self.context()
        self.assertIsNone(p.lookup_files(ctx, {'different': True}))

    def test_negative_slot_never_supplies_product_files(self):
        ctx, _ = self.context(); row = next(r for r in self.result if r['id'] in p.REFUSALS)
        with self.assertRaises(ValueError): p.lookup_files(ctx, row['prompt'], row['id'])

    def test_exact_named_positive_map_keeps_history(self):
        ctx, _ = self.context(); row = self.result[0]; expected = self.expected(row)
        got = p.phase2_input(ctx, expected)
        self.assertEqual(got['product_sha256'], expected['product_sha256'])
        self.assertEqual(got['historical_product_sha256'], expected['product_sha256'])

    def test_repaired_current_map_keeps_distinct_old_hash(self):
        ctx, capture = self.context(); row = self.result[0]
        capture.REPAIRED[row['id']] = row['files_map_sha256']
        expected = self.expected(row); expected['product_sha256'] = '4' * 64
        got = p.phase2_input(ctx, expected)
        self.assertEqual(got['historical_product_sha256'], '4' * 64)
        self.assertEqual(got['product_sha256'], row['files_map_sha256'])
        self.assertEqual(got['source_role'], 'same-candidate-repaired-current-map')

    def test_unadmitted_positive_map_change_refused(self):
        ctx, _ = self.context(); expected = self.expected(self.result[0]); expected['product_sha256'] = '4' * 64
        with self.assertRaises(ValueError): p.phase2_input(ctx, expected)

    def test_negative_role_keeps_old_hash_and_no_files(self):
        ctx, _ = self.context(); row = next(r for r in self.result if r['id'] in p.REFUSALS)
        got = p.phase2_input(ctx, self.expected(row))
        self.assertIsNone(got['files']); self.assertIsNone(got['product_sha256'])
        self.assertEqual(got['historical_product_sha256'], '3' * 64)

    def test_python_refusal_retains_both_raw_form_roles(self):
        ctx, _ = self.context(); row = next(r for r in self.result if r['id'] in p.REFUSALS)
        got = p.refusal(ctx, row['id'], 'python')
        self.assertEqual(got['python_forms'], row['forms'])
        self.assertFalse(got['product_executed']); self.assertFalse(got['product_acceptance_passed'])

    def test_non_refusal_id_cannot_skip_product_tests(self):
        ctx, _ = self.context(); self.assertIsNone(p.refusal(ctx, self.result[0]['id'], 'python'))

    def test_missing_negative_form_refused(self):
        ctx, _ = self.context(); row = next(r for r in self.result if r['id'] in p.REFUSALS); row['forms'].pop()
        with self.assertRaises(ValueError): p.refusal(ctx, row['id'], 'python')

    def test_422_or_product_directory_cannot_be_a_current_refusal(self):
        ctx, _ = self.context(); row = next(r for r in self.result if r['id'] in p.REFUSALS)
        row['forms'][0]['compile_status'] = 422
        with self.assertRaises(ValueError): p.refusal(ctx, row['id'], 'python')
        ctx.cache.clear(); row['forms'][0]['compile_status'] = 400; row['forms'][0]['product_dir'] = 'invented'
        with self.assertRaises(ValueError): p.refusal(ctx, row['id'], 'python')

    def test_guarded_reader_refuses_linked_artifact(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); (root / 'raw').write_bytes(b'raw'); (root / 'alias').symlink_to('raw')
            with self.assertRaises(ValueError): p.read(root / 'alias')


class RustTypedProcessProof(unittest.TestCase):
    """Use retained process fields and real frozen Rust bytes; start no service."""

    @contextmanager
    def fixture(self, identity='RS12_CNL_V3_FILTER_LIMIT'):
        old = CurrentInputRoles()
        ctx, capture = old.context()
        row = next(item for item in old.result if item['id'] == identity)
        case = RUST_FROZEN_CASES[identity]
        request, response = case['request'].encode(), case['response'].encode()
        row['prompt'] = json.loads(request)['prompt']
        row['prompt_sha256'] = p.sha(canonical(row['prompt']).encode())
        repo = Path(__file__).resolve().parents[4]
        source = {name: (repo / name).read_bytes() for name in (
            p.TYPED_DRIVER, p.CHECKER,
            'som-rs/scripts/hosted_readiness/raw_digest_compat.py',
            'som-rs/scripts/hosted_readiness/inputs/ts065-baseline-hashes-v1.json')}
        self.assertEqual(p.sha(source[p.TYPED_DRIVER]), p.TYPED_DRIVER_SHA256)
        module_name = '_current_input_real_refusal_checker'
        actual = ModuleType(module_name)
        actual.__file__ = str(repo / p.CHECKER)
        previous = sys.modules.get(module_name)
        sys.modules[module_name] = actual
        try:
            exec(compile(source[p.CHECKER], actual.__file__, 'exec', dont_inherit=True), actual.__dict__)
            baseline = json.loads(source[actual.BASELINE])
            self.assertEqual(p.sha(source[actual.BASELINE]), actual.BASELINE_SHA256)
            accepted = next(item for item in baseline['baselines']['rust']['rows']
                            if item['check'] == 'compile_equal' and item['phase'] == 'phase2'
                            and item['id'] == identity)
            self.assertEqual(p.sha(request), accepted['qa_request_sha256'])
            self.assertEqual(p.sha(response), accepted['qa_response_sha256'])
            # This fixture uses the real frozen-response method. Its constructor
            # factory work is outside this narrow retained-process unit scope.
            checker = actual.ExperimentalChecker.__new__(actual.ExperimentalChecker)
            checker.rows = {('rust', 'phase2', identity): (400, accepted['qa_response_sha256'],
                                                        accepted['baseline_response_sha256'])}
            pins = {name: p.sha(raw) for name, raw in source.items()}
            checker.repo = SimpleNamespace(commit=ctx.candidate_revision, pins=copy.deepcopy(pins))
            checker.after, checker.receipts = {}, {}
            checker_api = SimpleNamespace(ExperimentalChecker=lambda root: checker,
                value_sha=actual.value_sha, BASELINE=actual.BASELINE, BASELINE_SHA256=actual.BASELINE_SHA256)
            ctx.repo = repo
            ctx.sources.read = lambda name: source[name]
            ctx.module = lambda name, path: checker_api if path == p.CHECKER else capture
            # Shapes and owner values are from the retained c8a9 manifest. This
            # small source vector and candidate are synthetic fixture authority.
            file = {'sha256': 'a' * 64, 'size': 1, 'mode': 493, 'device': 2049, 'inode': 9187444}
            initial = {'source': pins, 'binary': copy.deepcopy(file),
                'assets': {'manifest.json': copy.deepcopy(file)},
                'tools': {name: copy.deepcopy(file) for name in ('SOM_RUFF', 'SOM_RUSTFMT', 'SOM_TS_NODE')}}
            initial['tools']['SOM_TS_PRETTIER'] = {'index.cjs': copy.deepcopy(file)}
            owner = {'pid': 5633, 'state': 'S', 'pgrp': 5633, 'session': 5633, 'start_ticks': 16608,
                'listener': {'table': 'tcp', 'address': '0100007F', 'inode': '18049'},
                'executable_inode': 9187444}
            record = {'index': 2030, 'target': 'rust', 'identity': {'phase': 'phase2', 'id': identity},
                'state': 'passed', 'http_status': 400, 'expected_request_sha256': p.sha(request),
                'current_response_sha256': p.sha(response), 'owned_socket': copy.deepcopy(owner)}
            proof = {'tested_sha': ctx.candidate_revision, 'experiment_complete': True, 'passed': 2041,
                'initial_identities': initial, 'final_identities': copy.deepcopy(initial),
                'identity_stability_passed': True,
                'independent_source_record': {'commit': ctx.candidate_revision, 'source_sha256': copy.deepcopy(pins)},
                'owned_start': owner, 'rows': [record],
                'cleanup': {'owned_pid': 5633, 'cleanup_passed': True, 'exit_code': -15,
                    'signal_target': 'owned new process group only', 'signals': ['SIGTERM'],
                    'remaining_owned_session_members': []}}
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                ctx.data['assembly_root'] = str(root)
                raw_dir = root / 'rows' / '2030'
                raw_dir.mkdir(parents=True)
                (raw_dir / 'request.raw').write_bytes(request)
                (raw_dir / 'response.raw').write_bytes(response)
                def check():
                    (root / 'manifest.json').write_text(json.dumps(proof))
                    # Every process mutation keeps both full frozen raw files.
                    self.assertEqual((raw_dir / 'request.raw').read_bytes(), request)
                    self.assertEqual((raw_dir / 'response.raw').read_bytes(), response)
                    return p.refusal(ctx, identity, 'rust')
                yield SimpleNamespace(ctx=ctx, proof=proof, record=record, source=source,
                                      root=root, raw_dir=raw_dir, check=check)
        finally:
            if previous is None:
                sys.modules.pop(module_name, None)
            else:
                sys.modules[module_name] = previous

    def test_both_actual_schema_refusals_pass_without_new_wait_field(self):
        for identity in sorted(p.REFUSALS):
            with self.subTest(identity=identity), self.fixture(identity) as f:
                got = f.check()
                process = got['typed_process_proof']
                self.assertEqual(process['verification_role'], 'source-based-retained-typed-process-proof')
                self.assertEqual(process['driver_source_sha256'], p.TYPED_DRIVER_SHA256)
                self.assertEqual(process['cleanup']['exit_code'], -15)
                self.assertNotIn('wait_result', process)
                self.assertNotIn('wait_result', f.proof['cleanup'])
                self.assertFalse(got['product_executed'])
                self.assertTrue(got['rust_typed']['checker']['byte_compatibility_passed'])

    def test_terminal_signal_sequences_and_transient_live_state_are_retained(self):
        for signals in ([], ['SIGTERM'], ['SIGTERM', 'SIGKILL']):
            with self.subTest(signals=signals), self.fixture() as f:
                f.proof['cleanup']['signals'] = signals
                f.record['owned_socket']['state'] = 'R'
                self.assertEqual(f.check()['typed_process_proof']['row_owned_socket']['state'], 'R')

    def test_missing_or_foreign_start_pid_refused_with_raws_unchanged(self):
        for value in (None, 0, True, 7000):
            with self.subTest(value=value), self.fixture() as f:
                if value is None:
                    f.proof['owned_start'].pop('pid')
                else:
                    f.proof['owned_start']['pid'] = value
                with self.assertRaises(ValueError): f.check()

    def test_missing_or_foreign_process_group_and_session_refused(self):
        for field in ('pgrp', 'session'):
            for value in (None, 7000):
                with self.subTest(field=field, value=value), self.fixture() as f:
                    if value is None:
                        f.proof['owned_start'].pop(field)
                    else:
                        f.proof['owned_start'][field] = value
                    with self.assertRaises(ValueError): f.check()

    def test_stale_row_start_stamp_and_changed_socket_identity_refused(self):
        for field, value in (('pid', 7000), ('start_ticks', 16609), ('executable_inode', 9187445),
                             ('listener', {'table': 'tcp', 'address': '0100007F', 'inode': '18050'}),
                             ('state', 'Z')):
            with self.subTest(field=field), self.fixture() as f:
                f.record['owned_socket'][field] = value
                with self.assertRaises(ValueError): f.check()

    def test_non_loopback_non_tcp_missing_socket_or_wrong_executable_refused(self):
        for field, value in (('table', 'tcp6'), ('address', '00000000'), ('inode', '0')):
            with self.subTest(field=field), self.fixture() as f:
                f.proof['owned_start']['listener'][field] = value
                with self.assertRaises(ValueError): f.check()
        with self.fixture() as f:
            f.proof['owned_start']['executable_inode'] += 1
            with self.assertRaises(ValueError): f.check()
        with self.fixture() as f:
            f.proof['owned_start'].pop('listener')
            with self.assertRaises(ValueError): f.check()

    def test_foreign_cleanup_owner_and_nonterminal_exit_refused(self):
        for field, value in (('owned_pid', 7000), ('exit_code', None), ('exit_code', True)):
            with self.subTest(field=field, value=value), self.fixture() as f:
                f.proof['cleanup'][field] = value
                with self.assertRaises(ValueError): f.check()
        with self.fixture() as f:
            f.proof['cleanup'].pop('exit_code')
            with self.assertRaises(ValueError): f.check()

    def test_remaining_process_or_false_cleanup_refused(self):
        for field, value in (('remaining_owned_session_members', [{'pid': 7000}]), ('cleanup_passed', False)):
            with self.subTest(field=field), self.fixture() as f:
                f.proof['cleanup'][field] = value
                with self.assertRaises(ValueError): f.check()

    def test_wrong_signal_target_or_sequence_refused(self):
        for field, value in (('signal_target', 'all listeners'), ('signals', ['SIGKILL']),
                             ('signals', ['SIGTERM', 'SIGTERM']), ('signals', ['SIGINT'])):
            with self.subTest(field=field, value=value), self.fixture() as f:
                f.proof['cleanup'][field] = value
                with self.assertRaises(ValueError): f.check()

    def test_typed_tested_helper_candidate_and_independent_commits_must_match(self):
        for name in ('tested', 'helper', 'independent'):
            with self.subTest(name=name), self.fixture() as f:
                if name == 'tested': f.proof['tested_sha'] = '2' * 40
                elif name == 'helper': f.ctx.helper_revision = '2' * 40
                else: f.proof['independent_source_record']['commit'] = '2' * 40
                with self.assertRaises(ValueError): f.check()

    def test_source_vector_missing_or_extra_independent_member_refused(self):
        for change in ('missing', 'extra'):
            with self.subTest(change=change), self.fixture() as f:
                vector = f.proof['independent_source_record']['source_sha256']
                if change == 'missing': vector.pop(p.CHECKER)
                else: vector['other.py'] = 'b' * 64
                with self.assertRaises(ValueError): f.check()

    def test_current_raw_git_source_and_fixed_driver_pin_are_rechecked(self):
        for name in (p.TYPED_DRIVER, p.CHECKER,
                     'som-rs/scripts/hosted_readiness/raw_digest_compat.py'):
            with self.subTest(path=name), self.fixture() as f:
                f.source[name] += b'\n# changed current source\n'
                with self.assertRaises(ValueError): f.check()
        with self.fixture() as f:
            for vector in (f.proof['initial_identities']['source'], f.proof['final_identities']['source'],
                           f.proof['independent_source_record']['source_sha256']):
                vector[p.TYPED_DRIVER] = 'b' * 64
            with self.assertRaises(ValueError): f.check()

    def test_full_vectors_cannot_omit_independently_derived_checker_source(self):
        with self.fixture() as f:
            for vector in (f.proof['initial_identities']['source'], f.proof['final_identities']['source'],
                           f.proof['independent_source_record']['source_sha256']):
                vector.pop('som-rs/scripts/hosted_readiness/raw_digest_compat.py')
            with self.assertRaises(ValueError): f.check()

    def test_each_initial_final_identity_vector_is_stable(self):
        for name in ('source', 'binary', 'assets', 'tools'):
            with self.subTest(vector=name), self.fixture() as f:
                if name == 'source': f.proof['final_identities'][name][p.CHECKER] = 'b' * 64
                elif name == 'binary': f.proof['final_identities'][name]['sha256'] = 'b' * 64
                elif name == 'assets': f.proof['final_identities'][name]['manifest.json']['sha256'] = 'b' * 64
                else: f.proof['final_identities'][name]['SOM_RUFF']['sha256'] = 'b' * 64
                with self.assertRaises(ValueError): f.check()

    def test_raw_request_and_response_tamper_still_refused(self):
        for name in ('request.raw', 'response.raw'):
            with self.subTest(name=name), self.fixture() as f:
                (f.raw_dir / name).write_bytes((f.raw_dir / name).read_bytes() + b' ')
                (f.root / 'manifest.json').write_text(json.dumps(f.proof))
                with self.assertRaises(ValueError): p.refusal(f.ctx, f.record['identity']['id'], 'rust')


# RUST_FROZEN_CASES is byte-for-byte fixture data checked against the independent
# TS065 request/reply hashes above. It grants no current candidate acceptance.
RUST_FROZEN_CASES = {'RS12_CNL_V3_FILTER_LIMIT': {'request': '{"format":"som.prompt-json.v1","target":"rust","prompt":{"logic":["Create a row. Return 409 for a unique value conflict.","Delete a row. Return 404 if it does not exist.","List rows with an optional exact boolean filter, limit, and offset in id order.","Update supplied fields. Return 404 if the row does not exist.","Get a row by id. Return 404 if it does not exist."],"migration":{"down_revision":null,"revision":"service1000"},"package":"todos","query":{"filter_field":"limit","max_limit":100},"resource":{"fields":[{"name":"title","required":true,"type":"str","unique":false},{"default":null,"name":"description","required":false,"type":"str","unique":false},{"default":false,"name":"limit","required":false,"type":"bool","unique":false}],"name":"Todo","path":"/todos","table":"todos"},"tests":["Check standalone CRUD, boolean filtering, page bounds, defaults, timestamps, missing id, and migration."],"version":"som.prompt.v3"}}', 'response': '{"code":"INVALID_VALUE","detail":"prompt.query.filter_field: name collides with a generated Python identifier","diagnostics":[{"allowed_form":"A prompt that meets the pinned compiler contract.","byte_end":479,"byte_start":472,"cause":"prompt.query.filter_field: name collides with a generated Python identifier","code":"INVALID_VALUE","column":473,"end_column":480,"end_line":1,"line":1,"prompt_pointer":"/prompt/query/filter_field"}],"status":"error"}'}, 'RS13_JSON_V3_FILTER_OFFSET': {'request': '{"format":"som.prompt-json.v1","target":"rust","prompt":{"logic":["Create a row. Return 409 for a unique value conflict.","Delete a row. Return 404 if it does not exist.","List rows with an optional exact boolean filter, limit, and offset in id order.","Update supplied fields. Return 404 if the row does not exist.","Get a row by id. Return 404 if it does not exist."],"migration":{"down_revision":null,"revision":"service1000"},"package":"todos","query":{"filter_field":"offset","max_limit":100},"resource":{"fields":[{"name":"title","required":true,"type":"str","unique":false},{"default":null,"name":"description","required":false,"type":"str","unique":false},{"default":false,"name":"offset","required":false,"type":"bool","unique":false}],"name":"Todo","path":"/todos","table":"todos"},"tests":["Check standalone CRUD, boolean filtering, page bounds, defaults, timestamps, missing id, and migration."],"version":"som.prompt.v3"}}', 'response': '{"code":"INVALID_VALUE","detail":"prompt.query.filter_field: name collides with a generated Python identifier","diagnostics":[{"allowed_form":"A prompt that meets the pinned compiler contract.","byte_end":480,"byte_start":472,"cause":"prompt.query.filter_field: name collides with a generated Python identifier","code":"INVALID_VALUE","column":473,"end_column":481,"end_line":1,"line":1,"prompt_pointer":"/prompt/query/filter_field"}],"status":"error"}'}}


if __name__ == '__main__':
    unittest.main()

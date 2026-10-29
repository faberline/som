"""GitHub-only source fixtures for complete product cohort admission."""
import json
import os
from pathlib import Path
import sys
import tempfile
from types import ModuleType, SimpleNamespace
import unittest
from unittest import mock

HERE = Path(__file__).absolute().parent
if os.environ.get('GITHUB_ACTIONS') != 'true':
    raise RuntimeError('Run these fixtures on GitHub only')


def subject(name, path):
    module = ModuleType(name)
    module.__file__, module.__cached__, module.__package__ = str(path), None, ''
    sys.modules[name] = module
    exec(compile(path.read_bytes(), str(path), 'exec', dont_inherit=True), module.__dict__)
    return module


driver = subject('full_product_driver_under_test', HERE / 'run_full_products.py')
context_module = subject('full_product_context_under_test', HERE / 'product_runtime/full_product_context.py')
cargo = subject('full_product_cargo_under_test', HERE / 'product_runtime/consume_cargo.py')
carrier = subject('full_product_carrier_under_test', HERE / 'product_runtime/candidate_carrier.py')


class CohortScopeTests(unittest.TestCase):
    def test_stride_join_preserves_all_584_named_slots(self):
        ids = ['case-' + str(index) for index in range(584)]
        chunks = [driver.scoped(ids, {'index': index, 'count': 16}) for index in range(16)]
        joined = [identity for index in range(584) for identity in chunks[index % 16][index // 16:index // 16 + 1]]
        self.assertEqual(ids, joined)
        self.assertEqual(sum(map(len, chunks)), 584)

    def test_no_overlap_between_671_product_shards(self):
        ids = ['python/' + str(index) for index in range(671)]
        chunks = [set(driver.scoped(ids, {'index': index, 'count': 16})) for index in range(16)]
        self.assertEqual(set.union(*chunks), set(ids))
        for index, chunk in enumerate(chunks):
            for other in chunks[index + 1:]:
                self.assertFalse(chunk & other)

    def test_repeated_ids_are_refused(self):
        with self.assertRaisesRegex(ValueError, 'duplicate'):
            driver.scoped(['A', 'A'], {'index': 0, 'count': 1})

    def test_boolean_shard_index_is_refused(self):
        with self.assertRaises(ValueError):
            driver.scoped(['A'], {'index': False, 'count': 1})

    def test_out_of_range_shard_is_refused(self):
        with self.assertRaises(ValueError):
            driver.scoped(['A'], {'index': 16, 'count': 16})

    def test_zero_shard_count_is_refused(self):
        with self.assertRaises(ValueError):
            driver.scoped(['A'], {'index': 0, 'count': 0})

    def test_each_named_duplicate_prompt_slot_is_kept(self):
        ids = ['PUBLIC_v6', 'RS01_CNL_V6', 'RS03_JSON_V6']
        self.assertEqual(driver.scoped(ids, {'index': 0, 'count': 1}), ids)

    def test_syntax_is_not_used_as_file_parity(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            original = {'app.py': 'value = 1\n'}
            (root / 'app.py').write_text('value=1\n')
            self.assertFalse(driver.unchanged(root, original))

    def test_acceptance_mode_refuses_before_loading_runtime(self):
        args = type('Args', (), {'mode': 'acceptance'})()
        with mock.patch.dict(os.environ, {'GITHUB_ACTIONS': 'true'}), mock.patch.object(driver.sys, 'platform', 'linux'):
            with self.assertRaisesRegex(ValueError, 'unsealed'):
                driver.prepare(args)

    def test_pending_gates_still_name_fresh_compiler_and_independent_scope(self):
        self.assertIn('fresh compile and assemble determinism and refusals', driver.PENDING)
        self.assertIn('ordinary-mode installer file scope independent audit', driver.PENDING)


class JoinRefusalTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.environment = mock.patch.dict(os.environ, {'GITHUB_ACTIONS': 'true', 'RUNNER_TEMP': str(self.root)})
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.addCleanup(self.temporary.cleanup)

    def row(self, name, **changes):
        value = {'version': 'som.full-product-preparation-shard.v1', 'stage': 'phase2-rust',
                 'shard': {'index': 0, 'count': 1}, 'identities': {'helper_revision': '1' * 40, 'candidate_revision': '2' * 40},
                 'product_acceptance_passed': False, 'whole_acceptance_complete': False,
                 'shard_complete': True, 'source_runtime_tools_unchanged': True}
        value.update(changes)
        path = self.root / name
        path.write_text(json.dumps(value))
        return path

    def test_partial_receipt_is_not_joined(self):
        path = self.row('partial.json', shard_complete=False)
        with self.assertRaisesRegex(ValueError, 'partial shard'):
            driver.join(type('Args', (), {'receipts': [path], 'out': self.root / 'join.json'})())

    def test_missing_tool_postcheck_is_refused(self):
        path = self.row('tools.json', source_runtime_tools_unchanged=False)
        with self.assertRaisesRegex(ValueError, 'postcheck'):
            driver.join(type('Args', (), {'receipts': [path], 'out': self.root / 'join.json'})())

    def test_false_preparation_acceptance_is_refused(self):
        path = self.row('accepted.json', product_acceptance_passed=True)
        with self.assertRaisesRegex(ValueError, 'acceptance claim'):
            driver.join(type('Args', (), {'receipts': [path], 'out': self.root / 'join.json'})())

    def test_mixed_candidate_binaries_are_refused(self):
        first = self.row('one.json')
        second = self.row('two.json', identities={'helper_revision': '1' * 40, 'candidate_revision': '3' * 40})
        with self.assertRaisesRegex(ValueError, 'mixed candidate'):
            driver.join(type('Args', (), {'receipts': [first, second], 'out': self.root / 'join.json'})())


class DependencyIdentityTests(unittest.TestCase):
    def setUp(self):
        self.helper, self.candidate = '1' * 40, '2' * 40
        self.admission = {'helper_revision': self.helper, 'candidate_revision': self.candidate,
                          'product_acceptance_passed': False, 'recipe': {'sha256': 'a' * 64},
                          'artifact': {'compile_manifest_sha256': 'b' * 64}}
        self.document = {'source_revision': self.helper, 'helper_revision': self.helper,
                         'candidate_revision': self.candidate, 'candidate_admission': self.admission}

    def test_dependency_proof_keeps_distinct_helper_and_candidate_roles(self):
        context_module.dependency_identity(self.document, self.helper, self.candidate, self.admission)

    def test_dependency_roles_are_explicit_and_cannot_default(self):
        for field in ('source_revision', 'helper_revision', 'candidate_revision'):
            for value in (None, '3' * 40):
                with self.subTest(field=field, value=value):
                    changed = {**self.document, field: value}
                    with self.assertRaisesRegex(context_module.Refusal, 'explicit dependency'):
                        context_module.dependency_identity(changed, self.helper, self.candidate, self.admission)

    def test_dependency_admission_binds_actual_recipe_and_artifact_proof(self):
        for admission in (None, {**self.admission, 'candidate_revision': '3' * 40},
                          {**self.admission, 'artifact': {'compile_manifest_sha256': 'c' * 64}}):
            with self.subTest(admission=admission):
                with self.assertRaisesRegex(context_module.Refusal, 'admission proof'):
                    context_module.dependency_identity({**self.document, 'candidate_admission': admission},
                                                       self.helper, self.candidate, self.admission)

    def test_cargo_tested_sha_names_candidate_and_still_reaches_full_content_checks(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw).resolve()
            producer = SimpleNamespace(BASE='contracts/', LOCKS={'phase': ('reference',)},
                                       lock_union=mock.Mock(side_effect=RuntimeError('full frozen lock check reached')))
            ctx = SimpleNamespace(helper_revision=self.helper, candidate_revision=self.candidate,
                                  candidate_admission=self.admission, data={'cargo_root': str(root)},
                                  module=mock.Mock(return_value=producer), sources=SimpleNamespace(read=lambda _: b'frozen'))
            report = {**self.document, 'tested_sha': self.candidate, 'version': 'som.product-cargo-closure.v1',
                      'status': 'registry_closure_verified_product_gates_unrun', 'vendor_package_count': 352}
            with mock.patch.dict(sys.modules, {'full_product_context': context_module}):
                (root / 'receipt.json').write_text(json.dumps(report))
                with self.assertRaisesRegex(RuntimeError, 'full frozen lock check reached'):
                    cargo.prepare(ctx)
                producer.lock_union.assert_called_once()
                for field, value in (('tested_sha', self.helper), ('source_revision', self.candidate),
                                     ('helper_revision', None), ('candidate_revision', None)):
                    with self.subTest(field=field):
                        producer.lock_union.reset_mock()
                        (root / 'receipt.json').write_text(json.dumps({**report, field: value}))
                        with self.assertRaises(context_module.Refusal):
                            cargo.prepare(ctx)
                        producer.lock_union.assert_not_called()

    def test_wheel_receipt_checks_candidate_roles_before_environment_use(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw).resolve()
            ctx = object.__new__(context_module.Context)
            ctx.helper_revision, ctx.candidate_revision, ctx.candidate_admission = self.helper, self.candidate, self.admission
            ctx.data = {'wheel_root': str(root)}
            report = {**self.document, 'version': 'som.hosted-python-wheel-closure-qualification.v1',
                      'wheel_closure_qualification_complete': True, 'all_named_environment_checks_complete': True}
            for field, value in (('helper_revision', None), ('candidate_revision', '3' * 40),
                                 ('candidate_admission', {**self.admission, 'recipe': {'sha256': 'd' * 64}})):
                with self.subTest(field=field):
                    (root / 'receipt.json').write_text(json.dumps({**report, field: value}))
                    with self.assertRaises(context_module.Refusal):
                        ctx.closure('observer')


class CarrierProvenanceTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        environment = mock.patch.dict(os.environ, {'GITHUB_ACTIONS': 'true'})
        environment.start()
        self.addCleanup(environment.stop)
        paths = {'binary': b'fake compiler fixture; never executed', 'assets/manifest.json': b'fake asset',
                 'native/bin/ruff': b'fake ruff', 'native/bin/node': b'fake node',
                 'native/prettier/package.json': b'fake prettier', 'compiler-rustfmt': b'fake rustfmt'}
        for name, body in paths.items():
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(body)
            path.chmod(0o755 if name in {'binary', 'native/bin/ruff', 'native/bin/node', 'compiler-rustfmt'} else 0o644)
        expected = carrier.identities(self.root)
        self.proof = {'tested_sha': '2' * 40, 'experiment_complete': True, 'identity_stability_passed': True,
                      'passed': 2041, 'initial_identities': expected, 'final_identities': expected,
                      'cleanup': {'cleanup_passed': True}}
        self.manifest = self.root / 'compile-proof.json'
        self.manifest.write_bytes(carrier.canonical(self.proof))
        self.args = SimpleNamespace(binary=self.root / 'binary', assets=self.root / 'assets',
                                    native=self.root / 'native', rustfmt=self.root / 'compiler-rustfmt',
                                    compile_manifest=self.manifest, candidate_revision='2' * 40,
                                    out=self.root / 'carrier-out')

    def test_original_carrier_defaults_to_no_rebuild_and_preserves_proof(self):
        record = carrier.pack(self.args)
        self.assertIs(record['compiler_rebuilt'], False)
        self.assertEqual(record['origin_role'], 'original already tested candidate bytes')
        self.assertEqual((self.args.out / 'compile-manifest.json').read_bytes(), self.manifest.read_bytes())

    def test_byte_identical_rebuild_records_new_origin_and_restores_same_identities(self):
        self.args.byte_identical_rebuild = True
        record = carrier.pack(self.args)
        self.assertIs(record['compiler_rebuilt'], True)
        self.assertEqual(record['origin_role'], 'new build compared byte-for-byte with the admitted old candidate')
        restored = carrier.restore(self.args.out, self.root / 'restored', self.manifest, self.args.candidate_revision)
        self.assertEqual(restored['identity'], self.proof['initial_identities'])
        self.assertEqual((self.args.out / 'compile-manifest.json').read_bytes(), self.manifest.read_bytes())

    def test_rebuild_flag_cannot_waive_binary_asset_or_tool_bytes_and_mode(self):
        self.args.byte_identical_rebuild = True
        for name in ('binary', 'assets/manifest.json', 'native/bin/ruff', 'compiler-rustfmt'):
            path = self.root / name
            original, mode = path.read_bytes(), path.stat().st_mode & 0o777
            for change in ('bytes', 'mode'):
                with self.subTest(name=name, change=change):
                    path.write_bytes(original + b'changed') if change == 'bytes' else path.chmod(mode ^ 0o100)
                    with self.assertRaisesRegex(ValueError, 'bytes changed before packing'):
                        carrier.pack(self.args)
                    self.assertFalse(self.args.out.exists())
                    path.write_bytes(original)
                    path.chmod(mode)

    def test_long_license_paths_roundtrip_without_pax_headers(self):
        path = self.root / 'native/licenses/node' / ('component-' + 'x' * 80) / 'LICENSE'
        path.parent.mkdir(parents=True)
        path.write_bytes(b'Synthetic public license; never executed.\n')
        self.assertGreater(len(path.relative_to(self.root).as_posix()), 100)
        record = carrier.pack(self.args)
        with carrier.tarfile.open(self.args.out / 'candidate.tar.gz', 'r:gz') as archive:
            self.assertTrue(all(not member.pax_headers for member in archive))
        restored = carrier.restore(self.args.out, self.root / 'restored', self.manifest, self.args.candidate_revision)
        self.assertEqual(restored['identity'], self.proof['initial_identities'])
        self.assertEqual((self.root / 'restored' / path.relative_to(self.root)).read_bytes(), path.read_bytes())
        self.assertEqual(record['files'][path.relative_to(self.root).as_posix()]['sha256'], carrier.digest(path.read_bytes()))

    def test_too_long_paths_fail_instead_of_selecting_extended_headers(self):
        path = self.root / 'native/licenses' / ('a' * 100) / ('b' * 100) / ('c' * 100) / 'LICENSE'
        path.parent.mkdir(parents=True)
        path.write_bytes(b'Synthetic public license.\n')
        with self.assertRaises(ValueError):
            carrier.pack(self.args)
        self.assertFalse((self.args.out / 'carrier.json').exists())


class Phase2ResultTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.output = self.root / 'phase2-python'
        (self.output / 'cases').mkdir(parents=True)
        (self.root / 'checker-postchecks').mkdir()
        self.ids, self.selected = ['A', 'B', 'C'], ['A', 'C']
        self.sources = {name: ('exact helper source ' + name).encode() for name in
                        (driver.SELF, driver.CONTEXT, 'som-rs/behavior/phase2/check_phase2.py')}
        self.ctx = SimpleNamespace(repo=self.root, work=self.root, helper_revision='1' * 40,
                                   candidate_revision='2' * 40, sources=SimpleNamespace(read=self.sources.__getitem__),
                                   data={'stage': 'phase2-python', 'shard': {'index': 0, 'count': 2}})
        self.command = {'exit_code': 0, 'wait_completed': True, 'owned_group_cleanup_passed': True, 'pid': 12345}
        self.summary = {'version': 'som.full-product-phase2-shard.v1', **self.ctx.data,
                        'helper_revision': self.ctx.helper_revision, 'candidate_revision': self.ctx.candidate_revision,
                        'selected_ids': self.selected, 'whole_inventory_total': len(self.ids),
                        'whole_inventory_sha256': driver.digest(driver.canonical(self.ids)),
                        'whole_inventory_complete': False, 'shard_complete': True, 'missing': [],
                        'worker_exit_codes': [0, 0], 'product_acceptance_passed': False, 'rows': []}
        for identity in self.selected:
            raw = driver.canonical({'id': identity, 'passed': True, 'full_transcript': ['original case evidence']}) + b'\n'
            (self.output / 'cases' / (identity + '.json')).write_bytes(raw)
            self.summary['rows'].append({'id': identity, 'passed': True, 'raw_sha256': driver.digest(raw)})
        self.summary_path = self.output / 'hosted-shard-summary.json'
        self.summary_path.write_bytes(driver.canonical(self.summary))
        self.proof = {'version': 'som.full-product-checker-postcheck.v1', 'tool': 'som-rs/behavior/phase2/check_phase2.py',
                      'pid': self.command['pid'], 'helper_revision': self.ctx.helper_revision,
                      'candidate_revision': self.ctx.candidate_revision, **self.ctx.data,
                      'source_sha256': {name: driver.digest(raw) for name, raw in self.sources.items()},
                      'source_runtime_tools_inputs_unchanged': True, 'product_acceptance_passed': False}
        self.proof_path = self.root / 'checker-postchecks/12345.json'
        self.proof_path.write_bytes(driver.canonical(self.proof))
        alias = mock.patch.dict(sys.modules, {'full_product_context': context_module})
        alias.start()
        self.addCleanup(alias.stop)

    def results(self, command=None, error=None):
        return driver.phase2_python_results(self.ctx, self.ids, self.selected, self.output, lambda value: value,
                                            2, self.command if command is None else command, error)

    def assert_failed_scope(self, rows):
        self.assertEqual([row['id'] for row in rows], self.selected)
        self.assertFalse(any(row['passed'] for row in rows))
        self.assertTrue(all(row['case_result']['full_transcript'] == ['original case evidence'] for row in rows))

    def test_success_binds_every_raw_row_summary_and_process_postcheck(self):
        rows = self.results()
        self.assertEqual([row['id'] for row in rows], self.selected)
        self.assertTrue(all(row['passed'] for row in rows))
        self.assertTrue(all(row['gates']['hosted_shard_summary']['raw_sha256'] for row in rows))

    def test_all_true_rows_cannot_hide_nonzero_driver_or_missing_wait_cleanup(self):
        for field, value in (('exit_code', 1), ('exit_code', -9), ('exit_code', None),
                             ('wait_completed', False), ('owned_group_cleanup_passed', False)):
            with self.subTest(field=field, value=value):
                self.assert_failed_scope(self.results({**self.command, field: value}))

    def test_tool_cleanup_exception_keeps_full_rows_but_fails_them(self):
        self.assert_failed_scope(self.results(error={'error': 'owned group cleanup failed'}))

    def test_phase2_entry_point_consumes_failed_tool_return(self):
        checker = SimpleNamespace(cases=lambda: [{'id': identity, 'checker': 'fixture'} for identity in self.ids],
                                  key=lambda value: value)
        with mock.patch.dict(sys.modules, {'check_phase2': checker}), \
             mock.patch.object(driver, 'tool', return_value={**self.command, 'exit_code': 1}):
            ids, rows = driver.phase2_python(self.ctx)
        self.assertEqual(ids, self.ids)
        self.assert_failed_scope(rows)

    def test_phase2_entry_point_preserves_rows_after_tool_exception(self):
        checker = SimpleNamespace(cases=lambda: [{'id': identity, 'checker': 'fixture'} for identity in self.ids],
                                  key=lambda value: value)
        with mock.patch.dict(sys.modules, {'check_phase2': checker}), \
             mock.patch.object(driver, 'tool', side_effect=ValueError('postcheck failed')):
            ids, rows = driver.phase2_python(self.ctx)
        self.assertEqual(ids, self.ids)
        self.assert_failed_scope(rows)

    def test_summary_binds_order_scope_shard_candidate_and_complete_raw_rows(self):
        for field, value in (('selected_ids', list(reversed(self.selected))), ('shard', {'index': 1, 'count': 2}),
                             ('helper_revision', '3' * 40), ('candidate_revision', '3' * 40),
                             ('whole_inventory_sha256', '0' * 64), ('rows', self.summary['rows'][:1]),
                             ('missing', ['C']), ('shard_complete', False)):
            with self.subTest(field=field):
                self.summary_path.write_bytes(driver.canonical({**self.summary, field: value}))
                self.assert_failed_scope(self.results())

    def test_nonzero_or_missing_worker_exit_cannot_pass_with_true_rows(self):
        for codes in ([0, 1], [0], [0, None], [0, False]):
            with self.subTest(codes=codes):
                self.summary_path.write_bytes(driver.canonical({**self.summary, 'worker_exit_codes': codes}))
                self.assert_failed_scope(self.results())

    def test_rehashed_named_case_cannot_replace_summary_raw_hash(self):
        path = self.output / 'cases/A.json'
        body = json.loads(path.read_bytes())
        body['full_transcript'].append('changed evidence')
        path.write_bytes(driver.canonical(body))
        rows = self.results()
        self.assertEqual([row['id'] for row in rows], self.selected)
        self.assertFalse(any(row['passed'] for row in rows))

    def test_extra_saved_case_cannot_expand_the_admitted_shard(self):
        (self.output / 'cases/B.json').write_bytes(driver.canonical({'id': 'B', 'passed': True}))
        self.assert_failed_scope(self.results())

    def test_missing_or_changed_driver_postcheck_fails_complete_rows(self):
        self.proof_path.rename(self.proof_path.with_suffix('.missing'))
        self.assert_failed_scope(self.results())
        for field, value in (('pid', 111), ('candidate_revision', '3' * 40),
                             ('source_runtime_tools_inputs_unchanged', False), ('source_sha256', {})):
            with self.subTest(field=field):
                self.proof_path.write_bytes(driver.canonical({**self.proof, field: value}))
                self.assert_failed_scope(self.results())


if __name__ == '__main__':
    if os.environ.get('GITHUB_ACTIONS') != 'true':
        raise SystemExit('Run these fixtures on GitHub only')
    unittest.main()

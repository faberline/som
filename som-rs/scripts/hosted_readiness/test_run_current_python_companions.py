"""Meaningful current-input fixtures. Run only on a GitHub runner."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace, ModuleType
import unittest
from unittest import mock

HERE = Path(__file__).absolute().parent
REPO = HERE.parents[2]
# Execute checked raw source bytes. No import loader can select a stale PYC.
if os.environ.get('GITHUB_ACTIONS') != 'true' or sys.platform != 'linux':
    raise SystemExit('Run these fixtures on GitHub Linux only')
helper = os.environ['HELPER_REVISION']
source_path = HERE / 'run_current_python_companions.py'
raw = source_path.read_bytes()
assert raw == subprocess.check_output(['/usr/bin/git', '--no-replace-objects', '-C', str(REPO),
    'cat-file', 'blob', helper + ':som-rs/scripts/hosted_readiness/run_current_python_companions.py'])
c = ModuleType('current_companions_under_test')
c.__file__, c.__cached__, c.__package__ = str(source_path), None, ''
exec(compile(raw, str(source_path), 'exec', dont_inherit=True), c.__dict__)



def reader(name):
    return (REPO / name).read_bytes()


def plain(name):
    return c.plain_module(reader, name)


def native_fixture(case, **options):
    relative = c.BASE + 'test_run_candidate_semantic_gates.py'
    raw = reader(relative)
    assert raw == subprocess.check_output(['/usr/bin/git', '--no-replace-objects', '-C', str(REPO),
        'cat-file', 'blob', helper + ':' + relative])
    module = ModuleType('_companion_source_owned_native_fixture')
    module.__file__ = str(REPO / relative)
    exec(compile(raw, module.__file__, 'exec', dont_inherit=True), module.__dict__)
    return module.core_native_fixture(case, kind='companions', **options)


class TempCase(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()


class RawSourceTests(TempCase):
    def test_duplicate_keys_and_nonfinite_json_are_refused(self):
        for raw in (b'{"a":1,"a":2}', b'{"a":NaN}', b'{"a":Infinity}'):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                c.parse(raw)

    def test_current_case_paths_cannot_escape_or_alias(self):
        for name in ('../x', '/tmp/x', 'a//b', 'a/./b', 'a\\b', 'a\n'):
            with self.subTest(name=name), self.assertRaises(ValueError):
                c.relative(name)

    def test_raw_reader_refuses_link_hardlink_and_bound_overflow(self):
        source = self.root / 'source'
        source.write_bytes(b'valid')
        linked = self.root / 'linked'
        linked.symlink_to(source)
        with self.assertRaises(ValueError): c.read(linked)
        os.link(source, self.root / 'hardlink')
        with self.assertRaises(ValueError): c.read(source)
        other = self.root / 'large'
        other.write_bytes(b'12345')
        with self.assertRaises(ValueError): c.read(other, 4)

    def test_helper_closure_refuses_preloaded_module_before_execution(self):
        sources = SimpleNamespace(read=lambda _: b'raise AssertionError("must not execute")')
        bound = c.BoundModules(sources)
        with mock.patch.dict(sys.modules, {'export_goldens': SimpleNamespace()}):
            with self.assertRaisesRegex(ValueError, 'preloaded'):
                with bound.installed(): pass

    def test_source_derived_descriptor_ids_are_read_without_execution(self):
        ids = c.descriptor_ids(reader)
        self.assertEqual(ids['python_backend'], 'som.compiler-backend.v1')
        self.assertEqual(ids['python_formatted_formatter'], 'som.compiler-formatter.v1')

    def test_output_universe_preserves_extra_file_for_later_refusal(self):
        (self.root / 'request.raw').write_bytes(b'exact')
        before = c.output_inventory(self.root)
        (self.root / 'unexpected').write_bytes(b'extra')
        self.assertNotEqual(c.output_inventory(self.root), before)


class PacketAndOracleTests(TempCase):
    def setUp(self):
        super().setUp()
        self.rs = c.parse(reader(c.RS_PACKET))
        self.coverage = c.parse(reader(c.COVERAGE_PACKET))
        self.export = plain('export_goldens')
        self.modules = {'export_goldens': self.export,
                        'run_experimental_native_compile': SimpleNamespace(compact=c.compact),
                        'raw_digest_compat': plain('raw_digest_compat')}
        self.sources = SimpleNamespace(read=reader)
        rows = []
        for index, row in enumerate(self.rs['rows']):
            body = c.parse(row['fresh_python_typed_request']['body'])
            body['target'] = 'rust'
            raw = c.compact(body)
            self.assertEqual(c.sha(raw), row['typed_rust_input']['raw_sha256'])
            directory = self.root / 'rows' / f'{index:04d}'
            directory.mkdir(parents=True)
            (directory / 'request.raw').write_bytes(raw)
            rows.append({'target': 'rust', 'identity': {'phase': 'phase2', 'id': row['id']},
                         'index': index, 'expected_request_sha256': c.sha(raw)})
        (self.root / 'manifest.json').write_bytes(c.compact({'tested_sha': c.CANDIDATE,
            'experiment_complete': True, 'passed': 2041, 'rows': rows}))

    def jobs(self):
        return c.prepare_jobs(self.sources, self.root, self.modules)

    def test_complete68_forms_preserve_all28_names_and64_assemblies(self):
        jobs = self.jobs()
        self.assertEqual(len(jobs), 68)
        self.assertEqual(len({(r['cohort'], r['id']) for r in jobs}), 28)
        self.assertEqual(sum(r['expected_status'] == 200 for r in jobs), 64)
        self.assertEqual(sum(r['expected_status'] == 400 for r in jobs), 4)
        self.assertEqual([r['form'] for r in jobs[-4:]], ['json-none', 'json-python', 'cnl-none', 'cnl-python'])

    def test_each_original_typed_rust_request_is_hash_bound(self):
        path = self.root / 'rows/0005/request.raw'
        path.write_bytes(path.read_bytes() + b' ')
        with self.assertRaisesRegex(ValueError, 'typed Rust request differs'):
            self.jobs()

    def test_current_candidate_proof_cannot_be_relabelled_before(self):
        path = self.root / 'manifest.json'
        value = c.parse(path.read_bytes())
        value['tested_sha'] = '8c4a91fbb5e3b9ae2f96881cf2198b3d2ce321af'
        path.write_bytes(c.compact(value))
        with self.assertRaisesRegex(ValueError, 'candidate compile proof'):
            self.jobs()

    def test_case_removal_and_named_duplicate_prompt_are_not_deduplicated(self):
        jobs = self.jobs()
        duplicate = [r for r in jobs if r['id'] in {'RS01_CNL_V6', 'RS03_JSON_V6'}]
        self.assertEqual(len(duplicate), 4)
        self.assertEqual(len({r['prompt_sha256'] for r in duplicate}), 1)
        path = self.root / 'manifest.json'
        value = c.parse(path.read_bytes())
        value['rows'].pop()
        path.write_bytes(c.compact(value))
        with self.assertRaisesRegex(ValueError, 'omits an RS'):
            self.jobs()

    def test_three_issue57_repairs_keep_both_old_and_fixed_before_hash_roles(self):
        rows = [r for r in self.jobs() if r.get('known_issue57_repair')]
        self.assertEqual(len(rows), 6)
        self.assertEqual({r['id'] for r in rows}, set(c.REPAIRED))
        for row in rows:
            self.assertNotEqual(row['historical_old_rs001']['product_sha256'], row['expected_files_map_sha256'])
            self.assertEqual(row['expected_files_map_sha256'], c.REPAIRED[row['id']])

    def test_all4_refusals_have_complete_independent_expected_diagnostics(self):
        oracle = c.RefusalOracle(self.sources, self.modules)
        jobs = [r for r in self.jobs() if r['expected_status'] == 400]
        self.assertEqual(len(jobs), 4)
        for job in jobs:
            raw, value = oracle.expected(job)
            self.assertEqual(c.parse(raw), value)
            self.assertEqual(set(value), {'status', 'code', 'detail', 'diagnostics'})
            self.assertEqual(set(value['diagnostics'][0]), {'code', 'line', 'column', 'end_line', 'end_column',
                'byte_start', 'byte_end', 'prompt_pointer', 'cause', 'allowed_form'})
            self.assertEqual(value['diagnostics'][0]['code'], 'INVALID_VALUE')

    def test_typed_rs12_span_is_exactly_derived_from_its_original_request_token(self):
        oracle = c.RefusalOracle(self.sources, self.modules)
        job = next(r for r in self.jobs() if r['id'] == 'RS12_CNL_V3_FILTER_LIMIT' and r['form'] == 'typed')
        _, expected = oracle.expected(job)
        self.assertEqual(expected, {'status': 'error', 'code': 'INVALID_VALUE',
            'detail': 'prompt.query.filter_field: name collides with a generated Python identifier',
            'diagnostics': [{'code': 'INVALID_VALUE', 'line': 1, 'column': 457, 'end_line': 1, 'end_column': 464,
                'byte_start': 456, 'byte_end': 463, 'prompt_pointer': '/prompt/query/filter_field',
                'cause': 'prompt.query.filter_field: name collides with a generated Python identifier',
                'allowed_form': 'A prompt that meets the pinned compiler contract.'}]})
        self.assertEqual(job['raw'][456:463], b'"limit"')

    def test_refusal_oracle_refuses_changed_policy_source_before_response_use(self):
        bad = SimpleNamespace(read=lambda name: reader(name) + (b' ' if name == c.POLICY else b''))
        with self.assertRaisesRegex(ValueError, 'diagnostic source differs'):
            c.RefusalOracle(bad, self.modules)

    def test_typed_collision_change_is_not_accepted_as_original_request(self):
        oracle = c.RefusalOracle(self.sources, self.modules)
        job = next(r for r in self.jobs() if r['id'] == 'RS12_CNL_V3_FILTER_LIMIT' and r['form'] == 'typed')
        changed = {**job, 'raw': job['raw'].replace(b'"filter_field":"limit"', b'"filter_field":"other"')}
        with self.assertRaisesRegex(ValueError, 'typed collision prompt'):
            oracle.expected(changed)


class BundleFileAndCaptureTests(TempCase):
    def setUp(self):
        super().setUp()
        self.export = plain('export_goldens')
        self.files = {'app.py': 'value = 1\n', 'tests/test_app.py': 'assert True\n'}
        self.job = {'cohort': 'coverage6', 'id': 'fake', 'form': 'json-none', 'syntax': 'json', 'target': None,
                    'raw': b'{"fixture":true}', 'request_sha256': c.sha(b'{"fixture":true}'),
                    'prompt_sha256': self.export.value_sha({'version': 'som.prompt.v1'}), 'expected_status': 200,
                    'expected_file_sha256': {n: c.sha(v.encode()) for n, v in self.files.items()}}
        self.bundle = {'version': 'som.dsl-bundle.v1', 'prompt': {'version': 'som.prompt.v1'},
                       'dsl': {'files': []}, 'assets': {'backend': {'id': 'backend', 'sha256': 'a' * 64},
                       'formatter': {'id': 'render', 'sha256': 'b' * 64}}}
        self.bundle['hashes'] = {'prompt_sha256': self.export.value_sha(self.bundle['prompt']),
            'dsl_sha256': self.export.value_sha(self.bundle['dsl']), 'payload_sha256': self.export.value_sha(self.bundle)}
        self.checker = SimpleNamespace(IDS={'python_backend': 'backend', 'python_render_only_formatter': 'render'},
            after={'python_backend': 'a' * 64, 'python_render_only_formatter': 'b' * 64})
        self.reply = c.compact({'status': 'ok', 'bundle': self.bundle})
        self.assembled = c.compact({'status': 'ok', 'files': self.files})

    def test_current_bundle_uses_all_own_canonical_hashes(self):
        self.assertEqual(c.validate_bundle(self.reply, self.job, self.checker, self.export), self.bundle)
        bad = json.loads(self.reply)
        bad['bundle']['dsl']['changed'] = True
        with self.assertRaisesRegex(ValueError, 'canonical hash'):
            c.validate_bundle(c.compact(bad), self.job, self.checker, self.export)

    def test_changed_prompt_or_source_descriptor_is_refused(self):
        for mutate in ('prompt', 'backend'):
            bad = json.loads(self.reply)
            if mutate == 'prompt': bad['bundle']['prompt']['unexpected'] = True
            else: bad['bundle']['assets']['backend']['id'] = 'other'
            if mutate == 'backend':
                bad['bundle']['hashes']['payload_sha256'] = self.export.value_sha({k:v for k,v in bad['bundle'].items() if k!='hashes'})
            with self.subTest(mutate=mutate), self.assertRaises(ValueError):
                c.validate_bundle(c.compact(bad), self.job, self.checker, self.export)

    def test_complete_frozen_files_refuse_missing_extra_and_changed_file(self):
        self.assertEqual(c.product_files(self.assembled, self.job, self.export)[0], self.files)
        for files in ({'app.py': self.files['app.py']}, {**self.files, 'extra.py': 'extra'}, {**self.files, 'app.py': 'changed'}):
            with self.subTest(files=files), self.assertRaisesRegex(ValueError, 'coverage file map'):
                c.product_files(c.compact({'status': 'ok', 'files': files}), self.job, self.export)

    def test_rs_whole_map_uses_fixed_before_and_preserves_original_bytes(self):
        job = {**self.job, 'cohort': 'rs22', 'expected_files_map_sha256': self.export.value_sha(self.files)}
        files, _ = c.product_files(self.assembled, job, self.export)
        self.assertEqual(files['app.py'].encode(), b'value = 1\n')
        with self.assertRaisesRegex(ValueError, 'fixed-before RS'):
            c.product_files(self.assembled, {**job, 'expected_files_map_sha256': '0' * 64}, self.export)

    def test_capture_preserves_both_named_forms_and_uses_each_own_bundle(self):
        class Interrupted(InterruptedError): pass
        driver = SimpleNamespace(DriverInterrupted=Interrupted, compact=c.compact)
        service = SimpleNamespace(call=mock.Mock(return_value=(200, self.reply, {'pid': 123})))
        modules = {'export_goldens': self.export, 'run_experimental_native_compile': driver}
        jobs = [self.job, {**self.job, 'form': 'cnl-none'}]
        with mock.patch.object(c, 'assemble', return_value=(200, self.assembled, {'pid': 123})) as assemble:
            rows = c.capture_rows(jobs, service, None, self.checker, modules, self.root)
        self.assertEqual([r['form'] for r in rows], ['json-none', 'cnl-none'])
        self.assertTrue(all(r['state'] == 'passed' for r in rows))
        self.assertEqual([call.args[1] for call in assemble.call_args_list], [c.compact({'bundle': self.bundle})] * 2)
        self.assertEqual((self.root / 'rows/0000/product/app.py').read_bytes(), self.files['app.py'].encode())

    def test_non200_compile_is_preserved_and_does_not_call_assemble(self):
        class Interrupted(InterruptedError): pass
        modules = {'export_goldens': self.export, 'run_experimental_native_compile': SimpleNamespace(DriverInterrupted=Interrupted)}
        service = SimpleNamespace(call=lambda *_: (503, b'{"status":"unavailable"}', {'pid': 123}))
        with mock.patch.object(c, 'assemble') as assemble:
            rows = c.capture_rows([self.job], service, None, self.checker, modules, self.root)
        self.assertEqual(rows[0]['state'], 'failed')
        self.assertEqual(rows[0]['compile_status'], 503)
        self.assertEqual((self.root / 'rows/0000/compile.response.raw').read_bytes(), b'{"status":"unavailable"}')
        assemble.assert_not_called()

    def test_interruption_keeps_prior_records_and_stops_new_calls(self):
        class Interrupted(InterruptedError): pass
        modules = {'export_goldens': self.export,
                   'run_experimental_native_compile': SimpleNamespace(DriverInterrupted=Interrupted, compact=c.compact)}
        service = SimpleNamespace(call=mock.Mock(side_effect=[(200, self.reply, {'pid': 123}), Interrupted('signal')]))
        jobs = [self.job, {**self.job, 'form': 'json-python'}, {**self.job, 'form': 'cnl-none'}]
        with mock.patch.object(c, 'assemble', return_value=(200, self.assembled, {'pid': 123})):
            with self.assertRaises(c.CaptureStopped) as stopped:
                c.capture_rows(jobs, service, None, self.checker, modules, self.root)
        self.assertEqual([r['state'] for r in stopped.exception.records], ['passed', 'interrupted'])
        self.assertEqual(service.call.call_count, 2)
        self.assertFalse((self.root / 'rows/0002').exists())


    def fixture_owner(self):
        return {'pid': 123, 'state': 'S', 'pgrp': 123, 'session': 123, 'start_ticks': 77,
                'executable_inode': 789, 'listener': {'table': 'tcp', 'address': '0100007F', 'inode': '456'}}

    def saved_positive(self):
        class Interrupted(InterruptedError): pass
        owner = self.fixture_owner()
        modules = {'export_goldens': self.export,
                   'run_experimental_native_compile': SimpleNamespace(DriverInterrupted=Interrupted, compact=c.compact)}
        service = SimpleNamespace(call=mock.Mock(return_value=(200, self.reply, owner)))
        with mock.patch.object(c, 'assemble', return_value=(200, self.assembled, owner)):
            row = c.capture_rows([self.job], service, None, self.checker, modules, self.root)[0]
        return row, c.owner_identity(owner)

    def test_saved_row_rechecks_real_bytes_own_bundle_and_whole_product(self):
        row, owner = self.saved_positive()
        bundle, files = c.verify_saved_row(self.root, 0, row, self.job, self.checker, self.export, None, owner)
        self.assertEqual((bundle, files), (self.bundle, self.files))
        (self.root / 'rows/0000/product/extra.py').write_bytes(b'extra')
        with self.assertRaisesRegex(ValueError, 'file universe'):
            c.verify_saved_row(self.root, 0, row, self.job, self.checker, self.export, None, owner)

    def test_saved_row_refuses_response_digest_and_other_bundle(self):
        row, owner = self.saved_positive()
        path = self.root / row['compile_response_path']
        path.write_bytes(path.read_bytes() + b' ')
        with self.assertRaisesRegex(ValueError, 'reply bytes'):
            c.verify_saved_row(self.root, 0, row, self.job, self.checker, self.export, None, owner)
        path.write_bytes(self.reply)
        path = self.root / row['assemble_request_path']
        raw = c.compact({'bundle': {**self.bundle, 'unexpected': True}})
        path.write_bytes(raw)
        row['assemble_request_sha256'] = c.sha(raw)
        with self.assertRaisesRegex(ValueError, 'own exact current bundle'):
            c.verify_saved_row(self.root, 0, row, self.job, self.checker, self.export, None, owner)

    def test_saved_row_refuses_alias_path_non200_and_changed_socket_pid(self):
        row, owner = self.saved_positive()
        changes = ({'request_path': 'rows/0001/compile.request.raw'}, {'assemble_status': 503},
                   {'compile_ownership': {**self.fixture_owner(), 'start_ticks': 78}},
                   {'assemble_ownership': {**self.fixture_owner(), 'pid': 999, 'pgrp': 999, 'session': 999}},
                   {'product_acceptance_passed': True})
        for fields in changes:
            with self.subTest(fields=fields), self.assertRaises(ValueError):
                c.verify_saved_row(self.root, 0, {**row, **fields}, self.job, self.checker, self.export, None, owner)

    def test_complete_source_refusal_cannot_carry_an_assembly(self):
        packet = c.parse(reader(c.RS_PACKET))
        source = next(r for r in packet['rows'] if r['id'] == 'RS12_CNL_V3_FILTER_LIMIT')
        job = {'cohort': 'rs22', 'id': source['id'], 'form': 'typed', 'syntax': 'json',
               'raw': source['fresh_python_typed_request']['body'].encode(),
               'request_sha256': source['fresh_python_typed_request']['raw_sha256'],
               'prompt_sha256': source['prompt_sha256'], 'expected_status': 400}
        oracle = c.RefusalOracle(SimpleNamespace(read=reader), {'raw_digest_compat': plain('raw_digest_compat')})
        raw, _ = oracle.expected(job)
        class Interrupted(InterruptedError): pass
        modules = {'export_goldens': self.export,
                   'run_experimental_native_compile': SimpleNamespace(DriverInterrupted=Interrupted)}
        service = SimpleNamespace(call=lambda *_: (400, raw, self.fixture_owner()))
        row = c.capture_rows([job], service, oracle, None, modules, self.root)[0]
        self.assertEqual(c.verify_saved_row(self.root, 0, row, job, None, self.export, oracle,
                                           c.owner_identity(self.fixture_owner())), (None, None))
        row['assemble_status'] = 200
        with self.assertRaisesRegex(ValueError, 'no-assembly'):
            c.verify_saved_row(self.root, 0, row, job, None, self.export, oracle, c.owner_identity(self.fixture_owner()))

    def test_saved_raw_pid_socket_fd_and_executable_proof_is_rederived(self):
        owner = self.fixture_owner()
        refs = {'process_stat': 'owned-start.proc-stat.raw', 'tcp': 'owned-start.tcp.raw',
                'tcp6': 'owned-start.tcp6.raw', 'file_descriptors': 'owned-start.fd-links.json'}
        fields = ['S', '1', '123', '123'] + ['0'] * 15 + ['77']
        (self.root / refs['process_stat']).write_bytes(('123 (binary) ' + ' '.join(fields) + '\n').encode())
        table = ('header\n0: 0100007F:' + format(c.PORT, '04X') + ' 00000000:0000 0A 0 0 0 0 0 456\n').encode()
        (self.root / refs['tcp']).write_bytes(table)
        (self.root / refs['tcp6']).write_bytes(b'header\n')
        (self.root / refs['file_descriptors']).write_bytes(c.compact({'3': 'socket:[456]'}))
        proof = {'owner': owner, 'raw_paths': refs, 'executable': {'device': 1, 'inode': 789, 'target': '/tmp/binary'}}
        c.verify_owned_socket_proof(self.root, proof, c.owner_identity(owner), '/tmp/binary')
        (self.root / refs['tcp']).write_bytes(table.replace(b' 456', b' 999'))
        with self.assertRaisesRegex(ValueError, 'raw socket FD'):
            c.verify_owned_socket_proof(self.root, proof, c.owner_identity(owner), '/tmp/binary')

    def test_cleanup_requires_same_group_empty_session_and_real_wait(self):
        owner = self.fixture_owner()
        report = {'owned_start': owner, 'health': {'owned_socket': owner},
                  'launch': {'argv': ['/tmp/binary', 'serve', '--assets', '/tmp/assets', '--port', str(c.PORT)]},
                  'protected_ports_before': {'8765': [], '18765': []},
                  'protected_ports_after': {'8765': [], '18765': []},
                  'cleanup': {'owned_pid': 123, 'cleanup_passed': True, 'signal_target': 'owned new process group only',
                              'signals': ['SIGTERM'], 'remaining_owned_session_members': [],
                              'wait_completed': True, 'wait_exit_code': -15, 'exit_code': -15}}
        self.assertEqual(c.verify_process_report(report), c.owner_identity(owner))
        for fields in ({'wait_completed': False}, {'wait_exit_code': None}, {'owned_pid': 999},
                       {'remaining_owned_session_members': [{'pid': 124}]}, {'signals': ['SIGKILL']}):
            with self.subTest(fields=fields), self.assertRaises(ValueError):
                c.verify_process_report({**report, 'cleanup': {**report['cleanup'], **fields}})
        report['launch']['argv'][-1] = '8765'
        with self.assertRaisesRegex(ValueError, 'fixed private port'):
            c.verify_process_report(report)

    def test_all68_service_status_events_and64_assembly_events_are_rederived(self):
        rows, events = [], [{'stage': 'listening', 'address': f'127.0.0.1:{c.PORT}', 'purpose': 'som.rust-compiler.v1'}]
        for index in range(68):
            directory = self.root / 'rows' / f'{index:04d}'
            directory.mkdir(parents=True)
            status = 200 if index < 64 else 400
            row = {'compile_status': status, 'compile_response_path': f'rows/{index:04d}/compile.response.raw'}
            (self.root / row['compile_response_path']).write_bytes(self.reply if status == 200 else b'{"status":"error"}')
            events.append({'stage': 'compile', 'total_ms': 1.0, 'http_status': status,
                           'bundle_bytes': len(self.export.canonical(self.bundle).encode()) if status == 200 else None})
            if status == 200:
                row.update(assemble_status=200, assemble_response_path=f'rows/{index:04d}/assemble.response.raw')
                (self.root / row['assemble_response_path']).write_bytes(self.assembled)
                events.append({'stage': 'assemble', 'total_ms': 2.0,
                               'formatted_bytes': sum(len(text.encode()) for text in self.files.values())})
            rows.append(row)
        log = self.root / 'server.log'
        raw = b''.join(c.compact(event) + b'\n' for event in events)
        log.write_bytes(raw)
        result = c.verify_events(self.root, rows, self.export)
        self.assertEqual(result['counts'], {'listening': 1, 'compile': 68, 'assemble': 64})
        self.assertEqual(result['compile_statuses_from_rows'], {'200': 64, '400': 4})
        for altered in (events[:-1], events + events[-1:], [events[0], events[2], events[1], *events[3:]],
                        [*events[:-1], {**events[-1], 'bundle_bytes': 0}]):
            log.write_bytes(b''.join(c.compact(event) + b'\n' for event in altered))
            with self.assertRaises(ValueError): c.verify_events(self.root, rows, self.export)

    def test_event_source_pins_use_target_service_and_fail_before_log_use(self):
        c.verify_event_sources(reader)
        bad = lambda name: reader(name) + (b' ' if name == 'som-rs/som-targets/src/main.rs' else b'')
        with self.assertRaisesRegex(ValueError, 'target service event source'):
            c.verify_event_sources(bad)


class ConsumerEnvelopeTests(TempCase):
    def test_before_incomplete_or_other_candidate_report_stops_before_source_execution(self):
        base = {'version': c.VERSION, 'role': 'current-candidate-compiled-inputs', 'helper_revision': '1' * 40,
                'candidate_revision': c.CANDIDATE, 'candidate_admission': {'fixed': True}, 'capture_complete': True,
                'actual_forms': 68, 'passed_forms': 68, 'positive_assemblies': 64, 'refusal_forms': 4,
                'product_acceptance_passed': False, 'whole_acceptance_complete': False,
                'before_role': c.FIXED_BEFORE_AUTHORITY, 'historical_missing_wires_recovered': False}
        for field, value in (('role', 'fresh-before-only'), ('capture_complete', False), ('actual_forms', 67),
                             ('positive_assemblies', 63), ('refusal_forms', 2), ('helper_revision', '2' * 40),
                             ('candidate_revision', '3' * 40), ('product_acceptance_passed', True)):
            with self.subTest(field=field):
                (self.root / 'manifest.json').write_bytes(c.compact({**base, field: value}))
                source = mock.Mock(side_effect=AssertionError('source must not execute'))
                with mock.patch.dict(os.environ, {'GITHUB_ACTIONS': 'true'}), mock.patch.object(c.sys, 'platform', 'linux'):
                    with self.assertRaisesRegex(ValueError, 'complete actual same-helper'):
                        c.verify_capture(self.root, source, '1' * 40, c.CANDIDATE, {'fixed': True}, self.root / 'assembly')
                source.assert_not_called()




class FinalCandidateSourceAuthorityTests(TempCase):
    def test_final_mode_rejects_f59_and_mixed_source_before_any_reader_call(self):
        reader = mock.Mock()
        for helper, candidate in [(c.CANDIDATE, c.CANDIDATE), ('1' * 40, '2' * 40), ('1' * 39, '1' * 39)]:
            with self.subTest(helper=helper), self.assertRaises(ValueError):
                c.native_source_authority(reader, ['complete-source'], helper, candidate)
        reader.assert_not_called()

    def test_native_authority_is_called_with_the_complete_source_inventory_and_identity_pair(self):
        body = "def verify(source_reader, mode, source_paths, helper_revision=None, candidate_revision=None):\n    assert source_paths == ['all-tracked.json', 'all-tracked.py']\n    return {'mode': mode, 'source_authority_active': True, 'helper_revision': helper_revision, 'candidate_revision': candidate_revision, 'product_acceptance_passed': False, 'compiler_acceptance_passed': False, 'whole_acceptance_complete': False}\n"
        fixture = native_fixture(self, authority_body=body)
        try:
            module = fixture.controller()
            observed = []
            def selected(name):
                observed.append(name)
                return fixture.raw[name]
            before = (module.CANDIDATE, module.BINARY_SHA, module.BEFORE_BINARY, module.FIXED_BEFORE_AUTHORITY)
            record = module.native_source_authority(selected, ['all-tracked.json', 'all-tracked.py'], '1' * 40, '1' * 40)
            self.assertEqual(record['candidate_revision'], '1' * 40)
            self.assertFalse(record['product_acceptance_passed'])
            self.assertEqual(observed, [c.BASE + 'native_qa_authority.py'])
            self.assertEqual((module.CANDIDATE, module.BINARY_SHA, module.BEFORE_BINARY, module.FIXED_BEFORE_AUTHORITY), before)
            self.assertIs(fixture.domain.records['bootstrap.authority'].module, fixture.authority)
        finally:
            fixture.stack.close()

    def test_pending_authority_or_product_acceptance_claim_cannot_select_final_capture(self):
        template = "def verify(source_reader, mode, source_paths, helper_revision=None, candidate_revision=None):\n    return {record!r}\n"
        good = {'mode': 'native-qa-v1', 'source_authority_active': True, 'helper_revision': '1' * 40,
                'candidate_revision': '1' * 40, 'product_acceptance_passed': False,
                'compiler_acceptance_passed': False, 'whole_acceptance_complete': False}
        for field, value in [('source_authority_active', False), ('candidate_revision', '2' * 40),
                             ('product_acceptance_passed', True), ('compiler_acceptance_passed', True),
                             ('whole_acceptance_complete', True)]:
            fixture = native_fixture(self, authority_body=template.format(record={**good, field: value}))
            try:
                module = fixture.controller()
                with self.subTest(field=field), self.assertRaisesRegex(ValueError, 'native source authority remains pending'):
                    module.native_source_authority(fixture.raw.__getitem__, ['tracked.json'], '1' * 40, '1' * 40)
            finally:
                fixture.stack.close()

    def test_recorded_default_does_not_silently_select_a_new_candidate(self):
        args = SimpleNamespace(candidate_revision='1' * 40, helper_revision='1' * 40)
        sources = mock.Mock()
        with self.assertRaisesRegex(ValueError, 'original f59'):
            c.selected_current_identity(args, sources)
        sources.read.assert_not_called()




class CompleteProofFactorySourceTests(TempCase):
    def fixture(self, missing_extra=False, changed_extra=False):
        values = {'factory.rs': b'factory source', 'extra-owning-helper.py': b'complete proof helper'}
        expected = {name: c.sha(raw) for name, raw in values.items()}
        seen = {'factory.rs': expected['factory.rs']}
        def read(name):
            if missing_extra and name == 'extra-owning-helper.py':
                raise ValueError('missing extra owning path')
            raw = values[name] + (b'changed' if changed_extra and name == 'extra-owning-helper.py' else b'')
            seen[name] = c.sha(raw)
            return raw
        descriptors = {'factory': 'd' * 64}
        repo = SimpleNamespace(commit='1' * 40, read=read)
        checker = SimpleNamespace(repo=repo, source_record=lambda: {'commit': repo.commit,
            'source_sha256': dict(seen), 'after_descriptors': descriptors})
        proof = {'tested_sha': repo.commit, 'initial_identities': {'source': expected},
                 'independent_source_record': {'source_sha256': expected, 'after_descriptors': descriptors}}
        return checker, proof, seen

    def test_native_producer_reads_every_extra_trusted_proof_source_before_saving_record(self):
        checker, proof, seen = self.fixture()
        result = c.bind_current_proof_factory_sources(checker, proof, '1' * 40)
        self.assertEqual(result['source_sha256'], proof['initial_identities']['source'])
        self.assertIn('extra-owning-helper.py', seen)

    def test_native_producer_missing_extra_owning_path_fails_instead_of_claiming_base_factory_complete(self):
        checker, proof, seen = self.fixture(missing_extra=True)
        with self.assertRaisesRegex(ValueError, 'missing extra owning'):
            c.bind_current_proof_factory_sources(checker, proof, '1' * 40)

    def test_native_producer_changed_extra_source_cannot_reuse_proof_vector(self):
        checker, proof, seen = self.fixture(changed_extra=True)
        with self.assertRaisesRegex(ValueError, 'owning source'):
            c.bind_current_proof_factory_sources(checker, proof, '1' * 40)


class CaptureImportLeaseTests(TempCase):
    def fixture(self):
        return native_fixture(self)

    def run_main(self, fixture, fail=False):
        # This is the actual capture borrow/manual-loader scope, not a mocked
        # production main/run or a full-product capture positive.
        probe = fixture.probe()
        result = probe.capture(probe.Sources(), fail)
        self.assertEqual(result, 'checked raw source')
        self.assertTrue(probe.BORROWED[-1]._closed)
        return 0

    def test_owned_import_and_manual_guards_close_on_success_and_failure_without_using_matching_pyc(self):
        import importlib.util
        before_spec, before_meta = importlib.util.spec_from_file_location, list(sys.meta_path)
        fixture = self.fixture()
        try:
            protected = importlib.util.spec_from_file_location
            self.assertEqual(self.run_main(fixture), 0)
            self.assertIs(importlib.util.spec_from_file_location, protected)
            fixture.controls.verify_installed()
            with self.assertRaisesRegex(RuntimeError, 'capture failed'):
                self.run_main(fixture, fail=True)
            self.assertTrue(fixture.probe().BORROWED[-1]._closed)
            fixture.controls.verify_installed()
            self.assertFalse(fixture.marker.exists())
        finally:
            # Core closes before its fixture's patch.dict module restoration.
            fixture.stack.close()
        self.assertIs(importlib.util.spec_from_file_location, before_spec)
        self.assertEqual(sys.meta_path, before_meta)
        self.assertFalse(fixture.marker.exists())

    def test_borrowed_context_import_guards_remain_owned_by_context_after_capture(self):
        import importlib.util
        fixture = self.fixture()
        protected = importlib.util.spec_from_file_location
        try:
            owner = fixture.controls
            self.assertTrue(owner._owns)
            self.assertEqual(self.run_main(fixture), 0)
            owner.verify_installed()
            self.assertIs(importlib.util.spec_from_file_location, protected)
            self.assertFalse(owner._closed)
            self.assertTrue(fixture.probe().BORROWED[-1]._closed)
            self.assertFalse(fixture.marker.exists())
        finally:
            fixture.stack.close()


class NativeCompanionProtocolTests(TempCase):
    def test_authority_wrong_raw_source_or_identity_cannot_borrow_seed(self):
        fixture = native_fixture(self)
        try:
            module = fixture.controller()
            with self.assertRaisesRegex(ValueError, 'actual native authority bytes differ'):
                module.native_source_authority(lambda name: fixture.raw[name] + b'\n# foreign bytes\n',
                    ['tracked.json'], '1' * 40, '1' * 40)
            with self.assertRaisesRegex(ValueError, 'helper/candidate identity differs'):
                module._native_controls(revision='2' * 40, candidate='2' * 40)
            self.assertEqual(list(fixture.body_markers.iterdir()), [])
        finally:
            fixture.stack.close()

    def test_unlisted_companion_prefix_refuses_without_a_plain_loader_fallback(self):
        fixture = native_fixture(self, variant='_unknown_companion_owner')
        try:
            module = fixture.controller()
            with self.assertRaisesRegex(ValueError, 'unlisted native companion namespace'):
                module._native_companion_prefix()
            self.assertEqual(list(fixture.body_markers.iterdir()), [])
        finally:
            fixture.stack.close()

    def test_actual_entry_whole_nine_role_preflight_precedes_any_helper_body(self):
        import ast
        fixture = native_fixture(self, entry=True)
        original_trace = sys.gettrace()
        try:
            def git(*arguments):
                return subprocess.check_output(['/usr/bin/git', '--no-replace-objects', '-C', str(fixture.root), *arguments],
                    env={'PATH': '/usr/bin:/bin', 'GIT_CONFIG_NOSYSTEM': '1', 'GIT_CONFIG_GLOBAL': '/dev/null',
                         'GIT_AUTHOR_NAME': 'Native preflight fixture', 'GIT_AUTHOR_EMAIL': 'fixture@example.invalid',
                         'GIT_COMMITTER_NAME': 'Native preflight fixture', 'GIT_COMMITTER_EMAIL': 'fixture@example.invalid'},
                    stderr=subprocess.DEVNULL, timeout=30)
            git('init', '--quiet'); git('add', '--', *sorted(fixture.raw))
            git('-c', 'commit.gpgsign=false', 'commit', '--quiet', '--no-verify', '-m', 'Explicit native entry protocol')
            revision = git('rev-parse', 'HEAD').decode().strip()
            fixture.transfer.revision = fixture.domain.revision = revision
            fixture.transfer.verify_entry_source = lambda path: self.assertEqual(path, c.SELF)
            line = next(node.lineno for node in ast.walk(ast.parse(fixture.raw[c.SELF]))
                if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call)
                and isinstance(node.value.func, ast.Name) and node.value.func.id == 'require'
                and len(node.value.args) == 2 and isinstance(node.value.args[1], ast.Constant)
                and node.value.args[1].value == 'fixed current capture helper source differs')
            last = next(reversed(fixture.group_roles.values())); original_row = fixture.rows[last]
            arguments = ['--repo', str(fixture.root), '--helper-revision', revision,
                         '--candidate-revision', revision, '--qa-mode', 'native-qa-v1']
            for name in ('assembly-root', 'carrier-root', 'restored-candidate', 'out'):
                arguments += ['--' + name, str(fixture.root.parent / name)]
            for mode in ('mismatch', 'missing'):
                reached = []
                def trace(frame, event, argument):
                    record = fixture.domain.records.get('caller')
                    if (event == 'line' and not reached and frame.f_lineno == line and record is not None
                            and frame.f_globals is record.namespace and frame.f_code.co_qualname == 'main'
                            and any(frame.f_code is code for code in record.codes.values())):
                        reached.append('actual-main-group-preflight')
                        if mode == 'mismatch': original_row['source'] = c.SELF
                        else: fixture.rows.pop(last)
                    return trace
                try:
                    sys.settrace(trace)
                    if mode == 'mismatch':
                        with self.assertRaisesRegex(ValueError, '^fixed current capture helper source differs$'):
                            fixture.seeds[fixture.core.ENTRY_MODULE]['module']._trusted_run(fixture.controls, 'caller', arguments)
                    else:
                        with self.assertRaises(KeyError) as missing:
                            fixture.seeds[fixture.core.ENTRY_MODULE]['module']._trusted_run(fixture.controls, 'caller', arguments)
                        self.assertEqual(missing.exception.args, (last,))
                finally:
                    sys.settrace(original_trace)
                    original_row['source'] = fixture.group_paths[next(reversed(fixture.group_paths))]
                    fixture.rows[last] = original_row
                self.assertEqual(reached, ['actual-main-group-preflight'])
                self.assertEqual(len(fixture.group_paths), 9)
                self.assertEqual(list(fixture.body_markers.iterdir()), [])
                self.assertNotIn('caller', fixture.domain.records)
                fixture.controls.verify_installed()
        finally:
            sys.settrace(original_trace)
            fixture.stack.close()

    def test_borrower_success_and_failure_never_remove_the_owner_finder_or_spec(self):
        import importlib.util
        for manual_variant in ('typescript', 'react'):
            fixture = native_fixture(self, manual_variant=manual_variant)
            try:
                probe = fixture.probe(); finder, spec = fixture.domain.finder, importlib.util.spec_from_file_location
                self.assertEqual(probe.capture(probe.Sources()), 'checked raw source')
                first = probe.BORROWED[-1]
                with self.assertRaisesRegex(RuntimeError, '^capture failed after import$'):
                    probe.capture(probe.Sources(), True)
                second = probe.BORROWED[-1]
                self.assertIsNot(first, second)
                self.assertTrue(first._closed and second._closed)
                self.assertFalse(fixture.controls._closed)
                self.assertIn(finder, sys.meta_path); self.assertIs(importlib.util.spec_from_file_location, spec)
                fixture.controls.verify_installed()
                self.assertFalse(fixture.marker.exists())
            finally:
                fixture.stack.close()


if __name__ == '__main__':
    if os.environ.get('GITHUB_ACTIONS') != 'true':
        raise SystemExit('Run these fixtures on GitHub only')
    unittest.main()

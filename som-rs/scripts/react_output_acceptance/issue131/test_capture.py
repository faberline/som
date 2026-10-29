#!/usr/bin/env python3
"""Hosted runner only: exercise source joins and reject altered evidence."""
import hashlib
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import capture


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def canonical(value):
    return digest(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode())


class CaptureTests(unittest.TestCase):
    def setUp(self):
        self.assertEqual(os.environ.get('GITHUB_ACTIONS'), 'true', 'fixtures run only on GitHub runners')
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.evidence, self.run, self.inventory, self.sealed = root / 'evidence', root / 'run003', root / 'inventory.json', root / 'sealed'
        self.report_path = self.evidence / 'run/rust-baseline/report.json'
        self.record_path = self.evidence / 'run/rust-baseline-raw/manifest.json'
        self.compile_request = b'{"original":true}'
        self.compile_response = capture.jbytes({'status': 'ok', 'bundle': {'prompt': '中文'}})
        self.refusal_request, self.refusal_response = b'bad-request', b'bad-response'
        self.qa_request = b'rebased-request'
        self.qa_response = capture.jbytes({'files': {'src/lib.rs': 'fixed'}})
        self.prior_request = capture.jbytes({'bundle': {'prompt': '中文'}})
        self.prior_response = b'candidate refused baseline bundle'
        self.files_sha = canonical({'src/lib.rs': 'fixed'})
        def call(number, port, route, status, request, response):
            base = f'calls/{number:06d}'
            row = {'id': number, 'method': 'POST', 'url': f'http://127.0.0.1:{port}{route}',
                   'complete': True, 'error': None, 'status': status, 'request_file': base + '.request.raw',
                   'response_file': base + '.response.raw', 'request_sha256': digest(request), 'response_sha256': digest(response)}
            self.put(self.record_path.parent / row['request_file'], request)
            self.put(self.record_path.parent / row['response_file'], response)
            self.write(self.record_path.parent / (base + '.json'), row)
            return row
        calls = [call(1, 18917, '/v1/compile', 200, self.compile_request, self.compile_response),
                 call(2, 18917, '/v1/compile', 400, self.refusal_request, self.refusal_response),
                 call(3, 18919, '/v1/assemble', 200, self.qa_request, self.qa_response),
                 call(4, 18919, '/v1/assemble', 400, self.prior_request, self.prior_response)]
        self.write(self.record_path, {'exit_code': 0, 'recording_errors': [], 'calls': calls})
        records, slots, rows = [], [], []
        for index in range(697):
            case, key = f'case-{index}', ('phase1', f'case-{index}')
            records.append({'id': case, 'files_sha256': self.files_sha})
            candidate = {'manifest_bound': True, 'status': 200, 'call_id': 1,
                         'request_member': 'run/rust-baseline-raw/calls/000001.request.raw',
                         'response_member': 'run/rust-baseline-raw/calls/000001.response.raw',
                         'request_package': {'sha256': digest(self.compile_request)},
                         'response_package': {'sha256': digest(self.compile_response)}}
            slots.append({'phase': key[0], 'case': case, 'cohort': 'rust006', 'report_status': 200,
                          'report_response_sha256': digest(self.compile_response),
                          'historical_request_sha256': digest(self.compile_request), 'source_candidates': [candidate]})
            slots.append({'phase': key[0], 'case': case, 'cohort': 'rust006_conditional_assemble', 'slot_id': index + 1})
            rows.extend([{'phase': key[0], 'id': case, 'check': 'compile_equal', 'passed': True,
                          'baseline_status': 200, 'baseline_response_sha256': digest(self.compile_response),
                          'baseline_request_sha256': digest(self.compile_request)},
                         {'phase': key[0], 'id': case, 'check': 'baseline_bundle_refused', 'passed': True,
                          'qa_status': 400, 'request_sha256': digest(capture.jbytes({'bundle': {'prompt': '中文'}})),
                          'qa_request_sha256': digest(capture.jbytes({'bundle': {'prompt': '中文'}})),
                          'qa_response_sha256': digest(self.prior_response)},
                         {'phase': key[0], 'id': case, 'check': 'rebased_files', 'passed': True, 'qa_status': 200,
                          'files_sha256': self.files_sha, 'recorded_files_sha256': self.files_sha,
                          'qa_request_sha256': digest(self.qa_request), 'qa_response_sha256': digest(self.qa_response)}])
        for case in ('RS12_CNL_V3_FILTER_LIMIT', 'RS13_JSON_V3_FILTER_OFFSET'):
            candidate = {'manifest_bound': True, 'status': 400, 'call_id': 2,
                         'request_member': 'run/rust-baseline-raw/calls/000002.request.raw',
                         'response_member': 'run/rust-baseline-raw/calls/000002.response.raw'}
            slots.append({'phase': 'phase2', 'case': case, 'cohort': 'rust006', 'report_status': 400,
                          'report_response_sha256': digest(self.refusal_response),
                          'historical_request_sha256': digest(self.refusal_request), 'source_candidates': [candidate]})
            rows.append({'phase': 'phase2', 'id': case, 'check': 'compile_equal', 'passed': True,
                         'baseline_status': 400, 'baseline_response_sha256': digest(self.refusal_response)})
        self.write(self.run / 'rust-products.json', {'cases': records})
        self.write(self.run / 'phase2/summary.json', {'case_sha256': {}})
        self.write(self.report_path, {'passed': True, 'not_run_count': 0, 'failed_count': 0, 'rows': rows,
                                     'rust_products_sha256': digest((self.run / 'rust-products.json').read_bytes()),
                                     'records_sha256': {'phase2_case_files': {}}})
        self.write(self.inventory, {'slots': slots})
        entries = [{'path': path.relative_to(self.evidence).as_posix(), 'sha256': digest(path.read_bytes())}
                   for path in self.evidence.rglob('*') if path.is_file()]
        self.write(self.sealed / 'manifest.json', {'cipher_sha256': 'fixture', 'files': entries})
        self.write(self.sealed / 'evidence-index.json', {'packages': [{'manifest': 'manifest.json',
                   'manifest_sha256': digest((self.sealed / 'manifest.json').read_bytes()), 'cipher_sha256': 'fixture'}]})
        capture.EVIDENCE_INDEX_SHA = digest((self.sealed / 'evidence-index.json').read_bytes())
        capture.INVENTORY_SHA = digest(self.inventory.read_bytes())
        capture.REPORT_SHA = digest(self.report_path.read_bytes())
        capture.RECORDING_SHA = digest(self.record_path.read_bytes())
        self.checker = SimpleNamespace(canonical_sha256=canonical)

    def tearDown(self):
        self.temp.cleanup()

    def put(self, path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(value)

    def write(self, path, value):
        self.put(path, capture.jbytes(value))

    def prepare(self):
        return capture.prepare(self.inventory, self.evidence, self.run, self.checker, self.sealed)

    def test_freezes_all_requests_and_separates_refusals(self):
        plan, expected, refused = self.prepare()
        self.assertEqual((len(plan), len(expected), len(refused)), (697, 697, 2))
        self.assertEqual(plan[0]['request'], b'{"bundle":{"prompt":"\xe4\xb8\xad\xe6\x96\x87"}}')
        self.assertEqual(plan[0]['expected_files_sha256'], self.files_sha)
        self.assertEqual(plan[0]['prior_candidate_raw_request_state'], 'verified')
        self.assertEqual(expected['1'], {'src/lib.rs': 'fixed'})

    def test_modified_accepted_map_fails_closed(self):
        self.put(self.record_path.parent / 'calls/000003.response.raw', b'{"files":{}}')
        with self.assertRaises(ValueError):
            self.prepare()

    def test_modified_compile_request_fails_closed(self):
        self.put(self.record_path.parent / 'calls/000001.request.raw', b'changed')
        with self.assertRaises(ValueError):
            self.prepare()

    def test_modified_run003_fails_closed(self):
        self.put(self.run / 'rust-products.json', b'{}')
        with self.assertRaises(ValueError):
            self.prepare()

    def test_refusal_request_sha_mismatch_fails_closed(self):
        report = json.loads(self.report_path.read_bytes())
        report['rows'][1]['request_sha256'] = '0' * 64
        report['rows'][1]['qa_request_sha256'] = '0' * 64
        self.write(self.report_path, report)
        capture.REPORT_SHA = digest(self.report_path.read_bytes())
        manifest_path = self.sealed / 'manifest.json'
        manifest = json.loads(manifest_path.read_bytes())
        entry = next(x for x in manifest['files'] if x['path'] == 'run/rust-baseline/report.json')
        entry['sha256'] = capture.REPORT_SHA
        self.write(manifest_path, manifest)
        index_path = self.sealed / 'evidence-index.json'
        index = json.loads(index_path.read_bytes())
        index['packages'][0]['manifest_sha256'] = digest(manifest_path.read_bytes())
        self.write(index_path, index)
        capture.EVIDENCE_INDEX_SHA = digest(index_path.read_bytes())
        with self.assertRaisesRegex(ValueError, 'original serializer request identity'):
            self.prepare()

    def test_missing_prior_raw_request_is_marked_unavailable(self):
        report = json.loads(self.report_path.read_bytes())
        report['rows'][1]['qa_response_sha256'] = '0' * 64
        self.write(self.report_path, report)
        capture.REPORT_SHA = digest(self.report_path.read_bytes())
        manifest_path = self.sealed / 'manifest.json'
        manifest = json.loads(manifest_path.read_bytes())
        next(x for x in manifest['files'] if x['path'] == 'run/rust-baseline/report.json')['sha256'] = capture.REPORT_SHA
        self.write(manifest_path, manifest)
        index_path = self.sealed / 'evidence-index.json'
        index = json.loads(index_path.read_bytes())
        index['packages'][0]['manifest_sha256'] = digest(manifest_path.read_bytes())
        self.write(index_path, index)
        capture.EVIDENCE_INDEX_SHA = digest(index_path.read_bytes())
        plan, _, _ = self.prepare()
        self.assertEqual(plan[0]['prior_candidate_raw_request_state'], 'unavailable')
        self.assertEqual(plan[0]['prior_candidate_raw_request_sources'], [])

    def test_modified_refusal_fails_closed(self):
        self.put(self.record_path.parent / 'calls/000002.response.raw', b'changed')
        with self.assertRaises(ValueError):
            self.prepare()

    def test_owner_import_uses_sibling_source(self):
        import owned_candidate
        self.assertEqual(Path(owned_candidate.__file__).resolve(), Path(capture.__file__).resolve().with_name('owned_candidate.py'))

    def test_self_hosted_runner_is_rejected_before_execution(self):
        with patch.dict(os.environ, {'RUNNER_ENVIRONMENT': 'self-hosted', 'RUNNER_OS': 'macOS'}):
            with self.assertRaisesRegex(ValueError, 'hosted macOS arm64 required'):
                capture.execute(SimpleNamespace(), self.checker)

    def test_attempt_without_comparison_stays_unrun(self):
        self.assertEqual(capture.count_unrun([1, 2, 3], [{'comparison': 'unrun'}]), 3)
        self.assertEqual(capture.count_unrun([1, 2, 3], [{'comparison': 'failed'}]), 2)

    def test_source_preparation_failure_records_all_unrun(self):
        out = Path(self.temp.name) / 'failed-capture'
        args = SimpleNamespace(out=out, inventory=self.inventory, evidence=self.evidence, run003=self.run, sealed=self.sealed)
        with patch.object(capture, 'prepare', side_effect=ValueError('fixture failure')):
            with self.assertRaisesRegex(ValueError, 'fixture failure'):
                capture.execute(args, self.checker)
        receipt = json.loads((out / 'receipt.json').read_bytes())
        self.assertEqual((receipt['unrun'], receipt['passed'], receipt['stage']), (697, False, 'source_preparation'))

    def fake_prestart(self):
        root = Path(self.temp.name)
        baseline, repo = root / 'baseline', root / 'repo'
        self.put(baseline / 'som', b'fake executable')
        self.put(baseline / 'source.json', b'fake source')
        self.put(baseline / 'build.json', b'fake build')
        self.write(baseline / 'runtime.json', {'health': {}})
        capture.BASELINE_SHA = digest((baseline / 'som').read_bytes())
        release = {'candidate': {'binary_sha256': capture.BASELINE_SHA,
                   'build_commit': '8c4a91fbb5e3b9ae2f96881cf2198b3d2ce321af', 'binary_bytes': 15,
                   'source_json_sha256': digest((baseline / 'source.json').read_bytes()),
                   'build_receipt_sha256': digest((baseline / 'build.json').read_bytes()),
                   'runtime_receipt_sha256': digest((baseline / 'runtime.json').read_bytes())}}
        self.write(repo / 'som-rs/evidence/typescript-output-release-2026-10-22-issue65.json', release)
        return SimpleNamespace(out=root / 'prestart', inventory=self.inventory, evidence=self.evidence,
                               run003=self.run, sealed=self.sealed, baseline=baseline, repo=repo)

    def test_provenance_mismatch_records_all_unrun(self):
        args = self.fake_prestart()
        self.put(args.baseline / 'source.json', b'changed source')
        with patch.object(capture, 'prepare', return_value=([], {}, [])), patch.object(capture.subprocess, 'Popen') as spawn:
            with self.assertRaisesRegex(ValueError, 'baseline provenance'):
                capture.execute(args, self.checker)
            spawn.assert_not_called()
        receipt = json.loads((args.out / 'receipt.json').read_bytes())
        self.assertEqual((receipt['unrun'], receipt['passed'], receipt['stage']), (697, False, 'baseline_provenance'))

    def test_busy_port_records_all_unrun(self):
        args = self.fake_prestart()
        with patch.object(capture, 'prepare', return_value=([], {}, [])), \
             patch.object(capture.subprocess, 'run', return_value=SimpleNamespace(stdout=b'Mach-O 64-bit executable arm64')), \
             patch.object(capture.subprocess, 'Popen') as spawn, \
             patch.object(capture, 'asset_identity', return_value={'ruff': 'fixture'}), \
             patch.object(capture, 'free_port', return_value=False):
            with self.assertRaisesRegex(ValueError, 'QA port already in use'):
                capture.execute(args, self.checker)
            spawn.assert_not_called()
        receipt = json.loads((args.out / 'receipt.json').read_bytes())
        self.assertEqual((receipt['unrun'], receipt['passed'], receipt['stage']), (697, False, 'port_check'))

    def test_cleanup_faults_preserve_wait_first_error_and_partial_rows(self):
        class Owner:
            def wait_after_signal(self, timeout): return -15
            def save(self): raise OSError('fixture')
        result = {'error': 'RuntimeError: first failure', 'rows': [
            {'matched': True, 'comparison': 'passed'}, {'matched': False, 'comparison': 'unrun'}]}
        proc = SimpleNamespace(pid=123)
        out = Path(self.temp.name) / 'cleanup'; out.mkdir()
        with patch.object(capture, 'free_port', return_value=True), patch.object(capture, 'group_gone', return_value=True), \
             patch.object(capture, 'read', side_effect=OSError('fixture')), \
             patch.object(capture, 'asset_identity', side_effect=ValueError('fixture')):
            capture.finalize_capture(result, proc, Owner(), out / 'som', out / 'assets', {}, [1, 2, 3], out)
        receipt = json.loads((out / 'receipt.json').read_bytes())
        self.assertEqual(receipt['error'], 'RuntimeError: first failure')
        self.assertEqual((receipt['wait_exit'], receipt['unrun'], receipt['passed']), (-15, 2, False))
        self.assertEqual([item['stage'] for item in receipt['cleanup_errors']],
                         ['owner_receipt', 'baseline_recheck', 'asset_recheck'])
        self.assertEqual(len(receipt['rows']), 2)

    def test_wait_failure_still_attempts_owner_receipt(self):
        class Owner:
            saved = False
            def wait_after_signal(self, timeout): raise TimeoutError('fixture')
            def save(self): self.saved = True
        owner = Owner(); out = Path(self.temp.name) / 'wait'; out.mkdir()
        binary = out / 'som'; self.put(binary, b'binary')
        result = {'error': None, 'rows': []}
        with patch.object(capture, 'free_port', return_value=True), patch.object(capture, 'group_gone', return_value=False), \
             patch.object(capture, 'asset_identity', return_value={}):
            capture.finalize_capture(result, SimpleNamespace(pid=123), owner, binary, out / 'assets', {}, [1], out)
        receipt = json.loads((out / 'receipt.json').read_bytes())
        self.assertTrue(owner.saved)
        self.assertEqual((receipt['wait_exit'], receipt['unrun'], receipt['passed']), (None, 1, False))
        self.assertEqual(receipt['cleanup_errors'][0]['stage'], 'direct_wait')

    def test_completed_wait_survives_owner_postwait_failure(self):
        class Owner:
            events = []
            def wait_after_signal(self, timeout):
                self.events.append({'kind': 'candidate-wait', 'state': 'completed', 'exit_code': -15})
                raise ValueError('postwait evidence failed')
            def save(self): pass
        out = Path(self.temp.name) / 'postwait'; out.mkdir()
        binary = out / 'som'; self.put(binary, b'binary')
        result = {'error': None, 'rows': []}
        with patch.object(capture, 'free_port', return_value=True), patch.object(capture, 'group_gone', return_value=True), \
             patch.object(capture, 'asset_identity', return_value={}):
            capture.finalize_capture(result, SimpleNamespace(pid=123), Owner(), binary, out / 'assets', {}, [1], out)
        receipt = json.loads((out / 'receipt.json').read_bytes())
        self.assertEqual((receipt['wait_exit'], receipt['unrun'], receipt['passed']), (-15, 1, False))
        self.assertEqual(receipt['cleanup_errors'][0]['stage'], 'direct_wait')

    def test_process_evidence_binds_listener_and_executable(self):
        out = Path(self.temp.name) / 'identity'; out.mkdir()
        binary = out / 'som'; self.put(binary, b'binary')
        proc = SimpleNamespace(pid=123)
        argv = [str(binary), 'serve']
        def response(command, **kwargs):
            if command[0] == '/bin/ps' and 'pid=,ppid=,pgid=,stat=,command=' in command:
                data = f'123 {os.getpid()} 123 S {" ".join(argv)}\n'.encode()
            elif command[0] == '/bin/ps':
                data = b'Thu Oct 29 12:00:00 2026\n'
            elif '-Fp' in command:
                data = b'p123\n'
            else:
                info = binary.stat()
                data = (f'p123\nftxt\nD{hex(info.st_dev)}\ni{info.st_ino}\nn{binary}\n').encode()
            return SimpleNamespace(returncode=0, stdout=data, stderr=b'')
        with patch.object(capture.subprocess, 'run', side_effect=response):
            identity = capture.process_evidence(proc, argv, binary, out, 'start')
            capture.process_evidence(proc, argv, binary, out, 'end', identity)
        self.assertEqual(identity['binary_inode'], binary.stat().st_ino)
        self.assertTrue((out / 'process-evidence/start-listeners.stdout').is_file())
        def wrong_listener(command, **kwargs):
            result = response(command, **kwargs)
            return SimpleNamespace(returncode=0, stdout=b'p999\n', stderr=b'') if '-Fp' in command else result
        with patch.object(capture.subprocess, 'run', side_effect=wrong_listener):
            with self.assertRaisesRegex(ValueError, 'listener'):
                capture.process_evidence(proc, argv, binary, out, 'wrong')

    def test_real_macos_macho_mapped_inode_and_listener(self):
        import subprocess
        binary = Path('/usr/bin/nc').resolve()
        description = subprocess.check_output(['/usr/bin/file', str(binary)]).decode()
        self.assertIn('Mach-O', description)
        self.assertTrue(capture.free_port(), 'fixture QA port must be unused')
        out = Path(self.temp.name) / 'real-macho'; out.mkdir()
        argv = [str(binary), '-l', '127.0.0.1', str(capture.PORT)]
        proc = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL, start_new_session=True)
        try:
            deadline = time.monotonic() + 15
            while capture.free_port():
                self.assertIsNone(proc.poll(), 'Mach-O fixture exited')
                self.assertLess(time.monotonic(), deadline, 'fixture listener did not start')
                time.sleep(0.1)
            identity = capture.process_evidence(proc, argv, binary, out, 'real')
            self.assertEqual(identity['binary_inode'], binary.stat().st_ino)
            with self.assertRaisesRegex(ValueError, 'mapped executable inode'):
                capture.process_evidence(proc, argv, Path('/bin/sleep').resolve(), out, 'negative')
        finally:
            if proc.poll() is None:
                proc.terminate()
            proc.wait(timeout=10)
        self.assertTrue(capture.free_port(), 'fixture listener must stop')


if __name__ == '__main__':
    unittest.main()

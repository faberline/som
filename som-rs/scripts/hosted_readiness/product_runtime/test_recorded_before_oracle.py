"""GitHub-only protocol fixtures. Synthetic authority never grants product acceptance."""
from __future__ import annotations
import hashlib
import copy
import http.client
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
from types import ModuleType
import unittest
import zipfile

if os.environ.get('GITHUB_ACTIONS') != 'true' or sys.platform != 'linux' or sys.version_info[:2] != (3, 12):
    raise RuntimeError('GitHub Linux Python3.12 fixtures only; never run locally')

REPO = Path(__file__).resolve().parents[4]
BASE = 'som-rs/scripts/hosted_readiness/'
SUBJECT = BASE + 'product_runtime/recorded_before_oracle.py'
LAUNCHER = BASE + 'run_current_candidate_gate.py'
REVISION = subprocess.check_output(['/usr/bin/git', '-C', str(REPO), 'rev-parse', 'HEAD']).decode().strip()


def bound(path, name):
    raw = (REPO / path).read_bytes()
    if raw != subprocess.check_output(['/usr/bin/git', '--no-replace-objects', '-C', str(REPO), 'cat-file', 'blob', REVISION + ':' + path]):
        raise RuntimeError('fixture subject differs from exact current Git')
    module = ModuleType(name)
    module.__file__, module.__cached__, module.__package__ = str(REPO / path), None, ''
    module.__som_raw_sha256__ = hashlib.sha256(raw).hexdigest()
    sys.modules[name] = module
    exec(compile(raw, module.__file__, 'exec', dont_inherit=True), module.__dict__)
    return module


q = bound(SUBJECT, '_recorded_oracle_fixture_subject')
p = bound(LAUNCHER, '_recorded_oracle_fixture_launcher')


def slot(number, case='one'):
    return {'slot_id': number, 'cohort': 'react_python', 'group': 'synthetic', 'case': case,
            'phase': None, 'form': 'json', 'variant': 'none', 'method': 'POST', 'endpoint': '/v1/compile'}


def entry(identity, request=b'{"x":1}', response=b'{"status":"ok"}\n', status=200):
    return {'identity': dict(identity), 'request': request, 'response': response,
            'status': status, 'source': {'role': 'synthetic protocol fixture only'}}


class ExactWireTests(unittest.TestCase):
    def test_reply_bytes_and_status_are_preserved_without_json_reencoding(self):
        raw = b'{ "status" : "ok", "x" : 1 }\n'
        s = slot(1)
        table = q.RecordedTable([s], [entry(s, response=raw, status=422)])
        status, actual, matches = table.lookup('POST', '/v1/compile', b'{"x":1}')
        self.assertEqual((status, actual), (422, raw))
        self.assertEqual(matches, [s])

    def test_equivalent_member_order_or_whitespace_cannot_replace_wire(self):
        s = slot(1)
        table = q.RecordedTable([s], [entry(s, request=b'{"a":1,"b":2}')])
        for raw in (b'{"b":2,"a":1}', b'{ "a":1,"b":2}', b'{"a":1,"b":2}\n'):
            with self.subTest(raw=raw), self.assertRaises(q.OracleError):
                table.lookup('POST', '/v1/compile', raw)

    def test_unicode_escape_and_normalized_values_do_not_create_raw_aliases(self):
        s = slot(1)
        table = q.RecordedTable([s], [entry(s, request='{"name":"é"}'.encode())])
        for raw in (b'{"name":"\\u00e9"}', '{"name":"é"}'.encode()):
            with self.subTest(raw=raw), self.assertRaises(q.OracleError):
                table.lookup('POST', '/v1/compile', raw)

    def test_method_endpoint_and_query_are_exact(self):
        s = slot(1); table = q.RecordedTable([s], [entry(s)])
        for method, path in [('GET', '/v1/compile'), ('POST', '/v1/assemble'), ('POST', '/v1/compile?x=1'), ('POST', '/v1/compile/')]:
            with self.subTest(method=method, path=path), self.assertRaises(q.OracleError):
                table.lookup(method, path, b'{"x":1}')

    def test_duplicate_named_slots_remain_distinct_without_invented_caller_identity(self):
        first, second = slot(1, 'first'), slot(2, 'second')
        table = q.RecordedTable([first, second], [entry(first), entry(second)])
        self.assertEqual(table.lookup('POST', '/v1/compile', b'{"x":1}')[2], [first, second])
        self.assertEqual(len(table.public()), 2)
        self.assertEqual(table.observed_keys()[0]['calls'], 1)
        self.assertEqual(table.observed_keys()[0]['matching_named_slot_ids'], [1, 2])

    def test_conflicting_status_or_raw_reply_is_refused_before_service(self):
        first, second = slot(1), slot(2)
        for changed in [entry(second, status=400), entry(second, response=b'{ "status":"ok" }')]:
            with self.subTest(changed=changed), self.assertRaises(q.OracleError):
                q.RecordedTable([first, second], [entry(first), changed])

    def test_duplicate_slot_id_or_changed_semantic_identity_is_refused(self):
        first, second = slot(1), slot(1, 'another')
        with self.assertRaises(q.OracleError):
            q.RecordedTable([first, second], [entry(first), entry(second)])
        changed = entry(first); changed['identity']['variant'] = 'python'
        with self.assertRaises(q.OracleError):
            q.RecordedTable([first], [changed])

    def test_json_duplicate_member_and_nonfinite_values_are_refused(self):
        for raw in (b'{"x":1,"x":2}', b'{"x":NaN}', b'{"x":Infinity}'):
            with self.subTest(raw=raw), self.assertRaises(q.OracleError):
                q.parse(raw)


class ArchiveTests(unittest.TestCase):
    def fixture(self, root, *, extra=False):
        root.mkdir()
        sealed, restored = root / 'sealed', root / 'restored'
        sealed.mkdir(); restored.mkdir()
        payload = b'{"synthetic":"payload"}\n'
        ciphertext = b'opaque fixture ciphertext, not an AES qualification'
        manifest = {'version': 'som.m6-sealed-inputs-manifest.v1', 'cipher_sha256': q.sha(ciphertext),
            'uncompressed_bytes': len(payload) + 128, 'total_files': 1,
            'files': [{'path': 'run/raw', 'sha256': q.sha(payload), 'bytes': len(payload), 'mode': 0o644}]}
        manifest_raw = q.json_bytes(manifest)
        index = {'version': 'som.m6-evidence-index.v1', 'key_id': 'synthetic', 'status': 'fixture', 'product_acceptance': 'not_run',
            'packages': [{'archive': 'package-000.sealed', 'manifest': 'package-000.manifest.json',
                'manifest_sha256': q.sha(manifest_raw), 'cipher_sha256': q.sha(ciphertext),
                'total_files': 1, 'bytes': len(payload)}]}
        index_raw = q.json_bytes(index)
        members = {'evidence-index.json': index_raw, 'package-000.manifest.json': manifest_raw, 'package-000.sealed': ciphertext}
        with zipfile.ZipFile(root / 'artifact.zip', 'w') as archive:
            for name, raw in members.items():
                archive.writestr(name, raw)
                (sealed / name).write_bytes(raw)
            if extra:
                archive.writestr('unexpected', b'extra')
        (restored / 'run').mkdir(); (restored / 'run/raw').write_bytes(payload)
        pin = {'id': 123, 'name': 'synthetic', 'size_in_bytes': (root / 'artifact.zip').stat().st_size,
            'digest': 'sha256:' + q.file_sha(root / 'artifact.zip'), 'run_id': 456,
            'head_sha': '1' * 40, 'index_sha256': q.sha(index_raw)}
        metadata = {**{name: pin[name] for name in ('id', 'name', 'size_in_bytes', 'digest')}, 'expired': False,
                    'workflow_run': {'id': 456, 'head_sha': '1' * 40, 'repository_id': 1390374878}}
        return pin, metadata, payload

    def test_whole_archive_original_manifest_and_payload_bytes_are_joined(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / 'artifact'; pin, metadata, payload = self.fixture(root)
            archive = q.Archive(root, pin, metadata)
            self.assertEqual(archive.read('run/raw'), payload)
            archive.verify()

    def test_raw_payload_bytes_are_distinct_from_encoded_envelope_and_cipher_size(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / 'artifact'; pin, metadata, payload = self.fixture(root)
            self.assertNotEqual(len(payload), (root / 'sealed/package-000.sealed').stat().st_size)
            self.assertNotEqual(len(payload), q.parse((root / 'sealed/package-000.manifest.json').read_bytes())['uncompressed_bytes'])
            self.assertEqual(q.Archive(root, pin, metadata).read('run/raw'), payload)

    def test_extra_zip_member_is_refused_even_with_a_matching_outer_digest(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / 'artifact'; pin, metadata, payload = self.fixture(root, extra=True)
            with self.assertRaises(q.OracleError):
                q.Archive(root, pin, metadata)

    def test_wrong_source_run_or_expired_metadata_is_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / 'artifact'; pin, metadata, payload = self.fixture(root)
            for changed in ({**metadata, 'expired': True}, {**metadata, 'workflow_run': {**metadata['workflow_run'], 'head_sha': '2' * 40}}):
                with self.subTest(changed=changed), self.assertRaises(q.OracleError):
                    q.Archive(root, pin, changed)

    def test_restored_symlink_cannot_replace_original_payload(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / 'artifact'; pin, metadata, payload = self.fixture(root)
            outside = Path(directory) / 'outside'; outside.write_bytes(payload)
            (root / 'restored/run/raw').unlink(); (root / 'restored/run/raw').symlink_to(outside)
            with self.assertRaises(q.OracleError):
                q.Archive(root, pin, metadata).read('run/raw')

    def test_final_source_mutation_is_detected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / 'artifact'; pin, metadata, payload = self.fixture(root)
            archive = q.Archive(root, pin, metadata); archive.read('run/raw')
            (root / 'restored/run/raw').write_bytes(b'changed')
            with self.assertRaises(q.OracleError):
                archive.verify()

    def test_sealed_manifest_mutation_cannot_hide_behind_a_valid_zip(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / 'artifact'; pin, metadata, payload = self.fixture(root)
            archive = q.Archive(root, pin, metadata)
            (root / 'sealed/package-000.manifest.json').write_bytes(b'changed')
            with self.assertRaises(q.OracleError):
                archive.verify()


class TwoPhaseTests(unittest.TestCase):
    def fixture(self, root):
        capture = {'version': 'synthetic.capture.v1', 'gate': 'm6-react003', 'helper_revision': '1' * 40,
            'candidate_revision': '1' * 40, 'product_acceptance_passed': False,
            'complete': False, 'all_required_passed': False, 'raw_logs_complete': False,
            'owned_waits_complete': False, 'owned_cleanup_complete': False}
        raw = p.canonical(capture) + b'\n'; (root / 'result.json').write_bytes(raw)
        proof = {'capture_result_sha256': p.sha(raw), 'synthetic_shared_proof': True}
        verified = {**capture, **{name: True for name in ('complete', 'all_required_passed', 'raw_logs_complete', 'owned_waits_complete', 'owned_cleanup_complete')},
                    'capture_result_sha256': p.sha(raw), 'shared_postclose_proof': proof}
        return capture, raw, proof, verified

    def test_new_verified_object_keeps_original_capture_bytes_unchanged(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); capture, raw, proof, verified = self.fixture(root)
            p.validate_owner_result(verified, 'm6-react003', '1' * 40)
            p.verify_owner_phase_join(root, raw, verified, proof)
            self.assertNotEqual(capture, verified)
            self.assertEqual((root / 'result.json').read_bytes(), raw)

    def test_capture_cannot_claim_shared_cleanup_before_close(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); capture, raw, proof, verified = self.fixture(root)
            capture['owned_cleanup_complete'] = True
            raw = p.canonical(capture); (root / 'result.json').write_bytes(raw)
            with self.assertRaises(ValueError):
                p.verify_owner_phase_join(root, raw, verified, proof)


    def test_same_capture_object_or_unbound_shared_proof_is_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); capture, raw, proof, verified = self.fixture(root)
            for value in (capture, {**verified, 'capture_result_sha256': '0' * 64}, {**verified, 'shared_postclose_proof': {}}):
                with self.subTest(value=value), self.assertRaises(ValueError):
                    p.verify_owner_phase_join(root, raw, value, proof)


    def test_overwritten_capture_cannot_pass_a_matching_old_digest_claim(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); capture, raw, proof, verified = self.fixture(root)
            (root / 'result.json').write_bytes(raw + b'\n')
            with self.assertRaises(ValueError):
                p.verify_owner_phase_join(root, raw, verified, proof)


class NativeStartupProtocolTests(unittest.TestCase):
    """Synthetic protocol mutations; full native Context qualification is separate."""
    def fixture(self):
        revision = '1' * 40
        requested, actual = p.recorded_python_arguments('/runtime/install/python/bin/python3.12', '/source',
            revision, revision, 'profile.json', '/inputs', '/owned/oracle', '2' * 64)
        sources = {p.CONTEXT: b'context fixture bytes', p.ORACLE: b'oracle fixture bytes'}
        startup = {'version': 'som.native-python-child-startup.v1', 'pid': 123,
            'actual_argv': actual, 'original_requested_argv': requested, 'cwd': '/owned',
            'helper_revision': revision, 'candidate_revision': revision, 'product_acceptance_passed': False}
        post = {'version': 'som.native-python-child-postcheck.v1', 'actual_argv': actual,
            'original_python_arguments': requested[4:], 'helper_revision': revision, 'candidate_revision': revision,
            'entry_executed': True, 'postchecks_passed': True, 'exit_code': 0, 'product_acceptance_passed': False,
            'payload': {'version': 'som.native-python-startup-payload.v1', 'role': 'qualified-base-no-site',
                'python': actual[0], 'site': None, 'pth_executed': False, 'product_acceptance_passed': False},
            'source_sha256': {name: p.sha(raw) for name, raw in sources.items()}}
        options = {'requested': requested, 'actual': actual, 'pid': 123, 'cwd': '/owned',
                   'revision': revision, 'source_reader': sources.__getitem__, 'exit_code': 0}
        return startup, post, options

    def test_direct_oracle_request_uses_the_existing_native_bootstrap_protocol(self):
        startup, post, options = self.fixture()
        self.assertEqual(options['actual'][1:7], ['-I', '-S', '-B', '/source/' + p.FULL_DRIVER,
                                                '--native-python-bootstrap', '--'])
        self.assertEqual(options['requested'][4], '/source/' + p.ORACLE)
        self.assertNotIn('/source/' + p.BOUND, options['actual'])
        p.validate_recorded_python_evidence(startup, post, **options)

    def test_actual_argv_or_original_request_mutation_is_refused(self):
        startup, post, options = self.fixture()
        for key in ('actual_argv', 'original_requested_argv'):
            changed = copy.deepcopy(startup); changed[key].append('--foreign')
            with self.subTest(key=key), self.assertRaises(ValueError):
                p.validate_recorded_python_evidence(changed, post, **options)

    def test_nonzero_signal_or_boolean_terminal_exit_cannot_pass(self):
        startup, post, options = self.fixture()
        for code in (1, -9, -15, False, None):
            changed = dict(options, exit_code=code)
            with self.subTest(code=code), self.assertRaises(ValueError):
                p.validate_recorded_python_evidence(startup, post, **changed)

    def test_missing_or_failed_terminal_postcheck_cannot_borrow_oracle_success(self):
        startup, post, options = self.fixture()
        mutations = [{}, {**post, 'postchecks_passed': False}, {**post, 'entry_executed': False},
                     {**post, 'exit_code': 1}, {**post, 'postcheck_error': 'source changed'},
                     {**post, 'actual_argv': options['requested']}]
        for changed in mutations:
            with self.subTest(changed=changed), self.assertRaises(ValueError):
                p.validate_recorded_python_evidence(startup, changed, **options)

    def test_terminal_source_or_payload_authority_mutation_is_refused(self):
        startup, post, options = self.fixture()
        for change in ('source-missing', 'source-digest', 'payload-site', 'payload-role'):
            changed = copy.deepcopy(post)
            if change == 'source-missing': changed['source_sha256'] = {}
            elif change == 'source-digest': changed['source_sha256'][p.ORACLE] = '0' * 64
            elif change == 'payload-site': changed['payload']['site'] = '/ambient/site-packages'
            else: changed['payload']['role'] = 'observer'
            with self.subTest(change=change), self.assertRaises(ValueError):
                p.validate_recorded_python_evidence(startup, changed, **options)


class RealHttpProtocolTests(unittest.TestCase):
    def test_real_owned_python_preserves_raw_reply_and_records_an_internal_miss(self):
        # This is a controlled one-slot protocol fixture. It deliberately has
        # no production data proof and cannot pass the 4,572-slot verifier.
        program = r'''
import hashlib,os,subprocess,sys
from pathlib import Path
from types import ModuleType
repo,revision,subject,out=sys.argv[1:]
raw=subprocess.check_output(['/usr/bin/git','--no-replace-objects','-C',repo,'cat-file','blob',revision+':'+subject])
assert raw==(Path(repo)/subject).read_bytes()
q=ModuleType('_owned_http_fixture_subject')
q.__file__,q.__cached__,q.__package__=str(Path(repo)/subject),None,''
q.__som_raw_sha256__=hashlib.sha256(raw).hexdigest()
sys.modules[q.__name__]=q
exec(compile(raw,q.__file__,'exec',dont_inherit=True),q.__dict__)
s={'slot_id':1,'cohort':'react_python','group':'synthetic','case':'one','phase':None,'form':'json','variant':'none','method':'POST','endpoint':'/v1/compile'}
table=q.RecordedTable([s],[{'identity':s,'request':b'{"x":1}','response':b'{ "reply" : "exact" }\n','status':200,'source':{'role':'synthetic protocol only'}}])
health={'role':q.ROLE,'actual_runtime':'qualified-linux-python','source_compiler':{'binary_sha256':q.COMPILER_SHA,'source_revision':q.COMPILER_SOURCE},'fixture_authority':'synthetic; not a qualified runtime or product'}
raise SystemExit(q.serve(table,Path(out),health,lambda:None))
'''
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); output = root / 'oracle'
            with (root / 'server.log').open('xb') as log:
                child = subprocess.Popen([str(Path(sys.executable).resolve()), '-I', '-S', '-B', '-c', program,
                    str(REPO), REVISION, SUBJECT, str(output)], stdout=log, stderr=subprocess.STDOUT,
                    stdin=subprocess.DEVNULL, start_new_session=True)
                def request(method, body=None):
                    connection = http.client.HTTPConnection('127.0.0.1', q.PORT, timeout=1)
                    try:
                        connection.request(method, '/healthz' if method == 'GET' else '/v1/compile', body=body)
                        response = connection.getresponse()
                        return response.status, response.read()
                    finally:
                        connection.close()
                try:
                    deadline = time.monotonic() + 20
                    while True:
                        self.assertIsNone(child.poll(), 'owned fixture process exited before listening')
                        try:
                            status, health = request('GET')
                            self.assertEqual(status, 200)
                            break
                        except OSError:
                            if time.monotonic() >= deadline:
                                raise
                            time.sleep(0.05)
                    status, raw = request('POST', b'{"x":1}')
                    self.assertEqual((status, raw), (200, b'{ "reply" : "exact" }\n'))
                    status, raw = request('POST', b'{ "x":1}')
                    self.assertEqual(status, 500)
                    self.assertEqual(json.loads(raw)['code'], 'INTERNAL_ORACLE_MISS')
                    self.assertIs(json.loads(raw)['compiler_refusal'], False)
                finally:
                    if child.poll() is None:
                        child.send_signal(signal.SIGTERM)
                    try:
                        code = child.wait(timeout=15)
                    except subprocess.TimeoutExpired:
                        child.kill(); child.wait(timeout=10)
                        raise
            self.assertEqual(code, 1)  # One internal miss keeps the run failed.
            result = json.loads((output / 'result.json').read_bytes())
            self.assertEqual(result['misses'], 1)
            self.assertIs(result['table_complete'], False)
            self.assertIs(result['product_acceptance_passed'], False)
            self.assertEqual(result['runtime_named_coverage'], 'owner_verifier_required')
            self.assertEqual((output / 'calls/000001.request.raw').read_bytes(), b'{"x":1}')
            self.assertEqual((output / 'calls/000001.response.raw').read_bytes(), b'{ "reply" : "exact" }\n')
            self.assertEqual((output / 'calls/000002.request.raw').read_bytes(), b'{ "x":1}')
            self.assertEqual(result['calls'][1]['matching_named_slots'][0]['slot_id'], 1)
            self.assertEqual(result['calls'][2]['matching_named_slots'], [])




if __name__ == '__main__':
    unittest.main(verbosity=2)

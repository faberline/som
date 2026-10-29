"""GitHub-only process, raw protocol and port fixtures for the draft collector."""
import json
import os
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent / 'audit'))
import tempfile
import unittest
import urllib.request

if os.environ.get('GITHUB_ACTIONS') != 'true' or os.environ.get('RUNNER_ENVIRONMENT') != 'github-hosted':
    raise RuntimeError('GitHub-hosted runner required')

import record_rust006_http as recorder
import mac_rust006_extra as extra
import mac_rust006_cluster as cluster
import run_hosted_acceptance as hosted


class HostedAdmissionTests(unittest.TestCase):
    def test_final_adapter_cannot_be_activated_before_review(self):
        self.assertFalse(hosted.FINAL_MAC_ADAPTER_REVIEWED)
        with self.assertRaises(SystemExit):
            hosted.main(['--out', '.local/m6-hosted/unused', '--candidate-revision', 'a' * 40])


class RawProtocolTests(unittest.TestCase):
    def test_only_candidate_loopback_and_frozen_routes(self):
        for url in ('http://127.0.0.1:18919/v1/compile', 'http://localhost:18919/v1/assemble'):
            request = urllib.request.Request(url, data=b'{}', method='POST')
            self.assertEqual(recorder.request_identity(request)[2], b'{}')
        for url in ('http://127.0.0.1:8765/v1/compile', 'http://127.0.0.1:18765/v1/compile',
                    'http://127.0.0.1:18917/v1/compile', 'http://example.com:18919/v1/compile',
                    'http://127.0.0.1:18919/other', 'http://127.0.0.1:18919/v1/compile?x=1'):
            with self.subTest(url=url), self.assertRaises(recorder.RecordingError):
                recorder.request_identity(urllib.request.Request(url, data=b'{}', method='POST'))

    def test_record_preserves_raw_request_and_response(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp).resolve()
            (out / 'calls').mkdir()
            owner = recorder.Recorder(out)
            call = recorder.Call(owner, ('POST', 'http://127.0.0.1:18919/v1/compile', b'original'), 1)
            call.record['status'] = 400
            call.append(b'{"code":"ASSET_HASH_MISMATCH"}')
            call.finish(complete=True)
            row = json.loads((out / 'calls/000001.json').read_bytes())
            self.assertEqual((out / row['request_file']).read_bytes(), b'original')
            self.assertEqual((out / row['response_file']).read_bytes(), b'{"code":"ASSET_HASH_MISMATCH"}')
            self.assertEqual(row['status'], 400)
            self.assertTrue(row['complete'])


class WaitEvidenceTests(unittest.TestCase):
    def test_actual_child_waits_and_keeps_failure_logs(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp).resolve()
            (out / 'logs').mkdir()
            row = extra.record_command(out, 1, 'bad', [sys.executable, '-c',
                'import sys; print("out"); print("err",file=sys.stderr); sys.exit(7)'], out, dict(os.environ), 10)
            self.assertEqual(row['state'], 'completed')
            self.assertEqual(row['wait_result'], 7)
            self.assertEqual(row['exit_code'], 7)
            self.assertEqual(row['cleanup'], 'requires-independent-raw-audit')
            self.assertEqual((out / row['stdout']).read_bytes(), b'out\n')
            self.assertEqual((out / row['stderr']).read_bytes(), b'err\n')

    def test_timeout_is_never_completed(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp).resolve()
            (out / 'logs').mkdir()
            row = extra.record_command(out, 1, 'slow', [sys.executable, '-c', 'import time; time.sleep(30)'],
                                       out, dict(os.environ), 0.5)
            self.assertEqual(row['state'], 'timeout')
            self.assertIsNone(row['wait_result'])
            self.assertEqual(row['cleanup'], 'unverified')
            self.assertIsInstance(row['cleanup_wait_result'], int)


class IndependentRawTests(unittest.TestCase):
    def fixture(self, root):
        import hashlib
        import audit_mac_rust006_extra as audit
        def h(raw): return hashlib.sha256(raw).hexdigest()
        script = root / 'som-rs/tests/rust-output-v1/validate_cases.py'
        script.parent.mkdir(parents=True)
        script.write_bytes(b'fixed-checker')
        cases = script.parent / 'cases'
        cases.mkdir()
        prompt = {'name':'sample'}
        prompt_raw = json.dumps(prompt).encode()
        cnl = b'create sample'
        (cases/'one.prompt.json').write_bytes(prompt_raw)
        (cases/'one.cnl').write_bytes(cnl)
        index = {'cases':[{'id':'one','prompt':'one.prompt.json','cnl':'one.cnl',
                          'prompt_file_sha256':h(prompt_raw),'cnl_sha256':h(cnl)}]}
        (cases/'index.json').write_text(json.dumps(index))
        files = {'main.py':'print(1)\n'}
        frozen = {'cases':[{'id':'one','prompt_sha256':'abc','python_files':{'main.py':h(files['main.py'].encode())}}]}
        evidence = root / 'som-rs/evidence/rust-output-v1/coverage-cases-python-2026-10-15.json'
        evidence.parent.mkdir(parents=True)
        evidence.write_text(json.dumps(frozen))
        bundle = {'hashes':{'prompt_sha256':'abc'},'value':'fixed'}
        raw = root / 'run'
        (raw/'calls').mkdir(parents=True)
        requests = [json.dumps({'format':'som.prompt-json.v1','prompt':prompt}).encode(),
                    json.dumps({'format':'som.cnl.v1','source':cnl.decode()}).encode(),
                    json.dumps({'bundle':bundle}).encode()]
        responses = [json.dumps({'bundle':bundle}).encode(), json.dumps({'bundle':bundle}).encode(),
                     json.dumps({'files':files}).encode()]
        rows=[]
        for i,(request,response) in enumerate(zip(requests,responses),1):
            req=f'calls/{i:06d}.request.raw'; res=f'calls/{i:06d}.response.raw'
            (raw/req).write_bytes(request); (raw/res).write_bytes(response)
            row={'id':i,'method':'POST','url':audit.QA+('/v1/assemble' if i==3 else '/v1/compile'),
                 'request_file':req,'request_sha256':h(request),'status':200,'response_file':res,
                 'response_sha256':h(response),'error':None,'complete':True}
            (raw/f'calls/{i:06d}.json').write_text(json.dumps(row))
            rows.append(row)
        manifest={'version':'som.m6-rust006-http-recording.v1',
                  'script':{'path':'som-rs/tests/rust-output-v1/validate_cases.py','sha256':h(b'fixed-checker')},
                  'exit_code':0,'calls':rows,'recording_errors':[]}
        (raw/'manifest.json').write_text(json.dumps(manifest))
        return raw

    def test_coverage_raw_checked_without_success_booleans(self):
        import audit_mac_rust006_extra as audit
        from unittest import mock
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp).resolve()
            raw=self.fixture(root)
            with mock.patch.object(audit,'REPO',root):
                self.assertEqual(audit.audit_cases(raw),{'cases':1,'raw_calls':3})
                response=raw/'calls/000003.response.raw'
                response.write_bytes(b'{"files":{"main.py":"tampered"}}')
                with self.assertRaises(ValueError): audit.audit_cases(raw)

    def test_refusal_rehash_and_pointer_are_derived_from_request(self):
        import audit_mac_rust006_extra as audit
        bundle={'version':'v','prompt':{'key':'x'},'dsl':{'files':[]},'assets':{'x':1},
                'hashes':{'prompt_sha256':'stale','dsl_sha256':'stale','payload_sha256':'stale'}}
        changed=audit.rehash(bundle)
        self.assertEqual(changed['hashes']['prompt_sha256'], audit.h(changed['prompt']))
        self.assertEqual(changed['hashes']['dsl_sha256'], audit.h(changed['dsl']))
        self.assertEqual(changed['hashes']['payload_sha256'], audit.h({k:v for k,v in changed.items() if k!='hashes'}))
        self.assertEqual(audit.pointer({'bundle':{'a/b':[{'~name':'wire'}]}}, '/bundle/a~1b/0/~0name'), 'wire')
        self.assertEqual(bundle['hashes']['payload_sha256'], 'stale')

    def test_final_audit_stays_incomplete(self):
        import audit_mac_rust006_extra as audit
        self.assertEqual(audit.ALLOWED, {'backend','block_library','catalog','compiler_contract','selection_policy'})


if __name__ == '__main__':
    unittest.main(verbosity=2)

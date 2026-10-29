"""GitHub-only source fixtures. These do not qualify the actual private data."""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import ModuleType
from types import SimpleNamespace
import unittest
from unittest import mock
import warnings
import zipfile

REPO=Path(__file__).resolve().parents[3]
ENTRY='som-rs/scripts/hosted_readiness/audit_recorded_before_raw.py'


def subject():
    revision=os.environ['HELPER_REVISION']
    raw=subprocess.check_output(['/usr/bin/git','--no-replace-objects','-C',str(REPO),'cat-file','blob',revision+':'+ENTRY])
    if raw!=(REPO/ENTRY).read_bytes():raise ValueError('working audit source differs from actual tested Git blob')
    module=ModuleType('_som_independent_raw_audit_fixture_subject');module.__file__=str(REPO/ENTRY)
    exec(compile(raw,module.__file__,'exec',dont_inherit=True),module.__dict__)
    return module


class IndependentRawAuditSource(unittest.TestCase):
    def test_producer_job_uses_only_the_fixed_endpoint_and_refuses_changed_identity(self):
        module=subject();calls=[];checks=[]
        job={'id':module.PRODUCER_JOB,'run_id':module.PRODUCER_RUN,'run_attempt':1,
            'head_sha':module.PRODUCER_HEAD,'name':'recorded-before-data','status':'completed',
            'conclusion':'success','steps':[{'status':'completed','conclusion':'success'}]}
        class Response:
            status=200
            def __enter__(self):return self
            def __exit__(self,*args):return False
            def read(self,limit):
                self_case.assertEqual(limit,1024**2+1)
                return json.dumps(current[0]).encode()
        class Opener:
            def open(self,request,timeout):
                calls.append(request.full_url);self_case.assertEqual(timeout,30)
                return Response()
        self_case=self;current=[job]
        source=SimpleNamespace(verify=lambda:checks.append('actual-source-double'))
        with mock.patch.dict(os.environ,{'SOM_DEPENDENCY_ACTIONS_TOKEN':'synthetic-read-only-token'}), \
             mock.patch.object(module,'build_opener',return_value=Opener()) as opened:
            self.assertEqual(module.producer_job(source),job)
            for key,value in [('id',1),('run_id',1),('run_attempt',2),('head_sha','0'*40),
                              ('name','unlisted-job'),('conclusion','failure'),('steps',[])]:
                current[0]={**job,key:value}
                with self.subTest(key=key),self.assertRaises(ValueError):module.producer_job(source)
            handler=opened.call_args.args[0]
            with self.assertRaisesRegex(ValueError,'redirect refused'):handler.redirect_request()
        self.assertTrue(calls)
        self.assertEqual(set(calls),{'https://api.github.com/repos/faberline/som/actions/jobs/'+str(module.PRODUCER_JOB)})
        self.assertGreater(len(checks),1)

    def test_safe_failure_diagnostic_keeps_locations_without_message_body_or_key(self):
        module=subject();secret='synthetic-private-key-and-request-body-must-not-be-published'
        try:raise RuntimeError(secret)
        except RuntimeError as original:diagnostic=module.safe_failure_diagnostic('prepare',original)
        raw=json.dumps(diagnostic)
        self.assertNotIn(secret,raw);self.assertEqual(diagnostic['phase'],'prepare')
        self.assertEqual(diagnostic['error_type'],'RuntimeError')
        self.assertFalse(diagnostic['exception_message_or_payload_published'])
        self.assertTrue(diagnostic['source_frames'])
        self.assertEqual(set(diagnostic['source_frames'][0]),{'file','line','function'})
        self.assertIsInstance(diagnostic['source_frames'][0]['line'],int)
        with self.assertRaises(ValueError):module.safe_failure_diagnostic(secret,RuntimeError(secret))

    def test_real_command_keeps_full_streams_terminal_wait_and_original_timeout(self):
        module=subject()
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory).resolve();environment=module.bounded_env()
            argv=[sys.executable,'-I','-S','-B','-c',"import sys;sys.stdout.write('whole stdout');sys.stderr.write('whole stderr')"]
            record=module.command(argv,root,root/'positive',environment=environment)
            self.assertTrue(record['wait_recorded']);self.assertTrue(record['group_gone'])
            self.assertEqual(record['exit_code'],0)
            self.assertEqual((root/'positive/stdout.raw').read_bytes(),b'whole stdout')
            self.assertEqual((root/'positive/stderr.raw').read_bytes(),b'whole stderr')
            with self.assertRaises(ValueError):
                module.command([sys.executable,'-I','-S','-B','-c','raise SystemExit(7)'],root,root/'negative',environment=environment)
            negative=json.loads((root/'negative/command.json').read_bytes())
            self.assertEqual(negative['exit_code'],7);self.assertTrue(negative['wait_recorded'])
            with self.assertRaises(subprocess.TimeoutExpired):
                module.command([sys.executable,'-I','-S','-B','-c','import time;time.sleep(20)'],root,root/'timeout',
                    environment=environment,timeout=0.05)
            timeout=json.loads((root/'timeout/command.json').read_bytes())
            self.assertTrue(timeout['wait_recorded']);self.assertTrue(timeout['group_gone'])
            self.assertEqual(timeout['first_error']['type'],'TimeoutExpired')
            self.assertIsInstance(timeout['exit_code'],int)

    def test_real_producer_receipt_requires_actual_source_run_and_false_native_roles(self):
        module=subject()
        receipt={'source_revision':module.PRODUCER_HEAD,'run_id':str(module.PRODUCER_RUN),'run_attempt':'1',
            'scope':'recorded-before-data-only-v1','actual_new_evidence_roundtrip_complete':True,
            'data_factory_qualified':True,'native_qa_source_admission_complete':False,
            'native_Context_or_service_qualified':False,'product_acceptance_passed':False}
        module.producer_receipt(receipt)
        for key,value in [('source_revision','0'*40),('run_id','1'),('run_attempt','2'),
                          ('native_qa_source_admission_complete',True),('native_Context_or_service_qualified',True),
                          ('product_acceptance_passed',True),('actual_new_evidence_roundtrip_complete',False)]:
            with self.subTest(key=key),self.assertRaises(ValueError):module.producer_receipt({**receipt,key:value})

    def test_full_zip_reader_rejects_duplicate_alias_traversal_and_real_CRC_damage(self):
        module=subject()
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            good=root/'good.zip'
            with zipfile.ZipFile(good,'w',compression=zipfile.ZIP_STORED) as z:z.writestr('raw/value.bin',b'actual-data-control')
            rows=module.zip_inventory(good)
            self.assertEqual(rows[0]['sha256'],hashlib.sha256(b'actual-data-control').hexdigest())
            for label,names in [('duplicate',['same','same']),('traversal',['../escape'])]:
                path=root/(label+'.zip')
                with warnings.catch_warnings():
                    warnings.simplefilter('ignore',UserWarning)
                    with zipfile.ZipFile(path,'w') as z:
                        for name in names:z.writestr(name,b'control')
                with self.subTest(label=label),self.assertRaises(ValueError):module.zip_inventory(path)
            alias=root/'alias.zip';info=zipfile.ZipInfo('alias');info.create_system=3;info.external_attr=0o120777<<16
            with zipfile.ZipFile(alias,'w') as z:z.writestr(info,b'outside')
            with self.assertRaises(ValueError):module.zip_inventory(alias)
            bad=root/'bad.zip';raw=bytearray(good.read_bytes());offset=raw.index(b'actual-data-control');raw[offset]^=1;bad.write_bytes(raw)
            with self.assertRaises(zipfile.BadZipFile):module.zip_inventory(bad)

    def test_physical_reader_refuses_real_hardlink_and_symlink_without_changing_target(self):
        module=subject()
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);target=root/'target';target.write_bytes(b'original control')
            alias=root/'alias';alias.symlink_to(target)
            with self.assertRaises(ValueError):module.raw_file(alias)
            alias.unlink();os.link(target,alias)
            with self.assertRaises(ValueError):module.raw_file(target)
            self.assertEqual(target.read_bytes(),b'original control')

    def test_exact_old_qualification_keeps_source_role_and_no_runtime_or_product_promotion(self):
        module=subject();proof={'inventory_sha256':'a'*64,'table_sha256':'b'*64};metadata={'role':'original saved source only'}
        result=module.original_qualification(proof,metadata,'c'*64)
        self.assertEqual(result['source_revision'],module.PRODUCER_HEAD)
        self.assertEqual(result['named_slots'],4572)
        self.assertEqual(result['original_calibrated_slots']+result['fresh_before_slots'],4572)
        self.assertEqual(result['source_compiler'],metadata)
        self.assertEqual(result['runtime_comparison'],'unrun')
        for name in ('native_qa_source_admission_complete','native_Context_or_service_qualified','product_acceptance_passed',
                     'original_inputs_in_new_evidence_envelope','original_inputs_are_new_observations'):
            self.assertIs(result[name],False)


if __name__=='__main__':unittest.main()

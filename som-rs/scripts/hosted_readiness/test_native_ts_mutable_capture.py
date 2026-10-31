"""GitHub-only Core checks around real mutable capture functions.

Real Core, the whole successor checker and whole RawCapture source run.
The reused checker fixture has explicit Source/Context/admission/runtime,
original CLI/HTTP/replay, digest, schema and host doubles. All recorded capture
bytes below are real test data. No tool, server, real Context or product passes.
"""
from __future__ import annotations
import copy,hashlib,json,os,subprocess
from pathlib import Path
import sys
from types import ModuleType
import unittest
if os.environ.get('GITHUB_ACTIONS')!='true':raise RuntimeError('GitHub tests only')
assert sys.flags.isolated and sys.flags.no_site and sys.dont_write_bytecode
path=Path(__file__).absolute().with_name('test_native_ts_checker_targets.py')
raw=path.read_bytes()
assert hashlib.sha256(raw).hexdigest()=='7ec49ee0fb0ea78c5b78458cdeaca1e31ef3d81e277514b7c0cd15c3bbc4201a'
checker_protocol=ModuleType('_som_ts_mutable_capture_checker_protocol')
checker_protocol.__file__,checker_protocol.__package__,checker_protocol.__cached__=str(path),'',None
exec(compile(raw,str(path),'exec',dont_inherit=True),checker_protocol.__dict__)
BASE='som-rs/scripts/hosted_readiness/'
CLI='som-rs/scripts/rust_output_acceptance/check_cli.py'
HTTP='som-rs/som-targets/qa/check_ts_products.py'
REPLAY='som-rs/scripts/rust_output_acceptance/python_replay.py'
CLI_SOURCE="""from _som_native_active_controls_binding import controls
from pathlib import Path
from subprocess import CompletedProcess
DONE=CompletedProcess(['som','compile','fixture.cnl'],0,b'{"ok":true}',b'')
SENT=[('/v1/compile',b'{"prompt":{}}')]
def _run_cli_with_request_capture(binary,base_url,cnl_path,selected):return DONE,SENT
def check(*args,**kwargs):
    assert kwargs['verify_request'] is True
    path=Path(__file__).with_name('capture-fixture.cnl');path.write_bytes(b'original test source')
    return _run_cli_with_request_capture('som','http://127.0.0.1:18978',path,'typescript')
def main():
    controls.verify_installed()
    done,sent=check()
    assert done is DONE and sent is SENT
    controls.verify_installed()
    return 0
"""
HTTP_MAIN="""
def main():
    controls.verify_installed()
    returned=ts_suite.http_send('http://127.0.0.1:18978','POST','/api/probe',b'{"x":1}')
    assert returned==(200,'application/json',b'{}')
    controls.verify_installed()
    return 0
"""
REPLAY_SOURCE="""from _som_native_active_controls_binding import controls
ORIGINAL_CALLS=[]
def asset_only_difference(expected,current):
    ORIGINAL_CALLS.append((expected,current))
    return None
def main():
    controls.verify_installed()
    cells=dict(zip(asset_only_difference.__code__.co_freevars,asset_only_difference.__closure__))
    ctx=cells['ctx'].cell_contents
    provider=cells['replay_digest'].cell_contents
    ctx.cache.setdefault('protocol_replay_digest_ids',[]).append(['main-before',id(provider)])
    assert asset_only_difference(b'historical',b'current') is None
    assert cells['replay_digest'].cell_contents is provider
    ctx.cache['protocol_replay_digest_ids'].append(['main-after',id(provider)])
    assert ORIGINAL_CALLS==[(b'historical',b'current')]
    controls.verify_installed()
    return 0
"""
DIGEST_SOURCE="""import sys
def authorities(ctx,target):
    checker=sys.modules['_som_ts_native_original_python_replay']
    callback=checker.asset_only_difference
    cells=dict(zip(callback.__code__.co_freevars,callback.__closure__))
    provider=cells['replay_digest'].cell_contents
    ctx.cache.setdefault('protocol_replay_digest_ids',[]).append(['preload.'+target,id(provider)])
    return (None,None,None,{})
def historical_python_replay(ctx,expected,current,original_check):
    ctx.cache['protocol_replay_digest_ids'].append(['replay',id(sys.modules[__name__])])
    result=original_check(expected,current)
    return result,{'role':'protocol-source-factory','original_historical_assertion_passed':result is None,'product_acceptance_passed':False}
"""

def replace_source(f,path,source):
    raw=source.encode()if type(source)is str else source
    f.raw[path]=raw;(f.root/path).write_bytes(raw)
    f.policy['sources'][path]['sha256']=hashlib.sha256(raw).hexdigest()

def fixture(case,tool=None):
    f=checker_protocol.CheckerFixture(case)
    if tool==CLI:replace_source(f,CLI,CLI_SOURCE)
    if tool==HTTP:replace_source(f,HTTP,checker_protocol.TS_TARGET_SOURCE+HTTP_MAIN)
    if tool==REPLAY:
        replace_source(f,REPLAY,REPLAY_SOURCE)
        replace_source(f,checker_protocol.DIGEST,DIGEST_SOURCE)
        for key,role,name in (
                ('typescript_regression_native_replay_digests','ctx.typescript-regression-native-replay-digests','_som_full_typescript_regression_native_replay_digests'),
                ('typescript_regression_preloaded_digests','ctx.typescript-regression-preloaded-digests','_som_full_typescript_regression_preloaded_digests')):
            f.rows[role]=copy.deepcopy(f.rows['test.checker.digests'])
            f.rows[role]['module']=name
            f.policy['context_keys'][key]=role
        for row in f.rows.values():row['closure']=sorted(f.rows)
    return f

class MutableCaptureCoreTests(unittest.TestCase):
    def test_actual_CLI_capture_mutates_rows_between_Core_checks_and_restores_owner(self):
        f=fixture(self,CLI);self.assertEqual(f.execute(CLI),0)
        post=json.loads((f.ctx.work/'capture/checker-postcheck.json').read_bytes());self.assertTrue(post['postcheck_complete'])
        self.assertEqual(len(post['cli_rows']),1);row=post['cli_rows'][0]
        root=f.ctx.work/'capture/cli-raw/000000'
        self.assertEqual((root/'stdout.raw').read_bytes(),b'{"ok":true}')
        self.assertEqual((root/'stderr.raw').read_bytes(),b'')
        self.assertEqual((root/'source.raw').read_bytes(),b'original test source')
        self.assertEqual((root/'request-000.raw').read_bytes(),b'{"prompt":{}}')
        self.assertEqual(json.loads((root/'result.json').read_bytes()),row)
        target=f.domain.records[checker_protocol.TABLE[CLI]['role']].module
        self.assertEqual(target._run_cli_with_request_capture.__module__,target.__name__)
        f.controls.verify_installed();f.finish()

    def test_actual_product_HTTP_capture_mutates_rows_between_Core_checks_and_restores_owner(self):
        f=fixture(self,HTTP);self.assertEqual(f.execute(HTTP),0)
        post=json.loads((f.ctx.work/'capture/checker-postcheck.json').read_bytes());self.assertTrue(post['postcheck_complete'])
        self.assertEqual(len(post['product_http_rows']),1);row=post['product_http_rows'][0]
        root=f.ctx.work/'capture/product-http/000000'
        self.assertEqual((root/'request.raw').read_bytes(),b'{"x":1}');self.assertEqual((root/'response.raw').read_bytes(),b'{}')
        self.assertEqual(json.loads((root/'result.json').read_bytes()),row)
        self.assertEqual(f.ctx.host_events,['configure','context','close'])
        f.controls.verify_installed();f.finish()

    def test_actual_current_difference_mutates_source_owned_outcome_between_Core_checks(self):
        f=fixture(self,REPLAY);self.assertEqual(f.execute(REPLAY),0)
        post=json.loads((f.ctx.work/'capture/checker-postcheck.json').read_bytes());self.assertTrue(post['postcheck_complete'])
        self.assertEqual(post['native_python_replay_proofs'],[{'role':'protocol-source-factory',
            'original_historical_assertion_passed':True,'product_acceptance_passed':False}])
        self.assertEqual(post['exit_code'],0);self.assertFalse(post['product_acceptance_passed'])
        self.assertIsNot(f.domain.records['ctx.typescript-regression-native-replay-digests'].module,f.domain.records['ctx.typescript-regression-preloaded-digests'].module)
        observed=f.ctx.cache['protocol_replay_digest_ids']
        self.assertEqual([row[0]for row in observed],['preload.python','preload.rust','preload.typescript','main-before','replay','main-after'])
        self.assertTrue(all(row[1]==id(f.domain.records['ctx.typescript-regression-native-replay-digests'].module)for row in observed))
        target=f.domain.records[checker_protocol.TABLE[REPLAY]['role']].module
        self.assertEqual(target.asset_only_difference.__module__,target.__name__)
        f.controls.verify_installed();f.finish()

    def test_source_owned_containers_keep_live_plain_data_identity_across_mutation(self):
        f=fixture(self);module=f.domain.load_known('test.checker.raw',f.domain.prepare('test.checker.raw'),None)
        rows=module.CaptureRows();plain=rows.value
        outcome=module.CaptureOutcome({'rows':plain});original=outcome.value
        f.controls.verify_installed();rows.append({'index':0,'ok':True});f.controls.verify_installed()
        self.assertIs(rows.value,plain);self.assertIs(outcome.value,original);self.assertIs(outcome['rows'],plain)
        self.assertEqual(outcome.value,{'rows':[{'index':0,'ok':True}]})
        self.assertEqual(rows[0],{'index':0,'ok':True});rows.clear();self.assertIs(rows.value,plain);self.assertEqual(plain,[])
        self.assertFalse(callable(rows));self.assertFalse(callable(outcome));f.controls.verify_installed();f.finish()

    def test_container_rejects_callback_Context_process_and_cycles_without_foreign_hooks(self):
        f=fixture(self);module=f.domain.load_known('test.checker.raw',f.domain.prepare('test.checker.raw'),None)
        rows=module.CaptureRows();calls=[]
        class Foreign:
            def __iter__(self):calls.append('iter');raise AssertionError('foreign iterator')
            def __str__(self):calls.append('str');raise AssertionError('foreign converter')
            def __call__(self):calls.append('call');raise AssertionError('foreign callable')
        for value in (Foreign(),lambda:None,f.ctx,subprocess.CompletedProcess(['protocol-only'],0),sys):
            with self.subTest(kind=type(value).__name__),self.assertRaisesRegex(ValueError,'only plain JSON data'):rows.append({'value':value})
        cycle=[];cycle.append(cycle)
        with self.assertRaisesRegex(ValueError,'cycle'):rows.append(cycle)
        with self.assertRaisesRegex(ValueError,'finite'):rows.append(float('nan'))
        self.assertEqual(calls,[]);self.assertEqual(rows.value,[]);f.controls.verify_installed();f.finish()

    def test_classes_are_pinned_in_completed_ordinary_provider_before_mutation_hook(self):
        f=fixture(self);record=f.domain.load_known('test.checker.raw',f.domain.prepare('test.checker.raw'),None)
        self.assertIn(('CaptureRows',),f.domain.records['test.checker.raw'].fixed_export_classes)
        self.assertIn(('CaptureOutcome',),f.domain.records['test.checker.raw'].fixed_export_classes)
        self.assertNotIn(checker_protocol.ENTRY,f.domain.records)
        cls=record.CaptureRows;original=cls.append;calls=[]
        def foreign(*args):calls.append(True);raise AssertionError('foreign data hook')
        cls.append=foreign
        try:
            with self.assertRaisesRegex(ValueError,'fixed exported class namespace changed'):f.controls.verify_installed()
            self.assertEqual(calls,[])
        finally:cls.append=original
        f.controls.verify_installed();f.finish()

if __name__=='__main__':unittest.main()

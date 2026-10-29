"""GitHub-only real child and first-failure fixtures. No product pass is granted."""
from __future__ import annotations
import builtins
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from types import ModuleType, SimpleNamespace
import unittest
from unittest import mock

if os.environ.get('GITHUB_ACTIONS') != 'true':
    raise RuntimeError('GitHub fixtures only')
HERE = Path(__file__).absolute().parent


def load(name):
    path = HERE / (name + '.py')
    module = ModuleType('_som_ts_v3_fixture_' + name)
    module.__file__, module.__package__, module.__cached__ = str(path), '', None
    exec(compile(path.read_bytes(), str(path), 'exec', dont_inherit=True), module.__dict__)
    return module


class RealChildFailureTests(unittest.TestCase):
    def setUp(self):
        self.raw = load('typescript_regression_raw_capture')
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.original_run, self.original_popen = subprocess.run, subprocess.Popen
        self.children = []
        self.addCleanup(self.cleanup)

    def cleanup(self):
        subprocess.run = self.original_run
        for proc in self.children:
            if proc.poll() is None:
                proc.kill()
            proc.wait(timeout=10)
            for name in ('stdin','stdout','stderr'):
                pipe = getattr(proc, name, None)
                if pipe is not None:
                    pipe.close()

    def proxy(self, *, cleanup_fails=False):
        fixture = self
        class HeldChild:
            def __init__(self, proc):
                self.real, self.first_error, self.calls = proc, None, []
                self.communications = 0
            def __getattr__(self, name): return getattr(self.real, name)
            def __enter__(self): raise AssertionError('implicit Popen context manager is forbidden')
            def __exit__(self, *args): raise AssertionError('implicit unbounded exit wait is forbidden')
            def communicate(self, *args, **kwargs):
                self.communications += 1; self.calls.append(('communicate', kwargs.get('timeout')))
                if cleanup_fails and self.communications > 1:
                    raise RuntimeError('injected cleanup communicate failure')
                try:
                    return self.real.communicate(*args, **kwargs)
                except BaseException as error:
                    if self.first_error is None: self.first_error = error
                    raise
            def kill(self):
                self.calls.append(('kill', None))
                if cleanup_fails: raise OSError('injected kill refusal')
                return self.real.kill()
            def wait(self, *, timeout):
                self.calls.append(('wait', timeout))
                if cleanup_fails: raise subprocess.TimeoutExpired(self.real.args, timeout)
                return self.real.wait(timeout=timeout)
        held = []
        def create(*args, **kwargs):
            actual = fixture.original_popen(*args, **kwargs); fixture.children.append(actual)
            result = HeldChild(actual); held.append(result); return result
        return create, held

    def test_actual_timeout_keeps_exact_first_error_when_kill_wait_and_writer_fail(self):
        create, held = self.proxy(cleanup_fails=True)
        writer_error = OSError('injected result evidence refusal'); original_save = self.raw.save
        def save(path, value):
            if path.name == 'result.json': raise writer_error
            return original_save(path, value)
        capture = self.raw.RawRuns(self.root / 'commands'); capture.install()
        started = time.monotonic()
        with mock.patch.object(self.raw.subprocess, 'Popen', side_effect=create), mock.patch.object(self.raw, 'save', side_effect=save):
            try:
                with self.assertRaises(subprocess.TimeoutExpired) as caught:
                    subprocess.run([sys.executable,'-I','-S','-B','-c','import time;time.sleep(30)'],
                        capture_output=True, timeout=0.02)
            finally:
                capture.close()
        self.assertLess(time.monotonic()-started, 3)
        self.assertIs(caught.exception, held[0].first_error)
        row = capture.rows[0]
        self.assertFalse(row['wait_completed']); self.assertFalse(row['streams_complete'])
        self.assertIn(('wait',10), held[0].calls)
        roles = {value['role'] for value in caught.exception.__som_typescript_secondary_failures__}
        self.assertTrue({'child_kill','child_cleanup_communicate','child_terminal_wait','command_result_evidence'} <= roles)
        self.assertIn('communicate', caught.exception.__som_typescript_failure_record__['traceback'])
        index = json.loads((self.root/'commands/index.json').read_bytes())
        self.assertEqual(index['rows'][0]['failure']['error'], 'TimeoutExpired')

    def test_actual_timeout_survives_stdout_writer_refusal_and_keeps_other_stream(self):
        create, held = self.proxy(); denied = OSError('injected stdout evidence refusal')
        original_write = Path.write_bytes
        def write(path, raw):
            if path == self.root/'commands/000000/stdout.raw': raise denied
            return original_write(path, raw)
        capture = self.raw.RawRuns(self.root/'commands'); capture.install()
        with mock.patch.object(self.raw.subprocess,'Popen',side_effect=create), mock.patch.object(Path,'write_bytes',new=write):
            try:
                with self.assertRaises(subprocess.TimeoutExpired) as caught:
                    subprocess.run([sys.executable,'-I','-S','-B','-c',
                        'import sys,time;print("before",flush=True);time.sleep(30)'],capture_output=True,timeout=0.05)
            finally: capture.close()
        self.assertIs(caught.exception, held[0].first_error)
        row=json.loads((self.root/'commands/000000/result.json').read_bytes())
        self.assertTrue(row['wait_completed']); self.assertTrue(row['stderr_present'])
        self.assertFalse(row['stdout_present'])
        self.assertEqual(row['evidence_failures'][0]['reason'],str(denied))

    def test_nonzero_real_child_check_precedes_any_later_evidence_error(self):
        capture=self.raw.RawRuns(self.root/'commands');capture.install()
        denied=OSError('injected result writer');original=self.raw.save
        def save(path,value):
            if path.name=='result.json':raise denied
            return original(path,value)
        with mock.patch.object(self.raw,'save',side_effect=save):
            try:
                with self.assertRaises(subprocess.CalledProcessError) as caught:
                    subprocess.run([sys.executable,'-I','-S','-B','-c','raise SystemExit(7)'],
                        capture_output=True,check=True)
            finally:capture.close()
        self.assertEqual(caught.exception.returncode,7)
        self.assertTrue(capture.rows[0]['wait_completed'])
        self.assertIn('check_returncode',caught.exception.__som_typescript_failure_record__['traceback'])
        self.assertEqual(caught.exception.__som_typescript_secondary_failures__[0]['reason'],str(denied))

    def test_writer_failure_without_a_child_error_is_propagated_as_primary(self):
        capture=self.raw.RawRuns(self.root/'commands');capture.install()
        denied=OSError('first evidence failure');original=self.raw.save
        def save(path,value):
            if path.name=='result.json':raise denied
            return original(path,value)
        with mock.patch.object(self.raw,'save',side_effect=save):
            try:
                with self.assertRaises(OSError) as caught:
                    subprocess.run([sys.executable,'-I','-S','-B','-c','pass'],capture_output=True)
            finally:capture.close()
        self.assertIs(caught.exception,denied)
        self.assertTrue(capture.rows[0]['wait_completed'])
        self.assertEqual(capture.rows[0]['failure']['role'],'command_result_evidence')


class OwnerCheckerFailureTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name).resolve()

    def owner_fixture(self):
        owner=load('typescript_regression_owner');out=self.root/'owner';out.mkdir()
        work=self.root/'work';work.mkdir()
        ctx=SimpleNamespace(helper_revision='a'*40,candidate_revision='a'*40,work=work,
            sources=SimpleNamespace(pins={},read=lambda path:b'fixture'),
            runtime=lambda:{'tools':{'python':Path(sys.executable).resolve()}})
        owner.admitted_context=lambda ctx:object();owner.plan_scope=lambda reader:{'fixture':'scope'}
        return owner,ctx,out

    def test_owner_original_error_survives_inventory_failure_with_full_trace(self):
        owner,ctx,out=self.owner_fixture();first=RuntimeError('actual collector refusal');later=OSError('inventory unreadable')
        def collect(ctx):raise first
        owner.admit_inputs=collect;owner.tree=lambda root:(_ for _ in ()).throw(later)
        with self.assertRaises(RuntimeError) as caught:owner.run_gate(ctx,self.root,object(),out,owner.GATE)
        self.assertIs(caught.exception,first)
        result=json.loads((out/'result.json').read_bytes())
        self.assertIn('collect',result['primary_failure']['traceback'])
        self.assertEqual(result['evidence_failures'][0]['reason'],str(later))
        self.assertFalse(result['complete']);self.assertFalse(result['product_acceptance_passed'])

    def test_owner_original_error_survives_final_result_writer_refusal(self):
        owner,ctx,out=self.owner_fixture();first=RuntimeError('actual owner first failure');later=OSError('result writer refused')
        def collect(ctx):raise first
        owner.admit_inputs=collect;owner.save=lambda path,value:(_ for _ in ()).throw(later)
        with self.assertRaises(RuntimeError) as caught:owner.run_gate(ctx,self.root,object(),out,owner.GATE)
        self.assertIs(caught.exception,first)
        self.assertEqual(first.__som_typescript_secondary_failures__[0]['role'],'owner_result_evidence')
        self.assertIn('collect',first.__som_typescript_failure_record__['traceback'])

    def checker_fixture(self,entry):
        checker=load('typescript_regression_checker');raw=load('typescript_regression_raw_capture')
        ctx=SimpleNamespace(work=self.root,repo=self.root,cache={},qa_mode='native-qa-v1',
            qa_source_authority={'source_authority_active':True,'admitted_repository_python_sources':[
                'fixture.py',checker.SELF,checker.RAW_CAPTURE,checker.DIGESTS]},
            sources=SimpleNamespace(read=lambda path:entry,pins={}),verify=lambda:None)
        ctx.module=lambda key,path:raw if path==checker.RAW_CAPTURE else SimpleNamespace(authorities=lambda *args:None)
        hosted=ModuleType('full_product_context');hosted.context=lambda:ctx
        return checker,hosted

    def test_checker_actual_entry_error_survives_postcheck_writer_failure(self):
        first=RuntimeError('original checker assertion');later=OSError('checker postcheck refused')
        checker,hosted=self.checker_fixture(b'def main():\n import builtins\n raise builtins.__ts_v3_first__\n')
        original=checker.save
        def save(path,value):
            if path.name=='checker-postcheck.json':raise later
            return original(path,value)
        with mock.patch.dict(sys.modules,{'full_product_context':hosted}), \
             mock.patch.object(builtins,'__ts_v3_first__',first,create=True),mock.patch.object(checker,'save',side_effect=save):
            with self.assertRaises(RuntimeError) as caught:
                checker.main(['--tool','fixture.py','--capture-root',str(self.root/'checker')])
        self.assertIs(caught.exception,first)
        self.assertIn('fixture.py',first.__som_typescript_failure_record__['traceback'])
        self.assertEqual(first.__som_typescript_secondary_failures__[-1]['role'],'checker_postcheck_evidence')

    def test_checker_writer_failure_after_success_is_not_a_success(self):
        checker,hosted=self.checker_fixture(b'def main():\n return 0\n');later=OSError('first postcheck write refusal')
        original=checker.save
        def save(path,value):
            if path.name=='checker-postcheck.json':raise later
            return original(path,value)
        with mock.patch.dict(sys.modules,{'full_product_context':hosted}),mock.patch.object(checker,'save',side_effect=save):
            with self.assertRaises(OSError) as caught:
                checker.main(['--tool','fixture.py','--capture-root',str(self.root/'checker')])
        self.assertIs(caught.exception,later)

    def test_missing_transport_consumer_refuses_before_any_service_or_product(self):
        owner,ctx,out=self.owner_fixture();services=SimpleNamespace(start_current=mock.Mock(),start_baseline=mock.Mock())
        owner.admit_inputs=lambda ctx:owner.require_full_input_transport_consumer()
        with self.assertRaisesRegex(NotImplementedError,'full ZIP/index/cipher/manifest/AEAD'):
            owner.run_gate(ctx,self.root,services,out,owner.GATE)
        services.start_current.assert_not_called();services.start_baseline.assert_not_called()
        report=json.loads((out/'result.json').read_bytes())
        self.assertFalse(report['complete']);self.assertEqual(report['rows'],[])

    def test_both_before_profile_roles_keep_all_strict_and_transport_obligations_unrun(self):
        domain=load('typescript_regression_domain')
        for role in ('recorded-before-supplemental','live-before-profile'):
            rows=domain.required_unrun_obligations(role)
            self.assertEqual({row['id'] for row in rows},{'ts.python_unchanged.strict_live_before',
                'ts.amendment002.live_before','rust.amendment006.live_before','ts.inputs.full_transport_consumer'})
            self.assertTrue(all(row['status']=='unrun' for row in rows))

    def test_ts_evidence_root_refuses_all_extra_known_inputs_before_source_or_mkdir(self):
        owner=load('typescript_regression_owner');capture=self.root/'raw/gate/owner';capture.mkdir(parents=True)
        (capture/'result.json').write_bytes(b'{}')
        roots={name:self.root/name for name in owner.PROOF_ROLES}
        for path in roots.values():path.mkdir()
        inputs={name:str(self.root/('ts-'+name)) for name in owner.INPUT_ROLES}
        for value in inputs.values():Path(value).mkdir()
        sibling=self.root/'rs22';sibling.mkdir();baseline=self.root/'baseline';baseline.mkdir()
        repo=self.root/'repo';repo.mkdir()
        data={'typescript_regression_inputs':{'roots':inputs,'siblings':{'rs_prompts':str(sibling)}},
              'baseline_inputs':{'profile':str(baseline)}}
        for name in ('runtime_root','wheel_root','rust_tools_root','cargo_root'):
            path=self.root/name;path.mkdir();data[name]=str(path)
        extra=[baseline]
        for name in ('build_provenance_root','current_companions_root'):
            path=self.root/name;path.mkdir();data[name]=str(path);extra.append(path)
        (capture/'context.input.json').write_text(json.dumps(data))
        for path in extra:
            bad=path/'verification'
            with self.subTest(input=path),mock.patch.dict(os.environ,{'RUNNER_TEMP':str(self.root)}), \
                 mock.patch.object(owner.Path,'mkdir',side_effect=AssertionError('must not create')):
                with self.assertRaisesRegex(ValueError,'disjoint from capture'):
                    owner.verify_result(capture,lambda path:self.fail('must not read source'),
                        'a'*40,'a'*40,owner.GATE,repo=repo,proof_roots=roots,verification_evidence_root=bad)
            self.assertFalse(bad.exists())


if __name__=='__main__':unittest.main()

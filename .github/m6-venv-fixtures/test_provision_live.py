"""Synthetic checks, restricted to GitHub-hosted runners."""
import io
import json
import os
from pathlib import Path
import sys
import tarfile
import tempfile
import unittest
from unittest import mock

if os.environ.get('GITHUB_ACTIONS') != 'true' or os.environ.get('RUNNER_ENVIRONMENT') != 'github-hosted':
    raise RuntimeError('GitHub hosted runner required')

import provision_live as subject

def build_tar(path, *, mutation=None):
    base = subject.ROOT + '/closures/' + subject.KEY
    target_prefix = '/home/runner/work/_temp/' + base + '/cache/archive-v0/'
    with tarfile.open(path,'w') as archive:
        for number in range(80):
            item = tarfile.TarInfo(base + '/cache/archive-v0/target%02d' % number)
            item.type = tarfile.DIRTYPE
            archive.addfile(item)
        for number in range(80):
            kind = 'index' if number < 40 else 'pypi'
            name = base + '/cache/wheels-v5/' + kind + '/item%02d' % number
            target = target_prefix + 'target%02d' % number
            if number == 79 and mutation == 'foreign': target = '/etc/passwd'
            if number == 79 and mutation == 'root': target = target_prefix + '/'
            if number == 79 and mutation == 'other-group': name = name.replace('/pypi/','/other/')
            item = tarfile.TarInfo(name)
            item.type = tarfile.SYMTYPE
            item.linkname = target
            archive.addfile(item)
        item = tarfile.TarInfo(base + '/requirements.in')
        item.size = 4
        archive.addfile(item,io.BytesIO(b'pin\n'))
        if mutation == 'link-parent':
            item = tarfile.TarInfo(base + '/cache/wheels-v5/index/item00/child')
            item.size = 1
            archive.addfile(item,io.BytesIO(b'x'))
        if mutation == 'duplicate':
            item = tarfile.TarInfo(base + '/requirements.in')
            item.size = 1
            archive.addfile(item,io.BytesIO(b'x'))


class RestoreTests(unittest.TestCase):
    def test_exact_cache_groups_and_safe_restore(self):
        with tempfile.TemporaryDirectory() as temp:
            tar,out = Path(temp)/'source.tar',Path(temp)/'out'
            build_tar(tar)
            subject.restore(tar,out)
            root = out/'closures'/subject.KEY
            self.assertEqual((root/'requirements.in').read_bytes(),b'pin\n')
            self.assertTrue((root/'cache/wheels-v5/index/item00').is_symlink())
            self.assertTrue((root/'cache/wheels-v5/pypi/item79').is_symlink())
            with self.assertRaises(subject.Refusal):
                subject.restore(tar,out)

    def test_bad_paths_links_and_duplicate_fail_closed(self):
        for mutation in ('foreign','root','other-group','link-parent','duplicate'):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as temp:
                tar,out = Path(temp)/'source.tar',Path(temp)/'out'
                build_tar(tar,mutation=mutation)
                with self.assertRaises(subject.Refusal):
                    subject.restore(tar,out)
                self.assertFalse(out.exists())


class ProvenanceTests(unittest.TestCase):
    def test_audit_identity_and_hash(self):
        report = {'state':'partial-evidence-consistent','product_acceptance_passed':False,
                  'run_id':40821432963,'artifact_id':12874482658,'source_commit':subject.ORIGINAL_HEAD,
                  'artifact_zip_sha256':subject.ZIP_SHA,'artifact_tar_sha256':'placeholder',
                  'requirements_count':39,'wheel_count':40}
        with tempfile.TemporaryDirectory() as temp:
            tar,audit = Path(temp)/'source.tar',Path(temp)/'audit.json'
            tar.write_bytes(b'fixture')
            digest = subject.sha(b'fixture')
            report['artifact_tar_sha256'] = digest
            raw = json.dumps(report).encode()
            audit.write_bytes(raw)
            with mock.patch.object(subject,'TAR_SHA',digest), mock.patch.object(subject,'AUDIT_SHA',subject.sha(raw)):
                subject.validate_provenance(audit,tar)
                tar.write_bytes(b'tampered')
                with self.assertRaises(subject.Refusal):
                    subject.validate_provenance(audit,tar)
            report['product_acceptance_passed'] = True
            raw = json.dumps(report).encode()
            audit.write_bytes(raw)
            with mock.patch.object(subject,'TAR_SHA',digest), mock.patch.object(subject,'AUDIT_SHA',subject.sha(raw)):
                with self.assertRaises(subject.Refusal):
                    subject.validate_provenance(audit,tar)


class ChildTests(unittest.TestCase):
    def test_real_wait_exit_and_raw_logs(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            row = subject.run_child([sys.executable,'-c','import sys; print("ok"); print("bad",file=sys.stderr); sys.exit(3)'],
                                    root,{'PATH':'/usr/bin:/bin'},root,'nonzero',timeout=10)
            self.assertEqual((row['status'],row['wait_result'],row['exit_code']),('completed',3,3))
            self.assertIs(row['group_stopped'],True)
            for stream in ('stdout','stderr'):
                raw = Path(row[stream]).read_bytes()
                self.assertEqual(row[stream+'_bytes'],len(raw))
                self.assertEqual(row[stream+'_sha256'],subject.sha(raw))

    def test_timeout_not_reported_as_successful_wait(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            row = subject.run_child([sys.executable,'-c','import time; time.sleep(10)'],root,
                                    {'PATH':'/usr/bin:/bin'},root,'timeout',timeout=0.2)
            self.assertEqual(row['status'],'timeout')
            self.assertIsNone(row['wait_result'])
            self.assertIsInstance(row['cleanup_wait_result'],int)
            self.assertIs(row['group_stopped'],True)


class AdmissionTests(unittest.TestCase):
    def test_interpreter_flags_required(self):
        flags = type('Flags', (), {'isolated':1, 'no_site':1, 'dont_write_bytecode':1, 'no_user_site':1})()
        with mock.patch.object(subject.sys, 'flags', flags):
            subject.interpreter_preconditions()
            for field in ('isolated','no_site','dont_write_bytecode','no_user_site'):
                with self.subTest(field=field):
                    setattr(flags,field,0)
                    with self.assertRaises(subject.Refusal):
                        subject.interpreter_preconditions()
                    setattr(flags,field,1)

    def test_github_revision_and_head_checked_before_source_execution(self):
        revision = 'a' * 40
        with mock.patch.dict(os.environ, {'GITHUB_SHA':revision}):
            with mock.patch.object(subject.subprocess,'check_output',return_value=b'b' * 40 + b'\n') as git:
                with self.assertRaises(subject.Refusal):
                    subject.bind_producer(Path('/repo'),revision)
                self.assertEqual(git.call_count,1)
            with mock.patch.dict(os.environ, {'GITHUB_SHA':'c' * 40}):
                with mock.patch.object(subject.subprocess,'check_output') as git:
                    with self.assertRaises(subject.Refusal):
                        subject.bind_producer(Path('/repo'),revision)
                    git.assert_not_called()

class ProvisionTests(unittest.TestCase):
    def exercise(self, sync_status):
        from contextlib import ExitStack
        from types import SimpleNamespace
        runner_temp = Path(os.environ['RUNNER_TEMP']).resolve(strict=True)
        with tempfile.TemporaryDirectory(dir=runner_temp) as tmp:
            base = Path(tmp)
            root_name = base.name + '/out'
            out = runner_temp/root_name
            names = ['pkg%02d' % i for i in range(40)]
            requirements = ''.join(name + '==1.0\n' for name in names[:39]).encode()
            pins = {name:{'version':'1.0','hashes':['h'+name]} for name in names}
            rows = [{'name':name,'version':'1.0','sha256':'h'+name} for name in names]
            closure_raw = json.dumps({'wheels':rows}).encode()
            policy,installer = b'{}',b'installer'
            producer_raw = b'producer'
            class Sources:
                def __init__(self,repo,revision): self.verified = False
                def read(self,path):
                    return {subject.PRODUCER:producer_raw, subject.REQ:requirements,
                            'policy':policy, 'installer':installer, 'native':b'native'}[path]
                def verify(self): self.verified = True
            checks = []
            tools = {'python':Path(sys.executable).absolute(),'uv':Path('/usr/bin/true')}
            class Producer:
                POLICY='policy'
                INSTALLER_AUTHORITY='installer'
                MAX_WHEEL=10000
                PIN=__import__('re').compile(r'([a-z0-9]+)==([0-9.]+)')
                PROBE='fixture probe'
                normalize=staticmethod(lambda x:x)
                installer_assets=staticmethod(lambda _: {})
                parse=staticmethod(lambda raw:json.loads(raw))
                read=staticmethod(lambda path,*_:path.read_bytes())
                locked_requirements=staticmethod(lambda _:pins)
                verify_closure=staticmethod(lambda _:checks.append('closure'))
                installed_inventory=staticmethod(lambda *_:{'bin/python':{'type':'symlink','target':str(tools['python'])}})
                verify_installed_members=staticmethod(lambda *_:{'exact_scope_verified':True})
                @staticmethod
                def admitted_runtime(*_):
                    reuse=SimpleNamespace(verify_installed_tree=lambda *_:checks.append('runtime'))
                    return reuse,{}, {}, tools, {'reuse_receipt_sha256':'runtime-sha'}
            Producer.Sources = Sources
            def fake_restore(_tar,target):
                closure = target/'closures'/subject.KEY
                for path in ('project','wheel-audits','cache','wheels'):
                    (closure/path).mkdir(parents=True,exist_ok=True)
                (closure/'receipt.json').write_bytes(closure_raw)
                (closure/'requirements.in').write_bytes(requirements)
                for name in ('requirements.selected.lock','requirements.offline.lock'):
                    (closure/name).write_text('locked')
                for name in ('pyproject.toml','uv.lock'):
                    (closure/'project'/name).write_text(name)
                for name in names:
                    audit=closure/'wheel-audits'/name
                    audit.mkdir()
                    (audit/'inventory.json').write_text(json.dumps({'metadata_sha256':'m'+name}))
            def fake_child(argv,cwd,env,logs,label,timeout=1200):
                checks.append(label)
                if label == 'fresh-offline-frozen-sync':
                    if sync_status == 'completed':
                        venv=out/'environments/react-api-qa/venv'
                        (venv/'bin').mkdir(parents=True)
                        (venv/'bin/python').symlink_to(tools['python'])
                    return {'status':sync_status,'wait_result':0 if sync_status=='completed' else None,
                            'exit_code':0 if sync_status=='completed' else 1, 'group_stopped':True}
                observation={'python':'3.12.11','executable':str(tools['python']),
                             'site':str(out/'environments/react-api-qa/venv/lib/python3.12/site-packages'),
                             'site_disabled':True,'site_in_sys_path':False,
                             'packages':[{'name':name,'version':'1.0','metadata_sha256':'m'+name} for name in names]}
                stdout=logs/'metadata.stdout'
                stdout.write_text(json.dumps(observation))
                return {'status':'completed','wait_result':0,'exit_code':0,'group_stopped':True,'stdout':str(stdout)}
            with ExitStack() as stack:
                for name,value in {'ROOT':root_name,'REQ_SHA':subject.sha(requirements),
                                   'PRODUCER_SHA':subject.sha(producer_raw), 'POLICY_SHA':subject.sha(policy),
                                   'INSTALLER_SHA':subject.sha(installer), 'CLOSURE_SHA':subject.sha(closure_raw),
                                   'NATIVE_SOURCE_SHAS':{'native':subject.sha(b'native')}}.items():
                    stack.enter_context(mock.patch.object(subject,name,value))
                for name,value in {'host':lambda:None, 'validate_provenance':lambda *_:None,
                                   'bind_producer':lambda *_:Producer(), 'restore':fake_restore,
                                   'run_child':fake_child}.items():
                    stack.enter_context(mock.patch.object(subject,name,value))
                result=subject.provision(Path('/repo'), 'a'*40, runner_temp/'som-product-runtime-qualification',
                                         out,Path('/tar'),Path('/audit'))
                saved=json.loads((out/'environments/react-api-qa/live-provision-receipt.json').read_text())
                self.assertEqual(saved,result)
                self.assertIs(result['product_acceptance_passed'],False)
                if sync_status=='completed':
                    self.assertEqual(result['state'],'fresh-offline-venv-ready-product-tests-unrun')
                    self.assertTrue(result['python_executable']['executable_access'])
                    self.assertEqual(result['python_executable']['mode_source'],'live stat observation')
                    self.assertIn('fresh-offline-no-site-metadata',checks)
                    self.assertIn('runtime',checks)
                else:
                    self.assertEqual(result['state'],'failed')
                    self.assertNotIn('fresh-offline-no-site-metadata',checks)

    def test_successful_main_provision_saves_evidence(self):
        self.exercise('completed')

    def test_failed_sync_records_failure_without_metadata_or_pass(self):
        self.exercise('timeout')

if __name__ == '__main__':
    unittest.main()

"""GH-only consumer source fixtures. No fixture is native or product admission."""
from __future__ import annotations
import hashlib
import io
import json
import os
from pathlib import Path
import signal
import stat
import subprocess
import sys
import tempfile
from types import ModuleType
import unittest
from unittest import mock
import zipfile

REPO=Path(__file__).resolve().parents[3]
ENTRY='som-rs/scripts/hosted_readiness/typescript_input_consumer.py'
def subject():
    raw=subprocess.check_output(['/usr/bin/git','--no-replace-objects','-C',str(REPO),'cat-file','blob',os.environ['HELPER_REVISION']+':'+ENTRY])
    if raw!=(REPO/ENTRY).read_bytes():raise ValueError('actual tested Git consumer source differs')
    module=ModuleType('_som_ts_input_consumer_source_fixture');module.__file__=str(REPO/ENTRY)
    exec(compile(raw,module.__file__,'exec',dont_inherit=True),module.__dict__)
    return module

class TypeScriptInputConsumerSource(unittest.TestCase):
    def test_postclose_base_Context_delta_refuses_changed_baseline_build_current_or_added_field(self):
        m=subject()
        original={'version':'som.full-product-context.v1','work_root':'/original/work','baseline_inputs':{'python':'/original/python'},
            'build_provenance_root':'/original/build','current_companions_root':'/original/current'}
        inputs={'receipt':'/new/receipt.json'};catalog={'original_producer':'identity'};proof={'actual_step':'identity'}
        actual={**original,'typescript_regression_inputs':inputs,'typescript_regression_input_producer':catalog,'typescript_regression_input_consumer':proof}
        m._context_delta(original,actual,inputs,catalog,proof)
        for key,value in [('baseline_inputs',{'python':'/changed/python'}),('build_provenance_root','/changed/build'),
                          ('current_companions_root',None),('work_root','/changed/work'),('unowned_field',True)]:
            with self.subTest(key=key),self.assertRaises(ValueError):m._context_delta(original,{**actual,key:value},inputs,catalog,proof)

    def test_exact_named_protection_set_rejects_missing_field_and_source_capture_overlap(self):
        m=subject()
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory).resolve();repo=root/'repo';repo.mkdir()
            protected={k:root/'protected'/k for k in m.PROTECTED_KEYS if k!='baseline_inputs'}
            protected['context_path']=root/'base-context.json'
            protected['baseline_inputs']={'python':root/'baseline-python','rust':root/'baseline-rust'}
            for path in [*protected['baseline_inputs'].values(),protected['build_provenance_root'],protected['current_companions_root']]:path.mkdir(parents=True)
            context={'version':'som.full-product-context.v1','qa_mode':'native-qa-v1','repo':str(repo),
                'work_root':str(protected['product_work']),'baseline_inputs':{k:str(v) for k,v in protected['baseline_inputs'].items()}}
            for key in ('assembly_root','python_inputs_root','rust_inputs_root','carrier_root','runtime_root','build_provenance_root','current_companions_root'):
                context[key]=str(protected[key])
            for key in m.AUTO_CONTEXT_INPUT_KEYS:
                path=root/'original-inputs'/key;path.parent.mkdir(parents=True,exist_ok=True)
                if key=='artifact_inputs':path.write_bytes(b'{}')
                else:path.mkdir()
                context[key]=str(path)
            protected['context_path'].write_bytes(m.canonical(context))
            with mock.patch.dict(os.environ,{'RUNNER_TEMP':str(root)}):
                actual=m._roots(repo,protected)
                self.assertEqual(actual[0],root/m.ROOT_NAME)
                missing=dict(protected);missing.pop('assembly_root')
                with self.assertRaises(ValueError):m._roots(repo,missing)
                with self.assertRaises(ValueError):m._roots(repo,{**protected,'capture_out':root/m.ROOT_NAME/'capture'})
                for key in ('build_provenance_root','current_companions_root'):
                    changed={**context,key:str(root)};protected['context_path'].write_bytes(m.canonical(changed))
                    with self.subTest(key=key),self.assertRaises(ValueError):m._roots(repo,{**protected,key:root})
                    protected['context_path'].write_bytes(m.canonical(context))
                    with self.subTest(key=key+'-omitted'),self.assertRaises(ValueError):m._roots(repo,{**protected,key:None})
                baseline={**protected['baseline_inputs'],'python':root}
                protected['context_path'].write_bytes(m.canonical({**context,'baseline_inputs':{k:str(v) for k,v in baseline.items()}}))
                with self.assertRaises(ValueError):m._roots(repo,{**protected,'baseline_inputs':baseline})
                protected['context_path'].write_bytes(m.canonical(context))
                with self.assertRaises(ValueError):m._roots(repo,{**protected,'baseline_inputs':{'rust':protected['baseline_inputs']['rust']}})
                optional={**context,'build_provenance_root':None,'current_companions_root':None}
                protected['context_path'].write_bytes(m.canonical(optional))
                m._roots(repo,{**protected,'build_provenance_root':None,'current_companions_root':None})
                self.assertFalse((root/m.ROOT_NAME).exists())

    def test_actual_baseline_source_profile_label_and_unsafe_keys_before_context_read_or_mkdir(self):
        m=subject()
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory).resolve();repo=root/'repo';repo.mkdir()
            label='som-rs/scripts/hosted_readiness/inputs/recorded-before-profile-v1.json'
            self.assertEqual(label,m.BASELINE_PROFILE)
            protected={k:root/'protected'/k for k in m.PROTECTED_KEYS if k!='baseline_inputs'}
            protected['context_path']=root/'base-context.json'
            protected['baseline_inputs']={label:root/'baseline-inputs'}
            for path in [*protected['baseline_inputs'].values(),protected['build_provenance_root'],protected['current_companions_root']]:path.mkdir(parents=True)
            context={'version':'som.full-product-context.v1','qa_mode':'native-qa-v1','repo':str(repo),
                'work_root':str(protected['product_work']),'baseline_inputs':{label:str(protected['baseline_inputs'][label])}}
            for key in ('assembly_root','python_inputs_root','rust_inputs_root','carrier_root','runtime_root','build_provenance_root','current_companions_root'):
                context[key]=str(protected[key])
            for key in m.AUTO_CONTEXT_INPUT_KEYS:
                path=root/'original-inputs'/key;path.parent.mkdir(parents=True,exist_ok=True)
                if key=='artifact_inputs':path.write_bytes(b'{}')
                else:path.mkdir()
                context[key]=str(path)
            original=m.canonical(context);protected['context_path'].write_bytes(original)
            class LabelSubclass(str):pass
            with mock.patch.dict(os.environ,{'RUNNER_TEMP':str(root)}), \
                 mock.patch.object(m,'git_source',side_effect=AssertionError('baseline labels are not source reads')), \
                 mock.patch.object(Path,'mkdir',side_effect=AssertionError('protection check must not mkdir')):
                actual=m._roots(repo,protected)
                self.assertEqual(actual[0],root/m.ROOT_NAME)
                self.assertEqual(protected['baseline_inputs'],{label:root/'baseline-inputs'})
                self.assertEqual(protected['context_path'].read_bytes(),original)
                self.assertFalse((repo/label).exists())
                invalid=[{},*[{key:root/'baseline-inputs'} for key in
                    ('../label','nested/../label','/absolute/label','nested//label','nested/./label','back\\slash','',1,LabelSubclass(label))],
                    {f'label-{i}':root/'baseline-inputs' for i in range(33)}]
                with mock.patch.object(m,'raw_file',side_effect=AssertionError('unsafe baseline keys must fail before Context read')):
                    for baseline in invalid:
                        with self.subTest(keys=list(baseline)),self.assertRaises(ValueError):
                            m._roots(repo,{**protected,'baseline_inputs':baseline})
                self.assertFalse((root/m.ROOT_NAME).exists())

    def catalog(self,m):
        return {'version':'som.ts-input-producer-catalog-draft.v1','job':m.PRODUCER_JOB,'run_id':os.environ['GITHUB_RUN_ID'],
            'run_attempt':os.environ['GITHUB_RUN_ATTEMPT'],'head_sha':os.environ['HELPER_REVISION'],'artifact_id':123,
            'artifact_digest':'sha256:'+'1'*64,'index_sha256':'2'*64,'receipt_sha256':'3'*64,'profile_sha256':'4'*64}

    def test_exact_ten_key_catalog_refuses_old_job_attempt_source_and_extra_field(self):
        m=subject();c=self.catalog(m);self.assertEqual(m._catalog(c,'4'*64,os.environ['HELPER_REVISION']),c)
        for key,value in [('job','roles'),('run_id','1'),('run_attempt','0'),('head_sha','0'*40),('profile_sha256','0'*64)]:
            with self.subTest(key=key),self.assertRaises(ValueError):m._catalog({**c,key:value},'4'*64,os.environ['HELPER_REVISION'])
        with self.assertRaises(ValueError):m._catalog({**c,'untrusted_capture':True},'4'*64,os.environ['HELPER_REVISION'])

    def test_full_frozen_role_map_parser_rejects_pending_and_modified_source_identity(self):
        m=subject();profile=json.loads((REPO/m.PROFILE).read_bytes())
        current={**profile,'pending':[]};parsed=m._profile(m.canonical(current))
        self.assertEqual(set(parsed['roles']),set(m.ROLES));self.assertEqual(len(parsed['siblings']['rs_prompts']['files']),22)
        with self.assertRaises(ValueError):m._profile(m.canonical({**current,'pending':['actual unresolved source edge']}))
        changed=json.loads(m.canonical(current));changed['roles']['node_tools']['files']['bin/node']['sha256']='0'*64
        with self.assertRaises(ValueError):m._profile(m.canonical(changed))

    def packet(self,m,path,*,extra=False,changed_cipher=False):
        cipher=b'opaque ciphertext fixture; parser is not an AEAD pass'
        manifest={'version':'som.m6-sealed-inputs-manifest.v1','cipher_sha256':m.sha(cipher),'uncompressed_bytes':100,
            'total_files':1,'files':[{'path':'run/raw.json','sha256':m.sha(b'wire'),'bytes':4,'mode':0o644}]}
        mr=m.canonical(manifest)
        index={'version':'som.m6-evidence-index.v1','status':'encrypted','packages':[{'archive':'package-000.sealed',
            'manifest':'package-000.manifest.json','cipher_sha256':m.sha(cipher),'manifest_sha256':m.sha(mr),'total_files':1,'bytes':4}]}
        ir=m.canonical(index)
        with zipfile.ZipFile(path,'w') as z:
            z.writestr('evidence-index.json',ir);z.writestr('package-000.manifest.json',mr)
            z.writestr('package-000.sealed',cipher+b'changed' if changed_cipher else cipher)
            if extra:z.writestr('unexpected.raw',b'unowned')
        return {'artifact_digest':'sha256:'+m.sha(path.read_bytes()),'index_sha256':m.sha(ir)}

    def test_whole_zip_parser_and_keyless_rederivation_reject_extra_or_tampered_cipher(self):
        m=subject()
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory).resolve();good=root/'good.zip';catalog=self.packet(m,good)
            packet=m._extract_packet(good,root/'sealed',catalog)
            self.assertEqual(packet,m._extract_packet(good,None,catalog))
            self.assertEqual(packet['authenticated_files']['run/raw.json']['sha256'],m.sha(b'wire'))
            for label,options in [('extra',{'extra':True}),('cipher',{'changed_cipher':True})]:
                path=root/(label+'.zip');catalog=self.packet(m,path,**options)
                with self.subTest(label=label),self.assertRaises(ValueError):m._extract_packet(path,root/(label+'-sealed'),catalog)

    def chunks(self,m,root,parts):
        folder=root/'node-binary-chunks';folder.mkdir();rows=[]
        for i,part in enumerate(parts):
            (folder/f'{i:03d}.part').write_bytes(part);rows.append({'path':f'node-binary-chunks/{i:03d}.part','size':len(part),'sha256':m.sha(part)})
        body=b''.join(parts);record={'version':'som.ts-node-chunk-transport.v1','source':'roles/node_tools/bin/node','size':len(body),
            'sha256':m.sha(body),'mode':0o755,'chunk_bytes':4,'chunks':rows,'product_acceptance':'not_run'}
        (root/'node-chunk-transport.json').write_bytes(m.canonical(record));return record,body

    def test_real_ordered_chunks_rederive_whole_bytes_and_refuse_rehashed_wrong_order(self):
        m=subject()
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory).resolve();record,body=self.chunks(m,root,[b'abcd',b'efgh',b'ij'])
            m._restore_chunks(root,root/'node',expected_sha=m.sha(body),expected_bytes=len(body),chunk_bytes=4)
            self.assertEqual((root/'node').read_bytes(),body)
            m._restore_chunks(root,None,expected_sha=m.sha(body),expected_bytes=len(body),chunk_bytes=4)
            first=root/'node-binary-chunks/000.part';second=root/'node-binary-chunks/001.part'
            first.write_bytes(b'efgh');second.write_bytes(b'abcd')
            record['chunks'][0]['sha256']=m.sha(b'efgh');record['chunks'][1]['sha256']=m.sha(b'abcd')
            (root/'node-chunk-transport.json').write_bytes(m.canonical(record))
            with self.assertRaises(ValueError):m._restore_chunks(root,None,expected_sha=m.sha(body),expected_bytes=len(body),chunk_bytes=4)

    def test_exact_internal_alias_is_data_and_external_alias_hardlink_and_hidden_directory_refuse(self):
        m=subject()
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory).resolve();role=root/'role';role.mkdir();(role/'code.pyc').write_bytes(b'bytecode data only')
            (role/'alias').symlink_to('code.pyc');snapshot=m._tree(role)
            self.assertEqual(snapshot['files']['alias']['target_sha256'],m.sha(b'bytecode data only'))
            (role/'alias').unlink();outside=root/'outside';outside.write_bytes(b'outside');(role/'alias').symlink_to(outside)
            with self.assertRaises(ValueError):m._tree(role)
            (role/'alias').unlink();os.link(role/'code.pyc',role/'extra-hardlink')
            with self.assertRaises(ValueError):m._tree(role)
            (role/'extra-hardlink').unlink();hidden=role/'hidden';hidden.mkdir();(hidden/'extra.py').write_bytes(b'never import')
            hidden.chmod(0)
            try:
                self.assertFalse(os.access(hidden,os.R_OK|os.X_OK),'GH fixture needs the actual unprivileged permission witness')
                with self.assertRaises(PermissionError):m._tree(role)
            finally:hidden.chmod(0o700)

    def test_readonly_cache_and_all_role_directory_universes_are_checked(self):
        m=subject()
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory).resolve();profile={'roles':{},'siblings':{'rs_prompts':{'files':{}}}}
            for role in m.ROLES:
                path=root/'roles'/role;path.mkdir(parents=True);(path/'data').write_bytes(role.encode())
                mode=0o444 if role=='npm_cache' else 0o644;(path/'data').chmod(mode)
                profile['roles'][role]={'files':{'data':{'sha256':m.sha(role.encode()),'size':len(role),'mode':mode}}}
                if role=='npm_cache':(path/'_logs').mkdir();(path/'_logs').chmod(0o555);path.chmod(0o555)
            (root/'siblings/rs_prompts').mkdir(parents=True)
            m._roles(profile,root)
            (root/'roles/public_run/extra-empty').mkdir()
            with self.assertRaises(ValueError):m._roles(profile,root)

    def test_trusted_step_proof_rejects_wrong_job_step_source_and_metadata_only_shortcut(self):
        m=subject();catalog=self.catalog(m);state={m.WORKFLOW:{'sha256':'5'*64}}
        proof={'version':'som.typescript-input-consumer-step-proof.v1','run_id':os.environ['GITHUB_RUN_ID'],
            'run_attempt':os.environ['GITHUB_RUN_ATTEMPT'],'head_sha':os.environ['HELPER_REVISION'],'workflow_path':m.WORKFLOW,
            'workflow_sha256':'5'*64,'consumer_job':os.environ['GITHUB_JOB'],'consumer_step':m.STEP_ID,
            'producer_catalog_sha256':m.sha(m.canonical(catalog)),'receipt_sha256':'6'*64,'evidence_manifest_sha256':'7'*64,
            'artifact_id':catalog['artifact_id'],'artifact_digest':catalog['artifact_digest'],'index_sha256':catalog['index_sha256'],
            'product_acceptance_passed':False}
        m._trusted_proof(proof,state,catalog,os.environ['HELPER_REVISION'])
        for key,value in [('consumer_job','other-job'),('consumer_step','capture-author'),('head_sha','0'*40),
                          ('producer_catalog_sha256','0'*64),('evidence_manifest_sha256','invalid'),('product_acceptance_passed',True)]:
            with self.subTest(key=key),self.assertRaises(ValueError):m._trusted_proof({**proof,key:value},state,catalog,os.environ['HELPER_REVISION'])
        with self.assertRaises(ValueError):m._trusted_proof({'actual_AEAD_passed':True},state,catalog,os.environ['HELPER_REVISION'])

    def test_actual_short_child_raw_wait_and_first_start_capture_error_are_retained(self):
        m=subject();executable=Path(sys.executable).resolve();info=executable.stat()
        expected={'device':info.st_dev,'inode':info.st_ino,'size':info.st_size};environment={'PATH':'/usr/bin:/bin'}
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory).resolve();argv=[executable,'-I','-S','-B','-c',"import sys,time;sys.stdout.write('complete stdout');sys.stderr.write('complete stderr');time.sleep(.2)"]
            record=m._run_owned(argv,root,root/'positive',environment,expected)
            self.assertTrue(record['wait_recorded']);self.assertTrue(record['owned_group_gone']);self.assertEqual(record['exit_code'],0)
            self.assertEqual((root/'positive/stdout.raw').read_bytes(),b'complete stdout')
            real_write=m.write
            def fail_start(path,*args,**kwargs):
                if Path(path).name=='owned-start-stat.raw':raise RuntimeError('actual first start record failure')
                return real_write(path,*args,**kwargs)
            with mock.patch.object(m,'write',side_effect=fail_start):
                with self.assertRaisesRegex(RuntimeError,'actual first start record failure'):
                    m._run_owned([executable,'-I','-S','-B','-c','import time;time.sleep(20)'],root,root/'failure',environment,expected)
            failed=json.loads((root/'failure/command.json').read_bytes())
            self.assertTrue(failed['wait_recorded']);self.assertTrue(failed['owned_group_gone'])
            self.assertEqual(failed['first_error']['message'],'actual first start record failure')
            self.assertIsInstance(failed['exit_code'],int)

if __name__=='__main__':unittest.main()

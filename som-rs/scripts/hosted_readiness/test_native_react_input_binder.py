"""GitHub protocol tests; no genuine Context or actual captured-input grant.

Actual Core, SourceBytes, helper code and temporary Git metadata run here.
Context construction and source admission are explicit protocol seams.
Synthetic data tests do not qualify the real18packages or668 captured pairs.
"""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import traceback
from types import ModuleType
import unittest
from unittest import mock

if not (os.environ.get('GITHUB_ACTIONS') == 'true' and sys.platform == 'linux'
        and sys.flags.isolated and sys.flags.no_site and sys.dont_write_bytecode):
    raise RuntimeError('actual GitHub Linux -I -S -B required')

HERE=Path(__file__).absolute().parent
REPO=HERE.parents[2]
BASE='som-rs/scripts/hosted_readiness/'
M6='som-rs/scripts/react_output_acceptance/'
BINDER=M6+'native_owner_input_binding.py'
DERIVED=M6+'reconstruct_historical_responses.py'
PROVENANCE=M6+'verify_historical_response_provenance.py'
CONTEXT=BASE+'product_runtime/full_product_context.py'
PINS={BINDER:'86b40f342f13afd609047050a077cc2f85c57f4122ee6545ed312426893cbdd5',
    DERIVED:'f7231671f41cb78cfe50e195159f54c644baa1f65a0a186c52f2abc2b8877e41',
    PROVENANCE:'82efed50d624cfe1929de5448ca756bbec5c28f8f04ba652fe8286fc285f5d1b',
    CONTEXT:'7329f3dc88d63957a7ac33c847ec3416f4a7b35b8c7b10793154f22a5516a440',
    BASE+'native_active_controls.py':'eead8e03186f8f55812566a11d5aa837fb5cdb542d2c3de9435da9bad6bb5b32',
    BASE+'test_native_active_controls.py':'f76335be9269618bcec26750895202a896bbe759cfc6ea490d7b8dffddc99e0d'}

def checked(name):
    raw=(REPO/name).read_bytes()
    if hashlib.sha256(raw).hexdigest()!=PINS[name]:raise RuntimeError('exact dependency differs: '+name)
    return raw

for name in PINS:checked(name)
protocol=ModuleType('_som_native_input_protocol_fixture')
protocol.__file__,protocol.__package__=str(HERE/'test_native_active_controls.py'),''
sys.modules[protocol.__name__]=protocol
exec(compile(checked(BASE+'test_native_active_controls.py'),protocol.__file__,'exec',dont_inherit=True),protocol.__dict__)

class GitTransfer(protocol.ProtocolTransfer):
    def __init__(self,fixture):
        super().__init__(fixture)
        self.revision=fixture.revision
        self.admission.update(helper_revision=self.revision,candidate_revision=self.revision)

class InputFixture(protocol.Fixture):
    def __init__(self,case,*,active=True):
        caller=('def run(controls,target="binder"):\n'
                '    return controls.load_module({"binder":"m6.input.binding","context":"runtime.context"}[target])\n')
        with mock.patch.object(protocol,'ProtocolTransfer',GitTransfer):
            super().__init__(case,caller)
        self.policy['imports'].append({'caller':'m6.input.provenance','name':'reconstruct_historical_responses',
            'level':0,'fromlist':[],'result':'m6.input.derived','from_roles':{}})
        self.module=self.caller().run(self.controls,'context')
        self.ctx=object.__new__(self.module.Context)
        self.ctx.repo,self.ctx.helper_revision,self.ctx.candidate_revision=self.root,self.revision,self.revision
        self.ctx.sources=self.module.SourceBytes(self.root,self.revision)
        self.ctx.qa_mode,self.ctx.qa_source_authority='native-qa-v1',self.transfer.admission
        self.ctx.qa_import_controls=self.controls.borrow()
        if active:self.module._ACTIVE=self.ctx
        self.binder=self.run()

    def add_role(self,role,path,raw,**kwargs):
        row=super().add_role(role,path,raw,**kwargs)
        if role!='m6.api.producer':return row
        super().add_role('runtime.context',CONTEXT,checked(CONTEXT),module='_som_input_fixture_context',
            callers=[{'role':'caller','function':'run'}],bindings={'__som_native_active_controls__':{'kind':'active-controls'}})
        bindings={'__som_native_active_controls__':{'kind':'active-controls'},
            '__som_native_context_module__':{'kind':'role-module','role':'runtime.context'},
            '__som_native_Context__':{'kind':'role-export','role':'runtime.context','attribute':'Context'},
            '__som_native_SourceBytes__':{'kind':'role-export','role':'runtime.context','attribute':'SourceBytes'}}
        super().add_role('m6.input.binding',BINDER,checked(BINDER),module='_som_m6_reviewed_derived_inputs',
            callers=[{'role':'caller','function':'run'}],bindings=bindings)
        super().add_role('fixture.input-type-pin',BASE+'fixture_input_type_pin.py','VALUE=1\n',
            bindings={'BoundInputs':{'kind':'role-export','role':'m6.input.binding','attribute':'BoundInputs'}})
        super().add_role('m6.input.derived',DERIVED,checked(DERIVED),module='reconstruct_historical_responses',
            callers=[{'role':'m6.input.binding','function':'_native_helpers'},
                     {'role':'m6.input.provenance','function':'<module>'}])
        super().add_role('m6.input.provenance',PROVENANCE,checked(PROVENANCE),module='verify_historical_response_provenance',
            callers=[{'role':'m6.input.binding','function':'_native_helpers'}],
            bindings={'r':{'kind':'role-module','role':'m6.input.derived'},
                      '__som_native_Refused__':{'kind':'role-export','role':'m6.input.derived','attribute':'Refused'}})
        env={'PATH':'/usr/bin:/bin','LANG':'C.UTF-8','LC_ALL':'C.UTF-8','GIT_CONFIG_GLOBAL':'/dev/null','GIT_CONFIG_NOSYSTEM':'1'}
        def git(*args):return subprocess.run(['/usr/bin/git','--no-replace-objects','-C',str(self.root),*args],
            env=env,check=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE).stdout
        git('init','--quiet');git('add','--','.')
        git('-c','user.name=Input protocol fixture','-c','user.email=fixture@example.invalid',
            '-c','core.hooksPath=/dev/null','commit','--quiet','-m','synthetic source protocol only')
        self.revision=git('rev-parse','HEAD').decode().strip()
        return row

    def helpers(self):
        with self.binder._native_owner() as (lease,ctx,sources):
            self.case.assertIs(ctx,self.ctx)
            return self.binder._native_helpers(lease,sources)

class NativeInputBinderTests(unittest.TestCase):
    def test_real_core_context_sourcebytes_and_fixed_import_pointer_without_path_change(self):
        f=InputFixture(self);before=list(sys.path)
        derived,provenance=f.helpers()
        self.assertIs(derived,f.domain.records['m6.input.derived'].module)
        self.assertIs(provenance,f.domain.records['m6.input.provenance'].module)
        self.assertIs(provenance.r,derived)
        self.assertIs(type(f.ctx.sources),f.module.SourceBytes)
        self.assertEqual(sys.path,before)
        self.assertIs(f.helpers()[0],derived)
        self.assertFalse(f.domain.closed)
        self.assertEqual(derived.__name__,'reconstruct_historical_responses')
        self.assertEqual(provenance.__name__,'verify_historical_response_provenance')

    def test_missing_active_context_is_explicit_pending_before_helper_body(self):
        f=InputFixture(self,active=False)
        with self.assertRaisesRegex(ValueError,'genuine active native Context is unavailable; upstream join pending'):
            f.binder.approved_inputs(None,None)
        self.assertNotIn('m6.input.derived',f.domain.records)
        self.assertNotIn('m6.input.provenance',f.domain.records)

    def test_missing_native_binding_refuses_before_legacy_search_path_or_helpers(self):
        f=InputFixture(self);b=f.binder;before=list(sys.path)
        controls=b.__dict__.pop('__som_native_active_controls__')
        try:
            with self.assertRaisesRegex(ValueError,'fixed active-controls binding was replaced'):
                b.approved_inputs(None,None)
            self.assertEqual(sys.path,before)
            self.assertNotIn('m6.input.derived',f.domain.records)
            self.assertNotIn('m6.input.provenance',f.domain.records)
        finally:b.__dict__['__som_native_active_controls__']=controls

    def test_removed_binding_and_core_key_keep_original_guard_before_legacy_or_helpers(self):
        f=InputFixture(self);b=f.binder;before=list(sys.path)
        name='reconstruct_historical_responses';foreign=ModuleType(name)
        self.assertNotIn(name,sys.modules)
        binding=b.__dict__.pop('__som_native_active_controls__')
        core=None
        try:
            core=sys.modules.pop('_som_native_active_controls')
            sys.modules[name]=foreign
            try:
                b.approved_inputs(None,None)
            except ValueError as error:
                self.assertEqual(error.args,('fixed active-controls binding was replaced',))
                frames=[row.name for row in traceback.extract_tb(error.__traceback__)]
                self.assertIn('checked_import',frames)
                self.assertIn('_native_route',frames)
                self.assertIsNone(error.__cause__)
            else:self.fail('the actual Core guard must refuse before legacy execution')
            self.assertEqual(sys.path,before)
            self.assertIs(sys.modules[name],foreign)
            self.assertNotIn('m6.input.derived',f.domain.records)
            self.assertNotIn('m6.input.provenance',f.domain.records)
        finally:
            if core is not None:sys.modules['_som_native_active_controls']=core
            b.__dict__['__som_native_active_controls__']=binding
            if sys.modules.get(name) is foreign:del sys.modules[name]

    def test_foreign_active_context_or_source_root_cannot_run_hooks(self):
        f=InputFixture(self);calls=[]
        class Foreign:
            def __getattribute__(self,name):calls.append(name);raise AssertionError('foreign attribute hook')
            def __eq__(self,other):calls.append('eq');raise AssertionError('foreign comparison hook')
            def __str__(self):calls.append('str');raise AssertionError('foreign path hook')
        f.module._ACTIVE=Foreign()
        try:
            with self.assertRaisesRegex(ValueError,'genuine active native Context is unavailable'):
                with f.binder._native_owner():pass
        finally:f.module._ACTIVE=f.ctx
        root=f.ctx.sources.root;f.ctx.sources.root=Foreign()
        try:
            with self.assertRaisesRegex(ValueError,'native input source owner differs'):
                with f.binder._native_owner():pass
        finally:f.ctx.sources.root=root
        mode=f.ctx.qa_mode;f.ctx.qa_mode=Foreign()
        try:
            with self.assertRaisesRegex(ValueError,'actual native input Context root/revisions differ'):
                with f.binder._native_owner():pass
        finally:f.ctx.qa_mode=mode
        revision=f.ctx.qa_source_authority['helper_revision'];f.ctx.qa_source_authority['helper_revision']=Foreign()
        try:
            with self.assertRaisesRegex(ValueError,'complete actual native input source authority required'):
                with f.binder._native_owner():pass
        finally:f.ctx.qa_source_authority['helper_revision']=revision
        self.assertEqual(calls,[])
        self.assertNotIn('m6.input.derived',f.domain.records)

    def test_foreign_public_helper_namespace_is_preserved_and_not_used(self):
        f=InputFixture(self);name='reconstruct_historical_responses';foreign=ModuleType(name)
        sys.modules[name]=foreign
        try:
            with self.assertRaisesRegex(ValueError,'preloaded source module refused'):f.helpers()
            self.assertIs(sys.modules[name],foreign)
            self.assertNotIn('m6.input.derived',f.domain.records)
        finally:
            if sys.modules.get(name) is foreign:del sys.modules[name]

    def test_actual_git_source_mutation_refuses_before_load(self):
        f=InputFixture(self);path=f.root/DERIVED;raw=path.read_bytes();path.write_bytes(raw+b'\n# changed source\n')
        try:
            with self.assertRaisesRegex(ValueError,'source differs from committed Git bytes'):f.helpers()
            self.assertNotIn('m6.input.derived',f.domain.records)
        finally:path.write_bytes(raw)

    def test_original_manifest_and_boundinputs_checks_keep_derived_role_separate(self):
        f=InputFixture(self);b=f.binder
        body=b'approved input';relative='inputs/original.raw';path=f.root/relative;path.parent.mkdir();path.write_bytes(body)
        manifest={'version':'som.m6-sealed-inputs-manifest.v1','cipher_sha256':'0'*64,'uncompressed_bytes':len(body),
            'total_files':1,'files':[{'path':relative,'sha256':b.sha(body),'bytes':len(body),'mode':0o644}]}
        manifest_raw=json.dumps(manifest).encode()
        index={'version':'som.m6-input-index.v1','packages':[{'archive':'package-000.sealed',
            'manifest':'package-000.manifest.json','manifest_sha256':b.sha(manifest_raw)}]}
        index_raw=json.dumps(index).encode()
        files=b.manifest_map(index_raw,b.sha(index_raw),lambda name:manifest_raw)
        derived_path='inputs/derived.raw';derived=b'later captured data';(f.root/derived_path).write_bytes(derived)
        merged=b.merge_derived(files,{derived_path:derived},f.root)
        self.assertNotIn('source',merged[relative]);self.assertEqual(merged[derived_path]['source'],'later-capture-derived')
        bound=b.BoundInputs(f.root,merged)
        self.assertEqual(bound.read(path),body);self.assertEqual(bound.read(f.root/derived_path),derived)
        path.write_bytes(b'tampered')
        with self.assertRaisesRegex(ValueError,'input bytes differ from approved manifest'):bound.read(path)
        with self.assertRaisesRegex(ValueError,'input outside repository'):bound.read(f.root.parent/'foreign.raw')
        manifest['files'].append(dict(manifest['files'][0]));manifest['total_files']=2
        bad=json.dumps(manifest).encode();index['packages'][0]['manifest_sha256']=b.sha(bad);raw=json.dumps(index).encode()
        with self.assertRaisesRegex(ValueError,'input member identity differs'):b.manifest_map(raw,b.sha(raw),lambda name:bad)

    def test_original_provenance_join_checks_all668_synthetic_pairs_and_raw_mutations(self):
        f=InputFixture(self);derived,provenance=f.helpers();files={};mapping={'reconstructed_cases':[]};calls=[];rows=[]
        request=b'{"later":true}';response=b'{"synthetic":true}';historical=b'{"historical":true}'
        for ident in range(668):
            rel=f'calls/{ident:06d}';name=provenance.BASE+rel
            call={'id':ident,'method':'POST','url':'http://127.0.0.1:18917/v1/assemble','status':200,
                  'complete':True,'error':None,'request_file':rel+'.request.raw','response_file':rel+'.response.raw',
                  'request_sha256':derived.sha(request),'response_sha256':derived.sha(response)}
            calls.append(call);files[name+'.request.raw']=request;files[name+'.response.raw']=response;files[name+'.json']=json.dumps(call).encode()
            item={'source':'public' if ident<127 else 'legacy','id':str(ident),'source_request_equal':False,
                  'historical_request_sha256':derived.sha(historical),'later_baseline_request_sha256':derived.sha(request),
                  'decoded_response_sha256':derived.sha(response),'source_candidates':[{'path':name+'.response.raw'}]}
            mapping['reconstructed_cases'].append(item)
            for variant in ('none','python'):
                rows.append({'source':item['source'],'id':item['id'],'variant':variant,'baseline_assemble_status':200,
                    'baseline_assemble_request_sha256':derived.sha(request),'baseline_assemble_response_sha256':derived.sha(response)})
        recording={'version':'som.m6-http-recording.v1','exit_code':0,'recording_errors':[],'calls':calls};report={'rows':rows}
        self.assertEqual(provenance.join(mapping,recording,report,files.__getitem__),{'derived_pairs':668,'later_capture_calls':668})
        key=provenance.BASE+'calls/000000.response.raw';original=files[key];files[key]=b'changed response'
        with self.assertRaises(derived.Refused):provenance.join(mapping,recording,report,files.__getitem__)
        files[key]=original;rows[0]['baseline_assemble_status']=400
        with self.assertRaises(derived.Refused):provenance.join(mapping,recording,report,files.__getitem__)
        rows[0]['baseline_assemble_status']=200;del files[provenance.BASE+'calls/000000.request.raw']
        with self.assertRaises(KeyError):provenance.join(mapping,recording,report,files.__getitem__)
        self.assertEqual(len(mapping['reconstructed_cases']),668)

    def test_first_failure_keeps_trace_while_all_owner_postchecks_and_borrow_close_run(self):
        f=InputFixture(self);b=f.binder;first=RuntimeError('actual protocol body first failure');calls=[]
        class ForeignRoot:
            def __str__(self):calls.append('str');raise AssertionError('foreign root hook')
            def __eq__(self,other):calls.append('eq');raise AssertionError('foreign root hook')
        original=f.ctx.sources.root;lease=None
        try:
            try:
                with b._native_owner() as (lease,ctx,sources):
                    sources.root=ForeignRoot()
                    raise first
            except RuntimeError as error:
                self.assertIs(error,first)
                self.assertIn('test_first_failure_keeps_trace_while_all_owner_postchecks_and_borrow_close_run',
                    [row.name for row in traceback.extract_tb(error.__traceback__)])
                self.assertGreaterEqual(len(error.__som_native_input_cleanup_secondary__),2)
            else:self.fail('original body error must propagate')
            self.assertEqual(calls,[])
            with self.assertRaisesRegex(ValueError,'active source lease is closed'):lease.verify_installed()
            self.assertFalse(f.domain.closed)
        finally:f.ctx.sources.root=original

if __name__=='__main__':unittest.main()

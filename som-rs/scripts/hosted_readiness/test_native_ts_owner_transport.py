"""GitHub-only owner consumer caller protocol. No actual full transport pass.

Real Core, whole proposed owner, whole fixed wrapper and producer source run.
Context/SourceBytes/transfer are explicit source constructor/admission/runtime
protocol doubles. Consumer keeps11 original helper bodies for exact12+4 root
and metadata checks, but verify_after_close is an explicit forwarding double.
No actual Context factory, private ZIP, AEAD, Node, tool or product is admitted.
"""
from __future__ import annotations
import ast,copy,hashlib,json,os
from pathlib import Path
import sys,tempfile
from types import ModuleType
import unittest
from unittest import mock
if os.environ.get('GITHUB_ACTIONS')!='true':raise RuntimeError('GitHub tests only')
assert sys.flags.isolated and sys.flags.no_site and sys.dont_write_bytecode
path=Path(__file__).absolute().with_name('test_native_active_controls.py')
protocol=ModuleType('_som_ts_owner_transport_core_protocol');protocol.__file__=str(path)
protocol.__cached__,protocol.__package__=None,''
exec(compile(path.read_bytes(),str(path),'exec',dont_inherit=True),protocol.__dict__)
BASE='som-rs/scripts/hosted_readiness/'
OWNER=BASE+'typescript_regression_owner.py'
CONTEXT=BASE+'product_runtime/full_product_context.py'
WRAPPER=BASE+'run_typescript_input_consumer.py'
CONSUMER=BASE+'typescript_input_consumer.py'
PRODUCER=BASE+'typescript_input_producer.py'
PROFILE=BASE+'inputs/typescript-regression-inputs-v1.json'
CONTEXT_DOUBLE="from pathlib import Path\nimport hashlib\nimport json\n_ACTIVE=None\nGETTER_CALLS=0\nclass SourceBytes:\n    def __init__(self,root,revision):self.root,self.revision,self.pins=root,revision,{}\n    def read(self,path):\n        raw=(self.root/path).read_bytes();self.pins[path]=hashlib.sha256(raw).hexdigest();return raw\n    def verify(self):return None\nclass Context:\n    def __init__(self,root,path,allowed):\n        self.repo,self.path=root,path\n        self.raw=path.read_bytes();self.data=json.loads(self.raw)\n        self.helper_revision=self.candidate_revision='a'*40\n        self.sources=SourceBytes(root,'a'*40)\n        self.qa_mode='native-qa-v1'\n        self.qa_source_authority={'admitted_repository_python_sources':allowed}\n        self.verified=0\n    def module(self,*args):raise AssertionError('unused protocol Context.module')\n    def verify(self):\n        assert self.path.read_bytes()==self.raw and json.loads(self.raw)==self.data\n        self.verified+=1\ndef context():\n    global GETTER_CALLS\n    GETTER_CALLS+=1\n    return _ACTIVE\n"
CONSUMER_DOUBLE='"""Caller protocol source double; actual transport/decrypt is never admitted."""\nfrom pathlib import Path\nimport hashlib,json,os,re,stat\nMAX=512*1024**2\nROOT_NAME=\'som-typescript-input-consumer\'\nPROTECTED_KEYS={\'product_work\',\'candidate_dir\',\'capture_out\',\'assembly_root\',\'python_inputs_root\',\'rust_inputs_root\',\'carrier_root\',\'runtime_root\',\'context_path\',\'baseline_inputs\',\'build_provenance_root\',\'current_companions_root\'}\nOPTIONAL_PROTECTED_KEYS={\'build_provenance_root\',\'current_companions_root\'}\nAUTO_CONTEXT_INPUT_KEYS=(\'wheel_root\',\'rust_tools_root\',\'cargo_root\',\'artifact_inputs\')\nCATALOG_KEYS={\'version\',\'job\',\'run_id\',\'run_attempt\',\'head_sha\',\'artifact_id\',\'artifact_digest\',\'index_sha256\',\'receipt_sha256\',\'profile_sha256\'}\nPRODUCER_JOB=\'typescript-regression-inputs\'\nHEX=re.compile(\'[0-9a-f]{64}\')\nCALLS=[]\ndef need(value, reason):\n    if not value: raise ValueError(reason)\n\ndef sha(raw): return hashlib.sha256(raw).hexdigest()\n\ndef parse(raw):\n    def pairs(rows):\n        result = {}\n        for key, value in rows:\n            need(key not in result, \'duplicate JSON key\')\n            result[key] = value\n        return result\n    return json.loads(raw, object_pairs_hook=pairs,\n        parse_constant=lambda _: (_ for _ in ()).throw(ValueError(\'nonfinite JSON\')))\n\ndef canonical(value):\n    return json.dumps(value, sort_keys=True, separators=(\',\', \':\'), ensure_ascii=False, allow_nan=False).encode()\n\ndef physical(path, directory=False):\n    path = Path(path).absolute()\n    need(\'..\' not in path.parts and not any(p.is_symlink() for p in (path, *path.parents)), \'physical path required\')\n    info = path.lstat()\n    need(stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode) and info.st_nlink == 1,\n         \'physical directory or single-link regular file required\')\n    return path\n\ndef file_identity(info):\n    return (info.st_dev,info.st_ino,info.st_mode,info.st_nlink,info.st_size,info.st_mtime_ns,info.st_ctime_ns)\n\ndef raw_file(path, maximum=MAX):\n    path = physical(path)\n    before = path.lstat()\n    need(before.st_size <= maximum, \'bounded data file required\')\n    with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK), \'rb\') as stream:\n        raw = stream.read(maximum + 1)\n        need(len(raw) == before.st_size and file_identity(os.fstat(stream.fileno())) == file_identity(before)\n             and file_identity(path.lstat()) == file_identity(before),\n             \'data changed during read\')\n    return raw\n\ndef relative(name):\n    need(type(name) is str and name and not name.startswith(\'/\') and \'\\\\\' not in name\n         and all(x not in {\'\',\'.\',\'..\'} for x in name.split(\'/\')),\'bounded relative name required\')\n    return name\n\ndef _roots(repo,protected_roots):\n    temporary=physical(os.environ[\'RUNNER_TEMP\'],True);root=temporary/ROOT_NAME\n    need(type(protected_roots) is dict and set(protected_roots)==PROTECTED_KEYS,\'exact named actual caller protection set required\')\n    baseline=protected_roots[\'baseline_inputs\']\n    need(type(baseline) is dict and 0<len(baseline)<=32\n         and all(type(k) is str and relative(k) for k in baseline),\n         \'complete named Context baseline input root map required\')\n    flat={k:v for k,v in protected_roots.items() if k!=\'baseline_inputs\' and k not in OPTIONAL_PROTECTED_KEYS}\n    flat.update({\'baseline_inputs.\'+k:v for k,v in baseline.items()})\n    for key in OPTIONAL_PROTECTED_KEYS:\n        if protected_roots[key] is not None:flat[key]=protected_roots[key]\n    for value in baseline.values():physical(value,True)\n    for key in OPTIONAL_PROTECTED_KEYS:\n        if protected_roots[key] is not None:physical(protected_roots[key],True)\n    context=parse(raw_file(protected_roots[\'context_path\']))\n    need(context.get(\'version\')==\'som.full-product-context.v1\' and context.get(\'qa_mode\')==\'native-qa-v1\'\n         and Path(context[\'repo\'])==Path(repo),\'actual existing native base Context required before consumer mkdir\')\n    bindings={\'product_work\':\'work_root\',\'assembly_root\':\'assembly_root\',\'python_inputs_root\':\'python_inputs_root\',\n        \'rust_inputs_root\':\'rust_inputs_root\',\'carrier_root\':\'carrier_root\',\'runtime_root\':\'runtime_root\',\n        \'build_provenance_root\':\'build_provenance_root\',\'current_companions_root\':\'current_companions_root\'}\n    for key,field in bindings.items():\n        actual=context.get(field)\n        need((None if actual is None else str(Path(actual)))==(None if protected_roots[key] is None else str(Path(protected_roots[key]))),\n             \'actual Context protection value differs: \'+field)\n    need(type(context.get(\'baseline_inputs\')) is dict\n         and {k:str(Path(v)) for k,v in context[\'baseline_inputs\'].items()}=={k:str(Path(v)) for k,v in baseline.items()},\n         \'full actual Context baseline root map differs\')\n    for key in AUTO_CONTEXT_INPUT_KEYS:\n        need(type(context.get(key)) is str and context[key],\'actual original Context input field required: \'+key)\n        physical(context[key],key!=\'artifact_inputs\')\n        flat[\'context.\'+key]=context[key]\n    for value in [repo,*flat.values()]:\n        need(Path(value).is_absolute(),\'absolute actual protected path required\');path=Path(value)\n        need(\'..\' not in path.parts and not any(p.is_symlink() for p in (path,*path.parents)),\'physical protected path required\')\n        need(path!=root and path not in root.parents and root not in path.parents,\'input consumer overlaps protected source/product root\')\n    return root,root/\'evidence\',root/\'restored/run\'\n\ndef _protection_snapshot(repo,protected_roots):\n    result={\'repository\':str(repo)}\n    for key,value in protected_roots.items():\n        result[key]={k:str(Path(v)) for k,v in value.items()} if key==\'baseline_inputs\' else None if value is None else str(Path(value))\n    return result\n\ndef _catalog(value,profile_sha,helper):\n    need(type(value) is dict and set(value)==CATALOG_KEYS\n         and value[\'version\']==\'som.ts-input-producer-catalog-draft.v1\'\n         and value[\'job\']==PRODUCER_JOB and value[\'run_id\']==os.environ[\'GITHUB_RUN_ID\']\n         and value[\'run_attempt\']==os.environ[\'GITHUB_RUN_ATTEMPT\'] and value[\'head_sha\']==helper,\n         \'trusted source-workflow needs producer catalog required\')\n    need(type(value[\'artifact_id\']) is int and value[\'artifact_id\']>0\n         and re.fullmatch(\'sha256:[0-9a-f]{64}\',value[\'artifact_digest\'])\n         and all(type(value[k]) is str and HEX.fullmatch(value[k]) for k in (\'index_sha256\',\'receipt_sha256\',\'profile_sha256\'))\n         and value[\'profile_sha256\']==profile_sha,\'complete producer artifact/profile identities differ\')\n    return parse(canonical(value))\n\ndef verify_after_close(sources,*,helper_revision,candidate_revision,context_inputs,producer_catalog,trusted_consumer_proof,protected_roots):\n    # Explicit API forwarding double. No ZIP/AEAD/product pass is fabricated.\n    assert helper_revision==candidate_revision==sources.revision\n    base=parse(raw_file(protected_roots[\'context_path\']))\n    assert context_inputs==base[\'typescript_regression_inputs\']\n    assert producer_catalog==base[\'typescript_regression_input_producer\']\n    assert trusted_consumer_proof==base[\'typescript_regression_input_consumer\']\n    _roots(sources.root,protected_roots)\n    CALLS.append({\'sources\':sources,\'protected_roots\':protected_roots,\'catalog\':producer_catalog,\'proof\':trusted_consumer_proof})\n    return {\'version\':\'som.typescript-input-postclose-verified.v1\',\'receipt_sha256\':trusted_consumer_proof[\'receipt_sha256\'],\n        \'producer_catalog_sha256\':sha(canonical(producer_catalog)),\'evidence_manifest_sha256\':trusted_consumer_proof[\'evidence_manifest_sha256\'],\n        \'role_counts\':{},\'original_actual_AEAD_step_proof_reverified\':True,\n        \'AEAD_reexecution\':\'not performed; verifier has no keys\',\'historical_PID_queries\':False,\n        \'native_qa_source_admission_complete\':False,\'strict_live_2bce_gates\':\'UNRUN\',\'product_acceptance_passed\':False}\n'
class OwnerTransportFixture(protocol.Fixture):
    def __init__(self,case):
        super().__init__(case,caller='def run(controls):\n    return controls.load_module("owner")._full_input_transport()\n')
        self.temporary=Path(self.stack.enter_context(tempfile.TemporaryDirectory(prefix='som-ts-consumer-caller-')))
        self.env={'RUNNER_TEMP':str(self.temporary),'GITHUB_RUN_ID':'101','GITHUB_RUN_ATTEMPT':'1'}
        self.stack.enter_context(mock.patch.dict(os.environ,self.env))
        self.context_module=self.domain.load_known('runtime.context',self.domain.prepare('runtime.context'),None)
        context_path=self.temporary/'som-callback-context/context.json';context_path.parent.mkdir()
        baseline=self.temporary/'before';baseline.mkdir()
        self.base={'version':'som.full-product-context.v1','qa_mode':'native-qa-v1','repo':str(self.root),
            'helper_revision':'a'*40,'candidate_revision':'a'*40,'baseline_inputs':{BASE+'inputs/recorded-before-profile-v1.json':str(baseline)}}
        for key in ('work_root','assembly_root','python_inputs_root','rust_inputs_root','carrier_root','runtime_root','wheel_root','rust_tools_root','cargo_root','build_provenance_root','current_companions_root'):
            folder=self.temporary/key;folder.mkdir();self.base[key]=str(folder)
        artifact=self.temporary/'artifact-inputs.json';artifact.write_bytes(b'{}');self.base['artifact_inputs']=str(artifact)
        profile_sha=hashlib.sha256((self.root/PROFILE).read_bytes()).hexdigest()
        self.catalog={'version':'som.ts-input-producer-catalog-draft.v1','job':'typescript-regression-inputs','run_id':'101','run_attempt':'1',
            'head_sha':'a'*40,'artifact_id':1,'artifact_digest':'sha256:'+'1'*64,'index_sha256':'2'*64,'receipt_sha256':'3'*64,'profile_sha256':profile_sha}
        self.proof={'version':'som.typescript-input-consumer-step-proof.v1','run_id':'101','run_attempt':'1','head_sha':'a'*40,
            'workflow_path':'.github/workflows/rust-hosted-readiness.yml','workflow_sha256':'4'*64,'consumer_job':'required-callbacks',
            'consumer_step':'typescript_input_consumer','producer_catalog_sha256':hashlib.sha256(self.canonical(self.catalog).encode()).hexdigest(),
            'receipt_sha256':'5'*64,'evidence_manifest_sha256':'6'*64,'artifact_id':1,'artifact_digest':'sha256:'+'1'*64,'index_sha256':'2'*64,'product_acceptance_passed':False}
        self.base.update(typescript_regression_inputs={'roots':{},'siblings':{},'receipt':'protocol-only','artifact':{}},
            typescript_regression_input_producer=copy.deepcopy(self.catalog),typescript_regression_input_consumer=copy.deepcopy(self.proof))
        context_path.write_text(self.canonical(self.base))
        self.ctx=self.context_module.Context(self.root,context_path,list(self.raw));self.context_module._ACTIVE=self.ctx
        self.owner=self.domain.load_known('owner',self.domain.prepare('owner'),None)
        self.data=self.domain.load_known('ts.transport.consumer-data',self.domain.prepare('ts.transport.consumer-data'),None)
        self.stack.enter_context(mock.patch.dict(os.environ,{'TS_INPUT_PRODUCER_CATALOG':self.canonical(self.catalog),'TS_INPUT_CONSUMER_PROOF':self.canonical(self.proof)}))
        self.stack.enter_context(mock.patch.dict(os.environ,{},clear=False))
        for key in ('M6_INPUTS_KEYRING','M6_INPUTS_KEY'):os.environ.pop(key,None)
        self.stack.callback(self.finish)
    @staticmethod
    def canonical(value):return json.dumps(value,sort_keys=True,separators=(',',':'))
    def add_role(self,role,path,raw,**kwargs):
        row=super().add_role(role,path,raw,**kwargs)
        if role!='m6.api.producer':return row
        super().add_role('runtime.context',CONTEXT,CONTEXT_DOUBLE,module='_som_owner_transport_context_double',
            callers=[{'role':'owner','function':'_input_transport_context'}])
        bindings={'__som_native_active_controls__':{'kind':'active-controls'},
            '__som_native_ContextModule__':{'kind':'role-module','role':'runtime.context'},
            '__som_native_Context__':{'kind':'role-export','role':'runtime.context','attribute':'Context'},
            '__som_native_SourceBytes__':{'kind':'role-export','role':'runtime.context','attribute':'SourceBytes'},
            '__som_native_context_getter__':{'kind':'role-export','role':'runtime.context','attribute':'context'}}
        super().add_role('owner',OWNER,(protocol.REPO/OWNER).read_bytes(),module='_som_owner_transport_actual_owner',
            bindings=copy.deepcopy(bindings),callers=[{'role':'caller','function':'run'}])
        for r,p,n,body in [('ts.owner.input-consumer-wrapper',WRAPPER,'_som_ts_owner_input_consumer_wrapper',(protocol.REPO/WRAPPER).read_bytes()),
                ('ts.transport.common',PRODUCER,'_som_ts_input_transport_common',(protocol.REPO/PRODUCER).read_bytes()),
                ('ts.transport.consumer-data',CONSUMER,'_som_ts_input_consumer_data',CONSUMER_DOUBLE)]:
            super().add_role(r,p,body,module=n,callers=[{'role':'owner','function':'_full_input_transport'}])
        for p in (PROFILE,'.github/workflows/rust-hosted-readiness.yml','.github/workflows/rust-same-candidate-products.yml'):
            body=(protocol.REPO/p).read_bytes();self.raw[p]=body;target=self.root/p;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(body)
        return row

class OwnerConsumerCallerTests(unittest.TestCase):
    def test_exact_actual_wrapper_protection_and_outside_step_outputs_reach_fixed_consumer(self):
        f=OwnerTransportFixture(self);report=f.run();self.assertEqual(len(f.data.CALLS),1)
        call=f.data.CALLS[0];self.assertIs(call['sources'],f.ctx.sources)
        self.assertEqual(call['catalog'],f.catalog);self.assertEqual(call['proof'],f.proof)
        self.assertEqual(set(call['protected_roots']),f.data.PROTECTED_KEYS)
        self.assertEqual(call['protected_roots']['baseline_inputs'],f.base['baseline_inputs'])
        self.assertEqual(call['protected_roots']['candidate_dir'],str(f.temporary/'som-callback-raw/gate/candidate'))
        self.assertEqual(call['protected_roots']['capture_out'],str(f.temporary/'som-callback-raw/gate/owner'))
        for key in ('build_provenance_root','current_companions_root'):self.assertEqual(call['protected_roots'][key],f.base[key])
        self.assertGreater(f.ctx.verified,0);self.assertFalse(report['product_acceptance_passed'])
        self.assertEqual(report['strict_live_2bce_gates'],'UNRUN')

    def test_missing_outside_consumer_step_output_refuses_before_verifier(self):
        f=OwnerTransportFixture(self)
        with mock.patch.dict(os.environ,{},clear=False):
            os.environ.pop('TS_INPUT_CONSUMER_PROOF',None)
            with self.assertRaisesRegex(ValueError,'actual preceding consumer step output required'):f.run()
        self.assertEqual(f.data.CALLS,[])

    def test_Context_authored_proof_cannot_replace_the_outside_workflow_step_output(self):
        f=OwnerTransportFixture(self)
        f.ctx.data['typescript_regression_input_consumer']['receipt_sha256']='9'*64
        f.ctx.raw=f.canonical(f.ctx.data).encode();f.ctx.path.write_bytes(f.ctx.raw)
        with self.assertRaisesRegex(ValueError,'outside-capture Root needs and consumer step outputs'):f.run()
        self.assertEqual(f.data.CALLS,[])

    def test_missing_actual_retained_Context_refuses_before_getter_or_consumer(self):
        f=OwnerTransportFixture(self);f.context_module._ACTIVE=None
        with self.assertRaisesRegex(ValueError,'retained actual native Context owner required'):f.run()
        self.assertEqual(f.context_module.GETTER_CALLS,0);self.assertEqual(f.data.CALLS,[])

    def test_foreign_consumer_export_refuses_before_foreign_hook(self):
        f=OwnerTransportFixture(self);calls=[];original=f.data.verify_after_close
        def foreign(*args,**kwargs):calls.append(True);raise AssertionError('foreign verifier hook')
        f.data.verify_after_close=foreign
        try:
            with self.assertRaises(ValueError):f.run()
            self.assertEqual(calls,[])
        finally:f.data.verify_after_close=original

    def test_postclose_report_waits_for_Domain_and_writer_failure_keeps_original_exception(self):
        f=OwnerTransportFixture(self);evidence=f.temporary/'verification';evidence.mkdir()
        first=RuntimeError('original Domain refusal');later=OSError('postclose evidence failure');original=Path.open
        def opened(path,*args,**kwargs):
            if path.name=='input-transport-postclose.json':raise later
            return original(path,*args,**kwargs)
        with mock.patch.object(Path,'open',new=opened):
            with self.assertRaises(RuntimeError)as caught:
                with f.owner._retain_input_transport_postclose(evidence,{'protocol_only':True}):
                    self.assertEqual(list(evidence.iterdir()),[])
                    raise first
        self.assertIs(caught.exception,first)
        self.assertEqual(first.__som_typescript_secondary_failures__[-1]['role'],'owner_input_transport_postclose_evidence')

    def test_postclose_writer_failure_without_Domain_error_is_propagated(self):
        f=OwnerTransportFixture(self);evidence=f.temporary/'verification';evidence.mkdir();later=OSError('first evidence failure');original=Path.open
        def opened(path,*args,**kwargs):
            if path.name=='input-transport-postclose.json':raise later
            return original(path,*args,**kwargs)
        with mock.patch.object(Path,'open',new=opened):
            with self.assertRaises(OSError)as caught:
                with f.owner._retain_input_transport_postclose(evidence,{'protocol_only':True}):pass
        self.assertIs(caught.exception,later)

if __name__=='__main__':unittest.main()

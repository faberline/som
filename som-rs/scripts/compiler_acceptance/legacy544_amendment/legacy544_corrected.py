"""Additive adapter for the exact QA57 runner. No acceptance logic changes."""
import hashlib
from datetime import datetime,timezone
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import traceback

sys.dont_write_bytecode=True
HERE=Path(__file__).resolve().parent
RUNNER_SHA='35df9239135bddce5e50e6068b62f8b7d34c66301d0359c8e1f083d046eca3a0'
QA_SHA='a3797384a1010de52ef3b86c5de22d18dd9c062f5aeb60b8282d35b033a23da3'
SOURCE_SHA='1e80e26f0128e99fe7fa3c4fcf6edae354e51bf75b13f3df70833107906d78bd'
SOURCE_ARCHIVE_SHA='1a13d73f499057cc9be00b09af5459a2c97720b54db18d338545d63bb39df575'
BINARY_SHA='3fad3cb7eafb35274f6a37f9a8a371f93807a6b5fedf3e9fe0e074cbf5bfef02'

def now():return datetime.now(timezone.utc).isoformat()

def load_module(name,path):
    spec=importlib.util.spec_from_file_location(name,path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module

codec=load_module('som_legacy_native_evidence',HERE/'native_evidence.py')
sha=lambda path:hashlib.sha256(Path(path).read_bytes()).hexdigest()

class ChildEnvironment:
    """Replace only the exact legacy100 checker child environment."""
    def __init__(self,delegate,repo,python):
        self.delegate=delegate;self.repo=Path(repo);self.python=python;self.records=[]
    def __getattr__(self,name):return getattr(self.delegate,name)
    def run(self,*args,**kwargs):
        argv=args[0]if args else kwargs.get('args')
        checker=str(self.repo/'som-core/scripts/verify_service_v3.py')
        if isinstance(argv,(list,tuple)) and len(argv)>1 and str(argv[1])==checker:
            expected_prefix=[self.python,checker,'--cases',str(self.repo/'som-core/data/service-regression-v4/heldout.jsonl'),'--heldout-index']
            if (len(argv)!=8 or list(argv[:5])!=expected_prefix or argv[6]!='--files-json'
                    or not str(argv[5]).isdigit() or not Path(argv[7]).is_absolute()):
                raise RuntimeError('refuse altered legacy100 checker invocation')
            if kwargs.get('env')is not None:raise RuntimeError('unexpected explicit legacy100 child environment')
            env=dict(os.environ)
            required=[str(self.repo/'som-core/src'),str(self.repo/'som-core/scripts')]
            old=env.get('PYTHONPATH','').split(os.pathsep)if env.get('PYTHONPATH')else []
            env['PYTHONPATH']=os.pathsep.join(required+[p for p in old if p not in required])
            kwargs={**kwargs,'env':env}
            self.records.append({'argv':list(argv),'PYTHONPATH':env['PYTHONPATH'],'changed_keys':['PYTHONPATH']})
        return self.delegate.run(*args,**kwargs)

def install_adapters(module,repo):
    proxy=ChildEnvironment(module.subprocess,repo,sys.executable)
    module.subprocess=proxy
    module.write_json=codec.atomic_write
    return proxy

def output_argument(repo):
    if sys.argv.count('--out')!=1:raise ValueError('one explicit --out is required')
    if '--inventory-only'in sys.argv or '--probe'in sys.argv:raise ValueError('adapter is for full HTTP acceptance only')
    output=Path(sys.argv[sys.argv.index('--out')+1])
    base=repo/'.local/rust-compiler-goal/acceptance/legacy544'
    if not output.is_absolute() or not output.is_relative_to(base)or output==base or output.exists():
        raise ValueError('fresh absolute legacy544 output is required')
    if '..'in output.parts or any(p.is_symlink()for p in [output,*output.parents]):
        raise ValueError('unsafe or linked output path')
    return output

def manifest_arguments():
    actual=[sys.executable,*sys.argv]
    binding={}
    for flag,key in [('--amendment-manifest','path'),('--amendment-manifest-sha256','sha256')]:
        if sys.argv.count(flag)!=1:raise ValueError('one explicit '+flag+' is required')
        index=sys.argv.index(flag)
        if index+1>=len(sys.argv):raise ValueError('missing '+flag+' value')
        binding[key]=sys.argv[index+1];del sys.argv[index:index+2]
    if not Path(binding['path']).is_absolute()or len(binding['sha256'])!=64:raise ValueError('invalid manifest binding')
    return actual,binding

def qualify_inputs(repo,binding):
    expected={str(Path(binding['path'])):binding['sha256']}
    if sha(binding['path'])!=binding['sha256']:raise RuntimeError('amendment manifest seal differs')
    manifest=json.loads(Path(binding['path']).read_text())
    if (manifest.get('version')!='som.legacy544-runner-amendment.v1'
            or manifest.get('repo')!=str(repo)or manifest.get('acceptance_criteria_changed')is not False):
        raise RuntimeError('amendment manifest schema or scope differs')
    files=manifest.get('additive_files',{})
    if not {'legacy544_corrected.py','native_evidence.py'}.issubset(files):raise RuntimeError('missing additive code binding')
    for name,digest in files.items():
        if Path(name).name!=name:raise RuntimeError('additive filename must be flat')
        expected[str(HERE/name)]=digest
    for key in ('original_runner','original_qa57','candidate_source','candidate_source_archive','candidate_binary','public127','public_execution'):
        entry=manifest[key];expected[entry['path']]=entry['sha256']
    for entry in manifest['preserved_failed003'].values():expected[entry['path']]=entry['sha256']
    source_path=repo/'.local/rust-compiler-goal/candidate-003/source.json'
    qa_path=repo/'.local/rust-compiler-goal/candidate-003/acceptance-tools.json'
    if manifest['candidate_source']!={'path':str(source_path),'sha256':SOURCE_SHA}:raise RuntimeError('candidate source JSON differs')
    if manifest['original_qa57']!={'path':str(qa_path),'sha256':QA_SHA}:raise RuntimeError('QA57 JSON differs')
    pinned={'original_runner':{'path':str(repo/'som-rs/scripts/compiler_acceptance/legacy544/runner.py'),'sha256':RUNNER_SHA},
            'candidate_source_archive':{'path':str(source_path.parent/'source.tar'),'sha256':SOURCE_ARCHIVE_SHA},
            'candidate_binary':{'path':str(source_path.parent/'som'),'sha256':BINARY_SHA}}
    if any(manifest[key]!=entry for key,entry in pinned.items()):raise RuntimeError('pinned runner/source/archive/binary binding differs')
    if Path(manifest['public127']['path'])!=repo/'.local/rust-compiler-goal/acceptance/public-products-003b/result.json':
        raise RuntimeError('public127 prerequisite path differs')
    for path,count in [(source_path,73),(qa_path,57)]:
        document=json.loads(path.read_text())
        if len(document['files'])!=count:raise RuntimeError('bound source inventory count differs')
        for relative,digest in document['files'].items():
            name=Path(relative)
            if name.is_absolute()or '..'in name.parts:raise RuntimeError('source file path outside repository')
            expected[str(repo/name)]=digest
    public=json.loads(Path(manifest['public127']['path']).read_text())
    if (public.get('complete')is not True or public.get('required')!=127 or public.get('passed')!=127
            or len(public.get('records',[]))!=127 or not all(row.get('passed')is True for row in public['records'])):
        raise RuntimeError('existing complete public127 prerequisite missing')
    if public.get('candidate_binary_sha256')!=BINARY_SHA or public.get('candidate_source_json_sha256')!=SOURCE_SHA:
        raise RuntimeError('public127 candidate/source binding differs')
    controller_path=repo/'.local/rust-compiler-goal/acceptance/controller-003d/result.json'
    if Path(manifest['public_execution']['path'])!=controller_path:raise RuntimeError('public execution controller path differs')
    controller=json.loads(controller_path.read_text());jobs={row['name']:row for row in controller['jobs']}
    if (controller['phase']!='legacy_gate_failed'or controller.get('accepted')is not False
            or jobs['public127']['exit_code']!=0 or jobs['legacy544']['exit_code']!=1):
        raise RuntimeError('historical controller scope differs; cannot claim overall success')
    actual={path:sha(path)for path in expected}
    if actual!=expected:raise RuntimeError('bound source/QA/amendment/public input changed')
    return actual

def main():
    repo=Path(os.environ['SOM_REPO']).resolve();output=output_argument(repo)
    actual_argv,binding=manifest_arguments()
    source=repo/'som-rs/scripts/compiler_acceptance/legacy544/runner.py'
    qa=repo/'.local/rust-compiler-goal/candidate-003/acceptance-tools.json'
    descriptor={'version':'som.legacy544-additive-execution.v1','started_at':now(),'cwd':str(Path.cwd()),
                'runner':{'path':str(source),'sha256':None},'original_qa57':{'path':str(qa),'sha256':None},
                'wrapper':{'path':str(Path(__file__).resolve()),'sha256':sha(__file__)},
                'codec':{'path':str(HERE/'native_evidence.py'),'sha256':sha(HERE/'native_evidence.py')},
                'argv':actual_argv,'delegated_argv':[sys.executable,*sys.argv],'amendment_manifest':binding,
                'native_serialization':'explicit date/datetime tags only; unknown native types refuse',
                'acceptance_criteria_changed':False,'stitched_prior_jobs':False,'required_jobs':544,'required_unique_prompts':540}
    proxy=None;code=1;error=None;before=None
    try:
        before=qualify_inputs(repo,binding);descriptor['input_hashes_before']=before
        descriptor['runner']['sha256']=sha(source);descriptor['original_qa57']['sha256']=sha(qa)
        if descriptor['runner']['sha256']!=RUNNER_SHA or descriptor['original_qa57']['sha256']!=QA_SHA:
            raise RuntimeError('original runner or QA57 identity differs')
        if json.loads(qa.read_text())['files']['som-rs/scripts/compiler_acceptance/legacy544/runner.py']!=RUNNER_SHA:
            raise RuntimeError('runner is not the exact QA57 file')
        module=load_module('som_frozen_legacy544',source)
        if module.REPO!=repo:raise RuntimeError('original runner repo differs')
        proxy=install_adapters(module,repo)
        from m3_local_fixture import interruption
        with interruption():module.main()
        code=0
    except SystemExit as failure:
        code=failure.code if type(failure.code)is int else 1
        if code not in (0,2):error='original runner exited '+str(code)
    except BaseException as failure:
        # No unknown object is copied into the emergency record.
        error=type(failure).__name__+': '+str(failure)
        descriptor['traceback']=traceback.format_exc()
        if isinstance(failure,codec.EvidenceTypeError):
            descriptor['serialization_refusal']={'pointer':failure.pointer,'native_type':failure.type_name}
    finally:
        if before is not None:
            try:
                after={path:sha(path)for path in before};descriptor['input_hashes_after']=after
                descriptor['inputs_unchanged']=after==before
                if after!=before:error=(error+'; 'if error else '')+'bound source/QA input changed';code=1
            except OSError as failure:
                descriptor['inputs_unchanged']=False;error=(error+'; 'if error else '')+'bound input unavailable: '+str(failure);code=1
        else:descriptor['inputs_unchanged']=False
        descriptor['ended_at']=now()
        descriptor.update(exit_code=code,error=error,child_environment_calls=[]if proxy is None else proxy.records,
                          completed_without_wrapper_error=error is None)
        output.mkdir(parents=True,exist_ok=True)
        codec.atomic_write(output/'additive-wrapper-execution.json',descriptor)
    return code

if __name__=='__main__':raise SystemExit(main())

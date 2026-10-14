"""Pure offline qualification. All process boundaries use a recording stub."""
from datetime import date,datetime,timedelta,timezone
import contextlib
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
from types import SimpleNamespace,ModuleType
import sqlalchemy as sa
from sqlalchemy.sql.elements import quoted_name
sys.dont_write_bytecode=True
HERE=Path(__file__).parent
ROOT=Path('/Users/chrischeng/faberlines/som')
def module(name,path):
    spec=importlib.util.spec_from_file_location(name,path);v=importlib.util.module_from_spec(spec);spec.loader.exec_module(v);return v
w=module('wrapper',HERE/'legacy544_corrected.py');c=w.codec
work=Path(tempfile.mkdtemp(prefix='qualification-',dir=HERE));checks=[]
def test(name,passed):checks.append({'name':name,'passed':passed is True})
def refusal(name,fn):
    try:fn()
    except (TypeError,ValueError,RuntimeError):test(name,True)
    else:test(name,False)
for name,value in [('naive',datetime(2026,10,14,7,2,30,123456)),('utc',datetime(2026,10,14,7,2,30,123456,tzinfo=timezone.utc)),
                   ('offset',datetime(2026,10,14,7,2,30,tzinfo=timezone(timedelta(hours=5,minutes=30),'fixed_name'))),
                   ('fold',datetime(2026,10,14,1,30,fold=1))]:
    tag=json.loads(c.serialize(value))[c.TAG]
    restored=datetime.fromisoformat(tag['iso8601']).replace(fold=tag['fold'])
    test(name+'.date_time_roundtrip',restored==value and restored.fold==value.fold and tag['timezone_name']==value.tzname()
         and tag['aware']==(value.utcoffset()is not None))
test('date_keeps_native_type',json.loads(c.serialize(date(2026,10,14)))=={c.TAG:{'type':'date','iso8601':'2026-10-14'}})
native={'records':[{'passed':True,'observations':{'created':datetime(2026,10,14,7,2,30,tzinfo=timezone.utc),'day':date(2026,10,14)}}]}
saved=json.loads(c.serialize(native))
test('emergency_pending_reload_tags_preserved',json.loads(c.serialize(saved))==saved)
test('pending_native_tags_stay_typed',saved['records'][0]['observations']['created'][c.TAG]['type']=='datetime')
plain={'status':'passed','passed':True,'boolean':False,'integer':0,'whole_float':0.0,'negative_zero':-0.0,'unicode':'中文','rows':[None,1,1.25]}
encoded=c.serialize(plain);expected=(json.dumps(plain,ensure_ascii=False,indent=2)+'\n').encode()
test('ordinary_json_bytes_unchanged',encoded==expected)
parsed=json.loads(encoded)
test('float_sign_bits_preserved',struct.pack('>d',parsed['negative_zero'])==struct.pack('>d',-0.0))
test('bool_int_float_types_preserved',type(parsed['boolean'])is bool and type(parsed['integer'])is int and type(parsed['whole_float'])is float)
engine=sa.create_engine('sqlite:///:memory:');metadata=sa.MetaData()
table=sa.Table(quoted_name('luna_evidence_rows',quote=True),metadata,
    sa.Column(quoted_name('id',quote=None),sa.Integer,primary_key=True),
    sa.Column(quoted_name('q_true',quote=True),sa.String),
    sa.Column(quoted_name('q_false',quote=False),sa.String),
    sa.Column(quoted_name('q_none',quote=None),sa.String),
    sa.Column(quoted_name('created_at',quote=None),sa.DateTime))
metadata.create_all(engine);stamp=datetime(2026,10,14,7,2,30,123456)
with engine.begin()as connection:connection.execute(table.insert(),{'q_true':'a','q_false':'b','q_none':'c','created_at':stamp})
with engine.connect()as connection:sqlalchemy_row=dict(connection.execute(sa.select(table)).mappings().one())
test('sqlalchemy_inmemory_row_uses_quoted_name',all(type(key)is quoted_name for key in sqlalchemy_row))
test('sqlalchemy_quoted_name_known_quote_flags',[key.quote for key in sqlalchemy_row if key!='created_at']==[None,True,False,None])
sqlalchemy_encoded=c.serialize(sqlalchemy_row);sqlalchemy_json=json.loads(sqlalchemy_encoded)
test('quoted_name_json_key_text_and_datetime_preserved',list(sqlalchemy_json)==['id','q_true','q_false','q_none','created_at'] and
     sqlalchemy_json['q_true']=='a' and sqlalchemy_json['q_false']=='b' and sqlalchemy_json['q_none']=='c' and
     sqlalchemy_json['created_at'][c.TAG]['type']=='datetime' and sqlalchemy_json['created_at'][c.TAG]['iso8601']=='2026-10-14T07:02:30.123456')
test('quoted_name_loaded_tag_reload_validates',c.serialize(sqlalchemy_json)==sqlalchemy_encoded)
engine.dispose()
for name,value in [('bytes',b'x'),('object',object()),('set',{1}),('nan',float('nan')),('inf',float('inf')),('key',{1:'x'}),('marker',{c.TAG:{'type':'date','iso8601':'forged'}})]:
    refusal('unknown_'+name,lambda:c.serialize(value))
refusal('unknown_object_key',lambda:c.serialize({object():'x'}))
try:c.serialize({1:'x'})
except c.EvidenceTypeError as e:test('unknown_key_error_reports_actual_type',e.pointer=='' and e.type_name=='unsupported dictionary key type builtins.int')
else:test('unknown_key_error_reports_actual_type',False)
try:c.serialize({'escaped/a~b':[object()]})
except c.EvidenceTypeError as e:test('unknown_error_pointer_exact',e.pointer=='/escaped~1a~0b/0' and e.type_name=='builtins.object')
path=work/'atomic.json';path.write_bytes(b'OLD\n')
refusal('serialization_refuses_before_overwrite',lambda:c.atomic_write(path,{'x':object()}))
test('old_bytes_remain_on_serializer_error',path.read_bytes()==b'OLD\n')
original_replace=c.os.replace
def failed_replace(*args):raise OSError('unit replace failure')
c.os.replace=failed_replace
try:
    try:c.atomic_write(path,{'x':date(2026,10,14)})
    except OSError:test('atomic_replace_failure_reported',True)
    else:test('atomic_replace_failure_reported',False)
finally:c.os.replace=original_replace
test('old_bytes_remain_on_replace_failure',path.read_bytes()==b'OLD\n')
test('temporary_files_cleaned',not list(work.glob('.atomic.json.*.tmp')))
c.atomic_write(path,{'x':date(2026,10,14)})
test('atomic_valid_native_write',json.loads(path.read_text())['x'][c.TAG]['type']=='date')

class Delegate:
    def __init__(self):self.calls=[]
    def run(self,*args,**kwargs):self.calls.append((args,kwargs));return SimpleNamespace(returncode=7,stdout='out',stderr='err')
delegate=Delegate();proxy=w.ChildEnvironment(delegate,ROOT,sys.executable)
argv=[sys.executable,str(ROOT/'som-core/scripts/verify_service_v3.py'),'--cases',str(ROOT/'som-core/data/service-regression-v4/heldout.jsonl'),'--heldout-index','99','--files-json',str(work/'response.json')]
old_env=dict(os.environ)
os.environ['PYTHONPATH']='/private/tmp/preexisting';os.environ['UNIT_ENV_VALUE']='unchanged'
try:
    result=proxy.run(argv,capture_output=True,text=True,check=False,timeout=180)
    actual_args,kwargs=delegate.calls[-1];env=kwargs['env']
    test('child_exact_argv_unchanged',actual_args==(argv,))
    test('child_exit_not_reclassified',result.returncode==7 and result.stdout=='out' and result.stderr=='err')
    test('child_only_pythonpath_changed',{k for k in env if env[k]!=os.environ.get(k)}=={'PYTHONPATH'} and env['UNIT_ENV_VALUE']=='unchanged')
    test('child_exact_public_path_priority',env['PYTHONPATH'].split(os.pathsep)==[str(ROOT/'som-core/src'),str(ROOT/'som-core/scripts'),'/private/tmp/preexisting'])
    test('child_flags_timeout_unchanged',{k:v for k,v in kwargs.items()if k!='env'}=={'capture_output':True,'text':True,'check':False,'timeout':180})
    other=['unit-nonexecuted','other'];proxy.run(other,text=False,timeout=3)
    test('other_child_unmodified',delegate.calls[-1]==((other,),{'text':False,'timeout':3}))
    changed=list(argv);changed[3]='/private/tmp/other-cases'
    refusal('altered_legacy_checker_args_refused',lambda:proxy.run(changed))
    refusal('unexpected_explicit_child_env_refused',lambda:proxy.run(argv,env={}))
finally:
    os.environ.clear();os.environ.update(old_env)

original_loader=w.load_module;original_out=w.output_argument;original_manifest_args=w.manifest_arguments;original_qualify=w.qualify_inputs;original_sysargv=sys.argv[:]
old_fixture=sys.modules.get('m3_local_fixture');fixture=ModuleType('m3_local_fixture')
fixture.interruption=contextlib.nullcontext;sys.modules['m3_local_fixture']=fixture
old_som_repo=os.environ.get('SOM_REPO');os.environ['SOM_REPO']=str(ROOT)
try:
    for name,fault,expected_code in [('success',None,0),('case_failure',SystemExit(2),2),('unknown_type',c.EvidenceTypeError('/records/0/native','builtins.object'),1),('ordinary_error',RuntimeError('unit'),1)]:
        destination=work/name
        def original_main(error=fault):
            if error:raise error
            raise SystemExit(0)
        fake=SimpleNamespace(REPO=ROOT,subprocess=Delegate(),write_json=None,main=original_main)
        w.load_module=lambda *args:fake;w.output_argument=lambda repo:destination
        w.manifest_arguments=lambda:([sys.executable,*sys.argv],{'path':'/private/tmp/unit-only-manifest','sha256':'a'*64})
        w.qualify_inputs=lambda *args:{str(HERE/'native_evidence.py'):w.sha(HERE/'native_evidence.py')}
        sys.argv=['wrapper','--out',str(destination)]
        code=w.main();receipt=json.loads((destination/'additive-wrapper-execution.json').read_text())
        test('delegated_'+name+'_exit_and_safe_receipt',code==expected_code and receipt['exit_code']==expected_code
             and receipt['acceptance_criteria_changed']is False and receipt['required_jobs']==544
             and receipt['required_unique_prompts']==540 and receipt['stitched_prior_jobs']is False)
        test('delegated_'+name+'_metadata_sealed',receipt['inputs_unchanged']is True and receipt['input_hashes_before']==receipt['input_hashes_after']
             and receipt['argv'][0]==sys.executable and receipt['started_at']<=receipt['ended_at']and receipt['cwd']==str(Path.cwd()))
        if name=='unknown_type':test('unknown_failure_record_keeps_location_type',receipt['serialization_refusal']=={'pointer':'/records/0/native','native_type':'builtins.object'})
    sentinel=work/'source-sentinel.txt';sentinel.write_text('before')
    destination=work/'changed_source'
    def changed_source_main():sentinel.write_text('after');raise SystemExit(0)
    fake=SimpleNamespace(REPO=ROOT,subprocess=Delegate(),write_json=None,main=changed_source_main)
    w.load_module=lambda *args:fake;w.output_argument=lambda repo:destination
    w.qualify_inputs=lambda *args:{str(sentinel):w.sha(sentinel)}
    sys.argv=['wrapper','--out',str(destination)]
    code=w.main();receipt=json.loads((destination/'additive-wrapper-execution.json').read_text())
    test('changed_bound_source_forces_nonzero',code==1 and receipt['inputs_unchanged']is False and 'changed'in receipt['error'])
finally:
    w.load_module=original_loader;w.output_argument=original_out;w.manifest_arguments=original_manifest_args;w.qualify_inputs=original_qualify;sys.argv=original_sysargv
    if old_fixture is None:del sys.modules['m3_local_fixture']
    else:sys.modules['m3_local_fixture']=old_fixture
    if old_som_repo is None:os.environ.pop('SOM_REPO',None)
    else:os.environ['SOM_REPO']=old_som_repo
manifest=HERE/'manifest.draft.json'
if manifest.exists():
    sealed={'path':str(manifest),'sha256':w.sha(manifest)}
    bound=w.qualify_inputs(ROOT,sealed)
    test('actual_complete_manifest_seal_and_inputs',bool(bound)and bound[str(ROOT/'.local/rust-compiler-goal/candidate-003/source.json')]==w.SOURCE_SHA)
    test('actual_all_bound_files_unchanged',bound=={path:w.sha(path)for path in bound})
    refusal('wrong_manifest_seal_refused',lambda:w.qualify_inputs(ROOT,{**sealed,'sha256':'0'*64}))
    bad=json.loads(manifest.read_text());bad['acceptance_criteria_changed']=True
    bad_path=work/'altered-manifest.json';bad_path.write_text(json.dumps(bad))
    refusal('rehashed_changed_criteria_refused',lambda:w.qualify_inputs(ROOT,{'path':str(bad_path),'sha256':w.sha(bad_path)}))
    for name,mutate in [('prior_amendment_hash',lambda value:value['previous_amendment_manifest'].update(sha256='0'*64)),
                        ('failed_diagnostic_hash',lambda value:value['failed_diagnostic004']['result'].update(sha256='0'*64))]:
        changed=json.loads(manifest.read_text());mutate(changed);changed_path=work/(name+'.json');changed_path.write_text(json.dumps(changed))
        refusal(name+'_cannot_be_rebound',lambda path=changed_path:w.qualify_inputs(ROOT,{'path':str(path),'sha256':w.sha(path)}))
else:test('actual_manifest_present',False)
bad_tag=json.loads(c.serialize(datetime(2026,10,14,tzinfo=timezone.utc)))
bad_tag[c.TAG]['utc_offset_microseconds']=False
refusal('typed_tag_boolean_offset_refused',lambda:c.serialize(bad_tag))
saved_argv=sys.argv[:]
try:
    sys.argv=['wrapper','--out','/private/tmp/unit-only','--amendment-manifest','/private/tmp/unit-manifest','--amendment-manifest-sha256','a'*64]
    actual,binding=w.manifest_arguments()
    test('actual_executable_and_flags_bound',actual[0]==sys.executable and '--amendment-manifest'in actual and binding=={'path':'/private/tmp/unit-manifest','sha256':'a'*64})
    test('only_adapter_flags_removed',sys.argv==['wrapper','--out','/private/tmp/unit-only'])
finally:sys.argv=saved_argv
result={'version':'som.legacy005-amendment-offline-qualification.v1','passed':all(r['passed']for r in checks),'count':len(checks),'checks':checks,
        'files':{p.name:hashlib.sha256(p.read_bytes()).hexdigest()for p in [HERE/'legacy544_corrected.py',HERE/'native_evidence.py',HERE/'qualify.py']},
        'boundary_stubs':['subprocess.run recording delegate','original runner.main test outcomes','m3 interruption context','wrapper output path to own scratch'],
        'actual_readonly_bindings':['Original QA57 JSON and original runner raw hash'],
        'scope':'No service/process/formatter/behavior execution. Synthetic rows never qualify a product.',
        'unrun':['Independent reviewer qualification','Cold legacy child import check','Native date values from a fresh real flow checker','Fresh all544 full acceptance','Root acceptance audit and switch']}
(HERE/'qualification.json').write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps({'passed':result['passed'],'count':len(checks),'failed':[r['name']for r in checks if not r['passed']]}))
raise SystemExit(0 if result['passed']else 1)

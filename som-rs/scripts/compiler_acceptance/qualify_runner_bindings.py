"""Pure source/evidence gate tests. Never call the candidate or formatter."""
import argparse, copy, importlib.util, json, os, sys, socket, tarfile, types
from unittest import mock
from pathlib import Path
HERE=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('corrected_runner',HERE/'public_products_corrected.py')
r=importlib.util.module_from_spec(spec);spec.loader.exec_module(r)
checks=[]
def check(name,fn):
 try:fn();checks.append({'name':name,'passed':True})
 except Exception as e:checks.append({'name':name,'passed':False,'error':type(e).__name__+': '+str(e)})
def refuse(fn):
 try:fn()
 except ValueError:return
 raise AssertionError('bad binding accepted')
def qualify_main_startup(a,manifest,old,focused):
 """Execute the actual main setup with real files and blocked I/O boundaries."""
 class StartupReached(BaseException):pass
 candidate=focused['candidate'];events=[];process_fixture_mutation={}
 identity=old['runtime_health_binding']['candidate']
 expected_index=identity['asset_index_sha256']
 indexes=[p for p in (r.ROOT/'.local/rust-compiler-goal/acceptance').glob('per-version-asset-index-*.json') if r.digest(p)==expected_index]
 assert indexes,'no exact runtime-bound asset index'
 asset_index=sorted(indexes)[0]
 argv=['public_products_corrected.py','--http-base',candidate['base_url'],'--asset-index',str(asset_index),'--formatter',manifest['dependencies']['formatter']['path'],'--candidate-binary',identity['candidate_binary'],'--source-json',identity['source_json'],'--runtime-receipt',identity['runtime_acceptance_receipt'],'--amendment-manifest',str(a.amendment_manifest)]
 actual_verify=r.verify_amendment;actual_freeze=r.qa.verify_freeze;actual_bind=r.bind_focused;actual_process=r.process_identity
 def checked_manifest(path):
  result=actual_verify(path);events.append('real additive/QA57/dependency/evidence manifest checks');return result
 def checked_freeze(root):
  result=actual_freeze(root);events.append('real contract freeze checks');return result
 def checked_bind(*args):
  result=actual_bind(*args);events.append('real same-candidate/process-metadata/focused bindings');return result
 def replay_process_command(command,**kwargs):
  frozen=candidate['process'];pid=str(frozen['pid']);port=str(frozen['port'])
  if command==['ps','-ww','-p',pid,'-o','pid=,uid=,lstart=,command=']:
   # Reconstruct the displayed row from the actual frozen verified metadata.
   # This executes the parser/guard; it is not a new live owner observation.
   stdout=f"{frozen['pid']} {frozen['uid']} {frozen['lstart']} {r.shlex.join(frozen['argv'])}\n"
   stdout=process_fixture_mutation.get('ps',stdout)
  elif command==['lsof','-nP','-iTCP:'+port,'-sTCP:LISTEN','-Fpcn']:
   stdout='\n'.join(frozen['lsof_listener_fields'])+'\n'
   stdout=process_fixture_mutation.get('listener',stdout)
  elif command==['lsof','-nP','-a','-p',pid,'-d','txt','-Fn']:
   stdout='\n'.join(frozen['lsof_executable_fields'])+'\n'
   stdout=process_fixture_mutation.get('executable',stdout)
  else:raise AssertionError('unapproved subprocess command: '+repr(command))
  return r.subprocess.CompletedProcess(command,0,stdout,'')
 def checked_process(runtime,binary,url):
  assert str(runtime.resolve())==identity['candidate_runtime_receipt'] and str(binary.resolve())==identity['candidate_binary']
  result=actual_process(runtime,binary,url)
  assert result==candidate['process']
  events.append('real process identity guard with frozen OS observation replay');return result
 def fake_health(url):
  assert url==candidate['base_url'];events.append('health boundary stub');return 200,candidate['health'],json.dumps(candidate['health']).encode()
 def stop_client(url,probe):
  assert url==candidate['base_url'] and probe is None;events.append('complete startup reached client boundary');raise StartupReached()
 def blocked(*args,**kwargs):raise AssertionError('forbidden live process/network/formatter boundary was called')
 def invoke(module,case,override=None):
  output=a.out/('startup-'+case);args=argv+['--out',str(output)]
  for key,value in (override or {}).items():args[args.index(key)+1]=str(value)
  events.clear()
  with mock.patch.object(sys,'argv',args),mock.patch.object(module,'OUTPUT_ROOT',a.out),mock.patch.object(module,'verify_amendment',checked_manifest),mock.patch.object(module.qa,'verify_freeze',checked_freeze),mock.patch.object(module,'bind_focused',checked_bind),mock.patch.object(module,'process_identity',checked_process),mock.patch.object(module,'get_health',fake_health),mock.patch.object(module.qa,'Client',stop_client),mock.patch.object(module.subprocess,'run',replay_process_command),mock.patch.object(module.subprocess,'Popen',blocked),mock.patch.object(socket,'socket',blocked),mock.patch.object(module.urllib.request,'build_opener',blocked),mock.patch.object(module.urllib.request,'urlopen',blocked),mock.patch.object(module.ref.quality,'finalize',blocked),mock.patch.object(module,'verify_quality',blocked):
   module.main()
  raise AssertionError('main returned before explicit startup boundary')
 def positive():
  try:invoke(r,'positive')
  except StartupReached:pass
  assert events==['real additive/QA57/dependency/evidence manifest checks','real contract freeze checks','real process identity guard with frozen OS observation replay','real same-candidate/process-metadata/focused bindings','health boundary stub','complete startup reached client boundary'],events
  output=a.out/'startup-positive'
  assert json.loads((output/'additive-amendment-binding.json').read_text())['manifest_sha256']==r.digest(a.amendment_manifest)
  assert json.loads((output/'freeze-verification.json').read_text())['status']=='passed'
  assert (output/'candidate-identity.json').is_file()
 check('ACTUAL main startup reaches all manifest/source/runtime/argv setup gates with only I/O boundaries stubbed',positive)
 def shadow_free():
  assert 'digest' not in r.main.__code__.co_varnames
  assert 'source_digest' in r.main.__code__.co_varnames
 check('main has no local binding that shadows digest helper',shadow_free)
 # Import the preserved v2 bytes and reproduce the original bug. No main I/O
 # boundary can be reached because UnboundLocalError occurs before verification.
 def original_bug():
  archive=r.bound_path(manifest['dependencies']['preserved_additive002_archive'])
  installed=json.loads(r.bound_path(manifest['dependencies']['preserved_additive002_receipt']).read_text())
  assert r.digest(archive)==installed['archive_sha256']
  with tarfile.open(archive) as saved:
   members=[m for m in saved.getmembers() if Path(m.name).name=='public_products_corrected.py']
   assert len(members)==1 and members[0].isfile()
   code=saved.extractfile(members[0]).read()
  assert r.hashlib.sha256(code).hexdigest()==installed['files']['som-rs/scripts/compiler_acceptance/public_products_corrected.py']
  old_module=types.ModuleType('preserved_v2_runner')
  old_module.__file__=str(HERE/'public_products_corrected.py')
  exec(compile(code,str(archive)+'!'+members[0].name,'exec'),old_module.__dict__)
  try:invoke(old_module,'preserved-v2')
  except UnboundLocalError as error:assert 'digest' in str(error)
  else:raise AssertionError('original startup failure not reproduced')
  assert not events and not (a.out/'startup-preserved-v2').exists()
 check('preserved v2 ACTUAL main reproduces digest UnboundLocalError before output or live calls',original_bug)
 wrong_formatter=a.out/'wrong-formatter';wrong_formatter.write_bytes(b'not approved formatter')
 wrong_index=a.out/'wrong-index.json';index=json.loads(asset_index.read_text());index['offline_negative_nonce']=True;wrong_index.write_text(json.dumps(index))
 wrong_runtime=a.out/'wrong-runtime.json';runtime=json.loads(Path(identity['runtime_acceptance_receipt']).read_text());runtime['checks']=runtime['checks'][:-1];wrong_runtime.write_text(json.dumps(runtime))
 for name,option,path in [('formatter','--formatter',wrong_formatter),('asset-index','--asset-index',wrong_index),('runtime37','--runtime-receipt',wrong_runtime),('http-port','--http-base','http://127.0.0.1:18766')]:
  def negative(name=name,option=option,path=path):
   try:invoke(r,'negative-'+name,{option:path})
   except ValueError:pass
   else:raise AssertionError('bad startup input passed')
   assert 'complete startup reached client boundary' not in events and 'real process identity guard with frozen OS observation replay' not in events
   assert not (a.out/('startup-negative-'+name)).exists()
  check('ACTUAL main refuses mismatched startup '+name+' before live boundary/output',negative)
 for name,mutation in [('listener',{'listener':'p1\ncsom\nn127.0.0.1:18765\n'}),('executable',{'executable':'p'+str(candidate['pid'])+'\nn/wrong/binary\n'}),('UID',{'ps':f"{candidate['pid']} 0 {candidate['process']['lstart']} {r.shlex.join(candidate['process']['argv'])}\n"})]:
  def wrong_os(name=name,mutation=mutation):
   process_fixture_mutation.update(mutation)
   try:
    try:invoke(r,'negative-process-'+name)
    except ValueError:pass
    else:raise AssertionError('real process guard accepted wrong recorded fixture')
    assert 'health boundary stub' not in events and 'complete startup reached client boundary' not in events
    assert not (a.out/('startup-negative-process-'+name)).exists()
   finally:process_fixture_mutation.clear()
  check('ACTUAL main real process guard refuses wrong '+name+' before health/API/output',wrong_os)
 (a.out/'startup-argv-and-boundaries.json').write_text(json.dumps({'argv':argv,'stubbed_boundaries':['get_health','Client'],'OS_observation_replay':'actual process_identity parser/guard; actual frozen lsof fields and lossless PS row from frozen verified metadata; no live ownership claimed','blocked_boundaries':['unrecognized subprocess.run','subprocess.Popen','socket.socket','urllib.build_opener','urllib.urlopen','quality.finalize','verify_quality'],'all_other_startup_checks':'real source/manifest/receipt reads and exact hashes','live_candidate_called':False,'service_ownership_claimed':False},indent=2)+'\n')

def main():
 p=argparse.ArgumentParser();p.add_argument('--amendment-manifest',type=Path,required=True);p.add_argument('--out',type=Path,required=True);a=p.parse_args()
 assert sys.version_info[:2]==(3,12)
 a.out.mkdir(parents=True,exist_ok=False)
 manifest,old,focused=r.verify_amendment(a.amendment_manifest)
 checks.append({'name':'all actual QA57/additive/dependency/candidate/old125/focused11/raw proof hashes validated','passed':True})
 identity=old['runtime_health_binding']['candidate'];process=focused['candidate']['process'];health=focused['candidate']['health']
 check('all candidate binary/source/archive/build/runtime/process/health bindings',lambda:r.bind_focused(manifest,old,focused,identity,process,health))
 for key in ('candidate_binary_sha256','source_json_sha256','source_archive_sha256','build_receipt_sha256','candidate_runtime_receipt_sha256','asset_manifest_sha256','asset_index_sha256'):
  def changed(key=key):
   copy_identity={**identity,key:'0'*64};refuse(lambda:r.bind_focused(manifest,old,focused,copy_identity,process,health))
  check('reject candidate identity mutation '+key,changed)
 for key,value in (('pid',12345),('uid',12345),('lstart','different'),('argv',['wrong']),('lsof_listener_fields',['wrong']),('lsof_executable_fields',['wrong'])):
  check('reject live process mutation '+key,lambda key=key,value=value:refuse(lambda:r.bind_focused(manifest,old,focused,identity,{**process,key:value},health)))
 check('reject serving health mutation',lambda:refuse(lambda:r.bind_focused(manifest,old,focused,identity,process,{**health,'model_loaded':True})))
 def manifest_case(name,mutation):
  changed=copy.deepcopy(manifest);mutation(changed);path=a.out/(name+'.json');path.write_text(json.dumps(changed))
  refuse(lambda:r.verify_amendment(path))
 check('reject empty additive source map',lambda:manifest_case('empty-additive',lambda m:m.update(additive_files={})))
 check('reject missing actual runner binding',lambda:manifest_case('missing-runner',lambda m:m['additive_files'].pop('public_products_corrected.py')))
 check('reject modified helper source digest',lambda:manifest_case('bad-helper',lambda m:m['additive_files'].__setitem__('reference_span_prepass.py','0'*64)))
 check('reject missing raw proof files',lambda:manifest_case('missing-raw',lambda m:m['focused_raw_proofs']['REF_BRANCH_EXPORT_SAME_TYPE'].update(raw_files=[])))
 # Change the hashed summary in a scratch file. No original evidence changes.
 def changed_summary(name,mutate):
  altered=copy.deepcopy(focused);mutate(altered);path=a.out/(name+'-summary.json');path.write_text(json.dumps(altered))
  def binding(m):m['focused_summary']={'path':str(path),'sha256':r.digest(path)}
  manifest_case(name,binding)
 for label,mutate in [('focused-false',lambda s:next(c for c in s['cases'] if c['id']=='REF_BRANCH_EXPORT_SAME_TYPE').update(independent_focused_behavior_passed=False)),('strict-false',lambda s:next(c for c in s['cases'] if c['id']=='REF_BRANCH_EXPORT_SAME_TYPE').update(strict_quality_passed=False)),('cleanup-false',lambda s:s.update(cleanup_passed=False)),('identity-false',lambda s:s.update(candidate_identity_stable=False)),('source-changed',lambda s:next(c for c in s['cases'] if c['id']=='REF_BRANCH_EXPORT_SAME_TYPE')['focused_behavior']['outcome']['source_sha256_after'].update({'README.md':'0'*64})),('missing-case',lambda s:s['cases'].pop()),('repeated-case',lambda s:s['cases'].__setitem__(0,copy.deepcopy(s['cases'][1])))]:
  check('reject summary '+label,lambda label=label,mutate=mutate:changed_summary(label,mutate))
 for case_id in sorted(r.KNOWN_REFERENCE_FORMATTER_FAILURES):
  files=json.loads(r.bound_path(manifest['original_failures'][case_id]['assemble_response']).read_text())['body']['files']
  check('bind live exact full product '+case_id,lambda case_id=case_id,files=files:r.focused_case_binding(case_id,files,focused,manifest))
  def file_change(case_id=case_id,files=files):
   wrong=dict(files);wrong['README.md']+='\nchanged';refuse(lambda:r.focused_case_binding(case_id,wrong,focused,manifest))
  check('reject one changed full product byte '+case_id,file_change)
 required=[row['id'] for row in old['records']];records=[]
 for case_id in required:
  row={'id':case_id,'passed':True,'reference_formatter_status':'passed'}
  if case_id in r.KNOWN_REFERENCE_FORMATTER_FAILURES:row.update(reference_formatter_status='failed',original_reference_check_passed=False,complete_file_parity=None,complete_normalized_ast_equal=False,corrected_reference_formatter_status='passed',corrected_reference_full_file_parity=True,focused_behavior_binding={'passed':True},quality={'passed':True},quality_cache_cleanup={'passed':True},independent_dsl_equal=True,problems=[])
  records.append(row)
 def count_ok():
  counts=r.final_case_inventory(records,required);assert counts=={'complete':True,'original_formatter_failure_count':2,'corrected_reference_count':2}
 check('new explicit full127 scope passes; old failure fields retained',count_ok)
 for name,mutate in [('focused',lambda rows:rows[118]['focused_behavior_binding'].update(passed=False)),('strict-quality',lambda rows:rows[118]['quality'].update(passed=False)),('cache-cleanup',lambda rows:rows[126]['quality_cache_cleanup'].update(passed=False)),('original-failure-changed',lambda rows:rows[118].update(reference_formatter_status='passed')),('corrected-byte-mismatch',lambda rows:rows[126].update(corrected_reference_full_file_parity=False)),('missing-case',lambda rows:rows.pop()),('duplicate-case',lambda rows:rows.__setitem__(0,copy.deepcopy(rows[1]))),('invented-case',lambda rows:rows[0].update(id='invented')),('any-failed-row',lambda rows:rows[0].update(passed=False)),('original-AST-fallback-true',lambda rows:rows[118].update(complete_normalized_ast_equal=True))]:
  def failed_count(mutate=mutate):
   rows=copy.deepcopy(records);mutate(rows);assert r.final_case_inventory(rows,required)['complete'] is False
  check('new final scope refuses '+name,failed_count)
 check('manifest-change digest guard',lambda:refuse(lambda:r.require(r.digest(a.amendment_manifest)=='0'*64,'changed amendment')))
 qualify_main_startup(a,manifest,old,focused)
 report={'version':'som.public-reference-runner-offline-qualification.v1','passed':all(x['passed'] for x in checks),'count':len(checks),'checks':checks,'python':sys.version,'python_executable':sys.executable,'python_executable_sha256':r.digest(Path(sys.executable)),'runner_sha256':r.digest(HERE/'public_products_corrected.py'),'test_sha256':r.digest(Path(__file__)),'amendment_manifest_sha256':r.digest(a.amendment_manifest),'scope':'source/evidence gate tests only; no formatter, service, process, socket or build call'}
 (a.out/'result.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps({'passed':report['passed'],'count':len(checks),'failed':[x for x in checks if not x['passed']]}));return 0 if report['passed'] else 2
if __name__=='__main__':raise SystemExit(main())

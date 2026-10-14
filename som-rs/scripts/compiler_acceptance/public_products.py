"""Validate every frozen public product against independent Python constructors.

This is an offline acceptance tool. It is not used by the Rust service.
Full strict quality remains scoped to the quality-enabled v6/v7 families.
The two previously recorded reference formatter failures retain their failures;
closed if/else assignments and lazy conditional expressions are compared by
an explicit, semantics-preserving AST normalization and separate live tests.
"""
import argparse, ast, hashlib, json, os, shlex, shutil, subprocess, sys, time
import urllib.error, urllib.parse, urllib.request
from pathlib import Path
PACKAGE_DIR=Path(__file__).resolve().parent
if str(PACKAGE_DIR) not in sys.path:sys.path.insert(0,str(PACKAGE_DIR))
from paths import default_output_root, find_repo, require_new_output
ROOT=find_repo(__file__)
OUTPUT_ROOT=default_output_root(ROOT)
KNOWN_REFERENCE_FORMATTER_FAILURES={'REF_BRANCH_EXPORT_SAME_TYPE','PUBLIC_v7-branch-null'}
sys.path[:0]=[str(ROOT/'som-core/src'),str(ROOT/'som-core/scripts')]
import qa_harness as qa
from verify_python_quality import verify as verify_quality
import public_reference as ref

def write(path,value):
 path.parent.mkdir(parents=True,exist_ok=True)
 if path.exists(): raise FileExistsError(path)
 path.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n')

class Normalize(ast.NodeTransformer):
 def visit_If(self,node):
  node=self.generic_visit(node)
  if len(node.body)==len(node.orelse)==1:
   a,b=node.body[0],node.orelse[0]
   if isinstance(a,ast.Assign) and isinstance(b,ast.Assign) and len(a.targets)==len(b.targets)==1 and isinstance(a.targets[0],ast.Name) and isinstance(b.targets[0],ast.Name) and a.targets[0].id==b.targets[0].id:
    return ast.copy_location(ast.Assign(targets=a.targets,value=ast.IfExp(test=node.test,body=a.value,orelse=b.value)),node)
  return node

def normalized(text): return ast.dump(Normalize().visit(ast.parse(text)),include_attributes=False)
def check_product(files,raw):
 if files.keys()!=raw.keys(): return False
 return all(normalized(files[n])==normalized(raw[n]) if n.endswith('.py') else files[n]==raw[n] for n in files)

def cleanup_mypy_cache(output,target):
 root=output.resolve(); item={'path':str(target),'removed':False,'passed':True,'free_bytes_before':shutil.disk_usage(root).free}
 try:
  current=output
  if output.is_symlink() or not output.resolve().is_relative_to(root):raise ValueError('output root is unsafe')
  rel=target.relative_to(output)
  for part in rel.parts:
   current=current/part
   if current.is_symlink():raise ValueError('cache path contains a symlink')
  if not target.resolve().is_relative_to(root):raise ValueError('cache resolves outside output')
  if not target.exists():item.update({'present':False,'bytes_before':0,'bytes_after':0})
  else:
   item['present']=True;item['bytes_before']=sum(x.stat().st_size for x in target.rglob('*') if x.is_file())
   ps=subprocess.run(['ps','-ww','-axo','pid=,command='],capture_output=True,text=True,check=False,timeout=10)
   if ps.returncode:raise RuntimeError('cannot verify mypy process absence: '+ps.stderr[-1000:])
   needle=str(target.resolve());live=[]
   for line in ps.stdout.splitlines():
    parts=line.strip().split(None,1)
    if len(parts)==2 and needle in parts[1]:live.append({'pid':parts[0],'command':parts[1][:500]})
   item['live_processes']=live
   if live:raise RuntimeError('a process still uses the exact job cache path')
   if target.is_symlink() or not target.resolve().is_relative_to(root):raise ValueError('cache safety changed before deletion')
   shutil.rmtree(target);item.update({'removed':True,'bytes_after':0})
  item['free_bytes_after']=shutil.disk_usage(root).free
 except Exception as exc:item.update({'passed':False,'error':type(exc).__name__+': '+str(exc),'free_bytes_after':shutil.disk_usage(root).free})
 return item

def get_health(url):
 parsed=urllib.parse.urlsplit(url)
 if parsed.scheme!='http' or parsed.hostname not in ('127.0.0.1','localhost','::1') or not parsed.port or parsed.port==8765 or parsed.path not in ('','/'):
  raise ValueError('an explicit candidate loopback HTTP port other than 8765 is required')
 class NoRedirect(urllib.request.HTTPRedirectHandler):
  def redirect_request(self,*args): return None
 req=urllib.request.Request(url.rstrip('/')+'/healthz',method='GET')
 try:
  with urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect).open(req,timeout=20) as response:
   raw=response.read(); status=response.status
 except urllib.error.HTTPError as e: raw=e.read(); status=e.code
 body=json.loads(raw)
 return status,body,raw

def process_identity(runtime_path,binary,http_base):
 runtime=json.loads(runtime_path.read_text()); pid=runtime.get('pid'); port=runtime.get('port'); argv=runtime.get('argv')
 parsed=urllib.parse.urlsplit(http_base)
 if type(pid) is not int or pid<=0 or type(port) is not int or port!=parsed.port or not isinstance(argv,list) or not argv or Path(argv[0]).resolve()!=binary.resolve():raise ValueError('candidate runtime PID, argv, binary, or port metadata is invalid')
 if runtime.get('binary_sha256')!=hashlib.sha256(binary.read_bytes()).hexdigest():raise ValueError('candidate runtime metadata has the wrong binary hash')
 ps=subprocess.run(['ps','-ww','-p',str(pid),'-o','pid=,uid=,lstart=,command='],capture_output=True,text=True,check=False,timeout=10)
 fields=ps.stdout.strip().split(None,7) if ps.returncode==0 else []
 if len(fields)!=8 or int(fields[0])!=pid or int(fields[1])!=os.getuid() or shlex.split(fields[7])!=argv:raise ValueError('candidate process PID, owner, start time, or argv differs from runtime receipt')
 sock=subprocess.run(['lsof','-nP','-iTCP:'+str(port),'-sTCP:LISTEN','-Fpcn'],capture_output=True,text=True,check=False,timeout=10)
 lines=sock.stdout.splitlines()
 listener_names=[line[1:] for line in lines if line.startswith('n')]
 loopback_names=('127.0.0.1:'+str(port),'[::1]:'+str(port))
 if sock.returncode or set(line for line in lines if line.startswith('p'))!={'p'+str(pid)} or not listener_names or not all(any(host in name for host in loopback_names) for name in listener_names):raise ValueError('candidate PID does not exclusively own its recorded loopback listening port')
 exe=subprocess.run(['lsof','-nP','-a','-p',str(pid),'-d','txt','-Fn'],capture_output=True,text=True,check=False,timeout=10)
 exe_lines=exe.stdout.splitlines()
 if exe.returncode or 'n'+str(binary.resolve()) not in exe_lines:raise ValueError('candidate PID does not have the hashed binary loaded as its executable')
 return {'pid':pid,'uid':int(fields[1]),'lstart':' '.join(fields[2:7]),'argv':argv,'port':port,'binary_sha256':runtime['binary_sha256'],'source_sha256':runtime.get('source_sha256'),'lsof_listener_fields':lines,'lsof_executable_fields':exe_lines}

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--http-base',required=True);ap.add_argument('--asset-index',type=Path,required=True);ap.add_argument('--formatter',type=Path,required=True);ap.add_argument('--candidate-binary',type=Path,required=True);ap.add_argument('--source-json',type=Path,required=True);ap.add_argument('--runtime-receipt',type=Path,required=True);ap.add_argument('--out',type=Path,default=OUTPUT_ROOT/'public-products/run');a=ap.parse_args()
 out=require_new_output(a.out,OUTPUT_ROOT)
 freeze=qa.verify_freeze(ROOT)
 candidate=a.candidate_binary.resolve(); source_path=a.source_json.resolve(); source= json.loads(source_path.read_text())
 source_archive=source_path.parent/'source.tar'; asset_manifest=source_path.parent/'assets/manifest.json'
 build_path=source_path.parent/'build.json'; candidate_runtime_path=source_path.parent/'runtime.json'
 build_receipt=json.loads(build_path.read_text()); candidate_runtime=json.loads(candidate_runtime_path.read_text())
 identity={'candidate_binary':str(candidate),'candidate_binary_sha256':hashlib.sha256(candidate.read_bytes()).hexdigest(),
           'source_json':str(source_path),'source_json_sha256':hashlib.sha256(source_path.read_bytes()).hexdigest(),
           'source_archive':str(source_archive),'source_archive_sha256':hashlib.sha256(source_archive.read_bytes()).hexdigest(),
           'build_receipt':str(build_path.resolve()),'build_receipt_sha256':hashlib.sha256(build_path.read_bytes()).hexdigest(),
           'candidate_runtime_receipt':str(candidate_runtime_path.resolve()),'candidate_runtime_receipt_sha256':hashlib.sha256(candidate_runtime_path.read_bytes()).hexdigest(),
           'runtime_acceptance_receipt':str(a.runtime_receipt.resolve()),'runtime_acceptance_receipt_sha256':hashlib.sha256(a.runtime_receipt.read_bytes()).hexdigest(),
           'asset_manifest':str(asset_manifest),'asset_manifest_sha256':hashlib.sha256(asset_manifest.read_bytes()).hexdigest(),
           'asset_index_sha256':hashlib.sha256(a.asset_index.read_bytes()).hexdigest()}
 if source.get('archive_sha256')!=identity['source_archive_sha256']:
  raise ValueError('candidate source archive does not match source.json')
 if (build_receipt.get('complete') is not True or build_receipt.get('source_unchanged') is not True
     or build_receipt.get('source_sha256')!=identity['source_json_sha256']
     or build_receipt.get('binary_sha256')!=identity['candidate_binary_sha256']):
  raise ValueError('candidate binary or source does not match the complete build receipt')
 if (candidate_runtime.get('binary_sha256')!=identity['candidate_binary_sha256']
     or candidate_runtime.get('source_sha256')!=identity['source_json_sha256']):
  raise ValueError('candidate runtime receipt does not bind the built binary and source')
 source_mismatch=[]
 for relative,digest in source.get('files',{}).items():
  path=ROOT/relative
  if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest()!=digest:source_mismatch.append(relative)
 if source_mismatch:raise ValueError('candidate source.json differs from live sources: '+', '.join(source_mismatch[:12]))
 manifest=json.loads(asset_manifest.read_text()); index=json.loads(a.asset_index.read_text())
 backend={entry['backend']['sha256'] for entry in index['by_prompt_version'].values()}
 backend_manifest_sha=hashlib.sha256(qa.canonical(manifest.get('backend')).encode()).hexdigest()
 if len(backend)!=1 or next(iter(backend))!=backend_manifest_sha:
  raise ValueError('candidate package backend differs from independently pinned asset index')
 runtime=json.loads(a.runtime_receipt.read_text())
 if (runtime.get('status')!='passed' or runtime.get('passed') is not True or len(runtime.get('checks',[]))!=37
     or not all(x.get('passed') is True for x in runtime.get('checks',[]))
     or runtime.get('candidate_binary_sha256')!=identity['candidate_binary_sha256']
     or runtime.get('asset_index_sha256')!=identity['asset_index_sha256']):
  raise ValueError('runtime receipt does not bind this candidate binary and asset index')
 health_check=next((x for x in runtime.get('checks',[]) if x.get('id')=='health_contract'),None)
 expected_health=(health_check or {}).get('actual')
 if not isinstance(expected_health,dict) or expected_health.get('purpose')!='som.rust-compiler.v1' or expected_health.get('model_loaded') is not False:
  raise ValueError('runtime receipt lacks a valid compiler-only health contract')
 if candidate_runtime.get('health')!=expected_health:
  raise ValueError('candidate runtime health differs from the independently recorded runtime acceptance')
 process_before=process_identity(candidate_runtime_path,candidate,a.http_base)
 out.mkdir(parents=True)
 status_before,health_before,raw_before=get_health(a.http_base)
 (out/'health-before.response.raw').write_bytes(raw_before);write(out/'health-before.response.json',health_before)
 if status_before!=200 or health_before!=expected_health:
  write(out/'identity-refusal.json',{'reason':'serving endpoint health does not match bound runtime candidate','expected':expected_health,'actual':health_before,'http_status':status_before,'candidate':identity})
  raise ValueError('serving endpoint health does not match bound runtime candidate')
 write(out/'candidate-identity.json',identity)
 write(out/'runtime-health-binding.json',{'runtime_receipt':str(a.runtime_receipt.resolve()),'runtime_receipt_sha256':identity['runtime_acceptance_receipt_sha256'],'build_receipt_sha256':identity['build_receipt_sha256'],'candidate_runtime_receipt_sha256':identity['candidate_runtime_receipt_sha256'],'process_before':process_before,'health_expected':expected_health,'health_before_sha256':hashlib.sha256(raw_before).hexdigest(),'package_backend_sha256':backend_manifest_sha,'asset_index_backend_sha256':next(iter(backend))})
 write(out/'freeze-verification.json',freeze)
 ref.BINARY=a.formatter;ref.PROFILE=ROOT/'som-rs/contracts/python-quality-v1/pyproject.toml'
 frozen=ROOT/'som-rs/tests/compiler-v1/cnl-positive-fixtures.json'
 cases=[{'id':x['id'],'prompt':x['expected_prompt'],'cnl':x['cnl']} for x in json.loads(frozen.read_text())['cases']]
 examples=ROOT/'som-rs/contracts/compiler-v1/examples'
 cases +=[{'id':'PUBLIC_'+x['id'],'prompt':json.loads((examples/x['prompt']).read_text()),'cnl':(examples/x['cnl']).read_text()} for x in json.loads((examples/'index.json').read_text())['cases']]
 assert len(cases)==127
 assets=json.loads(a.asset_index.read_text());policy=json.loads((ROOT/'som-rs/contracts/compiler-v1/selection-policy.json').read_text())
 client=qa.Client(a.http_base,None);records=[]; interruption=None; process_after=None; after_error=None
 try:
  for i,c in enumerate(cases):
   d=out/f'{i:03d}';d.mkdir();start=time.monotonic();r={'id':c['id'],'passed':False,'problems':[]}
   try:
    write(d/'prompt.json',c['prompt']);(d/'source.cnl').write_text(c['cnl'])
    bundles=[]
    for mode,req in [('json',{'format':'som.prompt-json.v1','prompt':c['prompt']}),('cnl',{'format':'som.cnl.v1','source':c['cnl']})]:
     raw_request=json.dumps(req,ensure_ascii=False,separators=(',',':')).encode('utf-8')
     (d/f'compile-{mode}.request.raw').write_bytes(raw_request)
     status,body=client.call('/v1/compile',req);qa.persist_api_exchange(d,'compile-'+mode,req,status,body,client.last_response_raw)
     if status!=200 or body.get('status')!='ok':raise ValueError(f'{mode} compile {status}: {body}')
     bundle=body['bundle'];r['problems'] += qa.verify_bundle(bundle,c['prompt'],assets,policy);bundles.append(bundle)
    if not qa.exact_equal(*bundles):r['problems'].append('CNL and JSON bundle differ')
    expected_dsl=ref.reference_dsl(c['prompt']);write(d/'reference.dsl.json',expected_dsl)
    r['independent_dsl_equal']=qa.exact_equal(bundles[0]['dsl'],expected_dsl)
    if not r['independent_dsl_equal']:r['problems'].append('public constructor DSL differs')
    raw=ref.render_raw(c['prompt'],expected_dsl);write(d/'reference.raw-files.json',raw)
    req={'bundle':bundles[0]};(d/'assemble.request.raw').write_bytes(json.dumps(req,ensure_ascii=False,separators=(',',':')).encode('utf-8'));status,body=client.call('/v1/assemble',req);qa.persist_api_exchange(d,'assemble',req,status,body,client.last_response_raw)
    if status!=200 or body.get('status')!='ok':raise ValueError(f'assemble {status}: {body}')
    files=body['files'];product=d/'product';product.mkdir()
    for n,s in files.items():
     p=product/n
     if not p.resolve().is_relative_to(product.resolve()):raise ValueError('unsafe output path')
     p.parent.mkdir(parents=True,exist_ok=True);p.write_text(s)
     if n.endswith('.py'):compile(s,n,'exec')
    formatted_family=c['prompt']['version'] in ('som.prompt.v6','som.prompt.v7')
    try:
     expected=ref.quality.finalize(raw,ref.BINARY,ref.PROFILE,byte_limit=None if c['prompt']['version']=='som.prompt.v7' else 65536) if formatted_family else raw
     write(d/'reference.expected-files.json',expected)
     r['reference_formatter_status']='passed';r['complete_file_parity']=files==expected
     if c['id'] in KNOWN_REFERENCE_FORMATTER_FAILURES:r['problems'].append('frozen reference formatter failure unexpectedly disappeared')
     if not r['complete_file_parity']:r['problems'].append('complete independent formatted product differs')
    except ref.v1.V1Error as e:
     if e.code!='OUTPUT_QUALITY_FAILED' or c['id'] not in KNOWN_REFERENCE_FORMATTER_FAILURES:raise
     r['reference_formatter_status']='failed';r['reference_formatter_error']={'code':e.code,'detail':str(e)}
     r['complete_file_parity']=None;r['complete_normalized_ast_equal']=check_product(files,raw)
     if not r['complete_normalized_ast_equal']:r['problems'].append('reference formatter failed; full semantic AST comparison differs')
     r['separate_live_behavior_required']=True
    r['generated_bytes']=sum(len(s.encode()) for s in files.values());r['product_sha256']=qa.sha(files)
    if formatted_family:
     q=verify_quality(product,d/'quality');r['quality']=q
     if q.get('passed') is not True:r['problems'].append('strict quality failed')
    else:r['quality']={'scope':'original unformatted family; syntax and complete independent product parity'}
    r['quality_cache_cleanup']=cleanup_mypy_cache(out,d/'quality/mypy-cache')
    write(d/'quality-cache-cleanup.json',r['quality_cache_cleanup'])
    if r['quality_cache_cleanup'].get('passed') is not True:r['problems'].append('safe per-case mypy cache cleanup failed')
   except Exception as e:r['problems'].append(type(e).__name__+': '+str(e))
   r['passed']=not r['problems'];r['seconds']=time.monotonic()-start;records.append(r);write(d/'result.json',r)
   print(json.dumps({'completed':len(records),'required':127,'passed':sum(x['passed'] for x in records),'latest':c['id']}),flush=True)
 except BaseException as error:
  interruption=error
 finally:
  client.close()
  try:
   status_after,health_after,raw_after=get_health(a.http_base)
   process_after=process_identity(candidate_runtime_path,candidate,a.http_base)
   (out/'health-after.response.raw').write_bytes(raw_after);write(out/'health-after.response.json',health_after)
  except BaseException as error:
   status_after=0;health_after={};raw_after=b'';after_error=type(error).__name__+': '+str(error)
  if interruption is not None:
   write(out/'result.partial.json',{'version':'som.compiler-public-products.v1','status':'interrupted','required':127,'completed':len(records),'passed':sum(x.get('passed') is True for x in records),'records':records,'interruption':type(interruption).__name__+': '+str(interruption),'candidate':identity,'health_before':health_before,'health_after':health_after,'health_after_error':after_error,'process_before':process_before,'process_after':process_after})
 if interruption is not None:raise interruption
 health_binding={'candidate':identity,'runtime_receipt':str(a.runtime_receipt.resolve()),'runtime_receipt_sha256':hashlib.sha256(a.runtime_receipt.read_bytes()).hexdigest(),'expected_health':expected_health,'health_before_sha256':hashlib.sha256(raw_before).hexdigest(),'health_after_sha256':hashlib.sha256(raw_after).hexdigest(),'health_before_status':status_before,'health_after_status':status_after,'health_before_matches_runtime':health_before==expected_health,'health_after_matches_runtime':health_after==expected_health,'stable_health':health_before==health_after,'process_before':process_before,'process_after':process_after,'process_stable':process_before==process_after,'process_after_error':after_error,'package_backend_sha256':backend_manifest_sha,'asset_index_backend_sha256':next(iter(backend))}
 same_process=(process_before is not None and process_after is not None and all(process_before.get(k)==process_after.get(k) for k in ('pid','uid','lstart','argv','port','binary_sha256','source_sha256','lsof_listener_fields','lsof_executable_fields')))
 health_binding['process_stable']=same_process
 if status_after!=200 or health_after!=expected_health or health_before!=health_after or not same_process:
  write(out/'identity-refusal.json',{'reason':'serving endpoint identity changed during public-products acceptance','binding':health_binding})
  raise ValueError('serving endpoint identity changed during public-products acceptance')
 report={'version':'som.compiler-public-products.v1','required':127,'passed':sum(x['passed'] for x in records),'complete':len(records)==127 and all(x['passed'] for x in records),'records':records,'freeze_verification':freeze,'fixture_sha256':hashlib.sha256(frozen.read_bytes()).hexdigest(),'asset_index_sha256':hashlib.sha256(a.asset_index.read_bytes()).hexdigest(),'candidate_binary_sha256':identity['candidate_binary_sha256'],'candidate_source_sha256':identity['source_archive_sha256'],'candidate_source_json_sha256':identity['source_json_sha256'],'candidate_build_receipt_sha256':identity['build_receipt_sha256'],'candidate_runtime_receipt_sha256':identity['candidate_runtime_receipt_sha256'],'candidate_asset_manifest_sha256':identity['asset_manifest_sha256'],'runtime_health_binding':health_binding}
 write(out/'result.json',report);raise SystemExit(0 if report['complete'] else 2)
if __name__=='__main__':
 from m3_local_fixture import interruption
 with interruption():main()

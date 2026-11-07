"""Fresh same-run TypeScript input consumption. No product acceptance grant.

Load this exact source only through the caller's admitted native source path.
The caller supplies the trusted source-workflow needs catalog. The later
verifier needs the actual provision step output from outside product capture.
"""
from __future__ import annotations
from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import re
import signal
import stat
import subprocess
import sys
import time
import traceback
from urllib.request import Request, build_opener, HTTPRedirectHandler
import zipfile

BASE='som-rs/scripts/hosted_readiness/'
SELF=BASE+'typescript_input_consumer.py'
PROFILE=BASE+'inputs/typescript-regression-inputs-v1.json'
BASELINE_PROFILE=BASE+'inputs/recorded-before-profile-v1.json'
WORKFLOW='.github/workflows/rust-hosted-readiness.yml'
PIN='som-rs/contracts/native-tools-v1/artifacts.json'
M6='som-rs/scripts/react_output_acceptance/'
RESTORE=M6+'restore_hosted_evidence.mjs'
SEALED=M6+'sealed_inputs.mjs'
SOURCE_PATHS=(SELF,PROFILE,BASELINE_PROFILE,WORKFLOW,PIN,RESTORE,SEALED)
ROLES=('public_run','legacy_run','rust_run','npm_cache','node_tools')
ARTIFACT_NAME='same-candidate-typescript-regression-inputs'
PRODUCER_JOB='typescript-regression-inputs'
CATALOG_KEYS={'version','job','run_id','run_attempt','head_sha','artifact_id','artifact_digest','index_sha256','receipt_sha256','profile_sha256'}
ROOT_NAME='som-typescript-input-consumer'
STEP_ID='typescript_input_consumer'
PROTECTED_KEYS={'product_work','candidate_dir','capture_out','assembly_root','python_inputs_root','rust_inputs_root','carrier_root','runtime_root','context_path',
    'baseline_inputs','build_provenance_root','current_companions_root'}
OPTIONAL_PROTECTED_KEYS={'build_provenance_root','current_companions_root'}
AUTO_CONTEXT_INPUT_KEYS=('wheel_root','rust_tools_root','cargo_root','artifact_inputs')
MAX=512*1024**2
MAX_ZIP=512*1024**2
TOTAL=4*1024**3
NODE_SHA='3de740a941a630439b22b79aafb383f4912b76354a2694765e8d59a033746538'
NODE_BYTES=148010504
CHUNK=64*1024**2
ROLE_CONTENT_SHA='f45ce053c1174efb8a15ccc03a31da0ae730ebee099846ca0de4ef9417bfe32d'
HEX=re.compile('[0-9a-f]{64}')
PROOF_KEYS={'version','run_id','run_attempt','head_sha','workflow_path','workflow_sha256','consumer_job','consumer_step',
    'producer_catalog_sha256','receipt_sha256','evidence_manifest_sha256','artifact_id','artifact_digest','index_sha256','product_acceptance_passed'}

def need(value, reason):
    if not value: raise ValueError(reason)


def sha(raw): return hashlib.sha256(raw).hexdigest()


def parse(raw):
    def pairs(rows):
        result = {}
        for key, value in rows:
            need(key not in result, 'duplicate JSON key')
            result[key] = value
        return result
    return json.loads(raw, object_pairs_hook=pairs,
        parse_constant=lambda _: (_ for _ in ()).throw(ValueError('nonfinite JSON')))


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False).encode()


def physical(path, directory=False):
    path = Path(path).absolute()
    need('..' not in path.parts and not any(p.is_symlink() for p in (path, *path.parents)), 'physical path required')
    info = path.lstat()
    need(stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode) and info.st_nlink == 1,
         'physical directory or single-link regular file required')
    return path


def file_identity(info):
    return (info.st_dev,info.st_ino,info.st_mode,info.st_nlink,info.st_size,info.st_mtime_ns,info.st_ctime_ns)


def raw_file(path, maximum=MAX):
    path = physical(path)
    before = path.lstat()
    need(before.st_size <= maximum, 'bounded data file required')
    with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK), 'rb') as stream:
        raw = stream.read(maximum + 1)
        need(len(raw) == before.st_size and file_identity(os.fstat(stream.fileno())) == file_identity(before)
             and file_identity(path.lstat()) == file_identity(before),
             'data changed during read')
    return raw


def write(path, raw, mode=0o600):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    need(not any(p.is_symlink() for p in path.parents) and not path.is_symlink(), 'physical output parent required')
    with path.open('xb') as stream: stream.write(raw)
    path.chmod(mode)


def dump(path, value): write(path, canonical(value) + b'\n')


def git(repo, *args):
    return subprocess.check_output(['/usr/bin/git', '--no-replace-objects', '-C', str(repo), *args],
        env={'PATH':'/usr/bin:/bin','LANG':'C.UTF-8','LC_ALL':'C.UTF-8',
             'GIT_CONFIG_GLOBAL':'/dev/null','GIT_CONFIG_NOSYSTEM':'1'}, stderr=subprocess.PIPE, timeout=180)


def git_source(repo, revision, name):
    need(name and not name.startswith('/') and all(x not in {'', '.', '..'} for x in name.split('/')), 'fixed source name required')
    entry = git(repo, 'ls-tree', '-z', revision, '--', name)
    need(entry.count(b'\0') == 1 and entry.endswith(b'\t' + name.encode() + b'\0')
         and entry.startswith((b'100644 blob ', b'100755 blob ')), 'regular tracked source required')
    raw = git(repo, 'cat-file', 'blob', revision + ':' + name)
    path = physical(repo / name)
    need(len(raw) <= MAX and raw_file(path) == raw
         and bool(path.stat().st_mode & 0o111) == entry.startswith(b'100755'), 'working source differs from Git')
    return raw


def hash_file(path, maximum=MAX_ZIP):
    path=physical(path);before=path.stat();need(before.st_size<=maximum,'bounded file required')
    value=hashlib.sha256();count=0
    with os.fdopen(os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK),'rb') as stream:
        while body:=stream.read(1024**2):
            value.update(body);count+=len(body);need(count<=maximum,'file exceeds bound')
        need(file_identity(os.fstat(stream.fileno()))==file_identity(before),'open file changed')
    need(count==before.st_size and file_identity(path.stat())==file_identity(before),'file changed')
    return value.hexdigest(),count


def relative(name):
    need(type(name) is str and name and not name.startswith('/') and '\\' not in name
         and all(x not in {'','.','..'} for x in name.split('/')),'bounded relative name required')
    return name


def _roots(repo,protected_roots):
    temporary=physical(os.environ['RUNNER_TEMP'],True);root=temporary/ROOT_NAME
    need(type(protected_roots) is dict and set(protected_roots)==PROTECTED_KEYS,'exact named actual caller protection set required')
    baseline=protected_roots['baseline_inputs']
    need(type(baseline) is dict and 0<len(baseline)<=32
         and all(type(k) is str and relative(k) for k in baseline),
         'complete named Context baseline input root map required')
    flat={k:v for k,v in protected_roots.items() if k!='baseline_inputs' and k not in OPTIONAL_PROTECTED_KEYS}
    flat.update({'baseline_inputs.'+k:v for k,v in baseline.items()})
    for key in OPTIONAL_PROTECTED_KEYS:
        if protected_roots[key] is not None:flat[key]=protected_roots[key]
    for value in baseline.values():physical(value,True)
    for key in OPTIONAL_PROTECTED_KEYS:
        if protected_roots[key] is not None:physical(protected_roots[key],True)
    context=parse(raw_file(protected_roots['context_path']))
    need(context.get('version')=='som.full-product-context.v1' and context.get('qa_mode')=='native-qa-v1'
         and Path(context['repo'])==Path(repo),'actual existing native base Context required before consumer mkdir')
    bindings={'product_work':'work_root','assembly_root':'assembly_root','python_inputs_root':'python_inputs_root',
        'rust_inputs_root':'rust_inputs_root','carrier_root':'carrier_root','runtime_root':'runtime_root',
        'build_provenance_root':'build_provenance_root','current_companions_root':'current_companions_root'}
    for key,field in bindings.items():
        actual=context.get(field)
        need((None if actual is None else str(Path(actual)))==(None if protected_roots[key] is None else str(Path(protected_roots[key]))),
             'actual Context protection value differs: '+field)
    need(type(context.get('baseline_inputs')) is dict
         and {k:str(Path(v)) for k,v in context['baseline_inputs'].items()}=={k:str(Path(v)) for k,v in baseline.items()},
         'full actual Context baseline root map differs')
    for key in AUTO_CONTEXT_INPUT_KEYS:
        need(type(context.get(key)) is str and context[key],'actual original Context input field required: '+key)
        physical(context[key],key!='artifact_inputs')
        flat['context.'+key]=context[key]
    for value in [repo,*flat.values()]:
        need(Path(value).is_absolute(),'absolute actual protected path required');path=Path(value)
        need('..' not in path.parts and not any(p.is_symlink() for p in (path,*path.parents)),'physical protected path required')
        need(path!=root and path not in root.parents and root not in path.parents,'input consumer overlaps protected source/product root')
    return root,root/'evidence',root/'restored/run'


def _protection_snapshot(repo,protected_roots):
    result={'repository':str(repo)}
    for key,value in protected_roots.items():
        result[key]={k:str(Path(v)) for k,v in value.items()} if key=='baseline_inputs' else None if value is None else str(Path(value))
    return result


def _source_state(sources,helper,candidate):
    need(os.environ.get('GITHUB_ACTIONS')=='true' and os.environ.get('RUNNER_ENVIRONMENT')=='github-hosted'
         and sys.platform=='linux','current GitHub Linux input consumer required')
    need(re.fullmatch('[0-9a-f]{40}',helper) and candidate==helper==os.environ.get('GITHUB_SHA')==os.environ.get('GITHUB_WORKFLOW_SHA'),
         'current complete same-candidate source required')
    repo=physical(sources.root,True)
    need(sources.revision==helper and git(repo,'rev-parse','HEAD').decode().strip()==helper
         and git(repo,'rev-parse','--show-toplevel').decode().strip()==str(repo),'actual current source repository differs')
    need(os.environ.get('GITHUB_REPOSITORY')=='faberline/som','fixed source repository required')
    for key in ('GITHUB_RUN_ID','GITHUB_RUN_ATTEMPT','GITHUB_REPOSITORY_ID'):
        need(re.fullmatch('[1-9][0-9]*',os.environ.get(key,'')),'actual positive GitHub identity required')
    need(re.fullmatch('[A-Za-z_][A-Za-z0-9_-]*',os.environ.get('GITHUB_JOB','')),'actual source consumer job required')
    need(os.environ.get('GITHUB_EVENT_NAME') in ('push','workflow_dispatch')
         and os.environ.get('GITHUB_WORKFLOW_REF','').startswith('faberline/som/'+WORKFLOW+'@'),
         'fixed Root source workflow required')
    result={}
    for name in SOURCE_PATHS:
        raw=sources.read(name);need(raw==git_source(repo,helper,name),'source interface differs from actual Git bytes')
        result[name]={'sha256':sha(raw),'bytes':len(raw)}
    profile=parse(sources.read(PROFILE));need(profile['baseline_profile']==BASELINE_PROFILE,'fixed tracked baseline source path required')
    sources.verify()
    return result


def _profile(raw):
    profile=parse(raw)
    need(profile.get('version')=='som.typescript-regression-input-profile.v1'
         and profile.get('scope')=='typescript002-rust006-native-regression'
         and profile.get('pending')==[] and profile.get('product_acceptance')=='not_run',
         'final source profile required; draft pending edges cannot enter formal consumer')
    need(sha(canonical({k:profile[k] for k in ('roles','siblings','native_typescript_tools','baseline_binary_sha256','baseline_source_revision')}))==ROLE_CONTENT_SHA,
         'immutable original full role maps and tool/source identities differ')
    need(profile['baseline_binary_sha256']=='2bceb3a0a1de02b08c71eb557c0c3e32fe5ee7b646935e7821e9e07ab3d56e0c'
         and profile['baseline_source_revision']=='8c4a91fbb5e3b9ae2f96881cf2198b3d2ce321af',
         'original 2bce source data role required; no Linux rebuild/current relabel')
    need(set(profile['roles'])==set(ROLES) and set(profile['siblings'])=={'rs_prompts'}
         and len(profile['siblings']['rs_prompts']['files'])==22,'exact five roles and22 sibling files required')
    need(profile['native_typescript_tools']=={'node':'bin/node','npm_cli':'lib/node_modules/npm/bin/npm-cli.js','npm_root':'lib/node_modules/npm'},
         'exact source-declared native tool paths required')
    need(profile['provenance']['npm_exact_tree_sha256']=='3a9e0a20999d2a5c9727e911c554cd66230dba9a169fe0f0a6a54bd3dc7f9186',
         'exact licensed frozen npm source tree required')
    for role,specification in [*profile['roles'].items(),('rs_prompts',profile['siblings']['rs_prompts'])]:
        need(set(specification) in ({'files'},{'files','empty_directories'}),'unknown role declaration refused')
        files=specification['files'];need(type(files) is dict and 0<len(files)<=20000,'bounded complete role file map required')
        for name,item in files.items():
            relative(name)
            if item.get('type')=='internal-alias':
                need(role=='node_tools' and set(item)=={'type','target','mode','target_sha256'}
                     and item['mode']==0o777 and type(item['target']) is str and item['target']
                     and not os.path.isabs(item['target']) and HEX.fullmatch(item['target_sha256']),
                     'only exact Node internal source aliases permitted')
            else:
                need(set(item)=={'sha256','size','mode'} and HEX.fullmatch(item['sha256'])
                     and type(item['size']) is int and 0<=item['size']<=MAX
                     and item['mode'] in (0o444,0o644,0o755),'regular source role metadata differs')
        need(not any(str(parent) in files for name in files for parent in Path(name).parents if str(parent)!='.'),'file/directory role conflict')
        for name in specification.get('empty_directories',[]):relative(name)
    node=profile['roles']['node_tools']['files']
    need(node['bin/node']=={'sha256':NODE_SHA,'size':NODE_BYTES,'mode':0o755}
         and sum(item.get('type')=='internal-alias' for item in node.values())==9,'full Node binary/alias source closure differs')
    return profile


def _catalog(value,profile_sha,helper):
    need(type(value) is dict and set(value)==CATALOG_KEYS
         and value['version']=='som.ts-input-producer-catalog-draft.v1'
         and value['job']==PRODUCER_JOB and value['run_id']==os.environ['GITHUB_RUN_ID']
         and value['run_attempt']==os.environ['GITHUB_RUN_ATTEMPT'] and value['head_sha']==helper,
         'trusted source-workflow needs producer catalog required')
    need(type(value['artifact_id']) is int and value['artifact_id']>0
         and re.fullmatch('sha256:[0-9a-f]{64}',value['artifact_digest'])
         and all(type(value[k]) is str and HEX.fullmatch(value[k]) for k in ('index_sha256','receipt_sha256','profile_sha256'))
         and value['profile_sha256']==profile_sha,'complete producer artifact/profile identities differ')
    return parse(canonical(value))


def _api(suffix):
    need(suffix.startswith('/actions/'),'bounded Actions read required')
    token=os.environ.get('SOM_DEPENDENCY_ACTIONS_TOKEN')
    need(type(token) is str and token,'read-only Actions token required')
    class NoRedirect(HTTPRedirectHandler):
        def redirect_request(self,*args,**kwargs):return None
    request=Request('https://api.github.com/repos/faberline/som'+suffix,
        headers={'Authorization':'Bearer '+token,'Accept':'application/vnd.github+json','User-Agent':'som-m12-ts-input-consumer'})
    with build_opener(NoRedirect).open(request,timeout=60) as response:
        need(response.status==200,'Actions metadata refused');raw=response.read(8*1024**2+1)
    need(len(raw)<=8*1024**2,'Actions metadata exceeds bound')
    return parse(raw)


def _origin(catalog,helper):
    run=_api('/actions/runs/'+catalog['run_id'])
    need(run['id']==int(catalog['run_id']) and run['run_attempt']==int(catalog['run_attempt']) and run['head_sha']==helper
         and run['repository']['full_name']=='faberline/som' and run['repository']['id']==int(os.environ['GITHUB_REPOSITORY_ID'])
         and run['path'].split('@',1)[0]==WORKFLOW and run['event']==os.environ['GITHUB_EVENT_NAME'],'actual Root workflow/run/attempt differs')
    artifact=_api('/actions/artifacts/'+str(catalog['artifact_id']))
    need(artifact['id']==catalog['artifact_id'] and artifact['name']==ARTIFACT_NAME and artifact['digest']==catalog['artifact_digest']
         and artifact['expired'] is False and 0<artifact['size_in_bytes']<=MAX_ZIP
         and artifact['workflow_run']['id']==run['id'] and artifact['workflow_run']['head_sha']==helper
         and artifact['workflow_run']['repository_id']==run['repository']['id']
         and datetime.fromisoformat(artifact['expires_at'].replace('Z','+00:00'))>datetime.now(timezone.utc),
         'current live uploaded artifact origin differs')
    found=[]
    for page in range(1,101):
        jobs=_api(f'/actions/runs/{run["id"]}/attempts/{run["run_attempt"]}/jobs?per_page=100&page={page}')
        found.extend(j for j in jobs['jobs'] if j['name']==PRODUCER_JOB)
        if len(jobs['jobs'])<100:break
    else:raise ValueError('bounded complete attempt job list required')
    need(len(found)==1 and found[0]['run_id']==run['id'] and found[0]['head_sha']==helper
         and found[0]['status']=='completed' and found[0]['conclusion']=='success','fixed successful producer job in actual attempt required')
    return {'run':run,'artifact':artifact,'producer_job':found[0]}


def _tree(root):
    root=physical(root,True);files={};directories={};total=0
    def onerror(error):raise error
    for directory,children,leaves in os.walk(root,followlinks=False,onerror=onerror):
        folder=physical(directory,True);directories[folder.relative_to(root).as_posix()]=stat.S_IMODE(folder.stat().st_mode)
        for name in children+leaves:
            path=folder/name;info=path.lstat();key=path.relative_to(root).as_posix()
            if stat.S_ISLNK(info.st_mode):
                target=os.readlink(path);resolved=path.resolve(strict=True)
                need(not os.path.isabs(target) and root in resolved.parents and resolved.is_file(),'only strictly internal file alias allowed')
                pin,_=hash_file(resolved);files[key]={'type':'internal-alias','target':target,'mode':stat.S_IMODE(info.st_mode),'target_sha256':pin}
            elif stat.S_ISREG(info.st_mode):
                pin,size=hash_file(path);total+=size;need(total<=TOTAL,'tree byte bound exceeded')
                files[key]={'sha256':pin,'size':size,'mode':stat.S_IMODE(info.st_mode)}
            else:need(stat.S_ISDIR(info.st_mode),'special input entry refused')
        need(len(files)<=30000,'input member bound exceeded')
    return {'files':files,'directories':directories}


def _directory_names(specification,role):
    result={'.'};empty=specification.get('empty_directories',[])
    need(type(empty) is list,'explicit empty directory list required')
    for name in [*specification['files'],*empty,*(['_logs'] if role=='npm_cache' else [])]:
        relative(name);result.update(str(parent) for parent in Path(name).parents)
        if name in empty or role=='npm_cache' and name=='_logs':result.add(name)
    return result


def _roles(profile,run):
    roots={role:physical(run/'roles'/role,True) for role in ROLES}
    siblings={'rs_prompts':physical(run/'siblings/rs_prompts',True)};all_roots=[*roots.values(),*siblings.values()]
    need(all(a!=b and a not in b.parents and b not in a.parents for i,a in enumerate(all_roots) for b in all_roots[i+1:]),'input roots overlap')
    snapshots={}
    for role,path in [*roots.items(),*siblings.items()]:
        specification=profile['siblings']['rs_prompts'] if role=='rs_prompts' else profile['roles'][role]
        actual=_tree(path);need(actual['files']==specification['files'],'complete role file/alias universe differs: '+role)
        need(set(actual['directories'])==_directory_names(specification,role),'complete role directory universe differs: '+role)
        if role=='npm_cache':
            need(not any((path/'_logs').iterdir()) and all(mode==0o555 for mode in actual['directories'].values()),'read-only cache directory scope differs')
        snapshots[role]=actual
    return roots,siblings,snapshots


def _extract_packet(archive,out,catalog):
    need(hash_file(archive)[0]==catalog['artifact_digest'].removeprefix('sha256:'),'whole downloaded ZIP differs')
    if out is not None:need(not out.exists() and not out.is_symlink(),'new sealed destination required')
    expected={};plaintext={};total=0
    with zipfile.ZipFile(archive) as z:
        infos=z.infolist();need(0<len(infos)<=2001 and len(infos)==len({i.filename for i in infos}),'complete unique sealed ZIP universe required')
        need(sum(info.file_size for info in infos)<=MAX_ZIP,'complete encrypted ZIP expansion exceeds fixed transport cap')
        for info in infos:
            need('/' not in info.filename and '\\' not in info.filename and not info.is_dir() and not info.flag_bits&1
                 and info.file_size<=257*1024**2 and stat.S_IFMT(info.external_attr>>16) in (0,stat.S_IFREG),'regular bounded encrypted ZIP member required')
        need(z.getinfo('evidence-index.json').file_size<=1024**2,'bounded encrypted index required')
        raw=z.read('evidence-index.json');need(len(raw)<=1024**2 and sha(raw)==catalog['index_sha256'],'exact uploaded index differs')
        index=parse(raw);need(index['version']=='som.m6-evidence-index.v1' and index['status']=='encrypted'
            and type(index['packages']) is list and 0<len(index['packages'])<=1000,'encrypted index scope differs')
        expected['evidence-index.json']=catalog['index_sha256']
        for number,package in enumerate(index['packages']):
            need(package['archive']==f'package-{number:03d}.sealed' and package['manifest']==f'package-{number:03d}.manifest.json','ordered sealed packages required')
            mr=z.read(package['manifest']);manifest=parse(mr)
            need(sha(mr)==package['manifest_sha256'] and manifest['version']=='som.m6-sealed-inputs-manifest.v1'
                 and manifest['cipher_sha256']==package['cipher_sha256'] and manifest['total_files']==len(manifest['files'])==package['total_files'],
                 'sealed manifest/index scope differs')
            need(type(package['total_files']) is int and 0<package['total_files']<=20000 and type(package['bytes']) is int
                 and 0<=package['bytes']<=256*1024**2 and type(manifest['uncompressed_bytes']) is int
                 and 0<=manifest['uncompressed_bytes']<=256*1024**2,'fixed original package scope bounds required')
            expected[package['manifest']]=package['manifest_sha256'];expected[package['archive']]=package['cipher_sha256']
            for row in manifest['files']:
                name=relative(row['path']);need(set(row)=={'path','sha256','bytes','mode'} and name not in plaintext and HEX.fullmatch(row['sha256'])
                    and type(row['bytes']) is int and 0<=row['bytes']<=256*1024**2 and row['mode'] in (0o644,0o755),'exact original authenticated file declaration required')
                total+=row['bytes'];need(total<=TOTAL and len(plaintext)<30000,'authenticated file scope exceeds bound')
                plaintext[name]={'sha256':row['sha256'],'size':row['bytes'],'mode':row['mode']}
            need(sum(row['bytes'] for row in manifest['files'])==package['bytes'],'original full package file byte count differs')
        need(set(z.namelist())==set(expected),'missing or extra encrypted ZIP member')
        if out is not None:out.mkdir(mode=0o700)
        for name,pin in expected.items():
            value=hashlib.sha256();count=0
            with z.open(name) as stream,((out/name).open('xb') if out is not None else io.BytesIO()) as target:
                while body:=stream.read(1024**2):
                    count+=len(body);value.update(body)
                    if out is not None:target.write(body)
            need(value.hexdigest()==pin and count==z.getinfo(name).file_size,'whole ZIP member bytes or CRC differ')
            if out is not None:(out/name).chmod(0o600)
    return {'index':index,'authenticated_files':plaintext,'sealed_files':expected}


def _proc(pid):
    raw=Path('/proc',str(pid),'stat').read_bytes();head,tail=raw.decode().rsplit(')',1);fields=tail.split()
    need(int(head.split('(',1)[0])==pid and len(fields)>19,'actual child proc record differs')
    info=Path('/proc',str(pid),'exe').stat()
    return raw,{'pid':pid,'parent_pid':int(fields[1]),'pgrp':int(fields[2]),'session':int(fields[3]),'start_ticks':int(fields[19]),
        'executable_inode':{'device':info.st_dev,'inode':info.st_ino,'size':info.st_size},
        'executable_link':os.readlink(Path('/proc',str(pid),'exe'))}


def _run_owned(argv,cwd,evidence,environment,executable,timeout=1200):
    evidence.mkdir(mode=0o700);primary=None;errors=[];process=None;owned=None;waited=False;gone=False
    record={'argv':list(map(str,argv)),'cwd':str(cwd),'pid':None,'owned_start':None,'wait_recorded':False,'exit_code':None,
        'wait_attempts':[],'signals':[],'cleanup_errors':errors,'product_acceptance_passed':False}
    def failure(stage,error):
        nonlocal primary
        errors.append({'stage':stage,'type':type(error).__name__,'message':str(error)})
        if primary is None:primary=error
    def wait(stage,seconds):
        nonlocal waited
        item={'stage':stage,'completed':False};record['wait_attempts'].append(item)
        try:process.wait(timeout=seconds);waited=True;item.update(completed=True,exit_code=process.returncode)
        except BaseException as error:failure(stage,error)
    try:
        with (evidence/'stdout.raw').open('xb') as so,(evidence/'stderr.raw').open('xb') as se:
            process=subprocess.Popen(record['argv'],cwd=cwd,env=environment,stdin=subprocess.DEVNULL,stdout=so,stderr=se,start_new_session=True)
            record['pid']=process.pid
            try:
                raw,owned=_proc(process.pid)
                need(owned['parent_pid']==os.getpid() and owned['pgrp']==owned['session']==process.pid
                     and owned['executable_inode']==executable,'actual owned crypto executable/session differs')
                record['owned_start']=owned;write(evidence/'owned-start-stat.raw',raw)
                wait('initial',timeout)
            except BaseException as error:failure('capture-start',error)
            finally:
                for stage,sig in [('term',signal.SIGTERM),('kill',signal.SIGKILL)]:
                    try:
                        if process.poll() is None:
                            if owned is None:
                                process.send_signal(sig);record['signals'].append({'signal':int(sig),'target_owned_child':process.pid})
                            else:
                                _,current=_proc(process.pid)
                                need(all(current[k]==owned[k] for k in ('pid','parent_pid','pgrp','session','start_ticks','executable_inode')),
                                     'owned crypto identity changed before signal')
                                os.killpg(process.pid,sig);record['signals'].append({'signal':int(sig),'target_pgrp':process.pid})
                    except ProcessLookupError:pass
                    except BaseException as error:
                        failure(stage,error)
                        try:
                            if process.poll() is None:
                                process.send_signal(sig);record['signals'].append({'signal':int(sig),'target_owned_child':process.pid})
                        except BaseException as secondary:failure(stage+'-direct-child',secondary)
                    if not waited:wait(stage+'-wait',5)
                try:os.killpg(process.pid,0)
                except ProcessLookupError:gone=True
                except BaseException as error:failure('group-check',error)
    except BaseException as error:failure('command-body',error)
    finally:
        record.update(wait_recorded=waited,exit_code=None if process is None else process.returncode,owned_group_gone=gone)
        if primary is not None:record['first_error']={'type':type(primary).__name__,'message':str(primary)}
        for name in ('stdout','stderr'):
            try:pin,size=hash_file(evidence/(name+'.raw'));record[name+'_sha256']=pin;record[name+'_bytes']=size
            except BaseException as error:failure('read-'+name,error)
        try:dump(evidence/'command.json',record)
        except BaseException as error:
            if primary is None:primary=error
            else:primary.__som_ts_command_receipt_error__=type(error).__name__
    if primary is not None:raise primary
    need(waited and gone and process.returncode==0 and record['owned_start'] is not None,'crypto wait/cleanup/result incomplete')
    return record


def _node(sources,node):
    node=physical(node);pins=parse(sources.read(PIN));rows=[r for r in pins['tools'] if r['tool']=='node' and r.get('host')=='x86_64-unknown-linux-gnu']
    need(len(rows)==1 and rows[0]['executable_sha256']==NODE_SHA,'exact Linux Node authority required')
    pin,size=hash_file(node);info=node.stat()
    need(pin==NODE_SHA and size==NODE_BYTES and info.st_mode&0o111,'qualified actual Linux crypto Node differs')
    with node.open('rb') as stream:need(stream.read(4)==b'\x7fELF','Linux ELF crypto tool required')
    return {'path':str(node),'sha256':pin,'bytes':size,'mode':stat.S_IMODE(info.st_mode),
        'identity':list(file_identity(info)),'executable_inode':{'device':info.st_dev,'inode':info.st_ino,'size':info.st_size}}


def _restore(sources,node,sealed,out,evidence,source_state):
    before=_node(sources,node);need('M6_INPUTS_KEYRING' in os.environ,'step-bound V1 keyring required only for provision')
    environment={k:os.environ[k] for k in ('HOME','TMPDIR') if k in os.environ}
    environment.update(PATH='/usr/bin:/bin',LANG='C.UTF-8',LC_ALL='C.UTF-8',M6_INPUTS_KEYRING=os.environ['M6_INPUTS_KEYRING'])
    index_sha=sha(raw_file(sealed/'evidence-index.json'));primary=None;post={'complete':False,'before':before,'product_acceptance_passed':False}
    try:_run_owned([node,'--no-addons',sources.root/RESTORE,'--root',sealed,'--out',out,'--index-sha256',index_sha],
        evidence.parent,evidence,environment,before['executable_inode'])
    except BaseException as error:primary=error
    finally:
        try:
            after=_node(sources,node);need(after==before,'crypto executable changed during restore')
            need(_source_state(sources,sources.revision,sources.revision)==source_state,'raw source changed during restore')
            post.update(complete=True,after=after)
        except BaseException as error:
            post['error']={'type':type(error).__name__,'message':str(error)}
            if primary is None:primary=error
        try:dump(evidence/'postcheck.json',post)
        except BaseException as error:
            if primary is None:primary=error
    if primary is not None:raise primary
    receipt=parse(raw_file(evidence/'stdout.raw'))
    need(receipt['version']=='som.m6-evidence-restore-receipt.v1' and receipt['status']=='restored'
         and receipt['index_sha256']==index_sha and receipt['product_acceptance']=='not_inferred','actual AEAD child receipt differs')


def _copy(source,target,item,*,source_mode=None):
    source=physical(source);pin,size=hash_file(source)
    need(pin==item['sha256'] and size==item['size'] and stat.S_IMODE(source.stat().st_mode)==(item['mode'] if source_mode is None else source_mode),
         'complete authenticated source member differs')
    target.parent.mkdir(parents=True,exist_ok=True,mode=0o700);write(target,raw_file(source),item['mode'])


def _restore_chunks(original,dest,*,expected_sha=NODE_SHA,expected_bytes=NODE_BYTES,chunk_bytes=CHUNK):
    record=parse(raw_file(original/'node-chunk-transport.json'))
    need(record['version']=='som.ts-node-chunk-transport.v1' and record['source']=='roles/node_tools/bin/node'
         and record['size']==expected_bytes and record['sha256']==expected_sha and record['mode']==0o755
         and record['chunk_bytes']==chunk_bytes and record['product_acceptance']=='not_run','ordered Node chunk authority differs')
    count=(expected_bytes+chunk_bytes-1)//chunk_bytes;rows=record['chunks']
    need(type(rows) is list and len(rows)==count and {p.name for p in physical(original/'node-binary-chunks',True).iterdir()}=={f'{i:03d}.part' for i in range(count)},
         'full Node chunk universe differs')
    if dest is not None:dest.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    value=hashlib.sha256();total=0
    with (dest.open('xb') if dest is not None else io.BytesIO()) as target:
        for i,row in enumerate(rows):
            size=min(chunk_bytes,expected_bytes-i*chunk_bytes)
            need(set(row)=={'path','size','sha256'} and row['path']==f'node-binary-chunks/{i:03d}.part' and row['size']==size,'exact ordered full chunk sizes required')
            raw=raw_file(original/row['path']);need(len(raw)==size and sha(raw)==row['sha256'],'whole ordered Node chunk differs')
            if dest is not None:target.write(raw)
            value.update(raw);total+=len(raw)
    need(total==expected_bytes and value.hexdigest()==expected_sha,'whole reconstructed Node differs')
    if dest is not None:dest.chmod(0o755)


def _restore_node_role(profile,original,dest,profile_sha):
    expected={k:v for k,v in profile['roles']['node_tools']['files'].items() if k!='bin/node'}
    regular={k:v for k,v in expected.items() if v.get('type')!='internal-alias'};aliases={k:v for k,v in expected.items() if v.get('type')=='internal-alias'}
    record=parse(raw_file(original/'node-tools-transport.json'));archive=physical(original/'node-tools-files.zip');pin,size=hash_file(archive)
    need(record=={'version':'som.ts-node-role-transport.v1','profile_sha256':profile_sha,'archive_sha256':pin,'archive_bytes':size,
         'files':len(expected),'aliases':len(aliases),'node_binary':'separate-ordered-chunks','product_acceptance':'not_run'}
         and size<=128*1024**2,'complete Node role inner transport authority differs')
    if dest is not None:dest.mkdir(parents=True,mode=0o700)
    with zipfile.ZipFile(archive) as z:
        members=z.infolist();need(len(members)==len(set(z.namelist())) and set(z.namelist())==set(regular),'complete Node inner ZIP universe differs')
        for member in members:
            name=relative(member.filename);item=regular[name]
            need(not member.is_dir() and not member.flag_bits&1 and member.file_size==item['size']
                 and stat.S_IFMT(member.external_attr>>16)==stat.S_IFREG and stat.S_IMODE(member.external_attr>>16)==item['mode'],'Node inner member type/mode/size differs')
            raw=z.read(member);need(sha(raw)==item['sha256'],'whole Node inner member/CRC differs')
            if dest is not None:write(dest/name,raw,item['mode'])
    for name,item in sorted(aliases.items()):
        if dest is None:
            virtual=Path('/node-role')
            target=Path(os.path.normpath(str((virtual/name).parent/item['target'])))
            need(virtual in target.parents and target.relative_to(virtual).as_posix() in regular
                 and regular[target.relative_to(virtual).as_posix()]['sha256']==item['target_sha256'],
                 'source alias does not point to an exact regular inner ZIP member')
            continue
        target=dest/relative(name);target.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
        resolved=(target.parent/item['target']).resolve(strict=True)
        need(dest in resolved.parents and resolved.is_file() and hash_file(resolved)[0]==item['target_sha256'],'Node alias target differs or escapes role')
        os.symlink(item['target'],target)
    _restore_chunks(original,None if dest is None else dest/'bin/node')


def _materialize(profile,authenticated,run,profile_sha):
    need(not run.exists(),'fresh consumer role root required');run.mkdir(parents=True,mode=0o700)
    expected_original={}
    for role,specification in [*profile['roles'].items(),('rs_prompts',profile['siblings']['rs_prompts'])]:
        if role=='node_tools':continue
        prefix=('siblings' if role=='rs_prompts' else 'roles')+'/'+role
        dest=run/prefix;dest.mkdir(parents=True,mode=0o700)
        for name,item in specification['files'].items():
            need(item.get('type') is None,'alias outside Node role refused')
            mode=0o644 if role=='npm_cache' else item['mode']
            _copy(authenticated/'run'/prefix/name,dest/name,item,source_mode=mode)
            expected_original['run/'+prefix+'/'+name]={'sha256':item['sha256'],'size':item['size'],'mode':mode}
        for name in specification.get('empty_directories',[]):(dest/relative(name)).mkdir(parents=True,exist_ok=True,mode=0o700)
        if role=='npm_cache':
            (dest/'_logs').mkdir(mode=0o700,exist_ok=True)
            for directory,children,_ in os.walk(dest,followlinks=False):
                for name in children:(Path(directory)/name).chmod(0o555)
                Path(directory).chmod(0o555)
    original=authenticated/'run';_restore_node_role(profile,original,run/'roles/node_tools',profile_sha)
    receipt=parse(raw_file(original/'materialization-receipt.json'))
    counts={k:len(v['files']) for k,v in profile['roles'].items()};counts['rs_prompts']=22
    need(receipt=={'version':'som.ts-role-materialization-draft.v1','status':'verified-draft-input-bytes','profile_sha256':profile_sha,
        'role_counts':counts,'npm_cache_readonly':True,'npm_empty_logs':True,'current_run_artifact':'unrun','product_acceptance':'not_run'},
        'original materialization data record differs')
    expected_extra={'run/materialization-receipt.json','run/node-tools-files.zip','run/node-tools-transport.json','run/node-chunk-transport.json',
                    *{'run/node-binary-chunks/'+f'{i:03d}.part' for i in range(3)}}
    return expected_original,expected_extra


def _plain(authenticated,packet):
    snapshot=_tree(authenticated)
    need(snapshot['files']==packet['authenticated_files'],'full AEAD-restored file/hash/mode universe differs')
    dirs={'.'}
    for name in packet['authenticated_files']:dirs.update(str(parent) for parent in Path(name).parents)
    need(set(snapshot['directories'])==dirs,'unowned or missing authenticated plaintext directory')
    return snapshot


def _command_proof(sources,node,evidence,sealed,authenticated,packet,source_state):
    command=parse(raw_file(evidence/'restore/command.json'));post=parse(raw_file(evidence/'restore/postcheck.json'))
    index_sha=sha(raw_file(sealed/'evidence-index.json'))
    wanted=list(map(str,[node,'--no-addons',sources.root/RESTORE,'--root',sealed,'--out',authenticated,'--index-sha256',index_sha]))
    need(command['argv']==wanted and command['cwd']==str(evidence) and type(command['pid']) is int and command['pid']>1
         and type(command['exit_code']) is int and command['exit_code']==0 and command['wait_recorded'] is True and command['owned_group_gone'] is True
         and command['cleanup_errors']==[] and command['product_acceptance_passed'] is False,'actual complete crypto command/wait proof differs')
    start=command['owned_start'];raw=raw_file(evidence/'restore/owned-start-stat.raw');head,tail=raw.decode().rsplit(')',1);fields=tail.split()
    need(int(head.split('(',1)[0])==start['pid']==start['pgrp']==start['session']==command['pid']
         and int(fields[1])==start['parent_pid'] and int(fields[2])==start['pgrp'] and int(fields[3])==start['session']
         and int(fields[19])==start['start_ticks'],'original actual proc PID/parent/group/session/start differs')
    need(any(row['completed'] is True and type(row['exit_code']) is int and row['exit_code']==0 for row in command['wait_attempts']),
         'actual terminal successful wait missing')
    current=_node(sources,node)
    need(post['complete'] is True and post['before']==post['after']==current
         and start['executable_inode']==current['executable_inode'] and current['sha256']==NODE_SHA,
         'actual crypto inode and qualified before/after bytes differ')
    for stream in ('stdout','stderr'):
        pin,size=hash_file(evidence/'restore'/(stream+'.raw'))
        need(command[stream+'_sha256']==pin and command[stream+'_bytes']==size,'full saved command stream differs')
    receipt=parse(raw_file(evidence/'restore/stdout.raw'))
    need(receipt=={'version':'som.m6-evidence-restore-receipt.v1','status':'restored','index_sha256':index_sha,
         'package_count':len(packet['index']['packages']),'file_count':len(packet['authenticated_files']),
         'bytes':sum(row['size'] for row in packet['authenticated_files'].values()),'product_acceptance':'not_inferred'},
         'actual full AEAD decoder result differs from original complete file universe')
    need(_source_state(sources,sources.revision,sources.revision)==source_state,'actual retained raw source changed')
    return {'actual_command_sha256':sha(raw_file(evidence/'restore/command.json')),
            'postcheck_sha256':sha(raw_file(evidence/'restore/postcheck.json')),'actual_stdout_sha256':command['stdout_sha256'],
            'aead_role':'actual earlier provision step; keyless verifier does not claim a new decrypt',
            'old_pid_handling':'saved proc and terminal wait records only; never query or signal old PID',
            'product_acceptance_passed':False}


def _container_layout(root):
    need({p.name for p in root.iterdir()}=={'evidence','authenticated','restored','receipt.json','context-inputs.json','evidence-manifest.json','consumer-step-proof.json'},
         'complete consumer container universe differs')
    for path,names in [(root/'restored',{'run'}),(root/'restored/run',{'roles','siblings'}),
                       (root/'restored/run/roles',set(ROLES)),(root/'restored/run/siblings',{'rs_prompts'})]:
        physical(path,True);need({p.name for p in path.iterdir()}==names,'complete consumer role parent universe differs')
        for child in path.iterdir():physical(child,True)


def _emit(proof):
    path=physical(os.environ['GITHUB_OUTPUT']);temporary=physical(os.environ['RUNNER_TEMP'],True)
    need(temporary in path.parents and path.name.startswith('set_output_'),'actual current step output channel required')
    descriptor=os.open(path,os.O_WRONLY|os.O_APPEND|os.O_NOFOLLOW)
    with os.fdopen(descriptor,'ab') as stream:stream.write(b'consumer_proof='+canonical(proof)+b'\n')


def provision_inputs(sources,*,helper_revision,candidate_revision,producer_catalog,archive,node,protected_roots):
    """Call only in Root's typescript_input_consumer step, with step-bound V1 keyring.

    producer_catalog must come only from needs.typescript-regression-inputs.outputs.producer_catalog.
    sources is the caller's already admitted exact native Source. No new grant is made here.
    """
    state=_source_state(sources,helper_revision,candidate_revision)
    profile_raw=sources.read(PROFILE);profile=_profile(profile_raw)
    catalog=_catalog(producer_catalog,sha(profile_raw),helper_revision)
    root,evidence,run=_roots(sources.root,protected_roots)
    need(not root.exists() and not root.is_symlink(),'fresh physical consumer root required; old scratch receipts refused')
    need('M6_INPUTS_KEYRING' in os.environ and 'GITHUB_OUTPUT' in os.environ,'real provision key/step output required')
    base_context_raw=raw_file(protected_roots['context_path']);base_context=parse(base_context_raw)
    need(base_context['helper_revision']==base_context['candidate_revision']==helper_revision
         and 'typescript_regression_inputs' not in base_context and 'typescript_regression_input_consumer' not in base_context,
         'fresh original Context must precede the new consumer bindings')
    if 'typescript_regression_input_producer' in base_context:
        need(base_context['typescript_regression_input_producer']==catalog,'base Context producer must be the actual needs output')
    origin=_origin(catalog,helper_revision);node=physical(node);archive=physical(archive)
    runtime_root=physical(protected_roots['runtime_root'],True)
    need(runtime_root in node.parents,'actual qualified crypto Node must stay inside the protected runtime root')
    need(hash_file(archive)==(catalog['artifact_digest'].removeprefix('sha256:'),origin['artifact']['size_in_bytes']),
         'complete actual downloaded same-run ZIP differs')
    _roots(sources.root,protected_roots)
    need(raw_file(protected_roots['context_path'])==base_context_raw,'actual original Context changed before consumer mkdir')
    root.mkdir(mode=0o700);evidence.mkdir(mode=0o700)
    try:
        dump(evidence/'source-state.json',state);dump(evidence/'producer-catalog.json',catalog);dump(evidence/'live-origin.json',origin)
        dump(evidence/'protected-roots.json',_protection_snapshot(sources.root,protected_roots))
        write(evidence/'base-context.json',base_context_raw)
        copy=evidence/'artifact.zip';write(copy,raw_file(archive),0o600)
        packet=_extract_packet(copy,evidence/'sealed',catalog);dump(evidence/'packet.json',packet)
        authenticated=root/'authenticated'
        _restore(sources,node,evidence/'sealed',authenticated,evidence/'restore',state)
        plain_before=_plain(authenticated,packet);dump(evidence/'authenticated-snapshot.json',plain_before)
        expected,extra=_materialize(profile,authenticated,run,sha(profile_raw))
        need(set(packet['authenticated_files'])==set(expected)|extra
             and all(packet['authenticated_files'][k]==v for k,v in expected.items()),'complete original wire/profile transport universe differs')
        roots,siblings,snapshots=_roles(profile,run);dump(evidence/'role-snapshots.json',snapshots)
        command=_command_proof(sources,node,evidence,evidence/'sealed',authenticated,packet,state)
        dump(evidence/'authentication-proof.json',command)
        need(_plain(authenticated,packet)==plain_before,'authenticated original plaintext changed during materialization')
        need(hash_file(copy)==hash_file(archive) and _origin(catalog,helper_revision)['artifact']['digest']==catalog['artifact_digest'],
             'cipher or live artifact changed during provision')
        receipt={'version':'som.typescript-regression-restored-inputs.v1','helper_revision':helper_revision,'candidate_revision':candidate_revision,
            'run_id':os.environ['GITHUB_RUN_ID'],'run_attempt':os.environ['GITHUB_RUN_ATTEMPT'],'profile_sha256':sha(profile_raw),
            'roots':{k:str(v) for k,v in roots.items()},'files':{k:profile['roles'][k]['files'] for k in ROLES},
            'siblings':{k:str(v) for k,v in siblings.items()},'sibling_files':{'rs_prompts':profile['siblings']['rs_prompts']['files']},
            'producer_catalog_sha256':sha(canonical(catalog)),'producer_receipt_sha256':catalog['receipt_sha256'],
            'artifact_id':catalog['artifact_id'],'artifact_digest':catalog['artifact_digest'],'index_sha256':catalog['index_sha256'],
            'consumer_job':os.environ['GITHUB_JOB'],'consumer_step':STEP_ID,'workflow_path':WORKFLOW,
            'native_qa_source_admission_complete':False,'product_acceptance_passed':False,'strict_live_2bce_gates':'UNRUN'}
        receipt_path=root/'receipt.json';dump(receipt_path,receipt)
        receipt_sha=sha(raw_file(receipt_path));need(receipt_sha!=catalog['receipt_sha256'],'old producer receipt cannot be a fresh consumer receipt')
        artifact={'id':catalog['artifact_id'],'name':ARTIFACT_NAME,'digest':catalog['artifact_digest'],'receipt_sha256':receipt_sha}
        context={'roots':receipt['roots'],'siblings':receipt['siblings'],'receipt':str(receipt_path),'artifact':artifact};dump(root/'context-inputs.json',context)
        manifest=_tree(evidence);dump(root/'evidence-manifest.json',manifest)
        proof={'version':'som.typescript-input-consumer-step-proof.v1','run_id':os.environ['GITHUB_RUN_ID'],
            'run_attempt':os.environ['GITHUB_RUN_ATTEMPT'],'head_sha':helper_revision,'workflow_path':WORKFLOW,
            'workflow_sha256':state[WORKFLOW]['sha256'],'consumer_job':os.environ['GITHUB_JOB'],'consumer_step':STEP_ID,
            'producer_catalog_sha256':sha(canonical(catalog)),'receipt_sha256':receipt_sha,
            'evidence_manifest_sha256':sha(raw_file(root/'evidence-manifest.json')),'artifact_id':catalog['artifact_id'],
            'artifact_digest':catalog['artifact_digest'],'index_sha256':catalog['index_sha256'],'product_acceptance_passed':False}
        dump(root/'consumer-step-proof.json',proof)
        _container_layout(root)
        need(raw_file(protected_roots['context_path'])==base_context_raw,'original Context changed during actual provision')
        _emit(proof)
        return {'context_inputs':context,'consumer_proof':proof,'outside_evidence_root':str(evidence),'product_acceptance_passed':False}
    except BaseException as error:
        try:dump(evidence/'first-provision-failure.json',{'type':type(error).__name__,'message':str(error),'traceback':traceback.format_exc(),
            'product_acceptance_passed':False})
        except BaseException as secondary:error.__som_ts_first_failure_retention_error__=type(secondary).__name__
        raise


def _trusted_proof(proof,state,catalog,helper_revision):
    need(type(proof) is dict and set(proof)==PROOF_KEYS and proof['version']=='som.typescript-input-consumer-step-proof.v1'
         and proof['run_id']==os.environ['GITHUB_RUN_ID'] and proof['run_attempt']==os.environ['GITHUB_RUN_ATTEMPT']
         and proof['head_sha']==helper_revision and proof['workflow_path']==WORKFLOW and proof['workflow_sha256']==state[WORKFLOW]['sha256']
         and proof['consumer_job']==os.environ['GITHUB_JOB'] and proof['consumer_step']==STEP_ID and proof['product_acceptance_passed'] is False
         and proof['producer_catalog_sha256']==sha(canonical(catalog)) and proof['artifact_id']==catalog['artifact_id']
         and proof['artifact_digest']==catalog['artifact_digest'] and proof['index_sha256']==catalog['index_sha256'],
         'actual outside-capture trusted current provision step output required')
    need(all(type(proof[k]) is str and HEX.fullmatch(proof[k]) for k in
         ('workflow_sha256','producer_catalog_sha256','receipt_sha256','evidence_manifest_sha256','index_sha256')),
         'complete trusted proof hash identities required')


def _context_delta(original,actual,context_inputs,catalog,proof):
    added={'typescript_regression_inputs','typescript_regression_input_producer','typescript_regression_input_consumer'}
    need(set(actual)==set(original)|added and all(actual[k]==v for k,v in original.items())
         and actual['typescript_regression_inputs']==context_inputs and actual['typescript_regression_input_producer']==catalog
         and actual['typescript_regression_input_consumer']==proof,
         'actual protected Context changed original fields or did not bind exact trusted consumer outputs')


def verify_after_close(sources,*,helper_revision,candidate_revision,context_inputs,producer_catalog,trusted_consumer_proof,protected_roots):
    """Keyless, same-job postclose verifier. The proof must be Root's trusted step output.

    Root binds steps.typescript_input_consumer.outputs.consumer_proof into
    Context.typescript_regression_input_consumer before products start.
    Capture-authored dictionaries, old producer receipts, and archived live PID claims are not authority.
    """
    need('M6_INPUTS_KEYRING' not in os.environ and 'M6_INPUTS_KEY' not in os.environ,'product/postclose process must not receive input keys')
    state=_source_state(sources,helper_revision,candidate_revision)
    profile_raw=sources.read(PROFILE);profile=_profile(profile_raw);catalog=_catalog(producer_catalog,sha(profile_raw),helper_revision)
    root,evidence,run=_roots(sources.root,protected_roots);physical(root,True)
    _container_layout(root)
    proof=trusted_consumer_proof
    _trusted_proof(proof,state,catalog,helper_revision)
    need(proof==parse(raw_file(root/'consumer-step-proof.json')),'saved proof differs from trusted workflow output')
    manifest_raw=raw_file(root/'evidence-manifest.json')
    need(sha(manifest_raw)==proof['evidence_manifest_sha256'] and _tree(evidence)==parse(manifest_raw),'whole saved outside-capture evidence changed')
    need(parse(raw_file(evidence/'source-state.json'))==state and parse(raw_file(evidence/'producer-catalog.json'))==catalog,
         'source/catalog actual provenance differs')
    need(parse(raw_file(evidence/'protected-roots.json'))==_protection_snapshot(sources.root,protected_roots),
         'postclose caller protection set differs from actual provision')
    original_context=parse(raw_file(evidence/'base-context.json'));actual_context=parse(raw_file(protected_roots['context_path']))
    _context_delta(original_context,actual_context,context_inputs,catalog,proof)
    origin=_origin(catalog,helper_revision)
    old_origin=parse(raw_file(evidence/'live-origin.json'))
    need(old_origin['run']['id']==origin['run']['id'] and old_origin['run']['run_attempt']==origin['run']['run_attempt']
         and old_origin['run']['head_sha']==origin['run']['head_sha'] and old_origin['artifact']['id']==origin['artifact']['id']
         and old_origin['artifact']['digest']==origin['artifact']['digest'] and old_origin['producer_job']['id']==origin['producer_job']['id'],
         'saved raw API workflow/attempt/job/artifact identities differ from fresh live API')
    need(hash_file(evidence/'artifact.zip')==(catalog['artifact_digest'].removeprefix('sha256:'),origin['artifact']['size_in_bytes']),
         'retained whole uploaded ZIP differs')
    packet=parse(raw_file(evidence/'packet.json'));sealed=evidence/'sealed'
    need(_extract_packet(evidence/'artifact.zip',None,catalog)==packet,'whole cipher/index/manifest description does not rederive')
    with zipfile.ZipFile(evidence/'artifact.zip') as archive:
        need(set(archive.namelist())==set(packet['sealed_files']) and len(archive.namelist())==len(set(archive.namelist())),
             'saved whole ZIP universe differs')
        for name,pin in packet['sealed_files'].items():
            need(sha(archive.read(name))==hash_file(sealed/name)[0]==pin,'saved cipher/index/manifest whole bytes differ')
    need(sha(raw_file(sealed/'evidence-index.json'))==catalog['index_sha256'],'saved source-bound encrypted index differs')
    authenticated=root/'authenticated'
    need(_plain(authenticated,packet)==parse(raw_file(evidence/'authenticated-snapshot.json')),'original authenticated plaintext changed')
    _restore_node_role(profile,authenticated/'run',None,sha(profile_raw))
    _command_proof(sources,parse(raw_file(evidence/'restore/postcheck.json'))['before']['path'],evidence,sealed,authenticated,packet,state)
    receipt_raw=raw_file(root/'receipt.json');receipt=parse(receipt_raw)
    need(sha(receipt_raw)==proof['receipt_sha256']!=catalog['receipt_sha256']
         and receipt['producer_catalog_sha256']==proof['producer_catalog_sha256'] and receipt['producer_receipt_sha256']==catalog['receipt_sha256']
         and receipt['artifact_id']==catalog['artifact_id'] and receipt['artifact_digest']==catalog['artifact_digest']
         and receipt['index_sha256']==catalog['index_sha256'] and receipt['helper_revision']==receipt['candidate_revision']==helper_revision
         and receipt['run_id']==os.environ['GITHUB_RUN_ID'] and receipt['run_attempt']==os.environ['GITHUB_RUN_ATTEMPT']
         and receipt['profile_sha256']==sha(profile_raw) and receipt['native_qa_source_admission_complete'] is False
         and receipt['product_acceptance_passed'] is False and receipt['strict_live_2bce_gates']=='UNRUN','fresh consumer receipt origin differs')
    roots,siblings,snapshots=_roles(profile,run)
    need(snapshots==parse(raw_file(evidence/'role-snapshots.json')) and receipt['files']=={k:profile['roles'][k]['files'] for k in ROLES}
         and receipt['sibling_files']=={'rs_prompts':profile['siblings']['rs_prompts']['files']},'full current role universes changed')
    expected={'roots':{k:str(v) for k,v in roots.items()},'siblings':{k:str(v) for k,v in siblings.items()},'receipt':str(root/'receipt.json'),
        'artifact':{'id':catalog['artifact_id'],'name':ARTIFACT_NAME,'digest':catalog['artifact_digest'],'receipt_sha256':proof['receipt_sha256']}}
    need(context_inputs==expected==parse(raw_file(root/'context-inputs.json')) and receipt['roots']==expected['roots']
         and receipt['siblings']==expected['siblings'],'Context must bind this exact new physical receipt and roots')
    return {'version':'som.typescript-input-postclose-verified.v1','receipt_sha256':proof['receipt_sha256'],
        'producer_catalog_sha256':proof['producer_catalog_sha256'],'evidence_manifest_sha256':proof['evidence_manifest_sha256'],
        'role_counts':{k:len(v['files']) for k,v in snapshots.items()},'original_actual_AEAD_step_proof_reverified':True,
        'AEAD_reexecution':'not performed; verifier has no keys','historical_PID_queries':False,
        'native_qa_source_admission_complete':False,'strict_live_2bce_gates':'UNRUN','product_acceptance_passed':False}

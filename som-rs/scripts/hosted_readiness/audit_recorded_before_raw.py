"""Independent GitHub-only authenticated DATA audit. No compiler or service runs.

The external anchor is the independently reviewed immutable commit and the
controller-verified exact GitHub workflow. This program admits a separate
finite data-only source map before any fixed helper executes.
"""
from __future__ import annotations
import argparse
import collections
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
import tarfile
import time
import traceback
from types import ModuleType
import zipfile

BASE = 'som-rs/scripts/hosted_readiness/'
SELF = BASE + 'audit_recorded_before_raw.py'
DECL = BASE + 'inputs/recorded-before-independent-raw-audit-source-v1.json'
WORKFLOW = '.github/workflows/rust-recorded-before-independent-raw-audit.yml'
TEST = BASE + 'test_recorded_before_independent_raw_audit.py'
PROFILE = BASE + 'inputs/recorded-before-profile-v1.json'
SLOTS = BASE + 'inputs/recorded-before-slots-v1.json'
PIN = 'som-rs/contracts/native-tools-v1/artifacts.json'
RELEASE = 'som-rs/evidence/typescript-output-release-2026-10-22-issue65.json'
M6 = 'som-rs/scripts/react_output_acceptance/'
MODULES = {'graph': BASE + 'same_candidate_graph.py', 'transport': BASE + 'sealed_current_raw.py',
    'stager': BASE + 'stage_recorded_before_inputs.py', 'oracle': BASE + 'product_runtime/recorded_before_oracle.py',
    'fetch': M6 + 'fetch_hosted_inputs.py'}
JS = (M6 + 'sealed_inputs.mjs', M6 + 'seal_hosted_evidence.mjs', M6 + 'restore_hosted_evidence.mjs')
FILES = {SELF, WORKFLOW, TEST, PROFILE, SLOTS, PIN, RELEASE, *MODULES.values(), *JS}
SCOPE = 'recorded-before-independent-raw-data-audit-v1'
ROOT_NAME = 'som-recorded-before-independent-raw-audit'
MAX = 320 * 1024**2
MAX_ZIP = 600 * 1024**2
PRODUCER_HEAD = 'd35a3aad611f169e64087be5bc9415cbcc5a3744'
PRODUCER_RUN = 40879174297
PRODUCER_JOB = 123465775887
PRODUCER_WORKFLOW = '.github/workflows/rust-recorded-before-data-qualification.yml'
PRODUCER = {'run_id': PRODUCER_RUN, 'run_attempt': 1, 'head_sha': PRODUCER_HEAD,
    'job_id': PRODUCER_JOB, 'workflow_path': PRODUCER_WORKFLOW, 'artifacts': {
        'new': {'id':12899522703,'name':'recorded-before-data-new-evidence','size_in_bytes':1662438,
                'digest':'sha256:939d2a70c3f83499e4307c4dbeedad7b520d24ff306b91f32a1afb2254be7774'},
        'originals': {'id':12899024097,'name':'recorded-before-data-original-encrypted-inputs','size_in_bytes':455182373,
                'digest':'sha256:626deda5872d63b6b0126b32033993857fc7934b12bf60430940d01f3c7f2287'},
        'node': {'id':12899358195,'name':'recorded-before-data-official-node-source','size_in_bytes':33606100,
                'digest':'sha256:ccc9cf9bc0d5aec391704adb7b50e8ad5af1b82f44f5c82cd74145ceb2b7ff09'}}}


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


class AuditSources:
    """Finite controller-reviewed Git/workflow data scope. Not native QA admission."""
    def __init__(self, repo, revision):
        need(os.environ.get('GITHUB_ACTIONS') == 'true' and os.environ.get('RUNNER_ENVIRONMENT') == 'github-hosted'
             and sys.platform == 'linux' and sys.version_info[:2] == (3, 12)
             and sys.flags.isolated and sys.flags.no_site and sys.dont_write_bytecode, 'GitHub isolated Python3.12 data process required')
        self.root, self.revision, self.loaded = physical(repo, True), revision, {}
        need(re.fullmatch('[0-9a-f]{40}', revision) and git(self.root,'rev-parse','HEAD').decode().strip() == revision
             and git(self.root,'rev-parse','--show-toplevel').decode().strip() == str(self.root), 'exact current Git data source required')
        need(os.environ.get('GITHUB_SHA') == os.environ.get('GITHUB_WORKFLOW_SHA') == revision
             and os.environ.get('GITHUB_REPOSITORY') == 'faberline/som'
             and os.environ.get('GITHUB_JOB') == 'recorded-before-raw-audit'
             and os.environ.get('GITHUB_EVENT_NAME') in {'push','workflow_dispatch'}, 'actual reviewed data workflow identity required')
        for key in ('GITHUB_RUN_ID','GITHUB_RUN_ATTEMPT'):
            need(re.fullmatch('[1-9][0-9]*',os.environ.get(key,'')), 'actual run/attempt required')
        for name, module in tuple(sys.modules.items()):
            value = getattr(module,'__file__',None)
            need(name == '__main__' or value is None or self.root not in Path(value).absolute().parents,
                 'preloaded repository module refused before data source admission')
        self.declaration_raw = git_source(self.root,revision,DECL)
        self.declaration = parse(self.declaration_raw)
        need(set(self.declaration) == {'version','scope','data_source_admission_complete','native_qa_source_admission_complete',
              'product_acceptance_passed','workflow_path','files','approved19','original18','producer','producer_files','producer_declaration_sha256'}
             and self.declaration['version'] == 'som.recorded-before-independent-raw-audit-source.v1'
             and self.declaration['scope'] == SCOPE and self.declaration['data_source_admission_complete'] is True
             and self.declaration['native_qa_source_admission_complete'] is False
             and self.declaration['product_acceptance_passed'] is False
             and self.declaration['workflow_path'] == WORKFLOW and set(self.declaration['files']) == FILES,
             'explicit finite data-only source amendment required')
        self.initial = {}
        for name, pin in self.declaration['files'].items():
            need(set(pin) == {'sha256','bytes'} and re.fullmatch('[0-9a-f]{64}',pin['sha256']), 'exact data-source hash/size pin required')
            raw = git_source(self.root,revision,name)
            need(sha(raw) == pin['sha256'] and len(raw) == pin['bytes'], 'data-only source member differs')
            self.initial[name] = raw
        need(parse(self.initial[PROFILE])['admission_complete'] is True, 'exact Git profile admission amendment required')
        need(self.declaration['producer'] == PRODUCER
             and self.declaration['producer_declaration_sha256'] == 'c581c65e428922f40bbd54e126a1ed39c6012d2dca027659b89e7172b2349eb3',
             'separate immutable original producer role required')
        expected = (FILES - {SELF, WORKFLOW, TEST}) | {BASE+'qualify_recorded_before_data.py', PRODUCER_WORKFLOW}
        need(set(self.declaration['producer_files']) == expected, 'complete original producer source vector required')
        self.graph = self.module('graph')
        run = self.graph.github_json('faberline/som','/actions/runs/' + os.environ['GITHUB_RUN_ID'])
        need(run['id'] == int(os.environ['GITHUB_RUN_ID']) and run['run_attempt'] == int(os.environ['GITHUB_RUN_ATTEMPT'])
             and run['head_sha'] == revision and run['event'] == os.environ['GITHUB_EVENT_NAME']
             and run['repository']['full_name'] == 'faberline/som' and run['path'].split('@',1)[0] == WORKFLOW,
             'live data workflow source identity differs')
        self.workflow_record = run

    def read(self,name):
        need(name in self.initial, 'source outside finite data-only amendment')
        raw = git_source(self.root,self.revision,name)
        need(raw == self.initial[name], 'data source changed after admission')
        return raw

    def verify(self):
        need(git(self.root,'rev-parse','HEAD').decode().strip() == self.revision
             and git_source(self.root,self.revision,DECL) == self.declaration_raw, 'data source HEAD/amendment changed')
        for name in self.initial: self.read(name)

    def module(self,role):
        need(role in MODULES, 'fixed data helper role required')
        if role in self.loaded: return self.loaded[role]
        name = '_som_recorded_raw_audit_' + role
        need(name not in sys.modules, 'preloaded data helper refused')
        path = MODULES[role]; raw = self.read(path)
        module = ModuleType(name)
        module.__file__, module.__cached__, module.__package__ = str(self.root/path),None,''
        module.__som_raw_sha256__ = sha(raw)
        sys.modules[name] = module
        try: exec(compile(raw,module.__file__,'exec',dont_inherit=True),module.__dict__)
        except BaseException:
            sys.modules.pop(name,None)
            raise
        self.loaded[role] = module
        self.verify()
        return module


def bounded_env(keyring=False):
    result = {'PATH':'/usr/bin:/bin','LANG':'C.UTF-8','LC_ALL':'C.UTF-8','PYTHONDONTWRITEBYTECODE':'1'}
    for name in ('HOME','TMPDIR'):
        if name in os.environ: result[name] = os.environ[name]
    if keyring:
        need('M6_INPUTS_KEYRING' in os.environ, 'step-scoped authorized input keyring required')
        result['M6_INPUTS_KEYRING'] = os.environ['M6_INPUTS_KEYRING']
    return result


def paths():
    temporary=physical(os.environ['RUNNER_TEMP'],True)
    root=temporary/ROOT_NAME
    need(not root.is_symlink(),'owned independent audit root required')
    return root,root/'new-evidence',root/'node'


def hash_file(path, maximum=MAX_ZIP):
    path=physical(path);before=path.stat();need(before.st_size<=maximum,'bounded file required')
    value=hashlib.sha256();count=0
    with os.fdopen(os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK),'rb') as stream:
        while body:=stream.read(1024**2):
            value.update(body);count+=len(body);need(count<=maximum,'file exceeds bound')
        need(file_identity(os.fstat(stream.fileno()))==file_identity(before),'open file changed')
    need(count==before.st_size and file_identity(path.stat())==file_identity(before),'file changed')
    return value.hexdigest(),count


def command(argv,cwd,out,*,environment,stdout=None,timeout=1200):
    out=Path(out);out.mkdir(mode=0o700)
    destination=out/'stdout.raw' if stdout is None else Path(stdout)
    started=time.monotonic();primary=None;errors=[];process=None;waited=False;group_gone=False
    record={'argv':list(map(str,argv)),'pid':None,'wait_recorded':False,'exit_code':None,
        'owned_group_cleanup_attempted':False,'group_gone':False,'product_acceptance_passed':False}
    try:
        with destination.open('xb') as so,(out/'stderr.raw').open('xb') as se:
            process=subprocess.Popen(record['argv'],cwd=physical(cwd,True),env=environment,
                stdin=subprocess.DEVNULL,stdout=so,stderr=se,start_new_session=True)
            record['pid']=process.pid
            try:
                process.wait(timeout=timeout);waited=True
            except BaseException as error: primary=error
            finally:
                record['owned_group_cleanup_attempted']=True
                for operation in ('term','wait','kill','final-wait'):
                    try:
                        if operation=='term': os.killpg(process.pid,signal.SIGTERM)
                        elif operation=='kill': os.killpg(process.pid,signal.SIGKILL)
                        elif not waited: process.wait(timeout=5);waited=True
                    except ProcessLookupError: pass
                    except BaseException as error:
                        errors.append({'operation':operation,'type':type(error).__name__,'message':str(error)})
                        if primary is None: primary=error
                try: os.killpg(process.pid,0)
                except ProcessLookupError: group_gone=True
                except BaseException as error:
                    errors.append({'operation':'group-check','type':type(error).__name__,'message':str(error)})
                    if primary is None: primary=error
        need(process is not None,'actual command process required')
    except BaseException as error:
        if primary is None: primary=error
    finally:
        record.update(wait_recorded=waited,exit_code=None if process is None else process.returncode,
            group_gone=group_gone,elapsed_seconds=time.monotonic()-started,cleanup_errors=errors)
        if primary is not None: record['first_error']={'type':type(primary).__name__,'message':str(primary)}
        for name,path in [('stdout',destination),('stderr',out/'stderr.raw')]:
            try: pin,size=hash_file(path);record[name+'_sha256']=pin;record[name+'_bytes']=size
            except BaseException as error:
                if primary is None: primary=error
                record[name+'_read_error']=type(error).__name__
        try: dump(out/'command.json',record)
        except BaseException as error:
            if primary is None: primary=error
            else: primary.__som_audit_record_error__=type(error).__name__
    if primary is not None: raise primary
    need(waited and group_gone and process.returncode==0 and not errors,'actual command or cleanup failed')
    return record


def crypto(source,script,args,evidence,key=None):
    need(script in JS,'fixed crypto source required')
    _,_,node=paths();transport=source.module('transport')
    before=transport.checked_node(source,node/'bin/node')
    for name in JS:source.read(name)
    environment=bounded_env(key is None)
    if key is not None:environment['M6_INPUTS_KEY']=key
    primary=None;record=None;post={'complete':False,'node':before,'source_revision':source.revision,'product_acceptance_passed':False}
    try:
        record=command([node/'bin/node','--no-addons',source.root/script,*args],evidence.parent,evidence,
            environment=environment)
    except BaseException as error:primary=error
    finally:
        try:
            need(transport.checked_node(source,node/'bin/node')==before,'Node changed during data command')
            source.verify();post['complete']=True
        except BaseException as error:
            post['error']={'type':type(error).__name__,'message':str(error)}
            if primary is None:primary=error
        try:dump(evidence/'source-node-postcheck.json',post)
        except BaseException as error:
            if primary is None:primary=error
    if primary is not None:raise primary
    return record


def relative(name):
    need(type(name) is str and name and not name.startswith('/') and '\\' not in name
         and all(x not in {'','.','..'} for x in name.split('/')),'bounded relative name required')
    return name


def zip_inventory(path):
    physical(path);rows=[];total=0
    with zipfile.ZipFile(path) as archive:
        infos=archive.infolist();need(len(infos)<=20000 and len({i.filename for i in infos})==len(infos),'ZIP universe differs')
        for info in infos:
            relative(info.filename)
            need(not info.is_dir() and not info.flag_bits&1 and info.file_size<=MAX
                 and stat.S_IFMT(info.external_attr>>16) in (0,stat.S_IFREG),'regular bounded ZIP data required')
            value=hashlib.sha256();count=0
            with archive.open(info) as stream:
                while body:=stream.read(1024**2):value.update(body);count+=len(body)
            need(count==info.file_size,'ZIP member size differs')
            total+=count;need(total<=2*1024**3,'ZIP total exceeds bound')
            rows.append({'path':info.filename,'bytes':count,'sha256':value.hexdigest(),'crc32':f'{info.CRC:08x}'})
    return rows


def producer_receipt(receipt):
    need(receipt['source_revision']==PRODUCER_HEAD and receipt['run_id']==str(PRODUCER_RUN)
         and receipt['run_attempt']=='1' and receipt['scope']=='recorded-before-data-only-v1'
         and receipt['actual_new_evidence_roundtrip_complete'] is True and receipt['data_factory_qualified'] is True,
         'original producer data result differs')
    need(all(receipt[k] is False for k in ('native_qa_source_admission_complete','native_Context_or_service_qualified',
         'product_acceptance_passed')),'producer native/product grant refused')


def verify_inputs(source):
    root,_,_=paths()
    for role,pin in PRODUCER['artifacts'].items():
        actual=source.graph.artifact_metadata('faberline/som',pin['id'])
        need(all(actual.get(k)==v for k,v in pin.items()) and actual.get('expired') is False
             and actual['workflow_run']['id']==PRODUCER_RUN and actual['workflow_run']['head_sha']==PRODUCER_HEAD,
             'actual producer artifact identity differs')
        value,size=hash_file(root/'downloads'/(role+'.zip'))
        need('sha256:'+value==pin['digest'] and size==pin['size_in_bytes'],'whole producer ZIP differs')


def prepare(source):
    root,evidence,node=paths();need(not root.exists(),'fresh audit root required');root.mkdir(mode=0o700)
    for p in (evidence,node,root/'downloads'):p.mkdir(mode=0o700)
    dump(evidence/'source-admission.json',{'scope':SCOPE,'source_revision':source.revision,
        'declaration_sha256':sha(source.declaration_raw),'files':source.declaration['files'],
        'actual_workflow':source.workflow_record,'native_qa_source_admission_complete':False,'product_acceptance_passed':False})
    run=source.graph.github_json('faberline/som',f'/actions/runs/{PRODUCER_RUN}')
    need(run['id']==PRODUCER_RUN and run['run_attempt']==1 and run['head_sha']==PRODUCER_HEAD
         and run['path'].split('@',1)[0]==PRODUCER_WORKFLOW and run['conclusion']=='success'
         and run['repository']['full_name']=='faberline/som','original successful producer run required')
    jobs=source.graph.github_json('faberline/som',f'/actions/runs/{PRODUCER_RUN}/jobs?filter=latest&per_page=100')
    need(jobs['total_count']==1 and len(jobs['jobs'])==1 and jobs['jobs'][0]['id']==PRODUCER_JOB
         and jobs['jobs'][0]['conclusion']=='success'
         and all(s['conclusion']=='success' for s in jobs['jobs'][0]['steps']),'original successful producer job required')
    dump(evidence/'producer-run.json',run);dump(evidence/'producer-jobs.json',jobs)
    for role,pin in PRODUCER['artifacts'].items():
        actual=source.graph.artifact_metadata('faberline/som',pin['id']);dump(evidence/(role+'-metadata.json'),actual)
        environment=bounded_env();environment['GH_TOKEN']=os.environ['SOM_DEPENDENCY_ACTIONS_TOKEN']
        command(['/usr/bin/gh','api',f'repos/faberline/som/actions/artifacts/{pin["id"]}/zip'],root,
            evidence/('download-'+role),stdout=root/'downloads'/(role+'.zip'),environment=environment)
    verify_inputs(source)
    for role in PRODUCER['artifacts']:dump(evidence/(role+'-zip-inventory.json'),zip_inventory(root/'downloads'/(role+'.zip')))
    pins=parse(source.read(PIN));rows=[p for p in pins['tools'] if p['tool']=='node' and p.get('host')=='x86_64-unknown-linux-gnu']
    need(len(rows)==1,'one exact Node source pin required');pin=rows[0]
    prefix='_temp/som-recorded-before-data-qualification/node/'
    with zipfile.ZipFile(root/'downloads/node.zip') as archive:
        body=archive.read(prefix+'original.tar.xz')
        need(sha(body)==pin['archive_sha256'] and len(body)==pin['archive_bytes'],'original official Node archive differs')
        write(node/'original.tar.xz',body)
        expected={prefix+'original.tar.xz','som/som/tested-sha.txt','som/som/python-version.txt'}
        need(archive.read('som/som/tested-sha.txt').decode().strip()==PRODUCER_HEAD,'Node artifact source SHA differs')
        with tarfile.open(fileobj=io.BytesIO(body),mode='r:xz') as tar:
            members=tar.getmembers();need(len(members)==len({m.name for m in members}),'duplicate official archive member')
            selected={pin['executable_member']:pin['executable_sha256'],**pin['licenses']}
            need(len(pin['licenses'])==152,'complete Node notice scope required')
            for name,digest in selected.items():
                member=tar.getmember(name);need(member.isfile() and not member.linkname,'regular official source member required')
                raw=tar.extractfile(member).read();need(sha(raw)==digest,'official source member differs')
                if name==pin['executable_member']:write(node/'bin/node',raw,0o755)
                else:
                    need(archive.read(prefix+'notices/'+name)==raw,'uploaded notice differs');write(node/'notices'/relative(name),raw,0o644)
                    expected.add(prefix+'notices/'+name)
        need(set(archive.namelist())==expected,'complete public source ZIP universe differs')
    source.module('transport').checked_node(source,node/'bin/node');source.verify()


def provision(source):
    root,evidence,_=paths();fetch=source.module('fetch');wanted=source.declaration['approved19']
    need(wanted=={'commit':'02cd9e79cdcedfef92816f67a3ccdf2e089964ab',
         'index_sha256':'6953812ebe0f388cefe77ec90e8659a8f70d6e8ccf9517f6417e7a3b9b774c28'},'actual19 role required')
    config=root/'approved19-config.json';dump(config,{'version':'som.m6-hosted-inputs.v1',**wanted})
    packages=fetch.load_manifests(source.root,fetch.load_config(config));need(len(packages)==19,'complete19 scope required')
    ring=fetch.load_keyring(bounded_env(True),packages);private=root/'input19-private';private.mkdir(mode=0o700)
    files=[];expected={'encrypted-inputs/'+role+'.zip' for role in ('original','calibration','fresh')}
    with zipfile.ZipFile(root/'downloads/originals.zip') as archive:
        raw=archive.read('encrypted-approved19/inputs-index.json')
        need(sha(raw)==wanted['index_sha256'] and raw==git(source.root,'cat-file','blob',wanted['commit']+':inputs-index.json'),
             'original19 full index differs');expected.add('encrypted-approved19/inputs-index.json')
        for number,package in enumerate(packages):
            entry,manifest=package['entry'],package['manifest'];sealed=private/f'cipher-{number:03d}';sealed.mkdir(mode=0o700)
            mr=archive.read('encrypted-approved19/'+entry['manifest']);cipher=archive.read('encrypted-approved19/'+entry['archive'])
            need(mr==package['raw_manifest'] and sha(cipher)==manifest['cipher_sha256'],'original19 retained cipher/manifest differs')
            write(sealed/entry['manifest'],mr);write(sealed/entry['archive'],cipher)
            restored=private/f'restored-{number:03d}'
            crypto(source,JS[0],['restore','--archive',sealed/entry['archive'],'--manifest',sealed/entry['manifest'],'--out',restored],
                evidence/f'input19-{number:03d}',key=ring[manifest['cipher_sha256']])
            files.extend(fetch.check_restored(restored,manifest))
            expected.update('encrypted-approved19/'+entry[k] for k in ('archive','manifest'))
        need(set(archive.namelist())==expected,'original encrypted input ZIP universe differs')
    receipt={'version':fetch.PROVISION_VERSION,'stage':'input_provision','status':'passed','file_count':len(files),
        'bytes':sum(r['bytes'] for _,r in files),'index_sha256':wanted['index_sha256'],'product_suites':'not_run'}
    fetch.publish(root/'approved19',files,receipt);dump(evidence/'actual19-provision.json',receipt)
    original=source.declaration['original18'];need(original=={'commit':'1de8f05430a72f8d76f6159786cca71b0ae1a911',
        'index_sha256':'c468ae2086e4d64847b93ad8da08aa3828f30989008db470519cd408e55b67bf'},'strict original18 role required')
    metadata=root/'original18-metadata';metadata.mkdir(mode=0o700)
    raw=git(source.root,'cat-file','blob',original['commit']+':inputs-index.json');need(sha(raw)==original['index_sha256'],'original18 index differs')
    write(metadata/'inputs-index.json',raw);packages=parse(raw)['packages'];need(len(packages)==18,'original18 count differs')
    for p in packages:
        body=git(source.root,'cat-file','blob',original['commit']+':'+p['manifest']);need(sha(body)==p['manifest_sha256'],'original18 manifest differs')
        write(metadata/relative(p['manifest']),body)
    dump(evidence/'approved19-after-provision.json',check_approved19(source))
    source.verify();verify_inputs(source)


def check_approved19(source):
    root,_,_=paths();fetch=source.module('fetch')
    packages=fetch.load_manifests(source.root,fetch.load_config(root/'approved19-config.json'))
    files={r['path']:r for p in packages for r in p['manifest']['files']}
    need(len(files)==sum(len(p['manifest']['files']) for p in packages),'duplicate actual19 published member')
    names=set(files)|{'provision.json'};dirs={'.'}
    for name in names:dirs.update(str(p) for p in Path(name).parents)
    observed=set();observed_dirs=set();published=physical(root/'approved19',True)
    def onerror(error):raise error
    for directory,children,leaves in os.walk(published,followlinks=False,onerror=onerror):
        path=physical(directory,True);observed_dirs.add(path.relative_to(published).as_posix())
        for name in children:physical(path/name,True)
        for name in leaves:
            filename=physical(path/name);relative_name=filename.relative_to(published).as_posix();observed.add(relative_name)
            need(relative_name in names,'unowned actual19 published file')
            if relative_name in files:
                item=files[relative_name];raw=raw_file(filename,item['bytes'])
                need(len(raw)==item['bytes'] and sha(raw)==item['sha256'] and stat.S_IMODE(filename.stat().st_mode)==item['mode'],
                     'actual19 published bytes or mode changed')
    need(observed==names and observed_dirs==dirs,'actual19 published full universe differs')
    receipt=parse(raw_file(published/'provision.json'))
    need(receipt['index_sha256']==source.declaration['approved19']['index_sha256']
         and receipt['file_count']==len(files) and receipt['product_suites']=='not_run','actual19 publication receipt differs')
    return {'index_sha256':receipt['index_sha256'],'files':files,'provision_sha256':sha(raw_file(published/'provision.json'))}


def original_qualification(proof,metadata,profile_sha):
    return {'version':'som.recorded-before-data-qualification.v1','source_revision':PRODUCER_HEAD,
        'source_admission_scope':'recorded-before-data-only-v1','data_factory_qualified':True,
        'profile_sha256':profile_sha,'inventory_sha256':proof['inventory_sha256'],'table_sha256':proof['table_sha256'],
        'named_slots':4572,'original_calibrated_slots':3875,'fresh_before_slots':697,'source_compiler':metadata,
        'native_qa_source_admission_complete':False,'native_Context_or_service_qualified':False,
        'runtime_comparison':'unrun','product_acceptance_passed':False,
        'lossless_original_inputs_retained_as':'separate unchanged-ciphertext artifact recorded-before-data-original-encrypted-inputs; complete three original ZIPs and all19 authorized Git packages',
        'original_inputs_in_new_evidence_envelope':False,'original_inputs_are_new_observations':False}


def audit(source):
    root,evidence,node=paths();verify_inputs(source)
    approved19_before=check_approved19(source)
    need(approved19_before==parse(raw_file(evidence/'approved19-after-provision.json')),'actual19 changed after independent provision')
    transport=source.module('transport');stage=source.module('stager');oracle=source.module('oracle')
    with zipfile.ZipFile(root/'downloads/new.zip') as archive:
        receipt=parse(archive.read('receipt.json'));producer_receipt(receipt)
        expected={'receipt.json'};sealed=root/'producer-sealed';sealed.mkdir(mode=0o700)
        ir=archive.read('sealed/evidence-index.json');need(sha(ir)==receipt['index_sha256'],'producer index differs')
        index=parse(ir);need(len(index['packages'])==1 and index['status']=='encrypted','complete producer encrypted role required')
        write(sealed/'evidence-index.json',ir);expected.add('sealed/evidence-index.json')
        for n,p in enumerate(index['packages']):
            need(p['archive']==f'package-{n:03d}.sealed' and p['manifest']==f'package-{n:03d}.manifest.json','producer package order differs')
            mr=archive.read('sealed/'+p['manifest']);cipher=archive.read('sealed/'+p['archive']);m=parse(mr)
            need(sha(mr)==p['manifest_sha256'] and sha(cipher)==p['cipher_sha256']==m['cipher_sha256'],'producer cipher/manifest differs')
            write(sealed/p['manifest'],mr);write(sealed/p['archive'],cipher)
            expected.update('sealed/'+p[k] for k in ('archive','manifest'))
        for role in ('seal','restore'):
            prefix='transport-commands/'+role;record=parse(archive.read(prefix+'-command.json'))
            need(record['exit_code']==0 and record['wait_recorded'] is True and record['owned_group_cleanup_attempted'] is True
                 and record['source_and_node_postcheck_complete'] is True,'original actual transport command incomplete')
            expected.add(prefix+'-command.json')
            for stream in ('stdout','stderr'):
                need(sha(archive.read(prefix+'-m6.'+stream))==record[stream+'_sha256'],'original full transport stream differs')
                expected.add(prefix+'-m6.'+stream)
        need(set(archive.namelist())==expected,'original new-evidence outer ZIP universe differs')
    hosted=root/'producer-hosted'
    crypto(source,JS[2],['--root',sealed,'--out',hosted,'--index-sha256',receipt['index_sha256']],evidence/'restore-producer')
    folder=hosted/'run/transport';raw=raw_file(folder/'transport.json');descriptor=parse(raw)
    need(sha(raw)==receipt['transport_manifest_sha256'] and descriptor['source_revision']==PRODUCER_HEAD
         and descriptor['run_id']==str(PRODUCER_RUN) and descriptor['run_attempt']=='1'
         and descriptor['scope']=='recorded-before-data-only-v1' and descriptor['product_acceptance_passed'] is False,
         'authenticated original producer descriptor differs')
    need(descriptor['archive_sha256']==receipt['new_evidence_tar_sha256'] and descriptor['archive_bytes']==receipt['new_evidence_tar_bytes']
         and descriptor['inventory_sha256']==receipt['new_evidence_inventory_sha256'],'authenticated original whole capture differs')
    tar=root/'producer-raw.tar';transport.rejoin_archive(folder/'chunks',descriptor['chunks'],tar,descriptor['archive_bytes'],descriptor['archive_sha256'])
    saved=root/'producer-saved';transport.unpack_raw(tar,descriptor,saved)
    source_receipt=parse(raw_file(saved/'source-admission.json'));old_run=source_receipt['live_workflow_record']
    need(source_receipt['source_revision']==PRODUCER_HEAD and source_receipt['scope']=='recorded-before-data-only-v1'
         and source_receipt['declaration_sha256']==source.declaration['producer_declaration_sha256']
         and source_receipt['files']==source.declaration['producer_files']
         and source_receipt['native_qa_source_admission_complete'] is False and source_receipt['product_acceptance_passed'] is False
         and old_run['id']==PRODUCER_RUN and old_run['run_attempt']==1 and old_run['head_sha']==PRODUCER_HEAD
         and old_run['path'].split('@',1)[0]==PRODUCER_WORKFLOW,'original archived source/workflow role differs')
    need(not any(p.name.startswith('first-failure-') for p in saved.iterdir()),'original producer failure was not a successful factory')
    data=root/'fresh-data';data.mkdir(mode=0o700);profile=parse(source.read(PROFILE));catalog={}
    with zipfile.ZipFile(root/'downloads/originals.zip') as archive:
        for role in ('original','calibration','fresh'):
            pin=profile['artifacts'][role];metadata=source.graph.artifact_metadata('faberline/som',pin['id']);stage.artifact_metadata(pin,metadata)
            directory=data/role;directory.mkdir(mode=0o700);body=archive.read('encrypted-inputs/'+role+'.zip')
            need(len(body)==pin['size_in_bytes'] and 'sha256:'+sha(body)==pin['digest'],'original whole artifact differs')
            write(directory/'artifact.zip',body);zip_inventory(directory/'artifact.zip')
            stage.extract_sealed(directory/'artifact.zip',directory/'sealed',pin)
            crypto(source,JS[2],['--root',directory/'sealed','--out',directory/'restored','--index-sha256',pin['index_sha256']],
                   evidence/('restore-original-'+role))
            catalog[role]=metadata
    approved=stage.stage_approved(root/'original18-metadata',root/'approved19',data/'approved')
    write(data/'catalog.json',canonical(catalog)+b'\n');dump(evidence/'original18-stage.json',approved)
    table,metadata,proof,verify=oracle.prepare_data(source.read,PROFILE,data)
    need(len(table.entries)==proof['named_slots']==4572 and proof['cohorts']==oracle.COUNTS
         and proof['runtime_comparison']=='unrun' and proof['product_acceptance_passed'] is False
         and proof['original_missing_wire_fields_changed'] is False,'complete data-only table scope differs')
    raw=oracle.json_bytes(table.public())+b'\n'
    need(raw_file(saved/'table.json')==raw and sha(raw)==proof['table_sha256'],'whole original4572 table differs from fresh wire derivation')
    need(raw_file(saved/'factory-proof.json')==canonical(proof)+b'\n','whole original factory proof differs')
    expected=original_qualification(proof,metadata,sha(source.read(PROFILE)))
    need(raw_file(saved/'qualification.json')==canonical(expected)+b'\n','whole original qualification differs')
    write(evidence/'rederived-table.json',raw);dump(evidence/'rederived-factory-proof.json',proof)
    for entry in table.entries:
        folder=evidence/'derived-original-wire'/f'{entry["identity"]["slot_id"]:06d}'
        write(folder/'request.raw',entry['request']);write(folder/'response.raw',entry['response'])
        dump(folder/'role.json',{'identity':entry['identity'],'status':entry['status'],'source':entry['source'],
             'role':'fresh independent read of original immutable wire bytes; not new compiler execution'})
    verify();verify_inputs(source);source.verify()
    need(check_approved19(source)==approved19_before,'actual19 changed during independent audit')
    restored_rows,_=transport.inventory(saved)
    need(restored_rows==descriptor['inventory'],'original capture bytes/modes changed during independent audit')
    dump(evidence/'audit-result.json',{'version':'som.recorded-before-independent-raw-audit-result.v1',
        'source_revision':source.revision,'source_admission_scope':SCOPE,'producer':PRODUCER,
        'independent_plaintext_audit_complete':True,'named_slots':4572,'original_calibrated_slots':3875,'fresh_before_slots':697,
        'table_sha256':proof['table_sha256'],'factory_proof_sha256':sha(canonical(proof)+b'\n'),
        'original_qualification_sha256':sha(canonical(expected)+b'\n'),'original_missing_wire_fields_changed':False,
        'historical_process_records':'source-bound saved facts only; no live PID queries or compiler execution',
        'runtime_comparison':'unrun','native_qa_source_admission_complete':False,'native_Context_or_service_qualified':False,
        'product_acceptance_passed':False})


def seal(source):
    root,evidence,_=paths();temporary=physical(os.environ['RUNNER_TEMP'],True)
    work=temporary/'som-recorded-before-independent-raw-audit-seal-work'
    out=temporary/'som-recorded-before-independent-raw-audit-sealed'
    need(not work.exists() and not work.is_symlink() and not out.exists() and not out.is_symlink(),'fresh audit seal roots required')
    work.mkdir(mode=0o700);out.mkdir(mode=0o700);primary=None;failure_sealed=False
    transport=source.module('transport')
    try:
        tar=work/'raw.tar';record=transport.pack_raw(evidence,tar)
        record.update(version='som.recorded-before-independent-audit-evidence.v1',scope=SCOPE,source_revision=source.revision,
            run_id=os.environ['GITHUB_RUN_ID'],run_attempt=os.environ['GITHUB_RUN_ATTEMPT'],product_acceptance_passed=False)
        folder=work/'hosted/run/transport';folder.mkdir(parents=True)
        record['chunks']=transport.split_archive(tar,folder/'chunks');write(folder/'transport.json',transport.canonical(record)+b'\n')
        crypto(source,JS[1],['--root',work/'hosted','--out',out/'sealed'],work/'seal-command')
        index=sha(raw_file(out/'sealed/evidence-index.json'))
        crypto(source,JS[2],['--root',out/'sealed','--out',work/'roundtrip-hosted','--index-sha256',index],work/'restore-command')
        restored=work/'roundtrip-hosted/run/transport'
        need(raw_file(restored/'transport.json')==raw_file(folder/'transport.json'),'new audit descriptor roundtrip differs')
        transport.rejoin_archive(restored/'chunks',record['chunks'],work/'roundtrip.tar',record['archive_bytes'],record['archive_sha256'])
        transport.unpack_raw(work/'roundtrip.tar',record,work/'roundtrip-raw')
        rows,ids=transport.inventory(evidence);need(rows==record['inventory'] and ids==record['original_identities'],'new audit observations changed during seal')
        result=parse(raw_file(evidence/'audit-result.json')) if (evidence/'audit-result.json').is_file() else {}
        dump(out/'receipt.json',{'version':'som.recorded-before-independent-raw-audit-safe-receipt.v1','source_revision':source.revision,
            'run_id':os.environ['GITHUB_RUN_ID'],'run_attempt':os.environ['GITHUB_RUN_ATTEMPT'],'producer_run_id':PRODUCER_RUN,
            'actual_new_evidence_roundtrip_complete':True,'independent_plaintext_audit_complete':result.get('independent_plaintext_audit_complete') is True,
            'named_slots':result.get('named_slots'),'table_sha256':result.get('table_sha256'),'index_sha256':index,
            'new_evidence_tar_sha256':record['archive_sha256'],'new_evidence_tar_bytes':record['archive_bytes'],
            'native_qa_source_admission_complete':False,'native_Context_or_service_qualified':False,'product_acceptance_passed':False})
        source.verify()
    except BaseException as error:
        primary=error
        try:
            dump(work/'failure-hosted/run/first-seal-failure.json',{'type':type(error).__name__,'message':str(error),'traceback':traceback.format_exc(),
                 'actual_new_evidence_roundtrip_complete':False,'product_acceptance_passed':False})
            crypto(source,JS[1],['--root',work/'failure-hosted','--out',out/'failure-sealed'],work/'failure-seal-command');failure_sealed=True
        except BaseException as secondary:primary.__som_failure_seal_error__=type(secondary).__name__
        raise
    finally:
        errors=[]
        for role in ('seal','restore','failure-seal'):
            for name in ('stdout.raw','stderr.raw','command.json','source-node-postcheck.json'):
                original=work/(role+'-command')/name
                if original.is_file():
                    try:write(out/'transport-commands'/(role+'-'+name),raw_file(original))
                    except BaseException as error:errors.append(type(error).__name__)
        if primary is not None:
            try:dump(out/'failure-retention.json',{'first_error_type':type(primary).__name__,'private_first_failure_sealed':failure_sealed,
                'actual_new_evidence_roundtrip_complete':False,'transport_retention_errors':errors,'product_acceptance_passed':False})
            except BaseException as error:primary.__som_retention_error__=type(error).__name__
        elif errors:raise ValueError('audit transport evidence retention failed')


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--repo',type=Path,required=True)
    parser.add_argument('--revision',required=True);parser.add_argument('--phase',choices=('prepare','provision','audit','seal'),required=True)
    args=parser.parse_args()
    try:
        source=AuditSources(args.repo,args.revision)
        {'prepare':prepare,'provision':provision,'audit':audit,'seal':seal}[args.phase](source)
    except BaseException as error:
        _,evidence,_=paths()
        if evidence.is_dir():
            try:dump(evidence/('first-failure-'+args.phase+'.json'),{'phase':args.phase,'type':type(error).__name__,
                'message':str(error),'traceback':traceback.format_exc(),'product_acceptance_passed':False})
            except BaseException:pass
        raise
    return 0


if __name__=='__main__':
    try:raise SystemExit(main())
    except Exception:
        sys.stderr.write('independent recorded-before data audit refused; retain encrypted evidence\n')
        raise SystemExit(1)


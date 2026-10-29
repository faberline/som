"""Qualify recorded before DATA on GitHub. No compiler, service or product runs.

The separate tracked data-source amendment admits only this finite program and
its data/AEAD helpers. It does not activate the full native QA source policy.
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
from urllib.request import Request, build_opener

BASE = 'som-rs/scripts/hosted_readiness/'
SELF = BASE + 'qualify_recorded_before_data.py'
DECL = BASE + 'inputs/recorded-before-data-source-admission-v1.json'
WORKFLOW = '.github/workflows/rust-recorded-before-data-qualification.yml'
PROFILE = BASE + 'inputs/recorded-before-profile-v1.json'
SLOTS = BASE + 'inputs/recorded-before-slots-v1.json'
PIN = 'som-rs/contracts/native-tools-v1/artifacts.json'
RELEASE = 'som-rs/evidence/typescript-output-release-2026-10-22-issue65.json'
M6 = 'som-rs/scripts/react_output_acceptance/'
MODULES = {'graph': BASE + 'same_candidate_graph.py', 'transport': BASE + 'sealed_current_raw.py',
    'stager': BASE + 'stage_recorded_before_inputs.py', 'oracle': BASE + 'product_runtime/recorded_before_oracle.py',
    'fetch': M6 + 'fetch_hosted_inputs.py'}
JS = (M6 + 'sealed_inputs.mjs', M6 + 'seal_hosted_evidence.mjs', M6 + 'restore_hosted_evidence.mjs')
FILES = {SELF, WORKFLOW, PROFILE, SLOTS, PIN, RELEASE, *MODULES.values(), *JS}
SCOPE = 'recorded-before-data-only-v1'
ROOT_NAME = 'som-recorded-before-data-qualification'
MAX = 320 * 1024**2


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


class DataSources:
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
             and os.environ.get('GITHUB_JOB') == 'recorded-before-data'
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
              'product_acceptance_passed','workflow_path','files','approved19','original18'}
             and self.declaration['version'] == 'som.recorded-before-data-source-admission.v1'
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
        name = '_som_recorded_data_' + role
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


def paths():
    temporary = physical(os.environ['RUNNER_TEMP'],True)
    root = temporary / ROOT_NAME
    need(not root.is_symlink(), 'physical owned data root required')
    return root, root/'new-evidence', root/'encrypted-inputs', root/'encrypted-approved19', root/'node'


def bounded_env(keyring=False):
    result = {'PATH':'/usr/bin:/bin','LANG':'C.UTF-8','LC_ALL':'C.UTF-8','PYTHONDONTWRITEBYTECODE':'1'}
    for name in ('HOME','TMPDIR'):
        if name in os.environ: result[name] = os.environ[name]
    if keyring:
        need('M6_INPUTS_KEYRING' in os.environ, 'step-scoped authorized input keyring required')
        result['M6_INPUTS_KEYRING'] = os.environ['M6_INPUTS_KEYRING']
    return result


def command(argv,cwd,out,stdout=None,environment=None,timeout=1200):
    out.mkdir(mode=0o700)
    stdout = out/'stdout.raw' if stdout is None else stdout
    primary, waited, cleanup_errors = None,False,[]
    started = time.monotonic()
    with Path(stdout).open('xb') as output, (out/'stderr.raw').open('xb') as error:
        process = subprocess.Popen(list(map(str,argv)),cwd=cwd,env=bounded_env() if environment is None else environment,
            stdin=subprocess.DEVNULL,stdout=output,stderr=error,start_new_session=True)
        try:
            process.wait(timeout=timeout); waited=True
        except BaseException as failure: primary=failure
        finally:
            try: os.killpg(process.pid,signal.SIGTERM)
            except ProcessLookupError: pass
            except BaseException as failure: cleanup_errors.append({'type':type(failure).__name__,'message':str(failure)})
            if not waited:
                try:
                    process.wait(timeout=5);waited=True
                except subprocess.TimeoutExpired:
                    try: os.killpg(process.pid,signal.SIGKILL)
                    except ProcessLookupError: pass
                    except BaseException as failure: cleanup_errors.append({'type':type(failure).__name__,'message':str(failure)})
                    try: process.wait(timeout=5);waited=True
                    except BaseException as failure: cleanup_errors.append({'type':type(failure).__name__,'message':str(failure)})
                except BaseException as failure: cleanup_errors.append({'type':type(failure).__name__,'message':str(failure)})
    try:
        os.killpg(process.pid,0); gone=False
    except ProcessLookupError: gone=True
    except BaseException as failure:
        gone=False;cleanup_errors.append({'type':type(failure).__name__,'message':str(failure)})
    record={'argv':list(map(str,argv)),'pid':process.pid,'exit_code':process.returncode,
        'wait_completed':waited,'owned_group_gone':gone,'elapsed_seconds':time.monotonic()-started,
        'cleanup_errors':cleanup_errors,
        'stdout_sha256':sha(raw_file(stdout)),'stderr_sha256':sha(raw_file(out/'stderr.raw')),
        'product_acceptance_passed':False}
    try: dump(out/'command.json',record)
    except BaseException as failure:
        if primary is None: raise
        try: primary.__som_data_evidence_error__={'type':type(failure).__name__,'message':str(failure)}
        except BaseException: pass
    if primary is not None: raise primary
    need(waited and gone and not cleanup_errors and process.returncode == 0, 'data transport command failed or lacks cleanup')
    return record


def prepare(source):
    root,evidence,zips,encrypted,node = paths()
    need(not root.exists(), 'new runner data qualification root required')
    root.mkdir(mode=0o700)
    for path in (evidence,zips,encrypted,node): path.mkdir(mode=0o700)
    dump(evidence/'source-admission.json',{'scope':SCOPE,'source_revision':source.revision,
        'declaration_sha256':sha(source.declaration_raw),'files':source.declaration['files'],
        'live_workflow_record':source.workflow_record,'native_qa_source_admission_complete':False,
        'product_acceptance_passed':False})
    pin = parse(source.read(PIN))
    rows=[r for r in pin['tools'] if r['tool']=='node' and r.get('host')=='x86_64-unknown-linux-gnu']
    need(len(rows)==1,'one source-pinned Linux Node required')
    row=rows[0]
    need(row['url']=='https://nodejs.org/dist/v26.5.0/node-v26.5.0-linux-x64.tar.xz'
         and row['archive_sha256']=='9f619528f1db5ddc41dccf54211066fb42228d69a156733c69cb9d6cc92e358c',
         'fixed official data transport Node required')
    with build_opener().open(Request(row['url'],headers={'User-Agent':'som-recorded-data-qualification'}),timeout=60) as response:
        need(response.geturl().startswith('https://nodejs.org/'),'official Node redirect differs')
        archive_raw=response.read(row['archive_bytes']+1)
    need(len(archive_raw)==row['archive_bytes'] and sha(archive_raw)==row['archive_sha256'],'whole official Node archive differs')
    write(node/'original.tar.xz',archive_raw)
    selected={row['executable_member']:row['executable_sha256'],**row['licenses']}
    need(row['executable_member'] not in row['licenses'], 'executable and notice roles overlap')
    for name,pin in selected.items():
        need(not name.startswith('/') and all(p not in {'','.','..'} for p in name.split('/'))
             and re.fullmatch('[0-9a-f]{64}',pin),'clean exact official member pin required')
    seen=set();found=set()
    with tarfile.open(fileobj=io.BytesIO(archive_raw),mode='r:xz') as archive:
        for member in archive:
            need(member.name not in seen,'duplicate official archive member');seen.add(member.name)
            if member.name not in selected: continue
            need(member.isfile() and 0<=member.size<=MAX,'regular official Node member required')
            body=archive.extractfile(member).read()
            need(len(body)==member.size and sha(body)==selected[member.name],'official executable/notice differs')
            target=node/'bin/node' if member.name==row['executable_member'] else node/'notices'/member.name
            write(target,body,0o755 if member.name==row['executable_member'] else 0o644)
            found.add(member.name)
    need(found==set(selected) and len(row['licenses'])==152,'all original official notices required')
    probe=command([node/'bin/node','--version'],node,evidence/'node-version',timeout=30)
    need(raw_file(evidence/'node-version/stdout.raw').decode().strip()=='v'+row['version'],'actual Node version differs')
    dump(evidence/'node-source.json',{'archive_sha256':row['archive_sha256'],'executable_sha256':row['executable_sha256'],
        'notices':row['licenses'],'actual_version_probe':probe,'role':'data transport crypto only',
        'compiler_or_product_portability_qualified':False})
    source.verify()


def provision(source):
    root,evidence,_,encrypted,node=paths()
    wanted=source.declaration['approved19']
    need(wanted=={'commit':'02cd9e79cdcedfef92816f67a3ccdf2e089964ab',
        'index_sha256':'6953812ebe0f388cefe77ec90e8659a8f70d6e8ccf9517f6417e7a3b9b774c28'},
        'actual currently authorized19 input role required')
    config=evidence/'approved19-config.json'
    dump(config,{'version':'som.m6-hosted-inputs.v1',**wanted})
    index_raw=git(source.root,'cat-file','blob',wanted['commit']+':inputs-index.json')
    need(sha(index_raw)==wanted['index_sha256'],'original19 encrypted index differs')
    index=parse(index_raw);need(len(index['packages'])==19,'complete19 authorized inputs required')
    write(encrypted/'inputs-index.json',index_raw)
    for number,package in enumerate(index['packages']):
        need(package['archive']==f'package-{number:03d}.sealed' and package['manifest']==f'package-{number:03d}.manifest.json',
             'fixed authorized input sequence required')
        raw=git(source.root,'cat-file','blob',wanted['commit']+':'+package['manifest'])
        need(sha(raw)==package['manifest_sha256'],'authorized input manifest differs')
        write(encrypted/package['manifest'],raw)
        manifest=parse(raw)
        body=git(source.root,'cat-file','blob',wanted['commit']+':'+package['archive'])
        need(sha(body)==manifest['cipher_sha256'],'authorized input ciphertext differs')
        write(encrypted/package['archive'],body)
    original=source.declaration['original18']
    need(original=={'commit':'1de8f05430a72f8d76f6159786cca71b0ae1a911',
        'index_sha256':'c468ae2086e4d64847b93ad8da08aa3828f30989008db470519cd408e55b67bf'},
        'unchanged original18 metadata role required')
    metadata=root/'original18-metadata';metadata.mkdir(mode=0o700)
    raw=git(source.root,'cat-file','blob',original['commit']+':inputs-index.json')
    need(sha(raw)==original['index_sha256'],'original18 index differs');write(metadata/'inputs-index.json',raw)
    packages=parse(raw)['packages'];need(len(packages)==18,'original18 scope differs')
    for package in packages:
        raw=git(source.root,'cat-file','blob',original['commit']+':'+package['manifest'])
        need(sha(raw)==package['manifest_sha256'],'original18 metadata differs')
        write(metadata/package['manifest'],raw)
    fetch=source.module('fetch')
    transport=source.module('transport')
    node_record=transport.checked_node(source,node/'bin/node')
    # Use the unchanged reviewed decoder, keyring, file checks and publisher.
    # Observe the actual transport children here because fetch.provision's
    # existing quiet CLI deliberately discards their output and first cause.
    packages=fetch.load_manifests(source.root,fetch.load_config(config))
    need(len(packages)==19,'actual complete authorized decoder input scope required')
    keyring=fetch.load_keyring(bounded_env(True),packages)
    private_stage=root/'approved19-private-stage';private_stage.mkdir(mode=0o700)
    restored_files=[]
    for number,package in enumerate(packages):
        entry,manifest=package['entry'],package['manifest']
        need(raw_file(encrypted/entry['manifest'])==package['raw_manifest']
             and sha(raw_file(encrypted/entry['archive']))==manifest['cipher_sha256'],
             'retained actual19 decoder input differs')
        restored=private_stage/f'restored-{number:03d}'
        environment=bounded_env();environment['M6_INPUTS_KEY']=keyring[manifest['cipher_sha256']]
        need(transport.checked_node(source,node/'bin/node')==node_record,'Node changed before actual19 restore')
        command([node/'bin/node','--no-addons',source.root/JS[0],'restore',
            '--archive',encrypted/entry['archive'],'--manifest',encrypted/entry['manifest'],'--out',restored],
            private_stage,evidence/f'approved19-package-{number:03d}',environment=environment,timeout=600)
        need(transport.checked_node(source,node/'bin/node')==node_record,'Node changed during actual19 restore')
        restored_files.extend(fetch.check_restored(restored,manifest))
    receipt={'version':fetch.PROVISION_VERSION,'stage':'input_provision','status':'passed',
        'file_count':len(restored_files),'bytes':sum(record['bytes'] for _,record in restored_files),
        'index_sha256':wanted['index_sha256'],'product_suites':'not_run'}
    fetch.publish(root/'approved19',restored_files,receipt)
    need(receipt['index_sha256']==wanted['index_sha256'],'actual19 restoration receipt differs')
    dump(evidence/'actual-approved19-provision.json',receipt)
    source.verify()


def download(source):
    root,evidence,zips,_,_=paths()
    profile=parse(source.read(PROFILE))
    need(set(profile['artifacts'])=={'original','calibration','fresh'},'three exact original ZIP roles required')
    for role,pin in sorted(profile['artifacts'].items()):
        meta=source.graph.artifact_metadata('faberline/som',pin['id'])
        source.module('stager').artifact_metadata(pin,meta)
        environment=bounded_env();environment['GH_TOKEN']=os.environ['SOM_DEPENDENCY_ACTIONS_TOKEN']
        command(['/usr/bin/gh','api','repos/faberline/som/actions/artifacts/'+str(pin['id'])+'/zip'],
            root,evidence/('download-'+role),stdout=zips/(role+'.zip'),environment=environment,timeout=1200)
        path=zips/(role+'.zip')
        need(path.stat().st_size==pin['size_in_bytes'] and sha(raw_file(path))==pin['digest'].removeprefix('sha256:'),
             'whole downloaded original artifact differs')
        dump(evidence/('original-'+role+'-metadata.json'),meta)
    source.verify()


def qualify(source):
    root,evidence,zips,_,node=paths()
    stage=source.module('stager');transport=source.module('transport')
    stage.stage_inputs(source,source.graph,transport,PROFILE,
        {role:zips/(role+'.zip') for role in ('original','calibration','fresh')},
        root/'original18-metadata',root/'approved19',node/'bin/node',root/'data',evidence/'stage')
    oracle=source.module('oracle')
    table,metadata,proof,verify=oracle.prepare_data(source.read,PROFILE,root/'data')
    need(len(table.entries)==proof['named_slots']==4572 and proof['cohorts']==oracle.COUNTS
         and proof['runtime_comparison']=='unrun' and proof['product_acceptance_passed'] is False
         and proof['original_missing_wire_fields_changed'] is False,'complete data-only factory scope required')
    raw=oracle.json_bytes(table.public())+b'\n'
    need(sha(raw)==proof['table_sha256'],'whole named table differs')
    write(evidence/'table.json',raw)
    dump(evidence/'factory-proof.json',proof)
    verify();source.verify()
    dump(evidence/'qualification.json',{'version':'som.recorded-before-data-qualification.v1',
        'source_revision':source.revision,'source_admission_scope':SCOPE,'data_factory_qualified':True,
        'profile_sha256':sha(source.read(PROFILE)),'inventory_sha256':proof['inventory_sha256'],
        'table_sha256':proof['table_sha256'],'named_slots':4572,'original_calibrated_slots':3875,
        'fresh_before_slots':697,'source_compiler':metadata,
        'native_qa_source_admission_complete':False,'native_Context_or_service_qualified':False,
        'runtime_comparison':'unrun','product_acceptance_passed':False,
        'lossless_original_inputs_retained_as':'separate unchanged-ciphertext artifact recorded-before-data-original-encrypted-inputs; complete three original ZIPs and all19 authorized Git packages',
        'original_inputs_in_new_evidence_envelope':False,
        'original_inputs_are_new_observations':False})


def seal_payload(source):
    root,evidence,_,_,node=paths()
    transport=source.module('transport')
    temporary=physical(os.environ['RUNNER_TEMP'],True)
    work=temporary/'som-recorded-before-data-seal-work'
    out=temporary/'som-recorded-before-data-sealed'
    need(not work.exists() and not out.exists(),'new data evidence sealing roots required')
    work.mkdir(mode=0o700);out.mkdir(mode=0o700)
    archive=work/'raw.tar';record=transport.pack_raw(evidence,archive)
    hosted=work/'hosted';folder=hosted/'run/transport';folder.mkdir(parents=True)
    record.update(version='som.recorded-before-data-evidence-transport.v1',scope=SCOPE,
        source_revision=source.revision,run_id=os.environ['GITHUB_RUN_ID'],run_attempt=os.environ['GITHUB_RUN_ATTEMPT'],
        product_acceptance_passed=False)
    record['chunks']=transport.split_archive(archive,folder/'chunks')
    write(folder/'transport.json',transport.canonical(record)+b'\n')
    command_root=work/'sealer-command';command_root.mkdir(mode=0o700)
    transport.run_m6(source,node/'bin/node',transport.checked_node(source,node/'bin/node'),
        transport.SCRIPTS[0],['--root',hosted,'--out',out/'sealed'],command_root)
    index=sha(raw_file(out/'sealed/evidence-index.json'))
    index_record=parse(raw_file(out/'sealed/evidence-index.json'))
    need(index_record['version']=='som.m6-evidence-index.v1' and index_record['status']=='encrypted'
         and type(index_record['packages']) is list
         and 1<=len(index_record['packages'])<=1000,'sealed data evidence index differs')
    expected={'evidence-index.json'}
    for number,package in enumerate(index_record['packages']):
        need(package['archive']==f'package-{number:03d}.sealed' and package['manifest']==f'package-{number:03d}.manifest.json',
             'sealed data package sequence differs')
        expected.update((package['archive'],package['manifest']))
        manifest_raw=raw_file(out/'sealed'/package['manifest'])
        need(sha(manifest_raw)==package['manifest_sha256']
             and sha(raw_file(out/'sealed'/package['archive']))==package['cipher_sha256'],
             'sealed data evidence cipher/manifest differs')
    need({p.name for p in (out/'sealed').iterdir()}==expected,'sealed data evidence universe differs')
    # Re-open this exact newly sealed envelope. No old process or product
    # authority is inferred from the restored bytes.
    restore_commands=work/'restore-command';restore_commands.mkdir(mode=0o700)
    restored_hosted=work/'restored-hosted'
    transport.run_m6(source,node/'bin/node',transport.checked_node(source,node/'bin/node'),
        transport.SCRIPTS[1],['--root',out/'sealed','--out',restored_hosted,'--index-sha256',index],restore_commands)
    restored_folder=restored_hosted/'run/transport'
    need(raw_file(restored_folder/'transport.json')==raw_file(folder/'transport.json'),
         'restored original data evidence descriptor differs')
    roundtrip_tar=work/'roundtrip.tar'
    transport.rejoin_archive(restored_folder/'chunks',record['chunks'],roundtrip_tar,
        record['archive_bytes'],record['archive_sha256'])
    transport.unpack_raw(roundtrip_tar,record,work/'restored-new-evidence')
    rows,identities=transport.inventory(evidence)
    need(rows==record['inventory'] and identities==record['original_identities'],'new raw evidence changed while sealed')
    summary={'version':'som.recorded-before-data-safe-summary.v1','source_revision':source.revision,
        'run_id':os.environ['GITHUB_RUN_ID'],'run_attempt':os.environ['GITHUB_RUN_ATTEMPT'],
        'scope':SCOPE,'index_sha256':index,'transport_manifest_sha256':sha(raw_file(folder/'transport.json')),
        'new_evidence_inventory_sha256':record['inventory_sha256'],'new_evidence_tar_sha256':record['archive_sha256'],
        'new_evidence_tar_bytes':record['archive_bytes'],'actual_new_evidence_roundtrip_complete':True,
        'native_qa_source_admission_complete':False,
        'native_Context_or_service_qualified':False,'product_acceptance_passed':False}
    result=evidence/'qualification.json'
    summary['data_factory_qualified']=result.is_file() and parse(raw_file(result)).get('data_factory_qualified') is True
    write(out/'receipt.json',canonical(summary)+b'\n')
    if 'GITHUB_OUTPUT' in os.environ:
        with Path(os.environ['GITHUB_OUTPUT']).open('a') as stream: stream.write('index_sha256='+index+'\n')
    source.verify()


def seal(source):
    temporary=physical(os.environ['RUNNER_TEMP'],True)
    work=temporary/'som-recorded-before-data-seal-work'
    out=temporary/'som-recorded-before-data-sealed'
    need(not work.exists() and not work.is_symlink() and not out.exists() and not out.is_symlink(),
         'new owned seal and observation roots required before any writes')
    primary,private_failure_sealed,secondary=None,False,None
    try:
        seal_payload(source)
    except BaseException as failure:
        primary=failure
        # Preserve the first real transport failure in its own new encrypted
        # role. The already captured evidence is never rewritten or claimed
        # to contain observations made after that capture.
        failure_record={'phase':'seal','type':type(failure).__name__,'message':str(failure),
            'traceback':traceback.format_exc(),'actual_new_evidence_roundtrip_complete':False,
            'product_acceptance_passed':False}
        try:
            work.mkdir(mode=0o700,exist_ok=True);out.mkdir(mode=0o700,exist_ok=True)
            hosted=work/'failure-hosted'
            dump(hosted/'run/first-seal-failure.json',failure_record)
            commands=work/'failure-seal-command';commands.mkdir(mode=0o700)
            transport=source.module('transport');_,_,_,_,node=paths()
            transport.run_m6(source,node/'bin/node',transport.checked_node(source,node/'bin/node'),
                transport.SCRIPTS[0],['--root',hosted,'--out',out/'failure-sealed'],commands)
            private_failure_sealed=True
        except BaseException as followup:
            secondary={'type':type(followup).__name__,'message':str(followup)}
            try: primary.__som_data_failure_seal_error__=secondary
            except BaseException: pass
        raise
    finally:
        try:
            out.mkdir(mode=0o700,exist_ok=True)
            commands=out/'transport-commands';commands.mkdir(mode=0o700)
            # The fixed source CLIs print only counts, receipt metadata or a
            # generic refusal. Copy their actual bytes even on a failed wait,
            # seal, restore, chunk rejoin or USTAR member verification.
            for role,original in (('seal',work/'sealer-command'),('restore',work/'restore-command'),
                                  ('failure-seal',work/'failure-seal-command')):
                for name in ('m6.stdout','m6.stderr','command.json'):
                    if (original/name).is_file(): write(commands/(role+'-'+name),raw_file(original/name))
            if primary is not None:
                dump(out/'failure-retention.json',{'version':'som.recorded-before-data-safe-failure.v1',
                    'source_revision':source.revision,'phase':'seal','first_error_type':type(primary).__name__,
                    'actual_new_evidence_roundtrip_complete':False,'private_first_failure_sealed':private_failure_sealed,
                    'secondary_error_type':None if secondary is None else secondary['type'],
                    'product_acceptance_passed':False})
        except BaseException as retention_error:
            if primary is None: raise
            try: primary.__som_data_transport_retention_error__={
                'type':type(retention_error).__name__,'message':str(retention_error)}
            except BaseException: pass


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo',type=Path,required=True);parser.add_argument('--revision',required=True)
    parser.add_argument('--phase',choices=('prepare','provision','download','qualify','seal'),required=True)
    args=parser.parse_args()
    try:
        source=DataSources(args.repo,args.revision)
        {'prepare':prepare,'provision':provision,'download':download,'qualify':qualify,'seal':seal}[args.phase](source)
    except BaseException as primary:
        _,evidence,_,_,_=paths()
        if evidence.is_dir():
            try:
                dump(evidence/('first-failure-'+args.phase+'.json'),{'phase':args.phase,'type':type(primary).__name__,
                    'message':str(primary),'traceback':traceback.format_exc(),'product_acceptance_passed':False})
            except BaseException: pass
        raise
    return 0


if __name__=='__main__':
    try: raise SystemExit(main())
    except Exception:
        sys.stderr.write('recorded-before data qualification refused; retain encrypted evidence\n')
        raise SystemExit(1)

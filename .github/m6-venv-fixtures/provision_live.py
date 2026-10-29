#!/usr/bin/env python3
"""Create a fresh offline React QA venv. This is not product acceptance."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import platform
import re
import signal
import stat
import subprocess
import sys
import tarfile
import time
from types import ModuleType

KEY = '4669d1f312255b4d628224faad1326423741eac16521c3d59677eb994cd2f90b'
ROOT = 'som-react-python-closure'
PRODUCER = 'som-rs/scripts/hosted_readiness/product_python/produce_wheels.py'
PRODUCER_SHA = 'c9151e7b09b0d7fcadcc203f72b83bf1135f2da260becf2034f4228b26299a39'
REQ = 'som-rs/scripts/react_output_acceptance/hosted_python_requirements.txt'
REQ_SHA = '217f0d88231ad9d10cca3232a8707cdbb3355bd9a41e19fbddbab0d299c6db35'
POLICY_SHA = '7a7532ed476f2e983fedfdf4304f8034765efb51df89caac604ad89c953442d7'
INSTALLER_SHA = 'e166303c1376a77d6e92ae19d9ff0eddcdd24c3adbde8381ff835274bb850358'
NATIVE_SOURCE_SHAS = {
    'som-rs/scripts/hosted_readiness/product_runtime/reuse_runtime.py':'2a92e2df47fb05e76677fbb84e9cbb7f12d07d90eb8019bd816b5fe6b5588598',
    'som-rs/scripts/hosted_readiness/product_runtime/qualify_runtime.py':'f3c6f69e922ab0a8f27426a5f597c03f213d40be0fb7fc324ac33f1b48ae66f3',
    'som-rs/scripts/hosted_readiness/product_runtime/runtime-bf3-qualification.json':'4aaca5ef284440fd64bd50723619425201417937b4d635bb3c5d2d5835226b68',
    'som-rs/scripts/hosted_readiness/product_runtime/runtime-bf3-byte-audit.json':'7a2c04a65866474bd773b9722274c09c36646951fa7848d62e4b20b2cb94e75f',
    'som-rs/scripts/hosted_readiness/product_runtime/source-pins.json':'a4208c342bc9e923509bc5ba7ce6a9a1adfe3143d55b6215f0a313b4833550b2'}
TAR_SHA = '8a1ead27a72911acb9af76c622311c5924ad91073b3fc9a221a44db6a0dd4e3b'
AUDIT_SHA = '3fb0075a6795d4f4efcb0845f8ee05dcf16947f7e946954a02a3835677eb8389'
CLOSURE_SHA = 'c6669bc6820eea75d1f0cc596ed4d41a62936a1854026ecf20bee9510d2eb200'
TOP_SHA = '4fcfe69099e75f58c26558ce12dfddf697de8ce9d388d16f66996dc541b738f3'
ORIGINAL_HEAD = '071c55b0f02e2325a6f0993636d5330005b6a4fe'
ZIP_SHA = 'd568ebe48167bec64b4d067f1e2e246b625e16852fc6201bef3689c13c7850bf'
MAX_FILE = 180 * 1024 * 1024
MAX_TOTAL = 2 * 1024**3

class Refusal(ValueError):
    pass

def need(condition, reason):
    if not condition:
        raise Refusal(reason)

def sha(raw):
    return hashlib.sha256(raw).hexdigest()

def file_sha(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()

def regular(path, limit=MAX_FILE):
    need(not path.is_symlink() and path.is_file() and path.stat().st_nlink == 1 and path.stat().st_size <= limit,
         'regular bounded file required')
    need(all(not parent.is_symlink() for parent in path.parents), 'symlink parent refused')
    return path.read_bytes()

def document(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            need(key not in result, 'duplicate JSON key')
            result[key] = value
        return result
    result = json.loads(raw.decode('utf-8','strict'), object_pairs_hook=pairs,
                        parse_constant=lambda _: (_ for _ in ()).throw(Refusal('nonfinite JSON')))
    need(isinstance(result,dict), 'JSON object required')
    return result

def host():
    need(os.environ.get('GITHUB_ACTIONS') == 'true' and os.environ.get('RUNNER_ENVIRONMENT') == 'github-hosted'
         and platform.system() == 'Linux' and platform.machine() == 'x86_64', 'GitHub Linux runner required')
    fields = dict(line.strip().split('=',1) for line in Path('/etc/os-release').read_text().splitlines() if '=' in line)
    need(fields.get('ID') == 'ubuntu' and fields.get('VERSION_ID','').strip('"') == '22.04', 'Ubuntu 22.04 required')

def validate_provenance(audit_path, tar_path):
    raw = regular(audit_path,1024*1024)
    need(sha(raw) == AUDIT_SHA, 'independent audit bytes differ')
    report = document(raw)
    need(report.get('state') == 'partial-evidence-consistent' and report.get('product_acceptance_passed') is False
         and report.get('run_id') == 40821432963 and report.get('artifact_id') == 12874482658
         and report.get('source_commit') == ORIGINAL_HEAD and report.get('artifact_zip_sha256') == ZIP_SHA
         and report.get('artifact_tar_sha256') == TAR_SHA and report.get('requirements_count') == 39
         and report.get('wheel_count') == 40, 'independent audit identity differs')
    need(not tar_path.is_symlink() and tar_path.is_file() and tar_path.stat().st_nlink == 1
         and tar_path.stat().st_size <= MAX_TOTAL and file_sha(tar_path) == TAR_SHA, 'source TAR differs')

def restore(tar_path, out):
    """Restore only the audited closure. Never extract through an archive link."""
    prefix = ROOT+'/closures/'+KEY
    need(not out.exists() and not out.is_symlink(), 'fresh closure output required')
    entries, links, total = {}, [], 0
    with tarfile.open(tar_path, 'r:') as archive:
        for member in archive.getmembers():
            name = member.name.rstrip('/')
            if name != prefix and not name.startswith(prefix+'/'):
                continue
            parts = name.split('/')
            need(all(x not in ('','.','..') for x in parts) and '\\' not in name
                 and not any(ord(c)<32 or ord(c)==127 for c in name), 'unsafe archive path')
            need(name not in entries and (member.isdir() or member.isfile() or member.issym())
                 and 0 <= member.size <= MAX_FILE and not member.mode & 0o7000, 'unsafe archive member')
            total += member.size
            need(total <= MAX_TOTAL and len(entries) < 100000, 'archive bounds exceeded')
            if member.issym():
                match = re.fullmatch(re.escape(prefix)+r'/cache/wheels-v5/(index|pypi)/.+',name)
                target_prefix = '/home/runner/work/_temp/'+prefix+'/cache/archive-v0/'
                target = member.linkname
                need(match is not None and target.startswith(target_prefix), 'archive link refused')
                tail = target[len(target_prefix):].split('/')
                need(all(x not in ('','.','..') for x in tail) and not any('\\' in x for x in tail), 'archive target refused')
                links.append((name,target,match[1]))
            entries[name] = member
        need(sum(1 for _,_,kind in links if kind=='index') == 40
             and sum(1 for _,_,kind in links if kind=='pypi') == 40, 'cache link inventory differs')
        link_names = {name for name,_,_ in links}
        for name, member in entries.items():
            pieces = name.split('/')
            need(not any('/'.join(pieces[:i]) in link_names for i in range(1,len(pieces))), 'archive link parent refused')
        for _, target, _ in links:
            relative = target.removeprefix('/home/runner/work/_temp/')
            need(relative in entries and entries[relative].isdir(), 'cache link target missing')
        out.mkdir(mode=0o700)
        for name, member in sorted(entries.items(), key=lambda pair:(pair[0].count('/'), pair[0])):
            if member.issym():
                continue
            relative = PurePosixPath(name).relative_to(ROOT)
            dest = out.joinpath(*relative.parts)
            dest.parent.mkdir(parents=True,exist_ok=True)
            if member.isdir():
                dest.mkdir(exist_ok=True)
            else:
                stream = archive.extractfile(member)
                need(stream is not None, 'archive file unreadable')
                written = 0
                with dest.open('xb') as sink:
                    for block in iter(lambda: stream.read(1024*1024), b''):
                        written += len(block)
                        need(written <= member.size, 'archive file length differs')
                        sink.write(block)
                need(written == member.size, 'archive file truncated')
                dest.chmod(member.mode & 0o777)
        for name,target,_ in links:
            dest = out.joinpath(*PurePosixPath(name).relative_to(ROOT).parts)
            os.symlink(target,dest)

def interpreter_preconditions():
    need(sys.flags.isolated == 1 and sys.flags.no_site == 1 and sys.flags.dont_write_bytecode == 1
         and sys.flags.no_user_site == 1, 'isolated no-site no-bytecode interpreter required')

def bind_producer(repo, revision):
    need(re.fullmatch(r'[0-9a-f]{40}',revision) is not None
         and os.environ.get('GITHUB_SHA') == revision, 'GitHub source revision required')
    head = subprocess.check_output(['/usr/bin/git','--no-replace-objects','-C',str(repo),
                                    'rev-parse','--verify','HEAD^{commit}'],timeout=30).strip()
    need(head == revision.encode('ascii'), 'source HEAD differs')
    raw = subprocess.check_output(['/usr/bin/git','--no-replace-objects','-C',str(repo),'show',revision+':'+PRODUCER],timeout=30)
    need(sha(raw) == PRODUCER_SHA and regular(repo/PRODUCER,32*1024*1024) == raw, 'producer bytes differ')
    name = 'som_live_reviewed_producer'
    need(name not in sys.modules, 'producer module already loaded')
    module = ModuleType(name)
    module.__file__,module.__cached__,module.__package__ = str(repo/PRODUCER),None,''
    sys.modules[name] = module
    try:
        exec(compile(raw,module.__file__,'exec',dont_inherit=True),module.__dict__)
        return module
    except BaseException:
        if sys.modules.get(name) is module:
            del sys.modules[name]
        raise

def run_child(argv,cwd,env,logs,label,timeout=1200):
    need(re.fullmatch(r'[a-z0-9-]+',label) is not None, 'command label differs')
    stdout,stderr = logs/(label+'.stdout'),logs/(label+'.stderr')
    row = {'argv':[str(x) for x in argv], 'cwd':str(cwd), 'environment':dict(env), 'stdout':str(stdout),
           'stderr':str(stderr), 'pid':None, 'wait_result':None, 'exit_code':None, 'status':'started'}
    def group_exists(pid):
        try: os.killpg(pid,0)
        except ProcessLookupError: return False
        return True
    def stop_group(pid):
        for sig in (signal.SIGTERM,signal.SIGKILL):
            try: os.killpg(pid,sig)
            except ProcessLookupError: return True
            deadline = time.monotonic()+5
            while time.monotonic()<deadline:
                if not group_exists(pid): return True
                time.sleep(0.05)
        return not group_exists(pid)
    start = time.monotonic()
    try:
        with stdout.open('xb') as sink, stderr.open('xb') as errors:
            child = subprocess.Popen(row['argv'],cwd=cwd,env=env,stdout=sink,stderr=errors,start_new_session=True)
            row['pid'] = child.pid
            try:
                row['wait_result'] = child.wait(timeout=timeout)
                row['exit_code'] = child.returncode
                row['status'] = 'completed'
            except subprocess.TimeoutExpired:
                row['status'] = 'timeout'
                try: os.killpg(child.pid,signal.SIGTERM)
                except ProcessLookupError: pass
                try: row['cleanup_wait_result'] = child.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    try: os.killpg(child.pid,signal.SIGKILL)
                    except ProcessLookupError: pass
                    row['cleanup_wait_result'] = child.wait()
                row['exit_code'] = child.returncode
            except BaseException:
                row['status'] = 'interrupted'
                try: os.killpg(child.pid,signal.SIGKILL)
                except ProcessLookupError: pass
                row['cleanup_wait_result'] = child.wait()
                row['exit_code'] = child.returncode
            finally:
                if group_exists(child.pid):
                    row['group_stop_attempted'] = True
                    row['group_stopped'] = stop_group(child.pid)
                    if row['status'] == 'completed': row['status'] = 'descendants-present'
                else:
                    row['group_stopped'] = True
    except OSError:
        row['status'] = 'process-error'
    finally:
        row['elapsed_seconds'] = time.monotonic()-start
        for stream,path in [('stdout',stdout),('stderr',stderr)]:
            if path.is_file():
                row[stream+'_bytes'],row[stream+'_sha256'] = path.stat().st_size,file_sha(path)
    return row

def output(path, value):
    with path.open('x',encoding='utf-8') as stream:
        json.dump(value,stream,sort_keys=True,indent=2,allow_nan=False)
        stream.write('\n')

def provision(repo, revision, runtime, out, tar_path, audit_path):
    host()
    interpreter_preconditions()
    temp = Path(os.environ['RUNNER_TEMP']).resolve(strict=True)
    need(str(temp) == '/home/runner/work/_temp' and out == temp/ROOT
         and runtime == temp/'som-product-runtime-qualification' and repo.is_absolute(), 'fixed runner paths required')
    validate_provenance(audit_path,tar_path)
    producer = bind_producer(repo,revision)
    sources = producer.Sources(repo,revision)
    need(sha(sources.read(PRODUCER)) == PRODUCER_SHA, 'producer source changed')
    requirements = sources.read(REQ)
    need(sha(requirements) == REQ_SHA, 'requirements source changed')
    policy_raw = sources.read(producer.POLICY)
    installer_raw = sources.read(producer.INSTALLER_AUTHORITY)
    need(sha(policy_raw) == POLICY_SHA and sha(installer_raw) == INSTALLER_SHA, 'policy source changed')
    assets = producer.installer_assets(installer_raw)
    for path, expected in NATIVE_SOURCE_SHAS.items():
        need(sha(sources.read(path)) == expected, 'runtime source differs')
    reuse,qualification,runtime_inventory,tools,native = producer.admitted_runtime(runtime,sources,producer.parse(policy_raw),revision)
    need(Path(sys.executable).absolute() == tools['python'], 'source-qualified interpreter required')
    need(all(raw in producer.read(tools['uv'],producer.MAX_WHEEL) for raw in assets.values()), 'uv seed assets differ')
    restore(tar_path,out)
    closure_root = out/'closures'/KEY
    closure_raw = regular(closure_root/'receipt.json')
    need(sha(closure_raw) == CLOSURE_SHA, 'frozen closure receipt differs')
    record = document(closure_raw)
    selected = producer.locked_requirements(producer.read(closure_root/'requirements.selected.lock'))
    offline = producer.locked_requirements(producer.read(closure_root/'requirements.offline.lock'))
    direct = {}
    for line in requirements.decode('utf-8').splitlines():
        if not line or line.startswith('#'): continue
        match = producer.PIN.fullmatch(line)
        need(match is not None, 'direct requirement differs')
        name = producer.normalize(match.group(1))
        need(name not in direct, 'duplicate direct requirement')
        direct[name] = match.group(2)
    need(len(direct) == 39 and len(selected) == 40 and selected == offline
         and all(name in selected and selected[name]['version'] == version for name,version in direct.items()), 'pin coverage differs')
    rows = record.get('wheels',[])
    need(len(rows) == 40 and len({row['name'] for row in rows}) == 40 and set(selected) == {row['name'] for row in rows}, 'wheel inventory differs')
    need(all(row['version'] == selected[row['name']]['version'] and
             selected[row['name']]['hashes'] == [row['sha256']] for row in rows), 'wheel and selected lock differ')
    audits = {row['name']:producer.parse(producer.read(closure_root/'wheel-audits'/row['name']/'inventory.json')) for row in rows}
    closure = {'root':closure_root,'cache':closure_root/'cache','wheels':closure_root/'wheels',
               'pins':selected,'record':record,'audits':audits}
    producer.verify_closure(closure)
    out.joinpath('environments').mkdir()
    named = out/'environments'/'react-api-qa'
    named.mkdir()
    venv = named/'venv'
    need(not venv.exists() and not venv.is_symlink(), 'venv must be fresh')
    for filename,source in [('pyproject.toml',closure_root/'project'/'pyproject.toml'),
                            ('uv.lock',closure_root/'project'/'uv.lock'),('requirements.in',closure_root/'requirements.in')]:
        (named/filename).write_bytes(regular(source))
    for directory in ('home','tmp','logs'):
        (out/directory).mkdir()
    probe = out/'probe-installed-metadata.py'
    probe.write_text(producer.PROBE,encoding='utf-8')
    env = {'PATH':str(tools['python'].parent)+':'+str(tools['uv'].parent)+':/usr/bin:/bin',
           'HOME':str(out/'home'),'TMPDIR':str(out/'tmp'),'LANG':'C.UTF-8','LC_ALL':'C.UTF-8',
           'PYTHONDONTWRITEBYTECODE':'1','PYTHONNOUSERSITE':'1','UV_PYTHON_DOWNLOADS':'never','UV_NO_CONFIG':'1',
           'UV_NO_BUILD':'1','UV_KEYRING_PROVIDER':'disabled','UV_DEFAULT_INDEX':'https://pypi.org/simple',
           'UV_CACHE_DIR':str(closure['cache']),'UV_NO_PROGRESS':'1','UV_NO_INSTALLER_METADATA':'1',
           'UV_COMPILE_BYTECODE':'0','UV_OFFLINE':'1','UV_PROJECT_ENVIRONMENT':str(venv)}
    command = [tools['uv'],'sync','--frozen','--python',tools['python'],'--no-managed-python','--no-config',
               '--no-build','--offline','--no-index','--find-links',closure['wheels'],'--link-mode','copy']
    report = {'version':'som.react-live-venv.v1','state':'incomplete','product_acceptance_passed':False,
              'source_run_id':40821432963,'source_artifact_id':12874482658,'source_zip_sha256':ZIP_SHA,
              'source_tar_sha256':TAR_SHA,'independent_audit_run_id':40823392027,
              'independent_audit_artifact_id':12875218806,'independent_audit_sha256':AUDIT_SHA,
              'source_revision':revision,'closure_receipt_sha256':CLOSURE_SHA,
              'runtime_receipt_sha256':native['reuse_receipt_sha256'],'requirements_sha256':REQ_SHA,
              'requirements_count':39,'wheel_count':40,'venv':str(venv),'commands':[]}
    try:
        row = run_child(command,named,env,out/'logs','fresh-offline-frozen-sync')
        report['commands'].append(row)
        need(row['status']=='completed' and row['exit_code']==0 and row['wait_result']==0 and row.get('group_stopped') is True,
             'offline frozen sync failed or wait unverified')
        inventory = producer.installed_inventory(venv,out)
        python_path = venv/'bin/python'
        python_stat = python_path.stat()
        need(stat.S_ISREG(python_stat.st_mode) and python_stat.st_mode & 0o111
             and os.access(python_path,os.X_OK), 'installed Python is not executable')
        python_mode = stat.S_IMODE(python_stat.st_mode)
        scope = producer.verify_installed_members(venv,inventory,closure,assets,tools['python'],'som-react-api-qa')
        metadata_env = dict(env)
        metadata_env.pop('UV_PROJECT_ENVIRONMENT')
        row = run_child([tools['python'],'-I','-S','-B',probe,venv/'lib/python3.12/site-packages'],named,metadata_env,
                        out/'logs','fresh-offline-no-site-metadata')
        report['commands'].append(row)
        need(row['status']=='completed' and row['exit_code']==0 and row['wait_result']==0 and row.get('group_stopped') is True,
             'metadata command failed or wait unverified')
        observation = producer.parse(regular(Path(row['stdout']),16*1024*1024))
        need(observation.get('python')=='3.12.11' and observation.get('executable')==str(tools['python'])
             and observation.get('site')==str(venv/'lib/python3.12/site-packages')
             and observation.get('site_disabled') is True and observation.get('site_in_sys_path') is False,
             'metadata probe identity differs')
        observed = {}
        for item in observation.get('packages',[]):
            name = producer.normalize(item['name'])
            need(name not in observed and name in audits and item.get('version') == selected[name]['version']
                 and item.get('metadata_sha256') == audits[name]['metadata_sha256'], 'installed metadata differs')
            observed[name] = item['version']
        need(set(observed) == set(selected) and producer.installed_inventory(venv,out) == inventory, 'installed scope changed')
        producer.verify_closure(closure)
        reuse.verify_installed_tree(runtime,runtime_inventory,qualification)
        sources.verify()
        need(regular(named/'requirements.in') == requirements and regular(named/'pyproject.toml') == regular(closure_root/'project'/'pyproject.toml')
             and regular(named/'uv.lock') == regular(closure_root/'project'/'uv.lock'), 'frozen named inputs changed')
        output(named/'live-installed-files.json',inventory)
        output(named/'live-installed-metadata.json',observation)
        report.update(state='fresh-offline-venv-ready-product-tests-unrun',installed_scope=scope,
                      venv_inventory_sha256=file_sha(named/'live-installed-files.json'),
                      installed_metadata_sha256=file_sha(named/'live-installed-metadata.json'),
                      probe_sha256=file_sha(probe),installed_versions=observed,
                      python_executable={'path':str(python_path), 'observed_target_mode':python_mode,
                                         'executable_access':True, 'mode_source':'live stat observation'})
    except BaseException as error:
        report.update(state='failed',failure_type=type(error).__name__)
    output(named/'live-provision-receipt.json',report)
    return report

def main():
    parser = argparse.ArgumentParser()
    for name in ('repo','runtime','out','tar','audit'):
        parser.add_argument('--'+name,type=Path,required=True)
    parser.add_argument('--source-revision',required=True)
    args = parser.parse_args()
    report = provision(args.repo,args.source_revision,args.runtime,args.out,args.tar,args.audit)
    print(json.dumps({'state':report['state'],'product_acceptance_passed':False}))
    return 0 if report['state']=='fresh-offline-venv-ready-product-tests-unrun' else 1

if __name__ == '__main__':
    raise SystemExit(main())

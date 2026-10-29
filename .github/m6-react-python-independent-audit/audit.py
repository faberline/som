#!/usr/bin/env python3
"""Independent React Python QA artifact audit. Product acceptance remains unrun."""
from __future__ import annotations
import argparse
import ast
import hashlib
import itertools
import json
import math
import os
from pathlib import Path
import platform
import posixpath
import re
import stat
import subprocess
import tarfile
import tomllib
from urllib.parse import quote, unquote, urlsplit
import zipfile
import wheel_audit

RUN = 40821432963
ARTIFACT = 12874482658
REVISION = '071c55b0f02e2325a6f0993636d5330005b6a4fe'
ZIP_SHA = 'd568ebe48167bec64b4d067f1e2e246b625e16852fc6201bef3689c13c7850bf'
ZIP_SIZE = 56158381
TAR_SHA = '8a1ead27a72911acb9af76c622311c5924ad91073b3fc9a221a44db6a0dd4e3b'
REQ = 'som-rs/scripts/react_output_acceptance/hosted_python_requirements.txt'
PRODUCER = 'som-rs/scripts/hosted_readiness/product_python/produce_wheels.py'
WRAPPER = '.github/m6-react-python-closure/qualify_react_python.py'
POLICY = 'som-rs/scripts/hosted_readiness/product_python/wheel-policy.json'
AUTH = 'som-rs/scripts/hosted_readiness/product_python/uv099-installer-authority.json'
QUAL = 'som-rs/scripts/hosted_readiness/product_runtime/runtime-bf3-qualification.json'
CONTROL = 'som-rs/scripts/hosted_readiness/product_runtime/runtime-bf3-byte-audit.json'
SOURCE_PINS = 'som-rs/scripts/hosted_readiness/product_runtime/source-pins.json'
PINS = {REQ:'217f0d88231ad9d10cca3232a8707cdbb3355bd9a41e19fbddbab0d299c6db35',
        PRODUCER:'c9151e7b09b0d7fcadcc203f72b83bf1135f2da260becf2034f4228b26299a39',
        WRAPPER:'c83660e629f743a759a8c9511345e9ed1568bd153f667fd2605956e006c26cdb',
        POLICY:'7a7532ed476f2e983fedfdf4304f8034765efb51df89caac604ad89c953442d7',
        AUTH:'e166303c1376a77d6e92ae19d9ff0eddcdd24c3adbde8381ff835274bb850358',
        QUAL:'4aaca5ef284440fd64bd50723619425201417937b4d635bb3c5d2d5835226b68',
        CONTROL:'7a2c04a65866474bd773b9722274c09c36646951fa7848d62e4b20b2cb94e75f',
        SOURCE_PINS:'a4208c342bc9e923509bc5ba7ce6a9a1adfe3143d55b6215f0a313b4833550b2',
        'som-rs/scripts/hosted_readiness/product_runtime/reuse_runtime.py':'2a92e2df47fb05e76677fbb84e9cbb7f12d07d90eb8019bd816b5fe6b5588598',
        'som-rs/scripts/hosted_readiness/product_runtime/qualify_runtime.py':'f3c6f69e922ab0a8f27426a5f597c03f213d40be0fb7fc324ac33f1b48ae66f3'}
QA = 'som-react-python-closure'
RT = 'som-product-runtime-qualification'
TEMP = '/home/runner/work/_temp'
PYTHON = TEMP + '/' + RT + '/install/python/bin/python3.12'
UV = TEMP + '/' + RT + '/install/uv/bin/uv'
PIN = re.compile(r'([A-Za-z0-9][A-Za-z0-9._-]*)==([A-Za-z0-9][A-Za-z0-9.!+_-]*)\Z')
HEX = re.compile(r'[0-9a-f]{64}\Z')
MAX_TAR = 2 * 1024**3
MAX_FILE = 180 * 1024**2
EXPECTED_CACHE_LINKS = 80

class AuditError(ValueError):
    pass

class Missing(AuditError):
    pass

def need(ok, reason):
    if not ok:
        raise AuditError(reason)

def sha(raw):
    return hashlib.sha256(raw).hexdigest()

def stream_sha(stream):
    digest = hashlib.sha256()
    for block in iter(lambda: stream.read(1024 * 1024), b''):
        digest.update(block)
    return digest.hexdigest()

def strict_json(raw):
    def pairs(items):
        out = {}
        for key, value in items:
            need(key not in out, 'duplicate JSON member')
            out[key] = value
        return out
    try:
        return json.loads(raw.decode('utf-8','strict'), object_pairs_hook=pairs,
                          parse_constant=lambda _: (_ for _ in ()).throw(AuditError('nonfinite JSON')))
    except (ValueError, UnicodeError) as exc:
        raise AuditError('invalid JSON') from exc

def clean(name):
    need(isinstance(name,str) and name and not any(c in name for c in ('\\','\x00','\n','\r')), 'archive path invalid')
    name = name.rstrip('/')
    need(not name.startswith('/') and all(x not in ('','.','..') for x in name.split('/')), 'archive traversal refused')
    return name

def unpack(source, destination):
    need(not source.is_symlink() and source.is_file() and source.stat().st_size == ZIP_SIZE, 'artifact size differs')
    with source.open('rb') as stream:
        need(stream_sha(stream) == ZIP_SHA, 'artifact ZIP hash differs')
    need(not destination.exists() and not destination.is_symlink(), 'TAR destination exists')
    name = 'react-python-qa-closure.tar'
    with zipfile.ZipFile(source) as archive:
        items = archive.infolist()
        need(len(items) == 2 and {x.filename for x in items} == {name, name+'.sha256'}, 'artifact ZIP members differ')
        index = {x.filename:x for x in items}
        need(all(not x.is_dir() and not x.flag_bits & 1 for x in items)
             and 0 < index[name].file_size <= MAX_TAR and index[name+'.sha256'].file_size <= 256, 'ZIP member unsafe')
        for item in items:
            mode = item.external_attr >> 16
            need(stat.S_IFMT(mode) in (0,stat.S_IFREG) and not mode & 0o7000, 'ZIP member mode refused')
        side = archive.read(index[name+'.sha256']).decode('ascii','strict')
        match = re.fullmatch(r'([0-9a-f]{64})  [^\n]*react-python-qa-closure\.tar\n', side)
        need(match is not None and match.group(1) == TAR_SHA, 'TAR sidecar differs')
        digest = hashlib.sha256()
        with archive.open(index[name]) as source_stream, destination.open('xb') as target:
            for block in iter(lambda: source_stream.read(1024*1024), b''):
                digest.update(block)
                target.write(block)
        need(digest.hexdigest() == match.group(1), 'TAR bytes differ')
    return digest.hexdigest()

def index_tar(tar):
    entries, total, absolute_cache_links = {}, 0, {}
    for member in tar.getmembers():
        name = clean(member.name)
        need(name not in entries and (name in (QA,RT) or name.startswith(QA+'/') or name.startswith(RT+'/')), 'TAR path differs')
        need(member.isfile() or member.isdir() or member.issym() or member.islnk(), 'TAR special member refused')
        need(0 <= member.size <= MAX_FILE and not (member.mode & 0o7000), 'TAR size or mode refused')
        total += member.size
        need(total <= MAX_TAR and len(entries) < 300000, 'TAR inventory exceeds bound')
        if member.issym():
            target = member.linkname
            need(target and not any(c in target for c in ('\\','\x00','\n','\r')), 'TAR symlink refused')
            if target.startswith('/'):
                match = re.fullmatch(QA + r'/closures/([0-9a-f]{64})/cache/wheels-v5/index/.+', name)
                need(match is not None, 'absolute TAR link outside pinned cache index')
                prefix = TEMP+'/'+QA+'/closures/'+match[1]+'/cache/archive-v0/'
                need(target.startswith(prefix), 'absolute TAR link leaves pinned cache')
                dest = clean(target.removeprefix(TEMP+'/'))
                absolute_cache_links[name] = dest
            else:
                dest = posixpath.normpath(posixpath.join(posixpath.dirname(name), target))
                root = name.split('/',1)[0]
                need(dest == root or dest.startswith(root+'/'), 'TAR symlink escapes root')
        if member.islnk():
            target = clean(member.linkname)
            need(target.split('/',1)[0] == name.split('/',1)[0], 'TAR hardlink escapes root')
        entries[name] = member
    links = {name for name,item in entries.items() if item.issym() or item.islnk()}
    for name,item in entries.items():
        parts = name.split('/')
        need(not any('/'.join(parts[:i]) in links for i in range(1,len(parts))), 'TAR link is parent')
        if item.islnk():
            target = clean(item.linkname)
            need(target in entries and entries[target].isfile(), 'TAR hardlink target differs')
    for target in absolute_cache_links.values():
        need(target in entries and entries[target].isdir(), 'absolute cache link target missing or not a directory')
    need(len(absolute_cache_links) == EXPECTED_CACHE_LINKS
         and sum(member.issym() for member in entries.values()) == EXPECTED_CACHE_LINKS,
         'fixed absolute cache symlink inventory differs')
    return entries

def data(tar, entries, name, limit=MAX_FILE):
    name = clean(name)
    if name not in entries:
        raise Missing('missing retained file: ' + name)
    member = entries[name]
    if member.islnk():
        member = entries[clean(member.linkname)]
    need(member.isfile() and member.size <= limit, 'retained file type or size differs')
    stream = tar.extractfile(member)
    need(stream is not None, 'retained file unreadable')
    raw = stream.read(limit+1)
    need(len(raw) == member.size, 'retained file truncated')
    return raw

def document(tar, entries, name):
    value = strict_json(data(tar, entries, name, 32*1024*1024))
    need(isinstance(value,dict), 'retained object required')
    return value

def sources(repo):
    need(repo.is_absolute() and repo.is_dir() and not repo.is_symlink(), 'repo path refused')
    result = {}
    for path, expected in PINS.items():
        raw = subprocess.check_output(['/usr/bin/git','--no-replace-objects','-C',str(repo),'show',REVISION+':'+path],
                                      stderr=subprocess.PIPE,timeout=30)
        need(sha(raw) == expected, 'fixed source digest differs: ' + path)
        result[path] = raw
    producer = result[PRODUCER]
    need(b'child.wait(timeout=1200)' in producer and b'owned_parent_stopped=child.poll() is not None' in producer
         and b'owned_group_stopped=stop_owned_descendants(child.pid)' in producer
         and b'require(record["exit_code"] == 0 and record["owned_group_stopped"]' in producer, 'source wait flow differs')
    return result

def normalized(name):
    need(isinstance(name,str) and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]*',name), 'package name invalid')
    return re.sub(r'[-_.]+','-',name).lower()

def requirements(raw):
    need(sha(raw) == PINS[REQ], 'requirements digest differs')
    result = {}
    for line in raw.decode('utf-8','strict').splitlines():
        if not line or line.startswith('#'):
            continue
        match = PIN.fullmatch(line)
        need(match is not None, 'requirements pin differs')
        name = normalized(match[1])
        need(name not in result, 'requirements name repeated')
        result[name] = match[2]
    need(len(result) == 39, '39 direct requirements required')
    return result

def lock_requirements(raw):
    result = {}
    for line in raw.decode('utf-8','strict').replace('\\\n',' ').splitlines():
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        words = line.split()
        match = PIN.fullmatch(words[0])
        need(match is not None and len(words)>1, 'locked requirement differs')
        name = normalized(match[1])
        need(name not in result, 'locked requirement repeated')
        hashes = []
        for word in words[1:]:
            need(word.startswith('--hash=sha256:') and HEX.fullmatch(word[14:]), 'locked hash differs')
            hashes.append(word[14:])
        need(len(set(hashes)) == len(hashes), 'lock hash repeated')
        result[name] = {'version':match[2], 'hashes':hashes}
    need(result, 'locked requirement set empty')
    return result

def runtime_identity(tar, entries, report, source):
    raw = data(tar, entries, RT+'/receipt.json', 32*1024*1024)
    receipt, native = strict_json(raw), report.get('native_runtime', {})
    qualification, policy = strict_json(source[QUAL]), strict_json(source[POLICY])
    control = strict_json(source[CONTROL])
    need(isinstance(receipt,dict) and isinstance(native,dict), 'runtime receipt missing')
    runner = receipt.get('runner',{})
    need(runner.get('RUNNER_OS') == 'Linux' and runner.get('RUNNER_ARCH') == 'X64'
         and runner.get('ImageOS') == 'ubuntu22' and runner.get('GITHUB_RUN_ID') == str(RUN)
         and isinstance(runner.get('ImageVersion'),str) and runner.get('ImageVersion')
         and isinstance(runner.get('GITHUB_RUN_ATTEMPT'),str) and runner.get('GITHUB_RUN_ATTEMPT').isdigit(),
         'runtime runner identity differs')
    need(receipt.get('version') == 'som.product-runtime-reuse.v1' and receipt.get('tested_sha') == REVISION
         and receipt.get('status') == 'audited_runtime_reinstalled_and_rechecked_for_hosted_tests'
         and receipt.get('product_runtime_ready') is False and receipt.get('product_acceptance_passed') is False,
         'runtime receipt scope differs')
    need(native.get('reuse_receipt_sha256') == sha(raw) and native.get('qualification_sha256') == PINS[QUAL]
         and native.get('inventory_sha256') == qualification['transport_files']['installed-files.json']
         and native.get('python_sha256') == policy['native_sha256']['python']
         and native.get('uv_sha256') == policy['native_sha256']['uv'], 'runtime source binding differs')
    origin_raw = data(tar,entries,RT+'/artifact-origin.json',1024*1024)
    origin = strict_json(origin_raw)
    need(receipt.get('origin',{}).get('metadata_sha256') == sha(origin_raw)
         and origin.get('id') == qualification['artifact_id'] and origin.get('name') == qualification['artifact_name']
         and origin.get('digest') == 'sha256:'+qualification['artifact_sha256'] and origin.get('expired') is False
         and origin.get('workflow_run',{}).get('id') == qualification['run_id']
         and origin['workflow_run'].get('head_sha') == qualification['source_revision'], 'runtime artifact origin differs')
    for name, expected in qualification['transport_files'].items():
        need(sha(data(tar,entries,RT+'/qualification-evidence/'+name,32*1024*1024)) == expected,
             'runtime transport evidence differs: '+name)
    pins = strict_json(source[SOURCE_PINS])
    license_paths = {}
    for archive in pins['archives']:
        for name, expected in archive['licenses'].items():
            path = RT+'/licenses/'+archive['name']+'/'+name
            need(path not in license_paths and sha(data(tar,entries,path,32*1024*1024)) == expected,
                 'runtime source license differs')
            license_paths[path] = expected
    observed_licenses = {name for name, item in entries.items()
                         if name.startswith(RT+'/licenses/') and (item.isfile() or item.islnk())}
    need(len(license_paths) == qualification['counts']['license_files'] == 13
         and observed_licenses == set(license_paths), 'runtime license inventory differs')
    inventory_raw = data(tar,entries,RT+'/qualification-evidence/installed-files.json',32*1024*1024)
    need(sha(inventory_raw) == native['inventory_sha256'] == control['installed_inventory_sha256'], 'runtime inventory source differs')
    inventory, admitted = strict_json(inventory_raw), receipt.get('admitted_executables',{})
    need(isinstance(inventory,dict) and isinstance(admitted,dict) and set(admitted) == set(qualification['admitted_paths'])
         and len(admitted) == qualification['counts']['admitted_executables'] == 41, 'runtime executable inventory differs')
    runtime_commands = receipt.get('commands')
    need(isinstance(runtime_commands,list) and len(runtime_commands) == 59, 'runtime command inventory differs')
    bindings = receipt.get('source_bindings',{})
    paths = ['som-rs/scripts/hosted_readiness/product_runtime/reuse_runtime.py',
             'som-rs/scripts/hosted_readiness/product_runtime/qualify_runtime.py', QUAL, CONTROL, SOURCE_PINS]
    need(bindings.get('revision') == REVISION and isinstance(bindings.get('files'),dict)
         and set(bindings['files']) == set(paths), 'runtime source binding inventory differs')
    git = ['/usr/bin/git','--no-pager','--no-replace-objects','-C','/home/runner/work/som/som']
    def runtime_log(index,label,argv,expected_stdout,expected_stderr=None):
        need(index < len(runtime_commands), 'runtime command missing')
        command = runtime_commands[index]
        need(command.get('argv') == argv and command.get('cwd') == TEMP+'/'+RT
             and type(command.get('pid')) is int and command['pid'] > 1
             and type(command.get('exit_code')) is int and command['exit_code'] == 0
             and 'interrupted' not in command, 'runtime command differs')
        for stream, expected in [('stdout',expected_stdout),('stderr',expected_stderr)]:
            relative = 'logs/%03d-%s.%s' % (index,label,stream)
            need(command.get(stream) == TEMP+'/'+RT+'/'+relative, 'runtime command log path differs')
            raw_log = data(tar,entries,RT+'/'+relative,32*1024*1024)
            need(sha(raw_log) == command.get(stream+'_sha256') and (expected is None or raw_log == expected),
                 'runtime command log differs')
    runtime_log(0,'source-head',git+['rev-parse','--verify','HEAD^{commit}'],(REVISION+'\n').encode())
    runtime_log(1,'source-root',git+['rev-parse','--show-toplevel'],b'/home/runner/work/som/som\n')
    for number, path in enumerate(paths):
        blob = source[path]
        oid = hashlib.sha1(b'blob '+str(len(blob)).encode()+b'\0'+blob).hexdigest()
        row = bindings['files'][path]
        need(row.get('git_blob_oid') == oid and row.get('sha256') == PINS[path]
             and row.get('working_sha256') == PINS[path] and row.get('working_bytes_match') is True,
             'runtime source binding differs')
        for offset, label in enumerate(('tree','size','blob')):
            index = 2+3*number+offset
            need(index < len(runtime_commands), 'runtime source command missing')
            command = runtime_commands[index]
            argv = {'tree':git+['ls-tree','-z',REVISION,'--',path],
                    'size':git+['cat-file','-s',oid], 'blob':git+['cat-file','blob',oid]}[label]
            need(type(command.get('pid')) is int and command['pid'] > 1
                 and type(command.get('exit_code')) is int and command['exit_code'] == 0
                 and command.get('argv') == argv and command.get('cwd') == TEMP+'/'+RT
                 and 'interrupted' not in command, 'runtime source command completion differs')
            for stream in ('stdout','stderr'):
                relative = 'logs/%03d-%s-%d.%s' % (index,label,number,stream)
                need(command.get(stream) == TEMP+'/'+RT+'/'+relative, 'runtime source log path differs')
                raw_log = data(tar,entries,RT+'/'+relative,32*1024*1024)
                need(sha(raw_log) == command.get(stream+'_sha256'), 'runtime source log hash differs')
                if stream == 'stdout':
                    if label == 'tree':
                        need(raw_log in (b'100644 blob '+oid.encode()+b'\t'+path.encode()+b'\0',
                                         b'100755 blob '+oid.encode()+b'\t'+path.encode()+b'\0'),
                             'runtime source tree differs')
                    elif label == 'size':
                        need(raw_log.strip() == str(len(blob)).encode(), 'runtime source size differs')
                    else:
                        need(raw_log == blob, 'runtime source blob bytes differ')
    indexes = set()
    for number, name in enumerate(qualification['admitted_paths']):
        row = admitted[name]
        need(row.get('sha256') == inventory[name].get('sha256') and row.get('status') == 'hash_and_version_rechecked'
             and type(row.get('version_exit_code')) is int and row['version_exit_code'] == 0, 'runtime executable receipt differs')
        index = row.get('version_command_index')
        need(type(index) is int and index == 17+number and index not in indexes,
             'runtime version command index differs')
        indexes.add(index)
        command = runtime_commands[index]
        path = TEMP+'/'+RT+'/'+name
        component = name.split('/')[1]
        argv = [path, '-B', '--version'] if component == 'python' else [path, '--version']
        need(row.get('path') == path and row.get('component') == component and command.get('argv') == argv
             and command.get('cwd') == TEMP+'/'+RT and type(command.get('pid')) is int and command['pid'] > 1
             and type(command.get('exit_code')) is int and command['exit_code'] == 0
             and 'interrupted' not in command, 'runtime version command differs')
        label = '%03d-recheck-%s-%s' % (index, component, name.rsplit('/',1)[-1])
        for stream in ('stdout','stderr'):
            relative = 'logs/'+label+'.'+stream
            need(command.get(stream) == TEMP+'/'+RT+'/'+relative, 'runtime log path differs')
            raw_log = data(tar,entries,RT+'/'+relative,1024*1024)
            need(sha(raw_log) == command.get(stream+'_sha256'), 'runtime log hash differs')
            if stream == 'stdout':
                need(raw_log.decode('utf-8','replace') == row.get('version_stdout'), 'runtime version output differs')
    need(admitted['install/python/bin/python3.12']['sha256'] == native['python_sha256']
         and admitted['install/uv/bin/uv']['sha256'] == native['uv_sha256'], 'runtime interpreter identity differs')
    module = receipt.get('python_modules',{})
    original_receipt = document(tar,entries,RT+'/qualification-evidence/receipt.json')
    module_probe = "import ssl,zlib,bz2,lzma,sqlite3,ctypes,venv,ensurepip,json; print(json.dumps({'openssl':ssl.OPENSSL_VERSION,'zlib':zlib.ZLIB_RUNTIME_VERSION,'sqlite':sqlite3.sqlite_version}))"
    need(module.get('command_index') == 58 and module.get('exit_code') == 0
         and isinstance(module.get('stdout'),str) and module['stdout'] == original_receipt.get('python_modules'),
         'runtime module receipt differs')
    runtime_log(58,'recheck-python-core-modules',[TEMP+'/'+RT+'/install/python/bin/python3.12','-I','-B','-c',module_probe],
                module['stdout'].encode(), b'')
    return native

def expected_env(cache, venv, offline):
    result = {'PATH':TEMP+'/'+RT+'/install/python/bin:'+TEMP+'/'+RT+'/install/uv/bin:/usr/bin:/bin',
              'HOME':TEMP+'/'+QA+'/home','TMPDIR':TEMP+'/'+QA+'/tmp','LANG':'C.UTF-8','LC_ALL':'C.UTF-8',
              'PYTHONDONTWRITEBYTECODE':'1','PYTHONNOUSERSITE':'1','UV_PYTHON_DOWNLOADS':'never',
              'UV_NO_CONFIG':'1','UV_NO_BUILD':'1','UV_KEYRING_PROVIDER':'disabled','UV_DEFAULT_INDEX':'https://pypi.org/simple',
              'UV_CACHE_DIR':cache,'UV_NO_PROGRESS':'1','UV_NO_INSTALLER_METADATA':'1','UV_COMPILE_BYTECODE':'0'}
    if venv is not None:
        result['UV_PROJECT_ENVIRONMENT'] = venv
    if offline:
        result['UV_OFFLINE'] = '1'
    return result

def commands(tar, entries, report, key):
    out = TEMP+'/'+QA
    base = out+'/closures/'+key
    project, cache, wheels = base+'/project', base+'/cache', base+'/wheels'
    envroot = out+'/environments/react-api-qa'
    namedvenv, seedvenv = envroot+'/venv', base+'/cache-preparation/venv'
    common = ['--python',PYTHON,'--no-managed-python','--no-config','--no-build']
    target = ['--python',PYTHON,'--python-version','3.12.11','--python-platform','x86_64-manylinux_2_35',
              '--no-managed-python','--no-config','--only-binary',':all:','--generate-hashes','--no-header','--no-annotate','--format','requirements.txt']
    label = 'closure-'+key[:20]
    specs = [
        ([UV,'lock',*common,'--default-index','https://pypi.org/simple'],project,None,False,label+'-universal-lock'),
        ([UV,'export','--frozen','--offline','--no-config','--no-emit-project','--format','requirements.txt','--output-file',base+'/requirements.universal.lock'],project,None,True,label+'-frozen-export'),
        ([UV,'pip','compile',base+'/requirements.universal.lock',*target,'--default-index','https://pypi.org/simple','--output-file',base+'/requirements.target.lock'],project,None,False,label+'-target-lock'),
        ([UV,'pip','compile',base+'/requirements.in','--constraint',base+'/requirements.selected.lock',*target,'--offline','--no-index','--find-links',wheels,'--output-file',base+'/requirements.offline.lock'],project,None,True,label+'-offline-original-closure'),
        ([UV,'sync','--frozen',*common,'--no-index','--find-links',wheels,'--link-mode','copy'],project,seedvenv,False,'cache-'+key+'-frozen-url-cache'),
        ([UV,'lock',*common,'--offline'],envroot,namedvenv,True,'react-api-qa-offline-original-lock'),
        ([UV,'sync','--frozen',*common,'--offline','--no-index','--find-links',wheels,'--link-mode','copy'],envroot,namedvenv,True,'react-api-qa-offline-frozen-sync'),
        ([PYTHON,'-I','-S','-B',out+'/probe-installed-metadata.py',namedvenv+'/lib/python3.12/site-packages'],envroot,None,True,'react-api-qa-offline-no-site-metadata')]
    rows = report.get('commands')
    need(isinstance(rows,list) and len(rows)==len(specs), 'command inventory differs')
    logs = {}
    for row,(argv,cwd,venv,offline,label) in zip(rows,specs):
        need(isinstance(row,dict) and row.get('argv')==argv and row.get('cwd')==cwd
             and row.get('environment')==expected_env(cache,venv,offline), 'command argv/cwd/environment differs')
        need(type(row.get('exit_code')) is int and row['exit_code']==0 and type(row.get('owned_pid')) is int
             and row['owned_pid']>1 and row.get('owned_parent_stopped') is True and row.get('owned_group_stopped') is True,
             'command completion or process receipt differs')
        elapsed = row.get('elapsed_seconds')
        need(type(elapsed) in (int,float) and math.isfinite(elapsed) and elapsed>=0
             and isinstance(row.get('started_at'),str) and isinstance(row.get('finished_at'),str), 'command timing missing')
        for stream in ('stdout','stderr'):
            path = 'logs/'+label+'.'+stream
            need(row.get(stream)==path, 'command raw path differs')
            raw = data(tar,entries,QA+'/'+path,32*1024*1024)
            need(type(row.get(stream+'_bytes')) is int and row[stream+'_bytes']==len(raw)
                 and row.get(stream+'_sha256')==sha(raw), 'command raw output differs')
            logs[(label,stream)] = raw
    return rows, logs

def safe_url(url, host):
    need(isinstance(url,str), 'publisher URL missing')
    parsed = urlsplit(url)
    need(parsed.scheme=='https' and parsed.netloc==host and parsed.path.startswith('/')
         and not parsed.query and not parsed.fragment, 'publisher URL differs')
    return url

def supported_tag(tags):
    for triple in tags:
        py, abi, host = triple.split('-')
        host_ok = (host in ('any','linux_x86_64','manylinux1_x86_64','manylinux2010_x86_64','manylinux2014_x86_64')
                   or (re.fullmatch(r'manylinux_2_([0-9]+)_x86_64',host) is not None
                       and 5 <= int(host.split('_')[2]) <= 35))
        python_ok = ((py=='cp312' and abi=='cp312' and host!='any')
                     or (py.startswith('cp3') and py[3:].isdigit() and 2<=int(py[3:])<=12 and abi=='abi3' and host!='any')
                     or (py=='cp312' and abi=='none') or (py in ('py3','py312') and abi=='none'))
        if host_ok and python_ok:
            return True
    return False

def wheel_rank(filename):
    if not isinstance(filename,str) or not re.fullmatch(r'[A-Za-z0-9_.+]+(?:-[A-Za-z0-9_.+]+){4,5}\.whl',filename):
        return None
    parts = filename[:-4].split('-')
    if len(parts) not in (5,6):
        return None
    candidates = []
    for py, abi, host in itertools.product(*(part.split('.') for part in parts[-3:])):
        if host=='any':
            host_rank = 100
        elif host=='linux_x86_64':
            host_rank = 90
        elif host in ('manylinux1_x86_64','manylinux2010_x86_64','manylinux2014_x86_64'):
            host_rank = 35-{'manylinux1_x86_64':5,'manylinux2010_x86_64':12,'manylinux2014_x86_64':17}[host]
        else:
            match = re.fullmatch(r'manylinux_2_([0-9]+)_x86_64',host)
            if match is None or not 5<=int(match[1])<=35:
                continue
            host_rank = 35-int(match[1])
        if py=='cp312' and abi=='cp312' and host!='any':
            py_rank = 0
        elif py.startswith('cp3') and py[3:].isdigit() and 2<=int(py[3:])<=12 and abi=='abi3' and host!='any':
            py_rank = 13-int(py[3:])
        elif py=='cp312' and abi=='none':
            py_rank = 20
        elif py in ('py3','py312') and abi=='none':
            py_rank = 30 if py=='py312' else 31
        else:
            continue
        candidates.append((py_rank,host_rank))
    return min(candidates) if candidates else None

def audit_closure(tar, entries, report, direct, source):
    closures, environments = report.get('closures'), report.get('environments')
    need(isinstance(closures,list) and len(closures)==1 and isinstance(environments,list) and len(environments)==1,
         'one closure and QA environment required')
    closure, named = closures[0], environments[0]
    key = closure.get('id')
    need(isinstance(key,str) and HEX.fullmatch(key) and closure.get('state')=='wheel-closure-qualified'
         and named.get('id')=='react-api-qa' and named.get('dependency_set')==key, 'closure identity differs')
    native = runtime_identity(tar,entries,report,source)
    formula = {'requirements_in_sha256':PINS[REQ], 'python_sha256':native['python_sha256'],
               'uv_sha256':native['uv_sha256'], 'installed_inventory_sha256':native['inventory_sha256'],
               'python':'3.12.11','host':'x86_64-manylinux_2_35'}
    need(sha(json.dumps(formula,sort_keys=True,separators=(',',':')).encode()) == key, 'dependency-set key differs')
    base, namedroot = QA+'/closures/'+key, QA+'/environments/react-api-qa'
    expected = {'requirements.in':'requirements_in_sha256','project/pyproject.toml':'pyproject_sha256',
                'project/uv.lock':'uv_lock_sha256','requirements.universal.lock':'universal_requirements_sha256',
                'requirements.target.lock':'target_requirements_sha256','requirements.selected.lock':'requirements_selected_sha256',
                'requirements.offline.lock':'requirements_offline_sha256'}
    raw = {name:data(tar,entries,base+'/'+name,32*1024*1024) for name in expected}
    for name, field in expected.items():
        need(sha(raw[name])==closure.get(field), 'closure file hash differs: '+name)
    need(raw['requirements.in']==source[REQ] and data(tar,entries,namedroot+'/requirements.in')==source[REQ],
         'requirements bytes differ')
    lines = [line for line in source[REQ].decode().splitlines() if line and not line.startswith('#')]
    project = ('[project]\nname = "som-react-api-qa"\nversion = "0.0.0"\n'
               'requires-python = ">=3.12,<3.13"\ndependencies = ' + json.dumps(lines)
               + '\n[dependency-groups]\ndev = []\n[tool.uv]\npackage = false\n').encode()
    need(raw['project/pyproject.toml']==project and data(tar,entries,namedroot+'/pyproject.toml')==project,
         'QA project source differs')
    universal = tomllib.loads(raw['project/uv.lock'].decode('utf-8','strict'))
    need(type(universal.get('version')) is int and universal['version']==1
         and universal.get('requires-python','').replace(' ','') in ('>=3.12,<3.13','==3.12.*'), 'universal lock differs')
    universal_map = {}
    for item in universal.get('package',[]):
        if item.get('source')=={'virtual':'.'}:
            continue
        need(item.get('source')=={'registry':'https://pypi.org/simple'}, 'universal package source differs')
        pair = (normalized(item['name']),item['version'])
        need(pair not in universal_map, 'universal package duplicate')
        universal_map[pair] = {x['hash'][7:] for x in item.get('wheels',[])
                               if isinstance(x.get('hash'),str) and x['hash'].startswith('sha256:')}
    target = lock_requirements(raw['requirements.target.lock'])
    selected = lock_requirements(raw['requirements.selected.lock'])
    offline = lock_requirements(raw['requirements.offline.lock'])
    need(selected==offline and len(selected)==40 and set(direct)<=set(selected), 'selected/offline/direct coverage differs')
    for name, version in direct.items():
        need(selected[name]['version']==version, 'direct version differs')
    for name, pin in selected.items():
        need(name in target and target[name]['version']==pin['version'] and len(pin['hashes'])==1
             and pin['hashes'][0] in target[name]['hashes']
             and pin['hashes'][0] in universal_map.get((name,pin['version']),set()), 'wheel not bound by both locks')
    need(document(tar,entries,base+'/receipt.json')==closure, 'closure durable receipt differs')
    wheel_rows = closure.get('wheels')
    need(isinstance(wheel_rows,list) and len(wheel_rows)==len(selected), 'wheel inventory differs')
    names = [row.get('name') for row in wheel_rows]
    need(len(set(names))==len(names) and set(names)==set(selected), 'wheel name coverage differs')
    metadata_hashes, missing_notices, signatures = {}, [], []
    for row in wheel_rows:
        name, pin = row['name'], selected[row['name']]
        filename, digest = row.get('filename'), pin['hashes'][0]
        need(row.get('version')==pin['version'] and row.get('sha256')==digest and isinstance(filename,str)
             and re.fullmatch(r'[A-Za-z0-9_.+]+(?:-[A-Za-z0-9_.+]+){4,5}\.whl',filename), 'wheel row differs')
        raw_wheel = data(tar,entries,base+'/wheels/'+filename,150*1024*1024)
        need(sha(raw_wheel)==digest, 'wheel lock hash differs')
        metadata_raw = data(tar,entries,base+'/publisher-metadata/'+name+'.json',16*1024*1024)
        need(sha(metadata_raw)==row.get('publisher_metadata_sha256'), 'publisher metadata hash differs')
        metadata = strict_json(metadata_raw)
        need(normalized(metadata.get('info',{}).get('name'))==name and metadata['info'].get('version')==pin['version'],
             'publisher package identity differs')
        hits = [x for x in metadata.get('urls',[]) if x.get('filename')==filename
                and x.get('digests',{}).get('sha256')==digest]
        need(len(hits)==1 and hits[0].get('packagetype')=='bdist_wheel' and not hits[0].get('yanked')
             and type(hits[0].get('size')) is int and hits[0]['size']==len(raw_wheel), 'publisher wheel binding differs')
        url = safe_url(hits[0].get('url'),'files.pythonhosted.org')
        need(unquote(urlsplit(url).path.rsplit('/',1)[-1])==filename, 'publisher URL filename differs')
        eligible = []
        for item in metadata.get('urls',[]):
            rank = wheel_rank(item.get('filename'))
            if item.get('packagetype')!='bdist_wheel' or item.get('yanked') or rank is None:
                continue
            if item.get('digests',{}).get('sha256') not in target[name]['hashes']:
                continue
            candidate_url = safe_url(item.get('url'),'files.pythonhosted.org')
            need(type(item.get('size')) is int and 0<item['size']<=150*1024*1024
                 and unquote(urlsplit(candidate_url).path.rsplit('/',1)[-1])==item['filename'],
                 'publisher candidate metadata differs')
            eligible.append((rank,item['filename']))
        need(eligible and min(eligible)[1]==filename, 'publisher deterministic wheel selection differs')
        inv_raw = data(tar,entries,base+'/wheel-audits/'+name+'/inventory.json',32*1024*1024)
        need(sha(inv_raw)==row.get('audit_sha256')
             and row.get('audit')=='closures/'+key+'/wheel-audits/'+name+'/inventory.json', 'wheel inventory binding differs')
        inventory = strict_json(inv_raw)
        expected_meta_url = 'https://pypi.org/pypi/'+quote(name,safe='')+'/'+quote(pin['version'],safe='')+'/json'
        need(inventory.get('publisher_metadata')=={'url':expected_meta_url,'sha256':sha(metadata_raw),'size':len(metadata_raw)}
             and inventory.get('download')=={'url':url,'sha256':digest,'size':len(raw_wheel)}, 'publisher provenance receipt differs')
        result = wheel_audit.audit_wheel(raw_wheel,filename,name,pin['version'],inventory)
        identity = result['identity']
        need(supported_tag(result['wheel_tags']), 'wheel has no reviewed compatible host tag')
        need(identity['metadata_sha256']==row.get('metadata_sha256') and identity['record_sha256']==row.get('record_sha256'),
             'wheel summary differs')
        for kind, content in result['raw_evidence'].items():
            need(data(tar,entries,base+'/wheel-audits/'+name+'/'+kind,32*1024*1024)==content,
                 'retained wheel metadata or notice differs')
        metadata_hashes[name] = {'sha256':identity['metadata_sha256'],'version':pin['version']}
        if not identity['license_notices']:
            missing_notices.append(name)
        if result['signatures_unverified']:
            signatures.append(name)
    prefix = base+'/wheels/'
    actual = {name[len(prefix):] for name,m in entries.items() if name.startswith(prefix) and (m.isfile() or m.islnk())}
    need(actual=={row['filename'] for row in wheel_rows}, 'wheelhouse extras or missing entries')
    return key, base, namedroot, closure, named, metadata_hashes, missing_notices, signatures

def audit_receipts(tar, entries, report, key, base, namedroot, closure, named, metadata_hashes):
    rows, logs = commands(tar,entries,report,key)
    seed = document(tar,entries,base+'/cache-preparation/receipt.json')
    checkpoint = document(tar,entries,base+'/cache-preparation/before-cleanup.json')
    need(report.get('frozen_url_cache')==seed and seed.get('commands')==[rows[4]]
         and seed.get('state')=='frozen-url-cache-prepared' and seed.get('owned_seed_venv_removed') is True
         and seed.get('original_inputs_unchanged') is True and seed.get('installed_code_executed') is False
         and seed.get('offline_product_sync')=='required separately'
         and seed.get('network_scope')=='preparation using the unchanged frozen official publisher URLs'
         and seed.get('installer_mode')=='qualification seed only; ordinary product mode is unchanged'
         and seed.get('cache')==TEMP+'/'+base+'/cache' and seed.get('venv')==TEMP+'/'+base+'/cache-preparation/venv'
         and seed.get('product_acceptance_passed') is False, 'seed receipt differs')
    need(seed.get('project_sha256')==closure.get('pyproject_sha256')
         and seed.get('uv_lock_sha256')==closure.get('uv_lock_sha256'), 'seed source inputs differ')
    before = {k:v for k,v in seed.items() if k!='owned_seed_venv_removed'}
    seed_venv = base+'/cache-preparation/venv'
    need(checkpoint==before and not any(x==seed_venv or x.startswith(seed_venv+'/') for x in entries),
         'seed checkpoint or cleanup differs')
    need(sha(data(tar,entries,base+'/cache-preparation/installed-files.json',32*1024*1024))==seed.get('installed_inventory_sha256'),
         'seed inventory bytes differ')
    named_receipt = document(tar,entries,namedroot+'/receipt.json')
    before_raw = data(tar,entries,namedroot+'/qualification-before-cleanup.json',32*1024*1024)
    before = strict_json(before_raw)
    after = {k:v for k,v in named.items() if k not in ('cleanup','qualification_before_cleanup_sha256')}
    named_venv = namedroot+'/venv'
    need(named.get('state')=='offline-environment-qualified' and named.get('product_tests')=='unrun'
         and named.get('frozen_sync_passed') is True and named.get('original_project_unchanged') is True
         and named_receipt==named and before==after and sha(before_raw)==named.get('qualification_before_cleanup_sha256')
         and named.get('command_receipts')==rows[5:] and named.get('cleanup',{}).get('removed') is True
         and not any(x==named_venv or x.startswith(named_venv+'/') for x in entries), 'named receipt or cleanup differs')
    for name,field in [('installed-files.json','installed_inventory_sha256'),
                       ('installed-metadata.json','installed_metadata_sha256'),('uv.lock','uv_lock_sha256')]:
        need(sha(data(tar,entries,namedroot+'/'+name,32*1024*1024))==named.get(field), 'named retained bytes differ: '+name)
    observation = strict_json(logs[('react-api-qa-offline-no-site-metadata','stdout')])
    need(observation==document(tar,entries,namedroot+'/installed-metadata.json')
         and observation.get('python')=='3.12.11' and observation.get('executable')==PYTHON
         and observation.get('site')==TEMP+'/'+named_venv+'/lib/python3.12/site-packages'
         and observation.get('site_disabled') is True and observation.get('site_in_sys_path') is False,
         'no-site raw observation differs')
    observed = {}
    for item in observation.get('packages',[]):
        name = normalized(item['name'])
        need(name not in observed, 'installed package duplicate')
        expected = metadata_hashes.get(name,{})
        need(item.get('metadata_sha256')==expected.get('sha256') and item.get('version')==expected.get('version'),
             'installed metadata or version differs from wheel')
        observed[name] = item['version']
    need(observed==named.get('exact_installed_versions') and set(observed)==set(metadata_hashes),
         'installed package version inventory differs')
    return len(rows)

def audit(repo, source_zip, tar_path):
    tar_digest = unpack(source_zip,tar_path)
    source = sources(repo)
    direct = requirements(source[REQ])
    with tarfile.open(tar_path,'r:') as tar:
        entries = index_tar(tar)
        report = document(tar,entries,QA+'/receipt.json')
        need(report.get('version')=='som.react-api-python-wheel-closure-draft.v1'
             and report.get('source_revision')==REVISION and report.get('product_runtime_ready') is False
             and report.get('product_acceptance_passed') is False and report.get('product_tests')=='unrun'
             and report.get('state')=='react-qa-closure-qualified-independent-audit-unrun'
             and report.get('independent_wheel_audit')=='unrun',
             'qualification scope differs')
        bindings = report.get('source_bindings',{})
        for role,path in [('wrapper',WRAPPER),('producer',PRODUCER),('requirements',REQ)]:
            need(bindings.get(role,{}).get('path')==path and bindings[role].get('sha256')==PINS[path],
                 'recorded source binding differs')
            raw_source = source[path]
            blob = hashlib.sha1(b'blob '+str(len(raw_source)).encode()+b'\0'+raw_source).hexdigest()
            need(bindings[role].get('git_blob_oid')==blob, 'recorded source Git blob differs')
        need(bindings.get('wheel_policy_sha256')==PINS[POLICY]
             and bindings.get('installer_authority_sha256')==PINS[AUTH], 'recorded policy binding differs')
        module = ast.parse(source[PRODUCER].decode('utf-8','strict'))
        probes = [ast.literal_eval(node.value) for node in module.body if isinstance(node,ast.Assign)
                  and any(isinstance(target,ast.Name) and target.id=='PROBE' for target in node.targets)]
        need(len(probes)==1 and isinstance(probes[0],str), 'pinned metadata probe source missing')
        probe_raw = data(tar,entries,QA+'/probe-installed-metadata.py',1024*1024)
        need(probe_raw==probes[0].encode() and report.get('probe_sha256')==sha(probe_raw), 'probe source differs')
        key,base,namedroot,closure,named,metadata_hashes,missing_notices,signatures = audit_closure(tar,entries,report,direct,source)
        command_count = audit_receipts(tar,entries,report,key,base,namedroot,closure,named,metadata_hashes)
    return {'version':'som.react-python-independent-audit.v1','state':'partial-evidence-consistent',
            'product_acceptance_passed':False,'product_runtime_ready':False,'run_id':RUN,'artifact_id':ARTIFACT,
            'source_commit':REVISION,'artifact_zip_sha256':ZIP_SHA,'artifact_tar_sha256':tar_digest,
            'requirements_count':len(direct),'wheel_count':len(metadata_hashes),'command_count':command_count,
            'source_bound_wait':'fixed producer normal-success flow calls child.wait; receipts carry exit and stopped observations, not an independent wait_result',
            'wheels_without_retained_notice':missing_notices,'wheels_with_unverified_signatures':signatures,
            'unverified':['Runtime installed executable bytes are not retained in this artifact; only source-bound identities and inventory were checked.',
                          'Runtime source working-tree hashes and equality are source-bound receipt claims; original working-tree bytes are not retained.',
                          'Both virtual environments were cleaned up; installed bytes cannot be independently rehashed from this artifact.',
                          'Wheel provenance is checked against retained locks and retained publisher responses; publisher identity is not independently authenticated.',
                          'Requires-Dist markers and the dependency graph are not independently resolved; independent legal review and all product tests remain unrun.',
                          'Command records follow fixed producer success control flow and retained logs; independent operating-system wait transcripts are absent.']}

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--repo',type=Path,required=True)
    parser.add_argument('--artifact-zip',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args = parser.parse_args()
    need(os.environ.get('GITHUB_ACTIONS')=='true' and os.environ.get('RUNNER_ENVIRONMENT')=='github-hosted'
         and platform.system()=='Linux' and platform.machine()=='x86_64', 'GitHub hosted Linux required')
    temp = Path(os.environ['RUNNER_TEMP']).resolve(strict=True)
    need(args.artifact_zip==temp/'react-python-qa-closure.zip' and args.output==temp/'react-python-independent-audit.json'
         and not args.output.exists() and not args.output.is_symlink(), 'fixed scratch paths required')
    try:
        result = audit(args.repo,args.artifact_zip,temp/'react-python-qa-closure.tar')
    except Missing as exc:
        result = {'version':'som.react-python-independent-audit.v1','state':'unverified-missing-evidence',
                  'product_acceptance_passed':False,'reason':str(exc)}
    except Exception as exc:
        result = {'version':'som.react-python-independent-audit.v1','state':'failed',
                  'product_acceptance_passed':False,'failure_type':type(exc).__name__}
        if isinstance(exc,(AuditError,wheel_audit.WheelAuditError)):
            result['reason'] = str(exc)
    with args.output.open('x',encoding='utf-8') as stream:
        json.dump(result,stream,sort_keys=True,indent=2)
        stream.write('\n')
    print(json.dumps(result,sort_keys=True))
    return 0 if result['state']=='partial-evidence-consistent' else 1

if __name__=='__main__':
    raise SystemExit(main())





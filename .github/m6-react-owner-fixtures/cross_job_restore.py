"""Draft cross-job collector snapshot and provenance binding; not acceptance.

The caller MUST independently authenticate the GitHub API bytes and admit the
source-bound receipt. This module never asserts that those prerequisites ran.
It does not restore or execute a process and never changes original evidence.
"""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat


def need(value, message):
    if not value:
        raise ValueError(message)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def parse(raw):
    def pairs(items):
        out={}
        for name,value in items:
            need(name not in out, 'duplicate JSON member')
            out[name]=value
        return out
    return json.loads(raw,object_pairs_hook=pairs,
                      parse_constant=lambda _: (_ for _ in ()).throw(ValueError('non-finite JSON')))


def read(path, maximum=128*1024*1024):
    path=Path(path)
    need(path.is_absolute() and '..' not in path.parts, 'absolute physical path required')
    need(all(not x.is_symlink() for x in (path,*path.parents)), 'linked path refused')
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
    try:
        before=os.fstat(fd)
        need(stat.S_ISREG(before.st_mode) and before.st_nlink==1 and before.st_size<=maximum,
             'bounded single-link file required')
        with os.fdopen(fd,'rb',closefd=False) as stream: raw=stream.read(maximum+1)
        after=os.fstat(fd);current=path.stat(follow_symlinks=False)
        stamp=lambda x:(x.st_dev,x.st_ino,x.st_size,x.st_mode,x.st_mtime_ns,x.st_ctime_ns)
        need(stamp(before)==stamp(after)==stamp(current) and len(raw)==before.st_size,'file changed while read')
        return raw
    finally: os.close(fd)


def hash_file(path, maximum=2*1024**3):
    path=Path(path)
    need(path.is_absolute() and '..' not in path.parts and all(not x.is_symlink() for x in (path,*path.parents)),
         'absolute physical file required')
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
    try:
        before=os.fstat(fd)
        need(stat.S_ISREG(before.st_mode) and before.st_nlink==1 and before.st_size<=maximum,
             'bounded single-link archive required')
        digest=hashlib.sha256()
        with os.fdopen(fd,'rb',closefd=False) as stream:
            while True:
                block=stream.read(1024*1024)
                if not block: break
                digest.update(block)
        after=os.fstat(fd);current=path.stat(follow_symlinks=False)
        stamp=lambda x:(x.st_dev,x.st_ino,x.st_size,x.st_mode,x.st_mtime_ns,x.st_ctime_ns)
        need(stamp(before)==stamp(after)==stamp(current),'archive changed while read')
        return digest.hexdigest(),before.st_size
    finally: os.close(fd)


def directory(path):
    p=Path(path)
    need(p.is_absolute() and '..' not in p.parts and p.is_dir()
         and all(not x.is_symlink() for x in (p,*p.parents)), 'physical directory required')
    return p


def canonical_old_path(value):
    need(type(value) is str and value.startswith('/') and '\\' not in value
         and not any(ord(c)<32 or ord(c)==127 for c in value), 'invalid original path')
    path=PurePosixPath(value)
    need(str(path)==value and value!='/' and all(x not in ('..','.') for x in path.parts), 'original path is not canonical')
    return path


def tree(root):
    root=directory(root); files={};dirs={}
    def failed(_): raise ValueError('restore tree scan failed')
    for current,subdirs,names in os.walk(root,topdown=True,followlinks=False,onerror=failed):
        for name in sorted(subdirs+names):
            path=Path(current)/name;rel=path.relative_to(root).as_posix();info=path.lstat()
            need(not stat.S_ISLNK(info.st_mode), 'linked restore member refused')
            if stat.S_ISDIR(info.st_mode): dirs[rel]=stat.S_IMODE(info.st_mode)
            else:
                need(stat.S_ISREG(info.st_mode),'special restore member refused')
                raw=read(path);files[rel]={'sha256':sha(raw),'size':len(raw),'mode':stat.S_IMODE(info.st_mode)}
    return {'files':dict(sorted(files.items())),'directories':dict(sorted(dirs.items())),'excluded':[]}


def verify_collector_restore(capture_raw, context_raw, retained, restored_repo, *, expected_capture_sha256, expected_context_sha256):
    """Recheck original producer snapshot against two restored physical trees.

    Expected hashes must come from a separately authenticated producer artifact.
    This function cannot authenticate that artifact or reconstruct a live process.
    """
    need(type(capture_raw) is bytes and type(context_raw) is bytes
         and sha(capture_raw)==expected_capture_sha256 and sha(context_raw)==expected_context_sha256,
         'capture or original context digest differs')
    capture,context=parse(capture_raw),parse(context_raw)
    need(type(capture) is dict and type(context) is dict and type(capture.get('input_paths')) is dict,
         'capture or original context differs')
    old_repo=canonical_old_path(context.get('repo'))
    old_run=canonical_old_path(capture['input_paths'].get('collector_out'))
    need(context.get('m6_react_inputs', {}).get('collector_out') == str(old_run),
         'producer context collector path differs')
    need(old_run.is_relative_to(old_repo/'.local') and old_run != old_repo/'.local',
         'original collector is outside the original repo .local tree')
    relative=old_run.relative_to(old_repo)
    restored_repo=directory(restored_repo)
    restored=directory(restored_repo.joinpath(*relative.parts))
    retained=directory(retained)
    need(retained!=restored and retained not in restored.parents and restored not in retained.parents,
         'retained and restored trees must be separate')
    proof=capture.get('collector_capture')
    need(type(proof) is dict and proof.get('source_stability_verified') is True
         and proof.get('regular_file_modes_preserved') is True and proof.get('empty_directories_preserved') is True
         and proof.get('excluded')==[] and proof.get('errors')==[], 'producer collector snapshot incomplete')
    snapshot=proof.get('source_snapshot')
    need(type(snapshot) is dict and set(snapshot)=={'files','directories','excluded'} and snapshot['excluded']==[]
         and type(snapshot['files']) is dict and type(snapshot['directories']) is dict,
         'producer full-tree snapshot missing')
    hashes={name:row['sha256'] for name,row in snapshot['files'].items()}
    need(proof.get('copied_sha256')==hashes, 'producer copied file universe differs')
    first_retained,first_restored=tree(retained),tree(restored)
    need(first_retained==snapshot==first_restored, 'collector retained or restored bytes/modes/tree differ')
    need(tree(retained)==first_retained and tree(restored)==first_restored, 'collector restore changed during verification')
    return {'version':'som.m6-react-cross-job-tree-proof.v1',
            'capture_sha256':sha(capture_raw),'original_context_sha256':sha(context_raw),
            'original_repo':str(old_repo),'original_collector':str(old_run),
            'restored_repo':str(restored_repo),'restored_collector':str(restored),
            'relative_collector':relative.as_posix(),'file_count':len(hashes),
            'directory_count':len(snapshot['directories']),
            'producer_tree_stability':'producer_attested_only',
            'artifact_origin_authentication':'external_required',
            'process_identity':'historical_only','product_acceptance_passed':False,'complete':False}


def verify_producer_metadata(receipt_raw, artifact_raw, run_raw, attempt_raw, *, archive_path):
    """Compare source-admitted receipt and API bytes; caller authenticates API origin."""
    receipt,artifact,run,attempt=map(parse,(receipt_raw,artifact_raw,run_raw,attempt_raw))
    need(type(receipt) is dict and set(receipt)=={'version','repository_id','run_id','run_attempt','head_sha',
         'artifact_id','artifact_name','artifact_sha256','artifact_size'}
         and receipt['version']=='som.m6-producer-artifact-source.v1', 'producer source receipt differs')
    for name in ('repository_id','run_id','run_attempt','artifact_id','artifact_size'):
        need(type(receipt[name]) is int and receipt[name]>0, 'producer identity integer differs')
    need(type(receipt['head_sha']) is str and re.fullmatch('[0-9a-f]{40}',receipt['head_sha'])
         and type(receipt['artifact_sha256']) is str and re.fullmatch('[0-9a-f]{64}',receipt['artifact_sha256'])
         and type(receipt['artifact_name']) is str and receipt['artifact_name'], 'producer identity fields differ')
    wf=artifact.get('workflow_run',{})
    need(artifact.get('id')==receipt['artifact_id'] and artifact.get('name')==receipt['artifact_name']
         and artifact.get('size_in_bytes')==receipt['artifact_size'] and artifact.get('expired') is False
         and artifact.get('digest')=='sha256:'+receipt['artifact_sha256']
         and wf.get('id')==receipt['run_id'] and wf.get('head_sha')==receipt['head_sha']
         and wf.get('repository_id')==receipt['repository_id'], 'GitHub artifact identity differs')
    for meta in (run,attempt):
        need(meta.get('id')==receipt['run_id'] and meta.get('head_sha')==receipt['head_sha']
             and meta.get('repository',{}).get('id')==receipt['repository_id'], 'GitHub run identity differs')
    need(attempt.get('run_attempt')==receipt['run_attempt'], 'original run attempt differs')
    archive_sha,archive_size=hash_file(archive_path)
    need(archive_size==receipt['artifact_size'] and archive_sha==receipt['artifact_sha256'], 'producer artifact bytes differ')
    return {'version':'som.m6-react-cross-job-producer-evidence.v1','receipt_sha256':sha(receipt_raw),
            'artifact_metadata_sha256':sha(artifact_raw),'run_metadata_sha256':sha(run_raw),
            'attempt_metadata_sha256':sha(attempt_raw),'producer':receipt,
            'api_authentication':'external_required',
            'artifact_to_attempt_attribution':'external_required',
            'product_acceptance_passed':False,'complete':False}

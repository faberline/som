"""Audit direct baseline wait and the descendants observed before stop."""
import argparse
import hashlib
import json
import os
from pathlib import Path

BASELINE_SHA='2bceb3a0a1de02b08c71eb557c0c3e32fe5ee7b646935e7821e9e07ab3d56e0c'
class Refused(ValueError):pass

def need(x):
    if not x:raise Refused('baseline cleanup audit refused')
def read(path):
    need(path.is_file() and not path.is_symlink() and not any(x.is_symlink() for x in path.parents));return path.read_bytes()
def obj(data):
    def unique(pairs):
        d={}
        for k,v in pairs:need(k not in d);d[k]=v
        return d
    return json.loads(data,object_pairs_hook=unique)
def sha(data):return hashlib.sha256(data).hexdigest()

def audit(out,frozen,expected_sha=BASELINE_SHA,original_frozen=None,receipt_path=None):
    receipt=obj(read(receipt_path if receipt_path is not None else out/'baseline-cleanup.json'));events=receipt['events'];pid=receipt.get('pid');parent=receipt.get('parent_pid')
    original_frozen=original_frozen if original_frozen is not None else frozen
    argv=[str(original_frozen/'som'),'serve','--assets',str(original_frozen/'assets'),'--port','18917']
    need(receipt.get('version')=='som.m6-baseline-cleanup.v1' and receipt.get('product_acceptance_passed') is False
         and type(pid) is int and pid>1 and type(parent) is int and parent>1 and receipt.get('argv')==argv
         and receipt.get('cwd')==str(original_frozen) and sha(read(frozen/'som'))==expected_sha)
    names=('ps','cwd','listener','before','after','listener-after')
    definitions=[['/bin/ps','-ww','-p',str(pid),'-o','pid=,ppid=,pgid=,stat=,command='],
                 ['/usr/sbin/lsof','-a','-p',str(pid),'-d','cwd','-Fn'],
                 ['/usr/sbin/lsof','-nP','-iTCP:18917','-sTCP:LISTEN','-t'],
                 ['/bin/ps','-axo','pid=,ppid=,pgid=,command='],
                 ['/bin/ps','-axo','pid=,ppid=,pgid=,command='],
                 ['/usr/sbin/lsof','-nP','-iTCP:18917','-sTCP:LISTEN','-t']]
    command={};positions={}
    for i,event in enumerate(events):
        if event.get('kind')!='os-command':continue
        name=event.get('name');need(name in names and name not in command and event.get('argv')==definitions[names.index(name)])
        raw=[]
        for stream in ('stdout','stderr'):
            item=event[stream];need(item['file']=='baseline-cleanup-'+name+'.'+stream)
            content=read(out/item['file']);need(sha(content)==item['sha256']);raw.append(content)
        need(not raw[1]);command[name]=(event['exit_code'],raw[0]);positions[name]=i
    need(set(command)==set(names) and all(command[name][0]==0 for name in names[:5]) and command['listener-after']==(1,b''))
    starts=[(i,e) for i,e in enumerate(events) if e.get('kind')=='signal']
    waits=[(i,e) for i,e in enumerate(events) if e.get('kind')=='wait']
    need(len(starts)==len(waits)==1);si,sig=starts[0];wi,wait=waits[0]
    need(all(positions[name]<si for name in names[:4]) and si<wi and all(positions[name]>wi for name in names[4:])
         and sig=={'kind':'signal','pid':pid,'signal':'TERM','state':'syscall-succeeded'}
         and wait.get('pid')==wait.get('waited_pid')==pid and wait.get('state')=='reaped'
         and type(wait.get('raw_status')) is int and type(wait.get('exit_code')) is int
         and os.waitstatus_to_exitcode(wait['raw_status'])==wait['exit_code'])
    ps=command['ps'][1].decode().strip().split(None,4)
    need(len(ps)==5 and ps[0]==str(pid) and ps[1]==str(parent) and ps[2].isdigit()
         and not ps[3].startswith('Z') and ps[4]==' '.join(argv))
    cwd=command['cwd'][1].decode().splitlines()
    need([x[1:] for x in cwd if x.startswith('p')]==[str(pid)] and [x[1:] for x in cwd if x.startswith('n')]==[str(original_frozen)])
    need(command['listener'][1].decode().split()==[str(pid)])
    def table(name):
        rows={}
        for line in command[name][1].decode().splitlines():
            fields=line.strip().split(None,3);need(len(fields)==4 and all(x.isdigit() for x in fields[:3]))
            k=int(fields[0]);need(k not in rows);rows[k]=int(fields[1])
        return rows
    before=table('before');after=table('after');need(before.get(pid)==parent and pid not in after)
    owned={pid}
    while True:
        updated=owned|{k for k,v in before.items() if v in owned}
        if updated==owned:break
        owned=updated
    need(not owned.intersection(after))
    return {'baseline_pid':pid,'direct_wait_exit_code':wait['exit_code'],'listener_absent':True,
            'observed_processes_absent':len(owned),'outside_observed_descendants':'unverified',
            'shared_process_group':'not_claimed','workflow_origin':'unverified'}

def main():
    p=argparse.ArgumentParser()
    for name in ('out','frozen','audit'):p.add_argument('--'+name,type=Path,required=True)
    a=p.parse_args();need(not a.audit.exists())
    report={'version':'som.m6-baseline-cleanup-audit.v1','baseline_observed_cleanup':'unrun','product_acceptance_passed':False}
    try:report['evidence']=audit(a.out,a.frozen);report['baseline_observed_cleanup']='passed'
    except Exception as exc:report['error']=type(exc).__name__+': '+str(exc)
    with a.audit.open('x') as f:json.dump(report,f,indent=2,sort_keys=True);f.write('\n')
    print(json.dumps({'baseline_observed_cleanup':report['baseline_observed_cleanup'],'product_acceptance_passed':False}))
    return 0 if report['baseline_observed_cleanup']=='passed' else 1
if __name__=='__main__':raise SystemExit(main())

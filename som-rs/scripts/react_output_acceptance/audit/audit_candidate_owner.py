"""Independently inspect candidate ownership and observed cleanup. Never acceptance."""
import argparse
import hashlib
import json
from pathlib import Path

class Refused(ValueError):pass

def need(ok):
    if not ok:raise Refused('candidate owner audit refused')
def read(path):
    need(path.is_file() and not path.is_symlink() and not any(p.is_symlink() for p in path.parents))
    return path.read_bytes()
def obj(raw):
    def pairs(items):
        d={}
        for k,v in items:need(k not in d);d[k]=v
        return d
    return json.loads(raw,object_pairs_hook=pairs)
def sha(data):return hashlib.sha256(data).hexdigest()

def audit(out,candidate):
    receipt=obj(read(out/'candidate-owner.json'));runtime=obj(read(candidate/'runtime.json'));build=obj(read(candidate/'build.json'))
    need(receipt.get('version')=='som.m6-owned-candidate.v1' and receipt.get('product_acceptance_passed') is False)
    events=receipt['events'];starts=[(i,e) for i,e in enumerate(events) if e.get('kind')=='candidate-start']
    waits=[(i,e) for i,e in enumerate(events) if e.get('kind')=='candidate-wait']
    prep=[(i,e) for i,e in enumerate(events) if e.get('kind')=='preparer-exit']
    need(len(starts)==len(waits)==len(prep)==1);si,start=starts[0];wi,wait=waits[0];pi,exit=prep[0]
    pid=start['pid'];parent=start['parent_pid'];argv=[str(candidate/'som'),'serve','--assets',str(candidate/'assets'),'--port','18919']
    need(type(pid) is int and pid>1 and type(parent) is int and parent>1 and si<pi<wi
         and start['argv']==runtime['argv']==argv and start['cwd']==str(candidate) and exit['exit_code']==0
         and runtime['pid']==pid and runtime['port']==18919 and wait['pid']==pid and wait['state']=='completed'
         and type(wait['exit_code']) is int and sha(read(candidate/'som'))==runtime['binary_sha256']==build['binary_sha256'])
    definitions={
      'owner-candidate-ps':['/bin/ps','-ww','-p',str(pid),'-o','pid=,ppid=,pgid=,command='],
      'owner-candidate-cwd':['/usr/sbin/lsof','-a','-p',str(pid),'-d','cwd','-Fn'],
      'owner-candidate-listener':['/usr/sbin/lsof','-nP','-iTCP:18919','-sTCP:LISTEN','-t'],
      'owner-candidate-table-before':['/bin/ps','-axo','pid=,ppid=,pgid=,command='],
      'owner-candidate-table-after':['/bin/ps','-axo','pid=,ppid=,pgid=,command='],
      'owner-candidate-listener-after':['/usr/sbin/lsof','-nP','-iTCP:18919','-sTCP:LISTEN','-t']}
    commands={};positions={}
    for i,event in enumerate(events):
        if event.get('kind')!='os-command':continue
        name=event.get('name');need(name in definitions and name not in commands and event['argv']==definitions[name])
        stdout=read(out/(name+'.stdout'));stderr=read(out/(name+'.stderr'))
        need(sha(stdout)==event['stdout_sha256'] and sha(stderr)==event['stderr_sha256'] and not stderr)
        commands[name]=(event['exit_code'],stdout);positions[name]=i
    need(set(commands)==set(definitions) and all(pi<positions[name]<wi for name in list(definitions)[:4])
         and all(positions[name]>wi for name in list(definitions)[4:]))
    need(all(commands[name][0]==0 for name in list(definitions)[:5]) and commands['owner-candidate-listener-after']==(1,b''))
    fields=commands['owner-candidate-ps'][1].decode().strip().split(None,3)
    need(fields==[str(pid),str(parent),str(pid),' '.join(argv)])
    lines=commands['owner-candidate-cwd'][1].decode().splitlines()
    need([line[1:] for line in lines if line.startswith('p')]==[str(pid)]
         and [line[1:] for line in lines if line.startswith('n')]==[str(candidate)])
    need(commands['owner-candidate-listener'][1].decode().split()==[str(pid)])
    def table(name):
        values={}
        for line in commands[name][1].decode().splitlines():
            parts=line.strip().split(None,3)
            need(len(parts)==4 and all(x.isdigit() for x in parts[:3]) and int(parts[0]) not in values)
            values[int(parts[0])]=(int(parts[1]),int(parts[2]))
        return values
    before=table('owner-candidate-table-before');after=table('owner-candidate-table-after')
    need(pid in before and before[pid]==(parent,pid) and pid not in after
         and all(pgid!=pid for _,pgid in after.values()))
    observed={pid}
    while True:
        more=observed|{child for child,(ppid,_) in before.items() if ppid in observed}
        if more==observed:break
        observed=more
    need(not observed.intersection(after))
    return {'candidate_pid':pid,'direct_wait_exit_code':wait['exit_code'],
            'observed_processes_absent':len(observed),'unobserved_descendants':'unverified',
            'workflow_origin':'unverified'}

def main():
    p=argparse.ArgumentParser()
    for name in ('out','candidate','audit'):p.add_argument('--'+name,type=Path,required=True)
    a=p.parse_args();need(not a.audit.exists())
    result={'version':'som.m6-owned-candidate-audit.draft.v1','status':'unrun','product_acceptance_passed':False}
    try:result['observed']=audit(a.out,a.candidate)
    except Exception as exc:result['error']=type(exc).__name__+': '+str(exc)
    with a.audit.open('x') as f:json.dump(result,f,indent=2,sort_keys=True);f.write('\n')
    print(json.dumps({'status':'unrun','product_acceptance_passed':False}));return 1
if __name__=='__main__':raise SystemExit(main())

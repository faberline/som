"""Record byte-exact product HTTP calls while the parity suite owns the products.

Evidence only: a successful recording is not product acceptance. Transport results
are returned unchanged. Recording faults force the caller to reject the capture.
"""
import hashlib
import inspect
import json
from pathlib import Path
import threading
from urllib.parse import urlsplit

LIMIT = 16 * 1024 * 1024
TOTAL_LIMIT = 2 * 1024 * 1024 * 1024

def sha(data): return hashlib.sha256(data).hexdigest()

class ProductRecorder:
    def __init__(self, out, suite, require_owner=True):
        self.out=Path(out); self.out.mkdir(mode=0o700); (self.out/'calls').mkdir(mode=0o700)
        self.suite=suite; self.old_send=suite.http_send; self.old_run=suite.run; self.old_strict=suite.Run.strict
        self.lock=threading.RLock(); self.local=threading.local(); self.calls=[]; self.errors=[]; self.runs={}; self.sequences={}; self.bytes=0; self.require_owner=require_owner; self.owners={}; self.owner_events=[]
    def context(self):
        frame=inspect.currentframe().f_back
        while frame:
            if frame.f_code.co_name=='parity_case':
                case=frame.f_locals.get('case');
                if type(case) is dict and type(case.get('id')) is str:
                    return case['id'], ('rust' if 'python' in frame.f_locals else 'python')
            frame=frame.f_back
        raise ValueError('parity case context missing')
    def bind(self, identity, base, pid, start_event):
        with self.lock:
            if identity in self.owners or type(pid) is not int or pid <= 0: raise ValueError('product owner differs')
            owner={'id':len(self.owner_events)+1,'case':identity[0],'target':identity[1],'base':base,
                   'pid':pid,'start_event':start_event,'stop_event':None}
            self.owner_events.append(owner); self.owners[identity]=owner
            return owner
    def release(self, identity, owner, stop_event):
        with self.lock:
            if self.owners.get(identity) is not owner: raise ValueError('product owner missing')
            owner['stop_event']=stop_event; del self.owners[identity]

    def run(self,prompt,base,send=None):
        case,target=self.context(); prior=getattr(self.local,'identity',None); self.local.identity=(case,target)
        try:
            result=self.old_run(prompt,base,send)
            with self.lock: self.runs[id(result)]=(case,target)
            return result
        finally:self.local.identity=prior
    def strict(self,owner):
        with self.lock: identity=self.runs.get(id(owner))
        if identity is None or identity[1]!='rust':
            with self.lock:self.errors.append('strict_owner_missing')
            return self.old_strict(owner)
        prior=getattr(self.local,'identity',None); self.local.identity=identity
        try:return self.old_strict(owner)
        finally:self.local.identity=prior
    def send(self,base,method,path,body,content_type):
        try:
            status,media,response=self.old_send(base,method,path,body,content_type)
        except BaseException:
            with self.lock:self.errors.append('transport_exception')
            raise
        try:self.record(base,method,path,body,content_type,status,media,response)
        except Exception as exc:
            with self.lock:self.errors.append('recording_'+type(exc).__name__)
        return status,media,response
    def record(self,base,method,path,body,content_type,status,media,response):
        identity=getattr(self.local,'identity',None); split=urlsplit(base)
        if not (identity and split.scheme=='http' and split.hostname=='127.0.0.1' and split.port and
                split.port not in (8765,18765,18917,18919) and split.path in ('','/') and not split.query and
                not split.fragment and not split.username and not split.password and
                method in ('GET','POST','PATCH','DELETE','PUT') and type(path) is str and path.startswith('/') and
                (body is None or type(body) is bytes) and type(response) is bytes and type(status) is int and
                type(content_type) is str and (media is None or type(media) is str)):
            raise ValueError('product call identity differs')
        request=b'' if body is None else body
        if len(request)>LIMIT or len(response)>LIMIT: raise ValueError('product call exceeds byte limit')
        frame=inspect.currentframe().f_back
        step=None; category=None
        while frame:
            if frame.f_code.co_name=='call' and frame.f_locals.get('self').__class__ is self.suite.Run:
                step=frame.f_locals.get('step');category=frame.f_locals.get('category');break
            frame=frame.f_back
        if type(step) is not str or type(category) is not str: raise ValueError('suite call identity missing')
        with self.lock:
            self.bytes+=len(request)+len(response)
            if self.bytes>TOTAL_LIMIT: raise ValueError('capture exceeds total limit')
            owner=self.owners.get(identity)
            if self.require_owner and (owner is None or owner['base'] != base): raise ValueError('product process owner missing')
            number=len(self.calls)+1; seq=self.sequences.get(identity,0)+1; self.sequences[identity]=seq
            req=f'calls/{number:06d}.request.raw'; res=f'calls/{number:06d}.response.raw'
            for name,data in ((req,request),(res,response)):
                with (self.out/name).open('xb') as f:f.write(data)
            self.calls.append({'id':number,'case':identity[0],'target':identity[1],'sequence':seq,'step':step,'category':category,
                'base':base,'owner':owner['id'] if owner else None,'method':method,'path':path,'body_present':body is not None,'content_type':content_type,
                'status':status,'response_content_type':media,'request_file':req,'request_sha256':sha(request),
                'response_file':res,'response_sha256':sha(response),'complete':True})
    def finish(self):
        with self.lock:
            if self.require_owner and (self.owners or any(o['stop_event'] is None for o in self.owner_events)):
                self.errors.append('product_owners_not_stopped')
            document={'version':'som.m6-mac-product-http.v1','calls':self.calls,'owners':self.owner_events,'recording_errors':self.errors,
                      'bytes':self.bytes,'product_acceptance_passed':False}
            with (self.out/'manifest.json').open('x') as f:json.dump(document,f,indent=2,sort_keys=True);f.write('\n')
        return not self.errors

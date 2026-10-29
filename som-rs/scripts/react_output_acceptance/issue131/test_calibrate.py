import hashlib
import json
import sys
from pathlib import Path
import unittest
from types import SimpleNamespace
import calibrate as c

def h(raw): return hashlib.sha256(raw).hexdigest()
def enc(obj): return json.dumps(obj,ensure_ascii=False,separators=(',',':')).encode()

class FakeSources:
    def __init__(self, files): self.files=files
    def read(self,name):
        if name not in self.files: raise c.Unrun('unbound evidence member')
        return self.files[name]
    def json(self,name): return json.loads(self.read(name))

class CalibrationTests(unittest.TestCase):
    def setUp(self):
        self.req=b'{"prompt":"x"}'
        self.resp=b'{"status":"ok","bundle":{"prompt":"x"}}'
        self.slot={'method':'POST','endpoint':'/v1/compile','historical_request_sha256':h(self.req),
                   'report_response_sha256':h(self.resp),'report_status':200}
        self.call={'id':1,'method':'POST','url':'http://127.0.0.1:18917/v1/compile',
                   'complete':True,'error':None,'status':200,'request_sha256':h(self.req),'response_sha256':h(self.resp)}
        self.prefix='run/python-baseline-raw/calls/000001'
        self.candidate={'capture':'python','call_id':1,'manifest_bound':True,
                        'request_member':self.prefix+'.request.raw','response_member':self.prefix+'.response.raw',
                        'request_package':{'sha256':h(self.req)},'response_package':{'sha256':h(self.resp)},
                        'response_sha256':h(self.resp),'status':200}
        self.files={self.prefix+'.json':enc(self.call),self.prefix+'.request.raw':self.req,self.prefix+'.response.raw':self.resp}
    def check(self):
        return c.source_candidate(self.slot,self.candidate,FakeSources(self.files),{'python':{1:self.call}})
    def test_bound_candidate(self): self.assertEqual(self.check(),(self.req,self.resp))
    def test_request_tamper(self):
        self.files[self.prefix+'.request.raw']=b'changed'
        with self.assertRaises(c.Invalid): self.check()
    def test_response_tamper(self):
        self.files[self.prefix+'.response.raw']=b'changed'
        with self.assertRaises(c.Invalid): self.check()
    def test_missing_source_is_unrun(self):
        del self.files[self.prefix+'.request.raw']
        with self.assertRaises(c.Unrun): self.check()
    def test_wrong_port_refused(self):
        self.call['url']='http://127.0.0.1:18919/v1/compile';self.files[self.prefix+'.json']=enc(self.call)
        with self.assertRaises(c.Invalid): self.check()
    def test_wrong_method_refused(self):
        self.call['method']='GET';self.files[self.prefix+'.json']=enc(self.call)
        with self.assertRaises(c.Invalid): self.check()
    def test_sidecar_mismatch_refused(self):
        self.files[self.prefix+'.json']=enc({'id':1})
        with self.assertRaises(c.Invalid): self.check()
    def test_status_mismatch_refused(self):
        self.candidate['status']=400
        with self.assertRaises(c.Invalid): self.check()
    def test_unbound_refused(self):
        self.candidate['manifest_bound']=False
        with self.assertRaises(c.Invalid): self.check()
    def test_path_traversal_refused(self):
        with self.assertRaises(c.Invalid): c.relative('../secret')
    def test_python_compact_serializer(self):
        self.assertEqual(enc({'bundle':{'é':'x'}}),b'{"bundle":{"\xc3\xa9":"x"}}')
    def test_typescript_owner_import_resolves_ts_collector(self):
        tsdir=Path(__file__).resolve().parents[1].parent/'typescript_output_acceptance'
        reactdir=Path(__file__).resolve().parents[1]
        sys.path[:0]=[str(tsdir),str(reactdir)]
        import check_bundles as ts
        import check_typescript_baseline as owner
        self.assertEqual(Path(ts.__file__).resolve(),(tsdir/'check_bundles.py').resolve())
        self.assertIs(owner.check_bundles,ts)
    def test_missing_raw_does_not_mean_success(self):
        source=FakeSources({})
        with self.assertRaises(c.Unrun): source.read('missing')
if __name__=='__main__': unittest.main()

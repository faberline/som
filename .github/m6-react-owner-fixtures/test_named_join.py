"""Synthetic named raw join checks; no real acceptance."""
import json, os, tempfile, unittest
from pathlib import Path
if os.environ.get('GITHUB_ACTIONS') != 'true' or os.environ.get('RUNNER_ENVIRONMENT') != 'github-hosted':
    raise RuntimeError('GitHub-hosted runner required')
import m6_react_callback as s

class NamedJoin(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=os.environ['RUNNER_TEMP']); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve(); self.run=self.root/'run'; self.oracle=self.root/'oracle'
        self.table=[]; self.calls=[]; self.records={'python': [], 'rust': []}; self.counts={'react_python':2,'rust006':1,'typescript125':2}
        self.py={'source':'public','id':'p','variant':'none'}; self.ru={'phase':'phase1','id':'r','check':'compile_equal'}
        self.ts={'form':'json'}
        for cohort,role,case,action in [('react_python','python','p','compile'),('react_python','python','p','assemble'),('rust006','rust','r','compile'),('typescript125','ts','t','compile'),('typescript125','ts','t','assemble')]:
            ident={'slot_id':cohort+action,'cohort':cohort,'group':'public','case':case,'phase':'phase1','form':'json','variant':'none','method':'POST','endpoint':'/v1/'+action}
            req=(cohort+action+' request').encode(); resp=(cohort+action+' response').encode(); reqsha=s.sha(req); respsha=s.sha(resp)
            self.table.append({'identity':ident,'request_sha256':reqsha,'response_sha256':respsha,'status':200,'source':{}})
            i=len(self.calls); op='calls/%06d'%i
            self.put(self.oracle/(op+'.request.raw'),req); self.put(self.oracle/(op+'.response.raw'),resp)
            self.calls.append({'id':i+1,'method':'POST','endpoint':ident['endpoint'],'request_file':op+'.request.raw','response_file':op+'.response.raw','request_sha256':reqsha,'response_sha256':respsha,'status':200,'matching_named_slots':[ident.copy()],'complete':True,'oracle_miss':False,'request_body_complete':True,'error':None})
            if role != 'ts':
                base=self.run/(role+'-baseline-raw'); name='calls/%06d'%len(self.records[role])
                self.put(base/(name+'.request.raw'),req); self.put(base/(name+'.response.raw'),resp)
                self.records[role].append({'url':'http://127.0.0.1:18979'+ident['endpoint'],'method':'POST','complete':True,'error':None,'request_file':name+'.request.raw','response_file':name+'.response.raw','request_sha256':reqsha,'response_sha256':respsha,'status':200})
                if role=='python': self.py.update({'baseline_'+action+'_request_sha256':reqsha,'baseline_'+action+'_response_sha256':respsha,'baseline_'+action+'_status':200})
                else:self.ru.update({'baseline_request_sha256':reqsha,'baseline_response_sha256':respsha,'baseline_status':200})
            else:
                reqname=action+'.request.raw'; respname=action+'.response.raw'; self.put(self.run/'ts-baseline'/reqname,req); self.put(self.run/'ts-baseline'/respname,resp)
                prefix='baseline' if action=='compile' else 'baseline_assemble'
                self.ts.update({('request_file' if action=='compile' else 'baseline_assemble_request_file'):reqname,('body_sha256' if action=='compile' else 'baseline_assemble_request_sha256'):reqsha,prefix+'_response_file':respname,prefix+'_sha256':respsha,prefix+'_status':200})
        self.putj(self.run/'python-baseline/report.json',{'rows':[self.py]}); self.putj(self.run/'rust-baseline/report.json',{'rows':[self.ru]}); self.putj(self.run/'ts-baseline/report.json',{'cases':[{'group':'public','id':'t','requests':[self.ts]}]})
        for role in self.records:self.putj(self.run/(role+'-baseline-raw/manifest.json'),{'calls':self.records[role]})
    def put(self,path,data):
        path=Path(path); path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(data)
    def putj(self,path,obj):self.put(path,json.dumps(obj).encode())
    def check(self):return s.join_recorded_named_rows(self.run,self.oracle,self.table,self.calls,expected_counts=self.counts)
    def test_exact_named_raw_join(self):
        result=self.check();self.assertEqual(result['named_slots'],5);self.assertEqual(result['raw_calls'],5)
    def test_reject_changed_named_status(self):
        self.table[0]['status']=201
        with self.assertRaises(ValueError):self.check()
    def test_reject_changed_oracle_bytes(self):
        self.put(self.oracle/self.calls[0]['response_file'],b'tampered')
        with self.assertRaises(ValueError):self.check()
    def test_reject_missing_and_extra_calls(self):
        self.calls.pop()
        with self.assertRaises(ValueError):self.check()
    def test_identical_wire_slots_keep_distinct_multiplicity(self):
        first=self.calls[0]; duplicate=self.calls[2]
        request=(self.oracle/first['request_file']).read_bytes(); response=(self.oracle/first['response_file']).read_bytes()
        self.put(self.oracle/duplicate['request_file'],request); self.put(self.oracle/duplicate['response_file'],response)
        duplicate['request_sha256']=s.sha(request);duplicate['response_sha256']=s.sha(response)
        identities=[self.table[0]['identity'],self.table[2]['identity']]
        first['matching_named_slots']=identities; duplicate['matching_named_slots']=identities
        self.table[2]['request_sha256']=s.sha(request);self.table[2]['response_sha256']=s.sha(response)
        record=self.records['rust'][0]; base=self.run/'rust-baseline-raw'
        self.put(base/record['request_file'],request);self.put(base/record['response_file'],response)
        record['request_sha256']=s.sha(request);record['response_sha256']=s.sha(response)
        self.ru['baseline_request_sha256']=s.sha(request);self.ru['baseline_response_sha256']=s.sha(response)
        self.putj(base/'manifest.json',{'calls':[record]});self.putj(self.run/'rust-baseline/report.json',{'rows':[self.ru]})
        self.assertEqual(self.check()['raw_calls'],5)
        self.calls.pop(2)
        with self.assertRaises(ValueError):self.check()
    def test_reject_wrong_named_candidate(self):
        self.calls[0]['matching_named_slots']=[self.table[1]['identity']]
        with self.assertRaises(ValueError):self.check()

"""Run only on GH-hosted runners: compare an independent model to frozen suite."""
import copy
import datetime
import json
import os
from pathlib import Path
import re
import sys
import unittest

if os.environ.get('GITHUB_ACTIONS') != 'true' or os.environ.get('RUNNER_ENVIRONMENT') != 'github-hosted':
    raise RuntimeError('GitHub-hosted runner required')
sys.path.insert(0,str(Path(__file__).parent))
sys.path.insert(0,str(Path.cwd()/'som-rs/behavior'))
import independent_behavior_oracle as oracle
import suite


class Draft(oracle.Oracle):
    def __init__(self,prompt): super().__init__(prompt,[]); self.rows=[]
    def call(self, category, step, method, path, status, expected, *, query=None, body=None, raw=None, content_type='application/json'):
        if query:
            from urllib.parse import urlencode, quote
            path+='?'+urlencode(query,quote_via=quote)
        payload=raw if raw is not None else (None if body is None else json.dumps(body,ensure_ascii=False).encode())
        if body is None and raw is None and method in ('POST','PATCH'):payload=b''
        req={'method':method,'path':path}
        if payload is not None:
            req['body']=payload.decode('utf-8','replace')
            if content_type!='application/json':req['content_type']=content_type
        media=(None if self.version=='som.prompt.v5' else 'application/json') if status==204 else 'application/json'
        self.rows.append(({'category':category,'step':step,'request':req,'status':status,'media':media,'body':copy.deepcopy(expected)},payload))


def plans(prompt,strict):
    d=Draft(prompt)
    if d.child:d.v5()
    else:d.resource(d.parent,None)
    if strict:d.strict()
    return d.rows


class FakeTransport:
    def __init__(self,prompt,rows):
        self.prompt,self.rows,self.index=prompt,rows,0
        self.timestamps={}
    def make_row(self,table,row,step,method,body):
        out={};token=row.get('id')
        for name,value in row.items():
            if name=='id' or (self.prompt['version']=='som.prompt.v5' and name==self.prompt['relation']['foreign_key']):
                out[name]=int(value[3:])
            elif name in ('created_at','updated_at') and value=='<timestamp>':
                key=(table,token)
                if key not in self.timestamps:self.timestamps[key]=0
                if name=='updated_at' and method=='PATCH' and body not in (None,b'{}') and ('patch ' in step):
                    self.timestamps[key]+=1
                dt=datetime.datetime(2026,1,1,tzinfo=datetime.timezone.utc)+datetime.timedelta(seconds=self.timestamps[key] if name=='updated_at' else 0)
                out[name]=dt.isoformat()
            else:out[name]=value
        return out
    def body(self,record,method,request):
        expected=record['body'];version=self.prompt['version']
        if record['status']==204:return b''
        if record['status']==422:
            return json.dumps({'detail':[{'type':'fixture','loc':[head,'field'],'msg':'invalid'} for head in expected['422_loc0']]}).encode()
        if record['status']>=400:return json.dumps(expected,ensure_ascii=False).encode()
        if version=='som.prompt.v5':
            parent=self.prompt['resources']['parent'];child=self.prompt['resources']['child']
            if type(expected) is dict and set(expected)=={'parent','children'}:
                doc={'parent':self.make_row(parent['table'],expected['parent'],record['step'],method,request),
                     'children':[self.make_row(child['table'],x,record['step'],method,request) for x in expected['children']]}
                return json.dumps(doc,ensure_ascii=False).encode()
            table=child if child['path'] in record['request']['path'] else parent
        else:table=self.prompt['resource']
        def row(item): return self.make_row(table['table'],item,record['step'],method,request)
        if isinstance(expected,list):doc=[row(x) for x in expected]
        elif type(expected) is dict and 'id' in expected:doc=row(expected)
        else:doc=expected
        return json.dumps(doc,ensure_ascii=False).encode()
    def send(self,method,path,body,content_type):
        row,want=self.rows[self.index];self.index+=1
        actual=re.sub(r'\{id#([0-9]+)\}',lambda m:m.group(1),row['request']['path'])
        if (method,path,body,content_type)!=(row['request']['method'],actual,want,row['request'].get('content_type','application/json')):
            raise AssertionError('producer request does not match independently generated script at '+row['step'])
        return row['status'],row['media'],self.body(row,method,body)


class OracleIntegration(unittest.TestCase):
    def test_frozen_suite_matches_all_v1_to_v5_prompt_models_and_refuses_tamper(self):
        root=Path.cwd()/'som-rs/tests/rust-output-v1/cases'
        for name in ('cov-v1-types','cov-v2-types','cov-v3-types','cov-v4-types','cov-v5-min','cov-v5-max'):
            with self.subTest(name=name):
                prompt=json.loads((root/(name+'.prompt.json')).read_text())
                expected=plans(prompt,True);transport=FakeTransport(prompt,expected)
                run=suite.run(prompt,'http://127.0.0.1:18801',send=transport.send)
                run.strict()
                self.assertEqual(run.failures,[],name)
                self.assertEqual(transport.index,len(expected))
                actual=[(normalized, None if raw['body'] is None else raw['body'].encode()) for raw,normalized in zip(run.raw,run.transcript)]
                self.assertEqual(oracle.check(prompt,actual,include_strict=True)['steps'],len(expected))
                for mutation in ('step','request','status','body','extra','missing'):
                    changed=copy.deepcopy(actual)
                    if mutation=='step':changed[0][0]['step']='unexpected'
                    elif mutation=='request':changed[1]=(changed[1][0],b'corrupt')
                    elif mutation=='status':changed[0][0]['status']=503
                    elif mutation=='body':changed[0][0]['body']={'altered':True}
                    elif mutation=='extra':changed.append(copy.deepcopy(changed[-1]))
                    else:changed.pop()
                    with self.assertRaises(oracle.OracleError,msg=name+' '+mutation):oracle.check(prompt,changed,include_strict=True)
    def test_type_equality_never_treats_boolean_as_integer(self):
        self.assertFalse(oracle.same(True,1))
        self.assertFalse(oracle.same(1,1.0))
        self.assertTrue(oracle.same({'x':[1,True,None]}, {'x':[1,True,None]}))

if __name__=='__main__':unittest.main(verbosity=2)

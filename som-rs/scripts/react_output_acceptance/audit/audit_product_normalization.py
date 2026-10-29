"""Independently recompute successful response normalization; not an oracle."""
import datetime
import json
import re
from urllib.parse import urlsplit

class Refused(ValueError): pass

def need(ok):
    if not ok: raise Refused('product response normalization refused')

def same(a,b):
    return type(a) is type(b) and (a.keys()==b.keys() and all(same(a[k],b[k]) for k in a) if isinstance(a,dict)
           else len(a)==len(b) and all(same(x,y) for x,y in zip(a,b)) if isinstance(a,list) else a==b)

def parse_json(raw):
    def unique(pairs):
        result={}
        for k,v in pairs:
            need(k not in result);result[k]=v
        return result
    return json.loads(raw,object_pairs_hook=unique,parse_constant=lambda _: (_ for _ in ()).throw(Refused('nonfinite')))

def timestamp(value):
    need(type(value) is str and re.fullmatch(r'[0-9]{4}-[0-9]{2}-[0-9]{2}[Tt][0-9]{2}:[0-9]{2}:[0-9]{2}(?:[.][0-9]+)?(?:[Zz]|[+-][0-9]{2}:[0-9]{2})',value) is not None)
    text=value.replace('t','T');text=text[:-1]+'+00:00' if text.endswith(('Z','z')) else text
    result=datetime.datetime.fromisoformat(text);need(result.utcoffset() is not None)
    return result

class Normalizer:
    def __init__(self,prompt):
        self.prompt=prompt;self.version=prompt['version'];self.tokens={};self.stamps={}
        if self.version=='som.prompt.v5':
            self.parent=prompt['resources']['parent'];self.child=prompt['resources']['child']
            self.tables=[self.parent,self.child];self.fk=prompt['relation']['foreign_key']
        else:
            self.parent=prompt['resource'];self.child=None;self.tables=[self.parent];self.fk=None
        for table in self.tables:
            need(type(table['table']) is str and type(table['path']) is str and type(table['fields']) is list)
    def token(self,table,number,create):
        if type(number) is not int:return {'<not-an-int-id>':number}
        known=self.tokens.setdefault(table['table'],{})
        if number not in known:
            if not create:return {'<unknown-id>':number}
            known[number]='id#'+str(len(known)+1)
        return known[number]
    def row(self,table,row):
        if type(row) is not dict:return {'<not-an-object>':row}
        token=self.token(table,row['id'],True) if 'id' in row else None
        types={field['name']:field['type'] for field in table['fields']};out={}
        for k,v in row.items():
            if k=='id': out[k]=token
            elif table is self.child and k==self.fk: out[k]=self.token(self.parent,v,False)
            elif self.version in ('som.prompt.v3','som.prompt.v4','som.prompt.v5') and k in ('created_at','updated_at'):
                try:timestamp(v);out[k]='<timestamp>'
                except (ValueError,Refused,OverflowError):out[k]={'<not-rfc3339>':v}
            elif types.get(k)=='float':out[k]=float(v) if type(v) in (int,float) else v
            elif types.get(k)=='int':out[k]=v if v is None or type(v) is int else {'<not-a-json-integer>':v}
            else:out[k]=v
        if self.version in ('som.prompt.v3','som.prompt.v4','som.prompt.v5') and type(token) is str:
            created=timestamp(row.get('created_at'));updated=timestamp(row.get('updated_at'))
            need(created<=updated);prior=self.stamps.get((table['table'],token))
            if prior:need(created==prior[0] and updated>=prior[1])
            self.stamps[(table['table'],token)]=(created,updated)
        return out
    def table_for_path(self,path):
        path=urlsplit(path).path
        if self.child is not None:
            parent=re.escape(self.parent['path']);child=re.escape(self.child['path'])
            if re.match('^'+parent+r'/[^/]+'+child+r'(?:/|$)',path):return self.child
        return self.parent
    def success(self,path,body):
        if type(body) is dict and set(body)=={'parent','children'} and self.child is not None:
            need(type(body['children']) is list)
            return {'parent':self.row(self.parent,body['parent']),
                    'children':[self.row(self.child,child) for child in body['children']]}
        table=self.table_for_path(path)
        if type(body) is list:return [self.row(table,item) for item in body]
        if type(body) is dict and 'id' in body:return self.row(table,body)
        return body
    def check(self,record,normalized,response):
        if not 200<=record['status']<300 or record['status']==204:return False
        body=parse_json(response.decode('utf-8'))
        need(same(self.success(record['path'],body),normalized['body']))
        return True

"""Partial independent CRUD invariants. This is not the complete behavior oracle."""
import json
from urllib.parse import urlsplit

class Refused(ValueError): pass

def need(value):
    if not value:raise Refused('product CRUD invariant refused')

def parse(raw):
    def unique(pairs):
        obj={}
        for key,value in pairs:
            need(key not in obj);obj[key]=value
        return obj
    return json.loads(raw,object_pairs_hook=unique,parse_constant=lambda _:(_ for _ in ()).throw(Refused('nonfinite')))

def equal(expected,actual,kind):
    if kind=='float' and type(expected) in (int,float) and type(actual) in (int,float):return float(expected)==float(actual)
    return type(expected) is type(actual) and expected==actual

class PartialOracle:
    def __init__(self,prompt):
        self.version=prompt['version'];self.known={}
        if self.version=='som.prompt.v5':
            self.parent=prompt['resources']['parent'];self.child=prompt['resources']['child']
            self.fk=prompt['relation']['foreign_key']
        else:self.parent=prompt['resource'];self.child=None;self.fk=None
    def table_for(self,path):
        path=urlsplit(path).path
        if self.child is not None:
            import re
            if re.match('^'+re.escape(self.parent['path'])+r'/[^/]+'+re.escape(self.child['path'])+r'(?:/|$)',path):return self.child
        return self.parent
    def observe(self,table,row,method,payload):
        need(type(row) is dict and type(row.get('id')) is int)
        fields={f['name']:f['type'] for f in table['fields']}; key=(table['table'],row['id'])
        previous=self.known.get(key)
        if method in ('POST','PATCH') and type(payload) is dict:
            for name,kind in fields.items():
                if name in payload:need(name in row and equal(payload[name],row[name],kind))
        if method=='PATCH' and previous is not None and type(payload) is dict:
            for name,kind in fields.items():
                if name not in payload and name in previous:need(name in row and equal(previous[name],row[name],kind))
        if method=='GET' and previous is not None:
            for name,kind in fields.items():
                if name in previous:need(name in row and equal(previous[name],row[name],kind))
        self.known[key]={name:row[name] for name in fields if name in row}
    def check(self,record,request,response):
        if record['status'] not in (200,201):return 0
        doc=parse(response.decode('utf-8')); payload=None
        if request:
            try:payload=parse(request.decode('utf-8'))
            except (ValueError,UnicodeDecodeError):return 0
        method=record['method'];table=self.table_for(record['path'])
        if type(doc) is dict and set(doc)=={'parent','children'} and self.child is not None:
            need(type(doc['children']) is list and type(payload) is dict and type(payload.get('children')) is list
                 and len(payload['children'])==len(doc['children']))
            self.observe(self.parent,doc['parent'],method,payload.get('parent'))
            for row,child in zip(doc['children'],payload['children']):
                need(row.get(self.fk)==doc['parent']['id']);self.observe(self.child,row,method,child)
            return 1+len(doc['children'])
        if type(doc) is list:
            for row in doc:self.observe(table,row,'GET',None)
            return len(doc)
        if type(doc) is dict and 'id' in doc:
            self.observe(table,doc,method,payload);return 1
        return 0

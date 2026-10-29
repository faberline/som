"""Reconstruct the frozen phase-1 behavior script from a prompt and audit exchanges.

Input records must already be joined to retained HTTP body bytes and independently
normalized by the caller. This module checks the exact script and expected bodies.
It does not grant acceptance or assert process cleanup or provenance.
"""
from __future__ import annotations

import datetime
import json
import urllib.parse

MISSING_ID = 2147483000
ROWS = 23
STRINGS = ('alpha', 'alpha beta', 'ALPHA', 'béta ✓', '', ' alpha')
INTEGERS = (0, -2147483648, 42, 2147483647, -7)
FLOATS = (0.5, -2.25, 3.0, 1e10, 7)
WRONG = {'str': 123, 'int': 1.5, 'float': 'abc', 'bool': 'maybe', 'date': '2026-02-30'}


class OracleError(ValueError):
    pass


def need(condition, label):
    if not condition:
        raise OracleError(label)


def same(a, b):
    if type(a) is not type(b):
        return False
    if isinstance(a, dict):
        return a.keys() == b.keys() and all(same(a[k], b[k]) for k in a)
    if isinstance(a, list):
        return len(a) == len(b) and all(same(x, y) for x, y in zip(a, b))
    return a == b


def sample(field, index):
    kind = field['type']
    if kind == 'str':
        return f"{field['name']}-{index}" if field['unique'] else STRINGS[index % len(STRINGS)]
    if kind == 'int':
        return 100000 + 7 * index if field['unique'] else INTEGERS[index % len(INTEGERS)]
    if kind == 'float': return FLOATS[index % len(FLOATS)]
    if kind == 'bool': return index % 3 == 0
    if kind == 'date': return (datetime.date(2026, 1, 1) + datetime.timedelta(days=(index * 37) % 365)).isoformat()
    raise OracleError('unknown prompt field type')


def changed(field, current, index):
    kind = field['type']
    if kind == 'str': return f"{field['name']}-patched-{index}" if field['unique'] else ('patched ✓' if current != 'patched ✓' else 'patched again')
    if kind == 'int': return 900000 + index if field['unique'] else (-5 if current != -5 else 5)
    if kind == 'float': return 6.5 if current != 6.5 else -6.5
    if kind == 'bool': return not current if current is not None else True
    if kind == 'date': return '2027-02-28' if current != '2027-02-28' else '2027-03-01'
    raise OracleError('unknown prompt field type')


class Table:
    def __init__(self, spec, query, service, missing, conflict, parent=None, fk=None):
        self.name, self.path = spec['table'], spec['path']
        self.fields, self.query, self.service = spec['fields'], query, service
        self.missing, self.conflict, self.parent, self.fk = missing, conflict, parent, fk
        self.rows, self.counter, self.created = {}, 0, 0
    def nullable(self, field):
        return not field['required'] and not (self.service and field.get('default') is False)
    def fresh(self, shape='full'):
        self.counter += 1
        return {f['name']: sample(f, self.counter) if f['required'] or shape == 'full' or not self.nullable(f) else None
                for f in self.fields if f['required'] or shape != 'required'}
    def store(self, values, parent=None):
        self.created += 1
        token = f'id#{self.created}'
        self.rows[token] = {'values': {f['name']: values.get(f['name'], f.get('default')) for f in self.fields}, 'parent': parent}
        return token
    def alive(self, parent): return [k for k, row in self.rows.items() if row['parent'] == parent]
    def row(self, token):
        record = self.rows[token]
        result = {'id': token, **record['values']}
        if self.fk: result[self.fk] = record['parent']
        if self.service: result.update(created_at='<timestamp>', updated_at='<timestamp>')
        for field in self.fields:
            v = result[field['name']]
            if field['type'] == 'float' and type(v) in (int, float): result[field['name']] = float(v)
        return result


class Oracle:
    """Consume every step exactly once; compare a prompt-derived state model."""
    def __init__(self, prompt, entries):
        self.prompt, self.entries, self.index = prompt, entries, 0
        self.version = prompt['version']
        if self.version == 'som.prompt.v5':
            p, c = prompt['resources']['parent'], prompt['resources']['child']
            self.parent = Table(p, p['query'], True, 'parent not found', 'relationship or unique value conflict')
            self.child = Table(c, c['query'], True, 'child not found', 'relationship or unique value conflict', self.parent, prompt['relation']['foreign_key'])
        else:
            self.parent = Table(prompt['resource'], prompt.get('query') or {}, self.version in ('som.prompt.v3','som.prompt.v4'), 'not found', 'conflict')
            self.child = None
        need(self.version in ('som.prompt.v1','som.prompt.v2','som.prompt.v3','som.prompt.v4','som.prompt.v5'), 'unsupported prompt')
    def coll(self, table, parent):
        return table.path if not table.parent else table.parent.path + '/{' + parent + '}' + table.path
    def item(self, table, parent, token): return self.coll(table, parent) + '/{' + token + '}'
    def top(self, table): return [] if self.version == 'som.prompt.v1' else [('limit',str(table.query['max_limit']))]
    def harmless(self, table):
        field = next((f for f in table.fields if not f['unique']), None)
        return {} if field is None else {field['name']: sample(field, 1)}
    def call(self, category, step, method, path, status, expected, *, query=None, body=None, raw=None, content_type='application/json'):
        need(self.index < len(self.entries), f'missing step: {step}')
        actual, request = self.entries[self.index]; self.index += 1
        if query: path += '?' + urllib.parse.urlencode(query, quote_via=urllib.parse.quote)
        payload = raw if raw is not None else (None if body is None else json.dumps(body,ensure_ascii=False).encode())
        if body is None and raw is None and method in ('POST','PATCH'): payload = b''
        exp_request = {'method':method,'path':path}
        if payload is not None:
            exp_request['body'] = payload.decode('utf-8','replace')
            if content_type != 'application/json': exp_request['content_type'] = content_type
        media = (None if self.version == 'som.prompt.v5' else 'application/json') if status == 204 else 'application/json'
        need(type(actual) is dict and actual.get('category') == category and actual.get('step') == step
             and same(actual.get('request'), exp_request), f'request step differs: {step}')
        need(actual.get('status') == status and actual.get('media') == media and same(actual.get('body'), expected), f'response differs: {step}')
        need((request is None and payload is None) or (type(request) is bytes and payload is not None and request == payload), f'raw request differs: {step}')
    def refuse(self, cat, step, method, path, loc, **kw): self.call(cat,step,method,path,422,{'422_loc0':loc},**kw)
    def create(self, table, parent, values, category, step):
        token = table.store(values,parent)
        self.call(category,step,'POST',self.coll(table,parent),201,table.row(token),body=values)
        return token
    def get(self, table, parent, token, category, step): self.call(category,step,'GET',self.item(table,parent,token),200,table.row(token))
    def listing(self, table, parent, query, category, step):
        rows = table.alive(parent); params = dict(query); q = table.query
        if self.version == 'som.prompt.v2' and 'filter_value' in params:
            rows = [t for t in rows if table.rows[t]['values'][q['filter_field']] == params['filter_value']]
        if self.version != 'som.prompt.v2' and q.get('filter_field') and q['filter_field'] in params:
            rows = [t for t in rows if table.rows[t]['values'][q['filter_field']] is (params[q['filter_field']] == 'true')]
        if q.get('date_field') and 'before' in params:
            rows = [t for t in rows if table.rows[t]['values'][q['date_field']] is not None and table.rows[t]['values'][q['date_field']] < params['before']]
        if self.version != 'som.prompt.v1': rows = rows[int(params.get('offset',0)):][:int(params.get('limit',20))]
        self.call(category,step,'GET',self.coll(table,parent),200,[table.row(t) for t in rows],query=query)
    def resource(self, t, parent):
        name, coll = t.name, self.coll(t,parent)
        self.listing(t,parent,[],'crud',f'{name}: list when empty')
        tokens=[]
        for k in range(1,ROWS+1):
            shape = 'required' if k%5==0 else 'nulls' if k%5==1 else 'full'
            cat = 'defaults' if shape=='required' else 'null' if shape=='nulls' else 'crud'
            tokens.append(self.create(t,parent,t.fresh(shape),cat,f'{name}: create row {k} ({shape})'))
        for k,token in enumerate(tokens,1): self.get(t,parent,token,'crud',f'{name}: get row {k}')
        self.lists(t,parent); self.not_found(t,parent); self.unique(t,parent,tokens); self.validation(t,parent,tokens); self.patches(t,parent,tokens)
        victim=tokens[2]; values=dict(t.rows[victim]['values'])
        self.call('crud',f'{name}: delete row 3','DELETE',self.item(t,parent,victim),204,None)
        del t.rows[victim];tokens.remove(victim)
        self.call('crud',f'{name}: get deleted row','GET',self.item(t,parent,victim),404,{'detail':t.missing})
        self.call('crud',f'{name}: delete deleted row','DELETE',self.item(t,parent,victim),404,{'detail':t.missing})
        self.call('crud',f'{name}: patch deleted row','PATCH',self.item(t,parent,victim),404,{'detail':t.missing},body={})
        tokens.append(self.create(t,parent,values,'unique',f'{name}: reuse deleted unique values'))
        self.listing(t,parent,self.top(t),'order',f'{name}: list after delete')
        self.fallbacks(t,parent,tokens[0],coll)
        return tokens
    def lists(self,t,parent):
        name,q=t.name,t.query
        if self.version=='som.prompt.v1':
            self.listing(t,parent,[],'order',f'{name}: list all in id order');return
        top=str(q['max_limit'])
        self.listing(t,parent,[],'limit',f'{name}: list with the default limit')
        for query,cat in (([('limit','1')],'limit'),([('limit',top)],'limit'),([('limit','5'),('offset','3')],'pagination'),([('offset','20')],'pagination'),([('offset',str(ROWS))],'pagination'),([('offset','1000')],'pagination'),([('limit',top),('offset','0')],'order')):
            self.listing(t,parent,query,cat,f"{name}: list {urllib.parse.urlencode(query)}")
        filters=[]
        if self.version=='som.prompt.v2': filters=[[('filter_value',text)] for text in (*STRINGS,'no such value','alpha%')]
        elif q.get('filter_field'): filters=[[(q['filter_field'],'true')],[(q['filter_field'],'false')]]
        filters += [x+[('limit','3'),('offset','1')] for x in filters]
        for query in filters:self.listing(t,parent,query,'filter',f"{name}: filter {urllib.parse.urlencode(query)}")
        if q.get('date_field'):
            cut=sorted(v['values'][q['date_field']] for v in t.rows.values() if v['values'][q['date_field']] is not None and v['parent']==parent)
            for value in (cut[len(cut)//2],cut[0],'2999-12-31','1999-01-01'):
                self.listing(t,parent,[('before',value)],'date_filter',f'{name}: before {value}')
            if q.get('filter_field'):
                self.listing(t,parent,[(q['filter_field'],'false'),('before',cut[len(cut)//2]),('limit','2')],'date_filter',f'{name}: filter and before')
        coll=self.coll(t,parent)
        for query,cat in (([('limit','0')],'limit'),([('limit',str(q['max_limit']+1))],'limit'),([('limit','many')],'limit'),([('offset','-1')],'pagination'),([('offset','x')],'pagination')):
            self.refuse(cat,f"{name}: refuse {urllib.parse.urlencode(query)}",'GET',coll,['query'],query=query)
        if self.version!='som.prompt.v2' and q.get('filter_field'):
            self.refuse('filter',f'{name}: refuse a bad boolean','GET',coll,['query'],query=[(q['filter_field'],'maybe')])
        if q.get('date_field'):
            for bad in ('2026-02-30','tomorrow'):
                self.refuse('date_filter',f'{name}: refuse before {bad}','GET',coll,['query'],query=[('before',bad)])
    def not_found(self,t,parent):
        for text in (str(MISSING_ID),'0','-1'):
            path=self.coll(t,parent)+'/'+text
            self.call('not_found',f'{t.name}: get missing {text}','GET',path,404,{'detail':t.missing})
            self.call('not_found',f'{t.name}: patch missing {text}','PATCH',path,404,{'detail':t.missing},body=self.harmless(t))
            self.call('not_found',f'{t.name}: delete missing {text}','DELETE',path,404,{'detail':t.missing})
        path=self.coll(t,parent)+'/abc'
        self.refuse('validation',f'{t.name}: get with a text id','GET',path,['path'])
        self.refuse('validation',f'{t.name}: patch with a text id','PATCH',path,['path'],body={})
        self.refuse('validation',f'{t.name}: delete with a text id','DELETE',path,['path'])
    def unique(self,t,parent,tokens):
        for field in (x for x in t.fields if x['unique']):
            fname=field['name'];taken=t.rows[tokens[1]]['values'][fname]; values=t.fresh(); values[fname]=taken
            self.call('unique',f'{t.name}: create with a taken {fname}','POST',self.coll(t,parent),409,{'detail':t.conflict},body=values)
            self.call('unique',f'{t.name}: patch to a taken {fname}','PATCH',self.item(t,parent,tokens[3]),409,{'detail':t.conflict},body={fname:taken})
            self.get(t,parent,tokens[3],'unique',f'{t.name}: row unchanged after a {fname} conflict')
        self.listing(t,parent,self.top(t),'unique',f'{t.name}: no row added by conflicts')
    def validation(self,t,parent,tokens):
        name,coll=t.name,self.coll(t,parent); base=t.fresh('required')
        for field in t.fields:
            fname=field['name']
            if field['required']:
                self.refuse('validation',f'{name}: create without {fname}','POST',coll,['body'],body={k:v for k,v in base.items() if k!=fname})
                self.refuse('null',f'{name}: create with null {fname}','POST',coll,['body'],body={**base,fname:None})
            elif not t.nullable(field):self.refuse('null',f'{name}: create with null {fname}','POST',coll,['body'],body={**base,fname:None})
            self.refuse('validation',f'{name}: create with a wrong {fname} type','POST',coll,['body'],body={**base,fname:WRONG[field['type']]})
            self.refuse('validation',f'{name}: patch with a wrong {fname} type','PATCH',self.item(t,parent,tokens[4]),['body'],body={fname:WRONG[field['type']]})
        positional=json.dumps([base[f['name']] for f in t.fields if f['name'] in base]).encode()
        for text,raw,content_type in (('invalid JSON',b'{"a": ','application/json'),('an array body',b'[]','application/json'),('an empty body',b'','application/json'),('a number body',b'1','application/json'),('a null body',b'null','application/json'),('the values as a positional array',positional,'application/json'),('a JSON object sent as text/plain',json.dumps(base).encode(),'text/plain')):
            self.refuse('validation',f'{name}: create with {text}','POST',coll,['body'],raw=raw,content_type=content_type)
            self.refuse('validation',f'{name}: patch with {text}','PATCH',self.item(t,parent,tokens[4]),['body'],raw=raw,content_type=content_type)
        if t.service:
            self.refuse('validation',f'{name}: create with an unknown member','POST',coll,['body'],body={**base,'zz_unknown':1})
            self.refuse('validation',f'{name}: patch with an unknown member','PATCH',self.item(t,parent,tokens[4]),['body'],body={'zz_unknown':1})
        else:
            token=t.store(base,parent)
            self.call('validation',f'{name}: create ignores an unknown member','POST',coll,201,t.row(token),body={**base,'zz_unknown':1});tokens.append(token)
        self.get(t,parent,tokens[4],'validation',f'{name}: row unchanged after refused patches')
        self.listing(t,parent,self.top(t),'validation',f'{name}: no row added by refused creates')
    def patches(self,t,parent,tokens):
        name=t.name;target=tokens[6];path=self.item(t,parent,target); index=0
        for field in t.fields:
            index+=1;fname=field['name'];new=changed(field,t.rows[target]['values'][fname],index);t.rows[target]['values'][fname]=new
            self.call('crud',f'{name}: patch {fname}','PATCH',path,200,t.row(target),body={fname:new})
            self.get(t,parent,target,'crud',f'{name}: get after patching {fname}')
        self.call('crud',f'{name}: empty patch','PATCH',path,200,t.row(target),body={})
        field=next((f for f in t.fields if f['type']=='str'),None)
        if field:
            fname=field['name'];t.rows[target]['values'][fname]='é'*255
            self.call('crud',f'{name}: patch {fname} to 255 characters','PATCH',path,200,t.row(target),body={fname:'é'*255})
        whole={}
        for field in t.fields:
            index+=1;whole[field['name']]=changed(field,t.rows[target]['values'][field['name']],index)
        t.rows[target]['values'].update(whole)
        self.call('crud',f'{name}: patch every field','PATCH',path,200,t.row(target),body=whole)
        for field in t.fields:
            fname=field['name']
            if t.nullable(field):
                t.rows[target]['values'][fname]=None
                self.call('null',f'{name}: patch {fname} to null','PATCH',path,200,t.row(target),body={fname:None})
            elif t.service:self.refuse('null',f'{name}: refuse null {fname}','PATCH',path,['body'],body={fname:None})
            else:self.call('null',f'{name}: null {fname} conflicts','PATCH',path,409,{'detail':t.conflict},body={fname:None})
        self.get(t,parent,target,'null',f'{name}: get after null patches')
        if not t.service:self.call('validation',f'{name}: patch ignores an unknown member','PATCH',path,200,t.row(target),body={'zz_unknown':1})
    def fallbacks(self,t,parent,token,coll):
        self.call('fallback',f'{t.name}: unknown route','GET',coll+'-none/x',404,{'detail':'Not Found'})
        self.call('fallback',f'{t.name}: put on the collection','PUT',coll,405,{'detail':'Method Not Allowed'},body={})
        self.call('fallback',f'{t.name}: delete on the collection','DELETE',coll,405,{'detail':'Method Not Allowed'})
        self.call('fallback',f'{t.name}: post on an item','POST',self.item(t,parent,token),405,{'detail':'Method Not Allowed'},body={})
    def all_parents(self,t,cat,step):
        top=t.query['max_limit']
        for offset in range(0,len(t.rows)+1,top):
            self.listing(t,None,[('limit',str(top)),('offset',str(offset))],cat,f'{step} (offset {offset})')
    def bounds(self,parent,child,first):
        for t,owner in ((parent,None),(child,first)):
            coll=self.coll(t,owner)
            for field in t.fields:
                fname=field['name']
                if field['type']=='str':
                    over=t.fresh();over[fname]='é'*256
                    self.refuse('bounds',f'{t.name}: create with 256 characters in {fname}','POST',coll,['body'],body=over)
                    edge=t.fresh();edge[fname]=('é'*255+str(t.counter))[-255:] if field['unique'] else 'é'*255
                    self.create(t,owner,edge,'bounds',f'{t.name}: create with 255 characters in {fname}')
                if field['type']=='int':
                    for wrong in (2147483648,-2147483649):
                        over=t.fresh();over[fname]=wrong
                        self.refuse('bounds',f'{t.name}: create with {fname} {wrong}','POST',coll,['body'],body=over)
            if t.fields:
                token=next(iter(t.alive(owner)));field=next((f for f in t.fields if f['type'] in ('str','int')),None)
                if field is not None:
                    wrong='é'*256 if field['type']=='str' else 2147483648
                    self.refuse('bounds',f"{t.name}: patch {field['name']} out of bounds",'PATCH',self.item(t,owner,token),['body'],body={field['name']:wrong})
    def batch_body(self,parent,child,count):
        top=parent.fresh();kids=[child.fresh() for _ in range(count)]
        return {'parent':top,'children':kids},top,kids
    def batches(self,parent,child):
        path=parent.path+'/with-children'
        for count in (1,20):
            body,top,kids=self.batch_body(parent,child,count);ptoken=parent.store(top);ctokens=[child.store(k,ptoken) for k in kids]
            self.call('batch',f'batch with {count} children','POST',path,201,{'parent':parent.row(ptoken),'children':[child.row(t) for t in ctokens]},body=body)
            self.get(parent,None,ptoken,'batch',f'parent of the {count}-child batch')
            self.listing(child,ptoken,self.top(child),'batch',f'children of the {count}-child batch')
        for count in (0,21):
            body,_,_=self.batch_body(parent,child,count)
            self.refuse('batch',f'refuse a batch with {count} children','POST',path,['body'],body=body)
        body,_,kids=self.batch_body(parent,child,3)
        long_field=next((f for f in child.fields if f['type']=='str'),None)
        if long_field is not None:
            kids[2][long_field['name']]='é'*256
            self.refuse('bounds','refuse a batch with a child out of bounds','POST',path,['body'],body=body)
        for field in (f for f in child.fields if f['unique']):
            body,_,kids=self.batch_body(parent,child,3);kids[2][field['name']]=kids[0][field['name']]
            self.call('batch_atomic',f"batch with two children sharing {field['name']}",'POST',path,409,{'detail':parent.conflict},body=body)
            body,_,kids=self.batch_body(parent,child,2);taken=next(iter(child.rows.values()))['values'][field['name']];kids[1][field['name']]=taken
            self.call('batch_atomic',f"batch with a child taking an existing {field['name']}",'POST',path,409,{'detail':parent.conflict},body=body)
        for field in (f for f in parent.fields if f['unique']):
            body,top,_=self.batch_body(parent,child,2);top[field['name']]=next(iter(parent.rows.values()))['values'][field['name']]
            self.call('batch_atomic',f"batch with a parent taking an existing {field['name']}",'POST',path,409,{'detail':parent.conflict},body=body)
        self.all_parents(parent,'batch_atomic','no parent left by refused batches')
        body,top,kids=self.batch_body(parent,child,2);ptoken=parent.store(top);ctokens=[child.store(k,ptoken) for k in kids]
        self.call('batch_atomic','batch after refused batches','POST',path,201,{'parent':parent.row(ptoken),'children':[child.row(t) for t in ctokens]},body=body)
    def v5(self):
        parent,child=self.parent,self.child
        self.call('healthz','healthz','GET','/healthz',200,{'status':'ok'})
        parents=self.resource(parent,None);first,second=parents[:2];children=self.resource(child,first)
        others=[self.create(child,second,child.fresh(),'isolation',f'child of the second parent {k}') for k in range(1,4)]
        self.listing(child,second,[],'isolation','second parent lists only its children')
        self.listing(child,first,self.top(child),'isolation','first parent lists only its children')
        for method,body in (('GET',None),('PATCH',{}),('DELETE',None)):
            self.call('isolation',f'{method} a child under the wrong parent',method,self.item(child,second,children[0]),404,{'detail':'child not found'},body=body)
        self.get(child,first,children[0],'isolation','child unchanged after wrong-parent requests')
        path=parent.path+'/'+str(MISSING_ID)+child.path
        self.call('scope','create a child under a missing parent','POST',path,404,{'detail':'parent not found'},body=child.fresh())
        self.call('scope','list children of a missing parent','GET',path,404,{'detail':'parent not found'})
        for method,body in (('GET',None),('PATCH',{}),('DELETE',None)):
            self.call('scope',f'{method} a child under a missing parent',method,path+'/{'+children[0]+'}',404,{'detail':'parent not found'},body=body)
        self.listing(child,first,self.top(child),'scope','no child added under a missing parent')
        for token,label in ((first,'first'),(second,'second')):
            self.call('restrict',f'delete the {label} parent with children','DELETE',self.item(parent,None,token),409,{'detail':parent.conflict})
            self.get(parent,None,token,'restrict',f'{label} parent kept')
        self.listing(child,second,[],'restrict','children kept after a refused parent delete')
        for token in others:
            self.call('restrict','delete a child of the second parent','DELETE',self.item(child,second,token),204,None)
            del child.rows[token]
        self.call('restrict','delete the emptied second parent','DELETE',self.item(parent,None,second),204,None)
        del parent.rows[second]
        self.call('restrict','list children of the deleted parent','GET',self.coll(child,second),404,{'detail':'parent not found'})
        self.bounds(parent,child,first);self.batches(parent,child)
    def strict(self):
        table=self.parent;coll=self.coll(table,None);token=next(iter(table.alive(None)));item=self.item(table,None,token);base=table.fresh('required')
        lax={'int':['1',1.0],'float':['1.5'],'bool':['true',1]}
        for field in table.fields:
            fname,kind=field['name'],field['type']
            for value in lax.get(kind,[]):
                label=json.dumps(value)
                self.refuse('D1',f'D1: create with {fname} as {label}','POST',coll,['body'],body={**base,fname:value})
                self.refuse('D1',f'D1: patch {fname} as {label}','PATCH',item,['body'],body={fname:value})
            wrong=['é'*256] if kind=='str' else [2147483648,-2147483649] if kind=='int' else []
            for bad in wrong:
                label='256 characters' if kind=='str' else str(bad)
                self.refuse('D2',f'D2: create with {fname} {label}','POST',coll,['body'],body={**base,fname:bad})
                self.refuse('D2',f'D2: patch {fname} {label}','PATCH',item,['body'],body={fname:bad})
        for text in ('2147483648','-2147483649','99999999999999999999'):
            path=coll+'/'+text
            self.call('D3',f'D3: get {text}','GET',path,404,{'detail':table.missing})
            self.call('D3',f'D3: patch {text}','PATCH',path,404,{'detail':table.missing},body=self.harmless(table))
            self.call('D3',f'D3: delete {text}','DELETE',path,404,{'detail':table.missing})
        if self.version!='som.prompt.v1':
            repeats=[[('limit','1'),('limit','2')],[('offset','0'),('offset','1')]]
            if table.query.get('filter_field'):
                name='filter_value' if self.version=='som.prompt.v2' else table.query['filter_field']
                repeats.append([(name,'true'),(name,'true')])
            if table.query.get('date_field'):repeats.append([('before','2026-06-01'),('before','2026-06-01')])
            for query in repeats:self.refuse('D4',f'D4: repeated {query[0][0]}','GET',coll,['query'],query=query)
        for method,path,body in (('GET',coll+'/',None),('POST',coll+'/',base),('GET',item+'/',None),('PATCH',item+'/',{}),('DELETE',item+'/',None)):
            self.call('D5',f'D5: {method} with a trailing slash',method,path,404,{'detail':'Not Found'},body=body)
        self.get(table,None,token,'D2','D1 to D5 changed no row')
        self.listing(table,None,self.top(table),'D2','D1 to D5 added no row')
    def run(self, *, include_strict=False):
        if self.child:self.v5()
        else:self.resource(self.parent,None)
        if include_strict:self.strict()
        need(self.index == len(self.entries), 'unexpected extra steps')
        return {'steps':self.index,'behavior_oracle':'checked','product_acceptance_passed':False}


def check(prompt, entries, *, include_strict=False):
    """entries are (independently-normalized transcript, exact request bytes or None)."""
    need(type(prompt) is dict and type(entries) is list and type(include_strict) is bool, 'invalid input')
    return Oracle(prompt, entries).run(include_strict=include_strict)

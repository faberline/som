"""Independent HTTP oracle for optional routes. Seeds only its own database."""
import concurrent.futures
import csv
from datetime import date, datetime, time, timezone
from decimal import Decimal
import gzip
import hashlib
import io
import json
import os
import statistics
import subprocess
import uuid
import time as clock

import httpx
import sqlalchemy as sa
import jwt
import redis

from business_database_checks import Mongo, Postgres
from business_test_support import Providers
from verify_composed_journeys import assert_partial_update
from verify_composed_transactions import verify_transactions
from verify_business_workflows import check_csv_values
from verify_related import Server, payload, run


def verify_subset(prompt, root, fixture):
    caps=prompt['capabilities']; resources=prompt['resources']; fk=prompt['relation']['foreign_key']
    selected={(x['scope'],x['action']) for x in prompt['operations']}
    mongo=caps['database']=='mongodb'
    backend=(Mongo if mongo else Postgres)(prompt,'som_test_subset_'+uuid.uuid4().hex[:12],fixture)
    providers=Providers(fixture,prompt)
    inherited={k:v for k,v in os.environ.items() if not k.startswith('AWS_') and k!='GOOGLE_APPLICATION_CREDENTIALS'}
    env=inherited|backend.env|providers.environment()|{
        'UV_CACHE_DIR':os.environ.get('SOM_EVAL_UV_CACHE_DIR','/private/tmp/som-uv-cache'),
        'UV_LINK_MODE':'copy','UV_PYTHON_DOWNLOADS':'never'}
    checks=[]; server=None
    report={'package':prompt['package'],'checks':checks,'operations':sorted(selected),'status':'running'}
    def done(name): checks.append(name)
    def raw_rows(role):
        if mongo:
            return list(backend.db[resources[role]['table']].find({}).sort('_id',1))
        with backend.engine.connect() as conn:
            table=getattr(backend,role)
            return [dict(r) for r in conn.execute(sa.select(table).order_by(table.c.id)).mappings()]
    def seed(n=3, many_role='child'):
        if mongo:
            for role in ('child','parent'): backend.db[resources[role]['table']].delete_many({})
        else:
            with backend.engine.begin() as conn:
                conn.execute(backend.child.delete());conn.execute(backend.parent.delete())
        counts={'parent':max(3,n) if many_role=='parent' else 3,'child':n}
        for role in ('parent','child'):
            rows=[]
            for i in range(counts[role]):
                values=payload(resources[role],90000+i,True)
                q=resources[role]['query']
                if q['filter_field']: values[q['filter_field']]=i%2==0
                if q['date_field']: values[q['date_field']]=['2030-01-01','2030-01-02',None][i%3]
                for f in resources[role]['fields']:
                    if f['type']=='date' and values[f['name']] is not None:
                        day=date.fromisoformat(values[f['name']]);values[f['name']]=datetime.combine(day,time(),timezone.utc) if mongo else day
                values['_id' if mongo else 'id']=90000+i
                values.update(created_at=datetime(2020,1,1,tzinfo=timezone.utc),updated_at=datetime(2020,1,1,tzinfo=timezone.utc))
                if role=='child': values[fk]=90001 if i==2 else 90000
                rows.append(values)
            if mongo:
                if rows:backend.db[resources[role]['table']].insert_many(rows)
            else:
                with backend.engine.begin() as conn:
                    if rows:conn.execute(getattr(backend,role).insert(),rows)
    def item_path(role, ident=90000, parent=90000):
        base=resources['parent']['path']
        return base+'/'+str(ident) if role=='parent' else base+'/'+str(parent)+resources['child']['path']+'/'+str(ident)
    def list_path(role,parent=90000):
        return resources['parent']['path'] if role=='parent' else resources['parent']['path']+'/'+str(parent)+resources['child']['path']
    def expected_custom(role,raw):
        result={}
        for f in resources[role]['fields']:
            value=raw.get(f['name'])
            if f['type']=='date' and value is not None:value=value.date().isoformat() if isinstance(value,datetime) else value.isoformat()
            result[f['name']]=value
        return result
    backend.create(run)
    try:
        run(['uv','sync','--python','3.12'],cwd=root,env=env);done('empty_directory_uv_sync')
        python=str(root/'.venv/bin/python')
        server=Server(root,prompt['package'],env)
        env['GOOGLE_REDIRECT_URI']=server.url+'/auth/google/callback'
        missing=env.copy();missing.pop(backend.config_key)
        probe=subprocess.run([python,'-c',f'from fastapi.testclient import TestClient\nfrom {prompt["package"]}.app import app\nwith TestClient(app): pass'],cwd=root,env=missing,text=True,capture_output=True)
        assert probe.returncode and backend.config_key in probe.stderr, (probe.returncode, probe.stderr)
        done('missing_database_configuration')
        run(backend.migration_command(python,True),cwd=root,env=env);backend.inspect();done('migration_and_database_constraints')
        seed();server.start()
        with httpx.Client(base_url=server.url,timeout=15) as client:
            providers.login(client)
            def request(method,path,status=200,**kw):
                response=client.request(method,path,**kw)
                assert response.status_code==status,(method,path,status,response.status_code,response.text)
                return response
            specification=request('GET','/openapi.json').json()['paths']
            for role in ('parent','child'):
                base=resources['parent']['path'] if role=='parent' else resources['parent']['path']+'/{parent_id}'+resources['child']['path']
                for action,method,suffix in [('create','post',''),('list','get',''),('get','get','/{parent_id}' if role=='parent' else '/{child_id}'),('patch','patch','/{parent_id}' if role=='parent' else '/{child_id}'),('delete','delete','/{parent_id}' if role=='parent' else '/{child_id}')]:
                    assert (method in specification.get(base+suffix,{}))==((role,action) in selected),(role,action)
            batch_path=resources['parent']['path']+'/with-children'
            assert ('post' in specification.get(batch_path,{}))==(('batch','batch_create') in selected)
            done('exact_selected_and_omitted_route_surface')
            env['TEST_AUTH_HEADERS']=json.dumps({k:v for k,v in client.headers.items() if k.lower() in {'authorization','x-csrf-token'}})
            env['TEST_AUTH_COOKIES']=json.dumps(dict(client.cookies))
            run([python,'-m','unittest','discover','-s','tests','-v'],cwd=root,env=env);done('generated_unittest')
            with httpx.Client(base_url=server.url,timeout=15) as stranger:
                assert stranger.get('/auth/me').status_code==401
                providers.login(stranger,'reader')
                assert stranger.get('/auth/me').status_code==200
                for role,action in selected:
                    if action not in {'create','patch','delete','batch_create'}:continue
                    method={'create':'POST','batch_create':'POST','patch':'PATCH','delete':'DELETE'}[action]
                    path=batch_path if role=='batch' else list_path(role) if action=='create' else item_path(role)
                    assert stranger.request(method,path,json={}).status_code==403
            done('identity_required_and_reader_denied_on_every_selected_write')
            if caps['auth']=='jwt':
                bad=[providers.jwt(changes={'exp':1}),providers.jwt(changes={'iss':'wrong'}),providers.jwt(changes={'aud':'wrong'}),providers.jwt(remove=['exp']),providers.jwt(algorithm='HS256',key=b'isolated-test-key-at-least-32-bytes')]
                prefix,signature=providers.jwt().rsplit('.',1)
                bad.append(prefix+'.'+('A' if signature[0]!='A' else 'B')+signature[1:])
                for token in bad:request('GET','/auth/me',401,headers={'Authorization':'Bearer '+token})
                done('jwt_signature_algorithm_claims_expiry')
            elif caps['auth']=='firebase':
                for state in ({'disabled':True},{'validSince':str(int(clock.time())+60)}):
                    providers.control(users={'editor':state});request('GET','/auth/me',401)
                providers.control(users={'editor':{}});request('GET','/auth/me')
                token=client.headers['Authorization'].removeprefix('Bearer ')
                claims=jwt.decode(token,options={'verify_signature':False})
                for change in ({'aud':'wrong'},{'exp':1},{'iss':'wrong'}):
                    bad=jwt.encode(claims|change,key=None,algorithm='none');request('GET','/auth/me',401,headers={'Authorization':'Bearer '+bad})
                done('firebase_project_expiry_revocation_and_recovery')
            else:
                request('GET','/auth/google/callback',400,params={'code':'wrong','state':'wrong'})
                replay=providers.login(client);request('GET',replay,400)
                first=request('GET','/auth/google/login',302);provider=client.get(first.headers['location'])
                with httpx.Client(base_url=server.url) as stranger:assert stranger.get(provider.headers['location']).status_code==400
                providers.control(faults={'nonce':True})
                first=request('GET','/auth/google/login',302);provider=client.get(first.headers['location']);request('GET',provider.headers['location'],401)
                providers.control(faults={});providers.login(client)
                for role,action in selected:
                    if action not in {'create','patch','delete','batch_create'}:continue
                    method={'create':'POST','batch_create':'POST','patch':'PATCH','delete':'DELETE'}[action]
                    path=batch_path if role=='batch' else list_path(role) if action=='create' else item_path(role)
                    request(method,path,403,json={},headers={'X-CSRF-Token':'wrong'})
                cookies=dict(client.cookies);request('POST','/auth/logout',204)
                with httpx.Client(base_url=server.url,cookies=cookies) as stale:assert stale.get('/auth/me').status_code==401
                providers.login(client)
                store=redis.Redis.from_url(fixture['redis_url']);prefix=env['REDIS_NAMESPACE']+':'+prompt['package']+':session:'
                for key in store.scan_iter(match=prefix+'*'):store.pexpire(key,1)
                clock.sleep(.02);request('GET','/auth/me',401)
                providers.control(redis='stop');request('GET','/auth/google/login',503)
                providers.control(redis='start');providers.login(client)
                done('oauth_pkce_state_browser_nonce_csrf_logout_expiry_redis_recovery')
            for role,action in sorted(selected):
                seed()
                if role=='batch':
                    body={'parent':payload(resources['parent'],1000),'children':[payload(resources['child'],1001)]}
                    for children in ([],[payload(resources['child'],2000+i) for i in range(21)]):
                        before=backend.snapshot();request('POST',batch_path,422,json=body|{'children':children});assert before==backend.snapshot()
                    result=request('POST',batch_path,201,json=body).json()
                    assert all(row[fk]==result['parent']['id'] for row in result['children'])
                    done('batch_valid_and_size_boundaries');continue
                r=resources[role]; route=list_path(role); item=item_path(role)
                if action=='create':
                    created=request('POST',route,201,json=payload(r,1000)).json()
                    for field in r['fields']:
                        if not field['required']: assert created[field['name']]==field['default']
                    assert abs((datetime.now(timezone.utc)-datetime.fromisoformat(created['created_at'])).total_seconds())<120
                    if role=='child':request('POST',list_path(role,99999999),404,json=payload(r,2000))
                    for extra in ('id','created_at','updated_at',fk,'extra_unknown'):
                        request('POST',route,422,json=payload(r,2000)|{extra:1})
                    for field in r['fields']:
                        if field['required']:
                            value=payload(r,3000);value.pop(field['name']);request('POST',route,422,json=value)
                        if field['required'] or field.get('default') is False:
                            request('POST',route,422,json=payload(r,3000)|{field['name']:None})
                elif action=='get':
                    row=request('GET',item).json();assert row['id']==90000
                    assert all(row[k]==v for k,v in expected_custom(role,raw_rows(role)[0]).items())
                    request('GET',item_path(role,99999999),404)
                    if role=='child':request('GET',item_path(role,parent=90001),404)
                elif action=='patch':
                    before=request('PATCH',item,json={}).json()
                    for i,field in enumerate(r['fields']):
                        key=field['name']; value={'str':'changed_'+str(i),'int':0,'float':0.0,'bool':not before[key],'date':'2031-02-03'}[field['type']]
                        after=request('PATCH',item,json={key:value}).json();assert_partial_update(before,after,key,value);before=after
                        if not field['required'] and field.get('default') is None:
                            after=request('PATCH',item,json={key:None}).json();assert_partial_update(before,after,key,None);before=after
                        else:request('PATCH',item,422,json={key:None})
                        invalid={'str':'x'*256,'int':2**31,'float':'Infinity','bool':None,'date':'2030-02-30'}[field['type']]
                        if field['type']!='bool' or field['required'] or field.get('default') is False:request('PATCH',item,422,json={key:invalid})
                    for key in ('id','created_at','updated_at',fk,'extra_unknown'):request('PATCH',item,422,json={key:1})
                    request('PATCH',item_path(role,99999999),404,json={})
                    if role=='child':request('PATCH',item_path(role,parent=90001),404,json={})
                elif action=='delete':
                    if role=='parent':
                        before=backend.snapshot();request('DELETE',item,409);assert before==backend.snapshot();request('DELETE',item_path(role,90002),204)
                    else:
                        request('DELETE',item_path(role,parent=90001),404);request('DELETE',item,204)
                    request('DELETE',item_path(role,99999999),404)
                elif action=='list':
                    seed(125,role)
                    for params in ({},{'limit':r['query']['max_limit'],'offset':1}, {'limit':1,'offset':0}):
                        actual=request('GET',route,params=params).json()
                        expected=[row for row in raw_rows(role) if role=='parent' or row[fk]==90000]
                        start=params.get('offset',0);length=params.get('limit',20)
                        assert [v['id'] for v in actual]==[v['_id' if mongo else 'id'] for v in expected[start:start+length]]
                    q=r['query'];params={}
                    if q['filter_field']:params[q['filter_field']]='false'
                    if q['date_field']:params['before']='2030-01-02'
                    actual=request('GET',route,params=params).json()
                    expected=[row for row in raw_rows(role) if (role=='parent' or row[fk]==90000) and (not q['filter_field'] or row[q['filter_field']] is False) and (not q['date_field'] or row[q['date_field']] is not None and (row[q['date_field']].date() if isinstance(row[q['date_field']],datetime) else row[q['date_field']])<date(2030,1,2))]
                    assert [v['id'] for v in actual]==[v['_id' if mongo else 'id'] for v in expected[:20]]
                    for params in ({'limit':0},{'limit':r['query']['max_limit']+1},{'offset':-1}):request('GET',route,422,params=params)
                    if role=='child':request('GET',list_path(role,99999999),404)
                done(role+'_'+action+'_contract')
            seed()
            if ('parent','delete') in selected and ('child','create') in selected:
                for i in range(5):
                    seed();path=resources['parent']['path']+'/90002'
                    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
                        a=pool.submit(client.delete,path);b=pool.submit(client.post,path+resources['child']['path'],json=payload(resources['child'],1000+i))
                        pair=a.result().status_code,b.result().status_code
                    assert pair in {(204,404),(409,201)},pair
                backend.no_orphans();done('concurrent_child_create_parent_delete_no_orphans')
            seed(); export_path=resources['parent']['path']+'/90000/export'
            exported=request('GET',export_path); raw=[r for r in raw_rows('child') if r[fk]==90000]
            wanted=[expected_custom('child',r) for r in raw]
            workflow=caps['workflow']
            if workflow in {'json_export','gzip_export'}:
                records=exported.json() if workflow=='json_export' else json.loads(gzip.decompress(exported.content))
                assert [r['id'] for r in records]==[r['_id' if mongo else 'id'] for r in raw]
                assert [{f['name']:r[f['name']] for f in resources['child']['fields']} for r in records]==wanted
                if workflow=='gzip_export':assert request('GET',export_path).content==exported.content
            elif workflow=='csv':
                check_csv_values(exported.text,resources['child']['fields'],wanted)
                fields=[f['name'] for f in resources['child']['fields']]
                def csv_body(items):
                    buf=io.StringIO();w=csv.DictWriter(buf,fields);w.writeheader();w.writerows(items);return buf.getvalue()
                path=resources['parent']['path']+'/90000/import'
                request('POST',path,201,content=csv_body([payload(resources['child'],1100,True)]),headers={'Content-Type':'text/csv'})
                for body in ('bad\nx\n',csv_body([]),csv_body([payload(resources['child'],1200+i,True) for i in range(21)])):
                    before=backend.snapshot();request('POST',path,422,content=body,headers={'Content-Type':'text/csv'});assert before==backend.snapshot()
                unique=next((f['name'] for f in resources['child']['fields'] if f['unique']),None)
                if unique:
                    a,b=payload(resources['child'],1300,True),payload(resources['child'],1301,True);b[unique]=a[unique]
                    before=backend.snapshot();request('POST',path,409,content=csv_body([a,b]),headers={'Content-Type':'text/csv'});assert before==backend.snapshot()
            elif workflow=='decimal_summary':
                totals={}
                for row in wanted:
                    v=row[caps['value_field']]
                    if v is not None:totals[row[caps['group_field']]]=totals.get(row[caps['group_field']],Decimal(0))+Decimal(str(v))
                assert {k:Decimal(v) for k,v in exported.json()['totals'].items()}==totals
            elif workflow=='statistics':
                values=[row[caps['value_field']] for row in wanted if row[caps['value_field']] is not None]
                assert exported.json()=={'count':len(values),'mean':statistics.fmean(values),'median':statistics.median(values),'minimum':min(values),'maximum':max(values)}
            request('GET',resources['parent']['path']+'/99999999/export',404)
            done(workflow+'_content_and_missing_parent')
            seed(1002);request('GET',export_path,413);seed();done('workflow_row_limit')
            if caps['storage']!='none':
                path='/objects/subset_'+uuid.uuid4().hex;body=b'isolated\x00\xff';kind=caps['storage']
                request('GET',path,404);request('PUT',path,422,content=b'');request('PUT',path,413,content=b'x'*(1048577));request('PUT','/objects/bad.key',422,content=body)
                providers.control(faults={kind:True})
                try:request('PUT',path,503,content=body)
                finally:providers.control(faults={})
                made=request('PUT',path,content=body).json();assert made['sha256']==hashlib.sha256(body).hexdigest()
                assert request('PUT',path,content=body).json()==made
                request('PUT',path,409,content=b'different')
                got=request('GET',path);assert got.content==body and got.headers['x-content-sha256']==made['sha256']
                providers.control(faults={kind:True})
                try:request('GET',path,503);request('DELETE',path,503)
                finally:providers.control(faults={})
                request('DELETE',path,204);request('GET',path,404);request('DELETE',path,204);done('storage_bounds_conflict_bytes_failure_recovery')
            before=backend.snapshot();server.stop();server.start();request('GET',export_path);assert backend.snapshot()==before;done('restart_persistence')
        server.stop();run(backend.migration_command(python,False),cwd=root,env=env);backend.absent()
        run(backend.migration_command(python,True),cwd=root,env=env);assert backend.snapshot()==(0,0);done('migration_downgrade_and_upgrade')
        backend.unavailable_health(run,python,root,env,server,fixture);done('database_health_failure')
        if ('batch','batch_create') in selected: report['transactions']=verify_transactions(prompt,root,fixture)
        report['status']='passed';return report
    finally:
        if server:server.stop();server.log.close()
        backend.close(run);providers.close()

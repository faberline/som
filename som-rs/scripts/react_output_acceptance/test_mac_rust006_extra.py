"""GitHub-only process, raw protocol and port fixtures for the draft collector."""
import json
import os
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent / 'audit'))
import tempfile
import unittest
import urllib.request

if os.environ.get('GITHUB_ACTIONS') != 'true' or os.environ.get('RUNNER_ENVIRONMENT') != 'github-hosted':
    raise RuntimeError('GitHub-hosted runner required')

import record_rust006_http as recorder
import mac_rust006_extra as extra
import mac_rust006_cluster as cluster
import mac_parity_capture as parity
import run_hosted_acceptance as hosted


class HostedAdmissionTests(unittest.TestCase):
    def test_final_adapter_cannot_be_activated_before_review(self):
        self.assertFalse(hosted.FINAL_MAC_ADAPTER_REVIEWED)
        with self.assertRaises(SystemExit):
            hosted.main(['--out', '.local/m6-hosted/unused', '--candidate-revision', 'a' * 40])


class RawProtocolTests(unittest.TestCase):
    def test_only_candidate_loopback_and_frozen_routes(self):
        for url in ('http://127.0.0.1:18919/v1/compile', 'http://localhost:18919/v1/assemble'):
            request = urllib.request.Request(url, data=b'{}', method='POST')
            self.assertEqual(recorder.request_identity(request)[2], b'{}')
        for url in ('http://127.0.0.1:8765/v1/compile', 'http://127.0.0.1:18765/v1/compile',
                    'http://127.0.0.1:18917/v1/compile', 'http://example.com:18919/v1/compile',
                    'http://127.0.0.1:18919/other', 'http://127.0.0.1:18919/v1/compile?x=1'):
            with self.subTest(url=url), self.assertRaises(recorder.RecordingError):
                recorder.request_identity(urllib.request.Request(url, data=b'{}', method='POST'))

    def test_record_preserves_raw_request_and_response(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp).resolve()
            (out / 'calls').mkdir()
            owner = recorder.Recorder(out)
            call = recorder.Call(owner, ('POST', 'http://127.0.0.1:18919/v1/compile', b'original'), 1)
            call.record['status'] = 400
            call.append(b'{"code":"ASSET_HASH_MISMATCH"}')
            call.finish(complete=True)
            row = json.loads((out / 'calls/000001.json').read_bytes())
            self.assertEqual((out / row['request_file']).read_bytes(), b'original')
            self.assertEqual((out / row['response_file']).read_bytes(), b'{"code":"ASSET_HASH_MISMATCH"}')
            self.assertEqual(row['status'], 400)
            self.assertTrue(row['complete'])


class WaitEvidenceTests(unittest.TestCase):
    def test_parity_recorder_keeps_direct_child_wait(self):
        with tempfile.TemporaryDirectory() as temp:
            observer = parity.TrackingRecorder(Path(temp).resolve())
            proc = observer.popen([sys.executable, '-c', 'raise SystemExit(7)'])
            self.assertEqual(proc.wait(timeout=10), 7)
            starts = [e for e in observer.events if e['kind'] == 'child-start']
            waits = [e for e in observer.events if e['kind'] == 'child-wait']
            self.assertEqual(len(starts), 1)
            self.assertTrue(any(e['identity'] == starts[0]['identity'] and e['exit_code'] == 7 for e in waits))

    def test_actual_child_waits_and_keeps_failure_logs(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp).resolve()
            (out / 'logs').mkdir()
            row = extra.record_command(out, 1, 'bad', [sys.executable, '-c',
                'import sys; print("out"); print("err",file=sys.stderr); sys.exit(7)'], out, dict(os.environ), 10)
            self.assertEqual(row['state'], 'completed')
            self.assertEqual(row['wait_result'], 7)
            self.assertEqual(row['exit_code'], 7)
            self.assertEqual(row['cleanup'], 'requires-independent-raw-audit')
            self.assertEqual((out / row['stdout']).read_bytes(), b'out\n')
            self.assertEqual((out / row['stderr']).read_bytes(), b'err\n')

    def test_timeout_is_never_completed(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp).resolve()
            (out / 'logs').mkdir()
            row = extra.record_command(out, 1, 'slow', [sys.executable, '-c', 'import time; time.sleep(30)'],
                                       out, dict(os.environ), 0.5)
            self.assertEqual(row['state'], 'timeout')
            self.assertIsNone(row['wait_result'])
            self.assertEqual(row['cleanup'], 'unverified')
            self.assertIsInstance(row['cleanup_wait_result'], int)


class IndependentRawTests(unittest.TestCase):
    def fixture(self, root):
        import hashlib
        import audit_mac_rust006_extra as audit
        def h(raw): return hashlib.sha256(raw).hexdigest()
        script = root / 'som-rs/tests/rust-output-v1/validate_cases.py'
        script.parent.mkdir(parents=True)
        script.write_bytes(b'fixed-checker')
        cases = script.parent / 'cases'
        cases.mkdir()
        prompt = {'name':'sample'}
        prompt_raw = json.dumps(prompt).encode()
        cnl = b'create sample'
        (cases/'one.prompt.json').write_bytes(prompt_raw)
        (cases/'one.cnl').write_bytes(cnl)
        index = {'cases':[{'id':'one','prompt':'one.prompt.json','cnl':'one.cnl',
                          'prompt_file_sha256':h(prompt_raw),'cnl_sha256':h(cnl)}]}
        (cases/'index.json').write_text(json.dumps(index))
        files = {'main.py':'print(1)\n'}
        frozen = {'cases':[{'id':'one','prompt_sha256':'abc','python_files':{'main.py':h(files['main.py'].encode())}}]}
        evidence = root / 'som-rs/evidence/rust-output-v1/coverage-cases-python-2026-10-15.json'
        evidence.parent.mkdir(parents=True)
        evidence.write_text(json.dumps(frozen))
        bundle = {'hashes':{'prompt_sha256':'abc'},'value':'fixed'}
        raw = root / 'run'
        (raw/'calls').mkdir(parents=True)
        requests = [json.dumps({'format':'som.prompt-json.v1','prompt':prompt}).encode(),
                    json.dumps({'format':'som.cnl.v1','source':cnl.decode()}).encode(),
                    json.dumps({'bundle':bundle}).encode()]
        responses = [json.dumps({'bundle':bundle}).encode(), json.dumps({'bundle':bundle}).encode(),
                     json.dumps({'files':files}).encode()]
        rows=[]
        for i,(request,response) in enumerate(zip(requests,responses),1):
            req=f'calls/{i:06d}.request.raw'; res=f'calls/{i:06d}.response.raw'
            (raw/req).write_bytes(request); (raw/res).write_bytes(response)
            row={'id':i,'method':'POST','url':audit.QA+('/v1/assemble' if i==3 else '/v1/compile'),
                 'request_file':req,'request_sha256':h(request),'status':200,'response_file':res,
                 'response_sha256':h(response),'error':None,'complete':True}
            (raw/f'calls/{i:06d}.json').write_text(json.dumps(row))
            rows.append(row)
        manifest={'version':'som.m6-rust006-http-recording.v1',
                  'script':{'path':'som-rs/tests/rust-output-v1/validate_cases.py','sha256':h(b'fixed-checker')},
                  'exit_code':0,'calls':rows,'recording_errors':[]}
        (raw/'manifest.json').write_text(json.dumps(manifest))
        return raw

    def test_coverage_raw_checked_without_success_booleans(self):
        import audit_mac_rust006_extra as audit
        from unittest import mock
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp).resolve()
            raw=self.fixture(root)
            with mock.patch.object(audit,'REPO',root):
                self.assertEqual(audit.audit_cases(raw),{'cases':1,'raw_calls':3})
                response=raw/'calls/000003.response.raw'
                response.write_bytes(b'{"files":{"main.py":"tampered"}}')
                with self.assertRaises(ValueError): audit.audit_cases(raw)

    def test_refusal_rehash_and_pointer_are_derived_from_request(self):
        import audit_mac_rust006_extra as audit
        bundle={'version':'v','prompt':{'key':'x'},'dsl':{'files':[]},'assets':{'x':1},
                'hashes':{'prompt_sha256':'stale','dsl_sha256':'stale','payload_sha256':'stale'}}
        changed=audit.rehash(bundle)
        self.assertEqual(changed['hashes']['prompt_sha256'], audit.h(changed['prompt']))
        self.assertEqual(changed['hashes']['dsl_sha256'], audit.h(changed['dsl']))
        self.assertEqual(changed['hashes']['payload_sha256'], audit.h({k:v for k,v in changed.items() if k!='hashes'}))
        self.assertEqual(audit.pointer({'bundle':{'a/b':[{'~name':'wire'}]}}, '/bundle/a~1b/0/~0name'), 'wire')
        self.assertEqual(bundle['hashes']['payload_sha256'], 'stale')

    def test_final_audit_stays_incomplete(self):
        import audit_mac_rust006_extra as audit
        self.assertEqual(audit.ALLOWED, {'backend','block_library','catalog','compiler_contract','selection_policy'})


class ClusterRawAuditTests(unittest.TestCase):
    def create(self, root):
        import hashlib
        import audit_mac_rust006_extra as audit
        def digest(data): return hashlib.sha256(data).hexdigest()
        config=root/'som-rs/contracts/rust-output-v1/toolchain.json'
        config.parent.mkdir(parents=True)
        names=('postgres','initdb','pg_ctl')
        pgdir='/opt/homebrew/opt/postgresql@17/bin'
        toolchain={'postgresql':{'bin_dir':pgdir,**{n:{'sha256':digest(n.encode())} for n in names}}}
        config.write_text(json.dumps(toolchain))
        run=root/'run'; lifecycle=run/'cluster-lifecycle'; lifecycle.mkdir(parents=True)
        (lifecycle/'postgresql-tools').mkdir()
        tool_rows={}
        for n in names:
            (lifecycle/'postgresql-tools'/n).write_bytes(n.encode())
            tool_rows[n]={'source':pgdir+'/'+n,'file':'postgresql-tools/'+n,'sha256':digest(n.encode()),
                          'source_before':digest(n.encode()),'source_after':digest(n.encode())}
        data=str(run/'cluster-work/pgdata'); log=str(run/'cluster-work/postgres.log'); port=18920; pid=1234
        calls=0
        def command(argv, stdout=b'', exit_code=0, kind='os'):
            nonlocal calls
            calls+=1
            stem=f'{kind}-{calls:04d}'
            (lifecycle/(stem+'.stdout')).write_bytes(stdout)
            (lifecycle/(stem+'.stderr')).write_bytes(b'')
            return {'argv':argv,'exit_code':exit_code,
                    'stdout':{'file':stem+'.stdout','sha256':digest(stdout)},
                    'stderr':{'file':stem+'.stderr','sha256':digest(b'')}}
        options=f"-p {port} -c listen_addresses=127.0.0.1 -c unix_socket_directories='' -c timezone=UTC -c log_timezone=UTC -c max_connections=300 -c fsync=off"
        init={'kind':'initdb',**command([pgdir+'/initdb','-D',data,'-U','postgres','-E','UTF8','--locale=C','--auth=trust','--no-sync'],kind='call')}
        start={'kind':'pg_ctl',**command([pgdir+'/pg_ctl','-D',data,'-l',log,'-o',options,'-w','start'],kind='call')}
        stop={'kind':'pg_ctl',**command([pgdir+'/pg_ctl','-D',data,'-m','fast','-w','stop'],kind='call')}
        post_raw=f'{pid}\n{data}\n123456\n{port}\n'.encode()
        post=[]
        for phase in ('after-start','before-stop'):
            name='postmaster-'+phase+'.pid'; (lifecycle/name).write_bytes(post_raw)
            post.append({'kind':'postmaster','phase':phase,'raw':{'file':name,'sha256':digest(post_raw)}})
        ps=['/bin/ps','-ww','-p',str(pid),'-o','pid=,ppid=,pgid=,command=']
        cwd=['/usr/sbin/lsof','-a','-p',str(pid),'-d','cwd','-Fn']
        table=['/bin/ps','-axo','pid=,ppid=,pgid=']
        before={'kind':'process','pid':pid,'phase':'before-pg-stop','pgid':pid,'pgid_error_errno':None,
                'ps':command(ps,f'{pid} 1 {pid} /opt/homebrew/bin/postgres -D {data}\n'.encode()),
                'cwd':command(cwd,f'p{pid}\nfcwd\nn{data}\n'.encode()),
                'table':command(table,f'1 0 1\n{pid} 1 {pid}\n1235 {pid} {pid}\n'.encode())}
        after={'kind':'process','pid':pid,'phase':'after-stop','pgid':None,'pgid_error_errno':3,'port':port,
               'ps':command(ps,exit_code=1),'cwd':command(cwd,exit_code=1),
               'listener':command(['/usr/sbin/lsof','-nP',f'-iTCP:{port}','-sTCP:LISTEN','-t'],exit_code=1),
               'table':command(table,b'1 0 1\n')}
        receipt={'version':'som.m6-mac-rust006-cluster.v1','port':port,'exit_code':0,'product_acceptance_passed':False,
                 'recording_errors':[],'tools':tool_rows,'events':[init,start,post[0],post[1],before,stop,after]}
        (lifecycle/'receipt.json').write_text(json.dumps(receipt))
        return lifecycle

    def test_cluster_raw_owner_and_absence(self):
        from unittest import mock
        import audit_mac_rust006_extra as audit
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp).resolve(); lifecycle=self.create(root)
            with mock.patch.object(audit,'REPO',root):
                self.assertEqual(audit.audit_cluster(lifecycle)['observed_descendants'], 1)
                self.assertEqual(audit.audit_cluster(lifecycle)['unobserved_descendants'], 'unverified')
                receipt=json.loads((lifecycle/'receipt.json').read_text())
                after=receipt['events'][-1]['table']['stdout']
                changed=b'1 0 1\n1235 1 1234\n'
                (lifecycle/after['file']).write_bytes(changed)
                import hashlib
                after['sha256']=hashlib.sha256(changed).hexdigest()
                (lifecycle/'receipt.json').write_text(json.dumps(receipt))
                with self.assertRaises(ValueError): audit.audit_cluster(lifecycle)


class ApprovedInputBindingTests(unittest.TestCase):
    def test_approved_manifest_binds_actual_request_and_rejects_tamper(self):
        import hashlib
        import audit_mac_rust006_extra as audit
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp).resolve()
            target=root/'.local/example/request.json';target.parent.mkdir(parents=True)
            target.write_bytes(b'{"original":true}')
            digest=hashlib.sha256(target.read_bytes()).hexdigest()
            manifest={'version':'som.m6-sealed-inputs-manifest.v1','cipher_sha256':'f'*64,
                      'uncompressed_bytes':100,'total_files':1,'files':[{'path':'.local/example/request.json',
                      'sha256':digest,'bytes':target.stat().st_size,'mode':0o644}]}
            raw=json.dumps(manifest).encode()
            index={'version':'som.m6-input-index.v1','packages':[{'archive':'package-000.sealed',
                   'manifest':'package-000.manifest.json','manifest_sha256':hashlib.sha256(raw).hexdigest()}]}
            index_raw=json.dumps(index).encode()
            files=audit.manifest_map(index_raw,hashlib.sha256(index_raw).hexdigest(),lambda name:raw)
            bound=audit.BoundInputs(root,files)
            self.assertEqual(bound.read(target),b'{"original":true}')
            target.write_bytes(b'{"original":false}')
            with self.assertRaises(ValueError): bound.read(target)
            other=root/'.local/example/other.json';other.write_bytes(b'{}')
            with self.assertRaises(ValueError): bound.read(other)
            with self.assertRaises(ValueError): audit.manifest_map(index_raw,'0'*64,lambda name:raw)

    def test_derived_authority_remains_distinct_and_rejects_tampering(self):
        import audit_mac_rust006_extra as audit
        with tempfile.TemporaryDirectory() as temp:
            repo=Path(temp).resolve();old=repo/'old';old.write_bytes(b'approved')
            new=repo/'derived';new.write_bytes(b'later-capture')
            files={'old':{'sha256':audit.sha(b'approved'),'bytes':8}}
            combined=audit.merge_derived(files,{'derived':b'later-capture'},repo)
            self.assertEqual(combined['derived']['source'],'later-capture-derived')
            bound=audit.BoundInputs(repo,combined);self.assertEqual(bound.read(new),b'later-capture')
            with self.assertRaises(ValueError):audit.merge_derived(files,{'old':b'approved'},repo)
            new.write_bytes(b'tampered')
            with self.assertRaises(ValueError):audit.merge_derived(files,{'derived':b'later-capture'},repo)
            with self.assertRaises(ValueError):bound.read(new)

    def test_approved_supplement_still_requires_both_source_provenances(self):
        import audit_mac_rust006_extra as audit
        self.assertEqual(audit.HISTORIC_RESPONSE_APPROVED_INDEX_SHA256,'c468ae2086e4d64847b93ad8da08aa3828f30989008db470519cd408e55b67bf')
        public=audit.REPO/'.local/rust-compiler-goal/acceptance/public-products-003b'
        legacy=audit.REPO/'.local/rust-compiler-goal/acceptance/legacy544/run-005'
        with self.assertRaises(ValueError):audit.approved_inputs(public,legacy)



class CommandRawAuditTests(unittest.TestCase):
    def test_fixed_commands_wait_and_log_tamper(self):
        import hashlib
        from unittest import mock
        import audit_mac_rust006_extra as audit
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp).resolve();run=root/'run';run.mkdir();(run/'logs').mkdir()
            candidate=root/'candidate';candidate.mkdir()
            public=root/'public';legacy=root/'legacy';public.mkdir();legacy.mkdir()
            parity=root/'parity.json';parity.write_bytes(b'parity')
            (public/'result.json').write_bytes(b'public');(legacy/'result.json').write_bytes(b'legacy')
            rels=('som-rs/scripts/react_output_acceptance/mac_rust006_extra.py',
                  'som-rs/scripts/react_output_acceptance/mac_rust006_cluster.py',
                  'som-rs/scripts/react_output_acceptance/record_rust006_http.py',
                  'som-rs/scripts/react_output_acceptance/record_api.py',
                  'som-rs/scripts/rust_output_acceptance/python_replay.py',
                  'som-rs/tests/rust-output-v1/validate_cases.py',
                  'som-rs/som-targets/qa/check_rust_products.py','som-rs/behavior/hosts.py',
                  'som-rs/scripts/react_output_acceptance/reconstruct_historical_responses.py',
                  'som-rs/scripts/react_output_acceptance/verify_historical_response_provenance.py',
                  'som-rs/scripts/react_output_acceptance/install_derived_inputs.py',
                  'som-rs/scripts/react_output_acceptance/audit_supplement_provision.py')
            for rel in rels:
                path=root/rel;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(rel.encode())
            python=root/'som-rs/runtime/acceptance-m3-venv/bin/python';script=root/'som-rs/scripts/react_output_acceptance'
            qa='http://127.0.0.1:18919'
            expected=[
                [python,script/'record_rust006_http.py','--out',run/'raw-python-replay','--',root/'som-rs/scripts/rust_output_acceptance/python_replay.py','--asset-break','--base-url',qa,'--public-run',public,'--legacy-run',legacy,'--out',run/'python-replay.json'],
                [python,script/'record_rust006_http.py','--out',run/'raw-validate-cases','--',root/'som-rs/tests/rust-output-v1/validate_cases.py','--base-url',qa,'--out',run/'validate-cases.json'],
                [python,script/'mac_rust006_cluster.py','--raw-out',run/'raw-rust-refusals','--lifecycle',run/'cluster-lifecycle','--cluster-root',run/'cluster-work','--','--qa',qa,'--work',run/'rust-products-work','--target-dir',run/'rust-target','--out',run/'rust-products.json','--no-gates','--refusals','phase2','--parity',parity]]
            names=('python-replay','validate-cases','rust-refusals');rows=[]
            for i,(name,argv) in enumerate(zip(names,expected),1):
                row={'index':i,'name':name,'argv':list(map(str,argv)),'cwd':str(root),'state':'completed',
                     'pid':100+i,'exit_code':0,'wait_result':0,'environment':{'CARGO_NET_OFFLINE':'true','PYTHONDONTWRITEBYTECODE':'1'}}
                for stream in ('stdout','stderr'):
                    rel=f'logs/{name}.{stream}';data=(name+stream).encode();(run/rel).write_bytes(data)
                    row[stream]=rel;row[stream+'_sha256']=hashlib.sha256(data).hexdigest()
                rows.append(row)
            sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
            rev='a'*40
            runner={'GITHUB_SHA':rev,'GITHUB_ACTIONS':'true','RUNNER_ENVIRONMENT':'github-hosted','RUNNER_OS':'macOS',
                    'RUNNER_ARCH':'ARM64','GITHUB_RUN_ID':'123','GITHUB_RUN_ATTEMPT':'1'}
            report={'candidate_revision':rev,'candidate_dir':str(candidate),'parity_sha256':sha(parity),
                    'public_result_sha256':sha(public/'result.json'),'legacy_result_sha256':sha(legacy/'result.json'),
                    'runner':runner,'rows':rows,'execution_complete':True,'product_acceptance_passed':False}
            (run/'collector.json').write_text(json.dumps(report))
            def fake_git(argv,timeout):
                if 'rev-parse' in argv:return (rev+'\n').encode()
                if 'ls-tree' in argv:
                    rel=argv[-1];return b'100644 blob '+b'0'*40+b'\t'+rel.encode()+b'\0'
                if 'show' in argv:return (root/argv[-1].split(':',1)[1]).read_bytes()
                raise AssertionError(argv)
            with mock.patch.object(audit,'REPO',root), mock.patch('subprocess.check_output',side_effect=fake_git):
                self.assertEqual(audit.audit_command_records(run,candidate,public,legacy,parity)['commands'],3)
                (run/'logs/python-replay.stdout').write_bytes(b'tampered')
                with self.assertRaises(ValueError):audit.audit_command_records(run,candidate,public,legacy,parity)


class UnorderedParityWireTests(unittest.TestCase):
    def test_raw_wire_join_is_unordered_and_tamper_fails(self):
        import hashlib
        from unittest import mock
        import audit_mac_parity as parity_audit
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp).resolve(); script=root/'som-rs/behavior/check_behavior.py'
            script.parent.mkdir(parents=True);script.write_bytes(b'fixed')
            raw=root/'wire';(raw/'calls').mkdir(parents=True)
            sha=lambda b:hashlib.sha256(b).hexdigest()
            bodies=[('/v1/compile',{'prompt':'a'},{'bundle':'x'}),('/v1/assemble',{'bundle':'x'},{'files':{}})]
            rows=[]
            for i,(route,request,response) in enumerate(bodies,1):
                req=json.dumps(request,ensure_ascii=False,separators=(',',':')).encode()
                res=json.dumps(response).encode()
                rf=f'calls/{i:06d}.request.raw';sf=f'calls/{i:06d}.response.raw'
                (raw/rf).write_bytes(req);(raw/sf).write_bytes(res)
                row={'id':i,'method':'POST','url':parity_audit.QA+route,'request_file':rf,
                     'request_sha256':sha(req),'status':200,'response_file':sf,'response_sha256':sha(res),
                     'error':None,'complete':True}
                (raw/f'calls/{i:06d}.json').write_text(json.dumps(row));rows.append(row)
            (raw/'manifest.json').write_text(json.dumps({'version':'som.m6-rust006-http-recording.v1',
                'exit_code':0,'recording_errors':[],'script':{'path':'som-rs/behavior/check_behavior.py',
                'sha256':sha(b'fixed')},'calls':rows}))
            with mock.patch.object(parity_audit,'REPO',root):
                index,count=parity_audit.raw_exchange_index(raw)
                self.assertEqual(count,2)
                self.assertEqual(json.loads(parity_audit.take(index,'/v1/assemble',{'bundle':'x'})),{'files':{}})
                self.assertEqual(json.loads(parity_audit.take(index,'/v1/compile',{'prompt':'a'})),{'bundle':'x'})
                self.assertFalse(any(index.values()))
                (raw/'calls/000002.response.raw').write_bytes(b'tampered')
                with self.assertRaises(ValueError):parity_audit.raw_exchange_index(raw)


class WorkflowOriginTests(unittest.TestCase):
    def test_origin_source_and_runner_metadata_are_bound(self):
        import subprocess
        import audit_workflow_origin as origin
        with tempfile.TemporaryDirectory() as temp:
            repo=Path(temp).resolve();workflow='.github/workflows/fixture.yml';source=repo/workflow;source.parent.mkdir(parents=True);source.write_bytes(b'fixture')
            subprocess.run(['git','init','-q',str(repo)],check=True);subprocess.run(['git','-C',str(repo),'add','.'],check=True)
            subprocess.run(['git','-C',str(repo),'-c','user.name=Fixture','-c','user.email=fixture@example.invalid','commit','-qm','fixture'],check=True)
            head=subprocess.check_output(['git','-C',str(repo),'rev-parse','HEAD'],text=True).strip()
            env={'GITHUB_ACTIONS':'true','RUNNER_ENVIRONMENT':'github-hosted','RUNNER_OS':'macOS','RUNNER_ARCH':'ARM64',
                 'GITHUB_REPOSITORY':'faberline/som','GITHUB_EVENT_NAME':'push','GITHUB_SHA':head,'GITHUB_WORKFLOW_SHA':head,
                 'GITHUB_RUN_ID':'123','GITHUB_RUN_ATTEMPT':'1','GITHUB_REF':'refs/heads/fixture',
                 'GITHUB_WORKFLOW_REF':'faberline/som/'+workflow+'@refs/heads/fixture','RUNNER_NAME':'runner'}
            repo_meta={'id':1390374878,'full_name':'faberline/som'}
            run={'id':123,'run_attempt':1,'event':'push','head_sha':head,'path':workflow,'status':'in_progress',
                 'repository':repo_meta,'head_repository':repo_meta}
            jobs={'total_count':1,'jobs':[{'id':17,'name':'fixtures','runner_name':'runner','status':'in_progress',
                  'steps':[{'name':'check','status':'completed','conclusion':'success'}]}]}
            def check():return origin.verify(repo,env,run,jobs,workflow=workflow,job_name='fixtures',completed_step='check',event='push')
            self.assertEqual(check()['job_id'],17)
            for key,value in (('head_sha','0'*40),('path','.github/workflows/other.yml'),('run_attempt',2)):
                old=run[key];run[key]=value
                with self.subTest(key=key),self.assertRaises(ValueError):check()
                run[key]=old
            jobs['jobs'][0]['runner_name']='other'
            with self.assertRaises(ValueError):check()
            jobs['jobs'][0]['runner_name']='runner';source.write_bytes(b'modified')
            with self.assertRaises(ValueError):check()

class OwnedCandidateTests(unittest.TestCase):
    def test_timeout_retry_signal_error_still_attempts_bounded_wait(self):
        import errno,subprocess
        from unittest import mock
        import owned_candidate
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp).resolve();candidate=root/'candidate';candidate.mkdir()
            owner=owned_candidate.CandidateOwner(root,candidate,18919);proc=mock.Mock();proc.pid=7123;proc.returncode=None
            proc.wait.side_effect=[subprocess.TimeoutExpired('fixture',1),-9];owner.process=proc
            line=('7123 '+str(os.getpid())+' 7123 S '+' '.join(owner.argv)+'\n').encode()
            with mock.patch.object(owned_candidate.os,'getpgid',return_value=7123), \
                 mock.patch.object(owned_candidate.subprocess,'run',return_value=subprocess.CompletedProcess([],0,line,b'')), \
                 mock.patch.object(owned_candidate.os,'killpg',side_effect=[None,None,PermissionError(errno.EPERM,'denied')]):
                with self.assertRaises(subprocess.TimeoutExpired):owner.wait_after_signal(timeout=1,grace=0)
            self.assertEqual(proc.wait.call_count,2)
            retry=next(e for e in owner.events if e['kind']=='candidate-cleanup-signal')
            self.assertEqual((retry['error_type'],retry['errno']),('PermissionError',errno.EPERM))
            self.assertTrue(any(e['kind']=='candidate-cleanup-wait' and e['state']=='completed' for e in owner.events))

    def test_eperm_is_failure_but_direct_wait_is_recorded(self):
        import errno,subprocess
        from unittest import mock
        import owned_candidate
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp).resolve();candidate=root/'candidate';candidate.mkdir()
            owner=owned_candidate.CandidateOwner(root,candidate,18919);proc=mock.Mock();proc.pid=7123;proc.returncode=None;proc.wait.return_value=-9;owner.process=proc
            line=('7123 '+str(os.getpid())+' 7123 S '+' '.join(owner.argv)+'\n').encode()
            with mock.patch.object(owned_candidate.os,'getpgid',return_value=7123), \
                 mock.patch.object(owned_candidate.subprocess,'run',return_value=subprocess.CompletedProcess([],0,line,b'')), \
                 mock.patch.object(owned_candidate.os,'killpg',side_effect=[PermissionError(errno.EPERM,'denied'),None]):
                with self.assertRaises(owned_candidate.OwnershipError):owner.wait_after_signal(timeout=1,grace=0)
            error=next(e for e in owner.events if e.get('error_type')=='PermissionError')
            self.assertEqual(error['errno'],errno.EPERM)
            self.assertTrue(any(e['kind']=='candidate-wait' and e['state']=='completed' for e in owner.events))
            proc.wait.assert_called_once()

    def test_zombie_only_group_skips_signals_and_waits(self):
        import subprocess
        from unittest import mock
        import owned_candidate
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp).resolve();candidate=root/'candidate';candidate.mkdir()
            owner=owned_candidate.CandidateOwner(root,candidate,18919);proc=mock.Mock();proc.pid=7123;proc.returncode=None;proc.wait.return_value=0;owner.process=proc
            line=('7123 '+str(os.getpid())+' 7123 Z <defunct>\n').encode()
            with mock.patch.object(owned_candidate.subprocess,'run',return_value=subprocess.CompletedProcess([],0,line,b'')), \
                 mock.patch.object(owned_candidate.os,'killpg') as kill:
                self.assertEqual(owner.wait_after_signal(timeout=1,grace=0),0)
            kill.assert_not_called()
            self.assertEqual([e['group'] for e in owner.events if e['kind']=='candidate-signal'],['no-live-members','no-live-members'])
            proc.wait.assert_called_once()

    def test_identity_live_to_zombie_race_rechecks_before_signal(self):
        import subprocess
        from unittest import mock
        import owned_candidate
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp).resolve();candidate=root/'candidate';candidate.mkdir()
            owner=owned_candidate.CandidateOwner(root,candidate,18919)
            proc=mock.Mock();proc.pid=7123;proc.returncode=None;proc.wait.return_value=0;owner.process=proc
            live=('7123 '+str(os.getpid())+' 7123 S '+' '.join(owner.argv)+'\n').encode()
            zombie=('7123 '+str(os.getpid())+' 7123 Z <defunct>\n').encode()
            group=(zombie.decode()+'7124 1 7123 S child\n').encode()
            replies=[subprocess.CompletedProcess([],0,live,b''),subprocess.CompletedProcess([],0,zombie,b''),
                     subprocess.CompletedProcess([],0,group,b''),subprocess.CompletedProcess([],0,group,b'')]
            with mock.patch.object(owned_candidate.os,'getpgid',side_effect=ProcessLookupError), \
                 mock.patch.object(owned_candidate.subprocess,'run',side_effect=replies) as ps, \
                 mock.patch.object(owned_candidate.os,'killpg') as kill:
                self.assertEqual(owner.wait_after_signal(timeout=1,grace=0),0)
            self.assertEqual(ps.call_count,4);self.assertEqual(kill.call_count,2)
            identity=next(e for e in owner.events if e['kind']=='candidate-signal-identity')
            self.assertEqual(identity['state'],'Z')
            self.assertIn(b'<defunct>',(root/'candidate-signal-identity.stdout').read_bytes())

    def test_timeout_kills_owned_group_but_never_counts_as_success(self):
        import subprocess
        from unittest import mock
        import owned_candidate
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp).resolve();candidate=root/'candidate';candidate.mkdir()
            owner=owned_candidate.CandidateOwner(root,candidate,18919)
            proc=mock.Mock();proc.pid=7123;proc.returncode=None
            proc.wait.side_effect=[subprocess.TimeoutExpired('fixture',1),-9];owner.process=proc
            command=('7123 '+str(os.getpid())+' 7123 S '+' '.join(owner.argv)+'\n').encode()
            with mock.patch.object(owned_candidate.os,'getpgid',return_value=7123), \
                 mock.patch.object(owned_candidate.subprocess,'run',return_value=subprocess.CompletedProcess([],0,command,b'')), \
                 mock.patch.object(owned_candidate.os,'killpg') as kill:
                with self.assertRaises(subprocess.TimeoutExpired):owner.wait_after_signal(timeout=1)
            self.assertEqual([call.args[1] for call in kill.call_args_list],[owned_candidate.signal.SIGTERM,owned_candidate.signal.SIGKILL,owned_candidate.signal.SIGKILL])
            self.assertEqual([event['state'] for event in owner.events if event['kind'].endswith('wait')],['timeout','completed'])
            self.assertEqual(owner.events[-1]['kind'],'candidate-cleanup-wait')

    def test_candidate_audit_checks_wait_and_observed_descendants(self):
        import hashlib
        import audit_candidate_owner as audit
        with tempfile.TemporaryDirectory() as temp:
            out=Path(temp).resolve();candidate=out/'candidate';candidate.mkdir();binary=candidate/'som';binary.write_bytes(b'binary')
            digest=hashlib.sha256(b'binary').hexdigest();pid=1234;parent=123
            argv=[str(binary),'serve','--assets',str(candidate/'assets'),'--port','18919']
            (candidate/'runtime.json').write_text(json.dumps({'pid':pid,'port':18919,'argv':argv,'binary_sha256':digest}))
            (candidate/'build.json').write_text(json.dumps({'binary_sha256':digest}))
            events=[{'kind':'candidate-start','pid':pid,'parent_pid':parent,'argv':argv,'cwd':str(candidate)},
                    {'kind':'preparer-exit','exit_code':0}]
            def observation(name,command,code,data):
                (out/(name+'.stdout')).write_bytes(data);(out/(name+'.stderr')).write_bytes(b'')
                events.append({'kind':'os-command','name':name,'argv':command,'exit_code':code,
                               'stdout_sha256':hashlib.sha256(data).hexdigest(),'stderr_sha256':hashlib.sha256(b'').hexdigest()})
            ps=['/bin/ps','-ww','-p',str(pid),'-o','pid=,ppid=,pgid=,command='];table=['/bin/ps','-axo','pid=,ppid=,pgid=,command=']
            listener=['/usr/sbin/lsof','-nP','-iTCP:18919','-sTCP:LISTEN','-t']
            observation('owner-candidate-ps',ps,0,(f'{pid} {parent} {pid} '+' '.join(argv)+'\n').encode())
            observation('owner-candidate-cwd',['/usr/sbin/lsof','-a','-p',str(pid),'-d','cwd','-Fn'],0,f'p{pid}\nn{candidate}\n'.encode())
            observation('owner-candidate-listener',listener,0,b'1234\n')
            observation('owner-candidate-table-before',table,0,b'1234 123 1234 candidate\n1235 1234 1234 worker\n')
            ident_argv=['/bin/ps','-ww','-p',str(pid),'-o','pid=,ppid=,pgid=,stat=,command=']
            identity=(f'{pid} {parent} {pid} S '+' '.join(argv)+'\n').encode()
            (out/'candidate-signal-identity.stdout').write_bytes(identity);(out/'candidate-signal-identity.stderr').write_bytes(b'')
            events.append({'kind':'candidate-signal-identity','pid':pid,'parent_pid':parent,'pgid':pid,'argv':ident_argv,'exit_code':0,'state':'S',
                           'stdout_sha256':hashlib.sha256(identity).hexdigest(),'stderr_sha256':hashlib.sha256(b'').hexdigest()})
            group_argv=['/bin/ps','-axo','pid=,ppid=,pgid=,stat=,command=']
            def group_observation(stage,data,count):
                (out/('candidate-group-'+stage+'.stdout')).write_bytes(data);(out/('candidate-group-'+stage+'.stderr')).write_bytes(b'')
                events.append({'kind':'candidate-group-observation','stage':stage,'argv':group_argv,'exit_code':0,'live_count':count,
                               'stdout_sha256':hashlib.sha256(data).hexdigest(),'stderr_sha256':hashlib.sha256(b'').hexdigest()})
            group_observation('before-term',b'1234 123 1234 S candidate\n1235 1234 1234 S worker\n',2)
            events.append({'kind':'candidate-signal','pid':pid,'signal':'TERM'})
            group_observation('before-kill',b'1234 123 1234 Z <defunct>\n',0)
            events.append({'kind':'candidate-signal','pid':pid,'signal':'KILL','group':'no-live-members'})
            events.append({'kind':'candidate-wait','pid':pid,'state':'completed','exit_code':-15})
            observation('owner-candidate-table-after',table,0,b'1 0 1 init\n')
            observation('owner-candidate-listener-after',listener,1,b'')
            def save(): (out/'candidate-owner.json').write_text(json.dumps({'version':'som.m6-owned-candidate.v1','product_acceptance_passed':False,'events':events}))
            save();self.assertEqual(audit.audit(out,candidate)['observed_processes_absent'],2)
            wait=next(e for e in events if e['kind']=='candidate-wait');wait['state']='timeout';save()
            with self.assertRaises(ValueError):audit.audit(out,candidate)
            wait['state']='completed';after=out/'owner-candidate-table-after.stdout';after.write_bytes(b'1 0 1 init\n1235 1 1234 worker\n')
            next(e for e in events if e.get('name')=='owner-candidate-table-after')['stdout_sha256']=hashlib.sha256(after.read_bytes()).hexdigest();save()
            with self.assertRaises(ValueError):audit.audit(out,candidate)

    def test_real_macos_owned_candidate_audit(self):
        import hashlib, socket, subprocess, time
        import owned_candidate, audit_candidate_owner
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp).resolve();candidate=root/'candidate';candidate.mkdir();(candidate/'assets').mkdir()
            source=root/'fixture.c';source.write_text('#include <sys/types.h>\n#include <sys/socket.h>\n#include <netinet/in.h>\n#include <unistd.h>\n#include <signal.h>\n#include <string.h>\nint main(void){signal(SIGPIPE,SIG_IGN);int s=socket(AF_INET,SOCK_STREAM,0);int v=1;setsockopt(s,SOL_SOCKET,SO_REUSEADDR,&v,sizeof(v));struct sockaddr_in a={0};a.sin_family=AF_INET;a.sin_port=htons(18919);a.sin_addr.s_addr=htonl(INADDR_LOOPBACK);if(bind(s,(void*)&a,sizeof(a))||listen(s,4))return 2;for(;;){int c=accept(s,0,0);char buf[4096];if(c>=0){if(read(c,buf,sizeof(buf))>0){const char*r="HTTP/1.1 200 OK\\r\\nContent-Type: application/json\\r\\nContent-Length: 15\\r\\nConnection: close\\r\\n\\r\\n{\\\"status\\\":\\\"ok\\\"}";write(c,r,strlen(r));}close(c);}}}\n')
            binary=candidate/'som';subprocess.run(['cc',str(source),'-o',str(binary)],check=True,timeout=30)
            preparer=root/'preparer.py'
            preparer.write_text('import subprocess,sys\nfrom pathlib import Path\nc=Path(sys.argv[1])\nsubprocess.Popen([str(c/"som"),"serve","--assets",str(c/"assets"),"--port","18919"],cwd=c,start_new_session=True)\n')
            owner=owned_candidate.CandidateOwner(root,candidate,18919);env=dict(os.environ)
            try:
                self.assertEqual(owner.run_preparer(preparer,[str(candidate)],env,timeout=10),0)
                for _ in range(100):
                    try:
                        with socket.create_connection(('127.0.0.1',18919),timeout=.1):break
                    except OSError:time.sleep(.05)
                else:self.fail('fake candidate failed to listen')
                digest=hashlib.sha256(binary.read_bytes()).hexdigest()
                revision='a'*40;(candidate/'source.tar').write_bytes(b'archive');(candidate/'assets/manifest.json').write_bytes(b'assets')
                (root/'source.rs').write_bytes(b'source')
                source_info={'commit':revision,'archive_sha256':hashlib.sha256(b'archive').hexdigest(),
                             'files':{'source.rs':hashlib.sha256(b'source').hexdigest()}}
                (candidate/'source.json').write_text(json.dumps(source_info));source_sha=hashlib.sha256((candidate/'source.json').read_bytes()).hexdigest()
                runtime={'pid':owner.process.pid,'argv':owner.argv,'port':18919,'binary_sha256':digest,
                         'source_sha256':source_sha,'accepted':False,'health':{'status':'ok'}}
                build={'binary_sha256':digest,'source_sha256':source_sha,'complete':True,'source_unchanged':True,
                       'tree_before':{'head':revision},'asset_manifest_sha256':hashlib.sha256(b'assets').hexdigest()}
                (candidate/'runtime.json').write_text(json.dumps(runtime));(candidate/'build.json').write_text(json.dumps(build))
                import mac_rust006_extra as extra, audit_mac_rust006_extra as independent
                from unittest import mock
                probe=root/'probe';(probe/'logs').mkdir(parents=True)
                before=extra.observe(probe,'before',runtime);after=extra.observe(probe,'after',runtime)
                collector={'version':'som.m6-mac-rust006-extra.v1','candidate_revision':revision,'candidate_binary_sha256':digest,
                           'product_acceptance_passed':False,'candidate_before':before,'candidate_after':after}
                (probe/'collector.json').write_text(json.dumps(collector))
                with mock.patch.object(independent,'REPO',root):
                    self.assertEqual(independent.audit_candidate(probe,candidate,revision)['pid'],owner.process.pid)
                diag=subprocess.run(['/bin/ps','-ww','-p',str(owner.process.pid),'-o','pid=,ppid=,pgid=,stat=,command='],capture_output=True,text=True)
                print('FAKE_IDENTITY',repr(diag.stdout),repr(diag.stderr),diag.returncode,'sigchld',repr(__import__('signal').getsignal(__import__('signal').SIGCHLD)),flush=True)
                cleanup=hosted.stop_owned_candidate(root,env,owner)
                print('FAKE_CLEANUP_METADATA',json.dumps(cleanup,sort_keys=True),flush=True)
                self.assertTrue(cleanup['ownership_verified']);self.assertTrue(cleanup['direct_wait']['completed'])
                self.assertTrue(cleanup['stopped'])
                evidence=audit_candidate_owner.audit(root,candidate)
                self.assertEqual(evidence['candidate_pid'],owner.process.pid)
                self.assertEqual(evidence['outside_observed_group'],'unverified')
                result=subprocess.run([sys.executable,str(Path(audit_candidate_owner.__file__)),'--out',str(root),
                                       '--candidate',str(candidate),'--audit',str(root/'owner-audit.json')],
                                      capture_output=True,check=False,timeout=30)
                self.assertEqual(result.returncode,0)
                report=json.loads((root/'owner-audit.json').read_bytes())
                self.assertEqual(report['status'],'observed');self.assertEqual(report['candidate_owned_group_cleanup'],'passed')
                self.assertEqual(report['observed']['outside_observed_group'],'unverified')
                self.assertFalse(report['product_acceptance_passed'])
                (root/'owner-candidate-listener.stdout').write_bytes(b'mutated')
                bad=subprocess.run([sys.executable,str(Path(audit_candidate_owner.__file__)),'--out',str(root),
                                    '--candidate',str(candidate),'--audit',str(root/'owner-audit-bad.json')],
                                   capture_output=True,check=False,timeout=30)
                self.assertEqual(bad.returncode,1)
                bad_report=json.loads((root/'owner-audit-bad.json').read_bytes())
                self.assertEqual(bad_report['candidate_owned_group_cleanup'],'unrun')
                self.assertFalse(bad_report['product_acceptance_passed'])
            finally:
                if owner.process is not None and owner.process.poll() is None:
                    owner.process.terminate();owner.process.wait(timeout=10)

    def test_macos_orphan_and_term_ignoring_descendant_cleanup(self):
        import subprocess,time,signal
        import owned_candidate
        csrc="""#include <sys/types.h>
#include <unistd.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
int main(void){pid_t p=fork();if(p<0)return 2;if(p==0){signal(SIGTERM,SIG_IGN);FILE*f=fopen(getenv("FIXTURE_PID"),"w");if(!f)_exit(3);fprintf(f,"%d",getpid());fclose(f);for(;;)pause();}if(getenv("FIXTURE_ORPHAN")){sleep(2);_exit(0);}for(;;)pause();}
"""
        for mode in ('orphan','term'):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as temp:
                root=Path(temp).resolve();candidate=root/'candidate';candidate.mkdir();(candidate/'assets').mkdir()
                source=root/'fixture.c';source.write_text(csrc);binary=candidate/'som'
                subprocess.run(['cc',str(source),'-o',str(binary)],check=True,timeout=30)
                preparer=root/'preparer.py';preparer.write_text('import subprocess,sys,time\nfrom pathlib import Path\nc=Path(sys.argv[1])\np=subprocess.Popen([str(c/"som"),"serve","--assets",str(c/"assets"),"--port","18919"],cwd=c,start_new_session=True)\nif sys.argv[2]=="orphan":\n time.sleep(2.5)\n if p.poll() is None or p.returncode is not None:raise SystemExit(9)\n raise SystemExit(3)\n')
                owner=owned_candidate.CandidateOwner(root,candidate,18919);env=dict(os.environ,FIXTURE_PID=str(root/'child.pid'))
                if mode=='orphan':env['FIXTURE_ORPHAN']='1'
                try:
                    self.assertEqual(owner.run_preparer(preparer,[str(candidate),mode],env,timeout=10),3 if mode=='orphan' else 0)
                    for _ in range(100):
                        if (root/'child.pid').is_file():break
                        time.sleep(.05)
                    else:self.fail('descendant did not start')
                    child=int((root/'child.pid').read_text())
                    if mode=='orphan':
                        for _ in range(100):
                            state=subprocess.run(['/bin/ps','-p',str(owner.process.pid),'-o','stat='],capture_output=True,text=True).stdout.strip()
                            if state.startswith('Z'):break
                            time.sleep(.05)
                        else:self.fail('leader did not become an unreaped zombie')
                    diag=subprocess.run(['/bin/ps','-ww','-p',str(owner.process.pid),'-o','pid=,ppid=,pgid=,stat=,command='],capture_output=True,text=True)
                    print('FAKE_ORPHAN_IDENTITY',mode,repr(diag.stdout),repr(diag.stderr),diag.returncode,'sigchld',repr(signal.getsignal(signal.SIGCHLD)),flush=True)
                    self.assertIsNone(owner.process.returncode)
                    owner.wait_after_signal(timeout=10,grace=.1)
                    signals=[event['signal'] for event in owner.events if event['kind']=='candidate-signal']
                    self.assertEqual(signals,['TERM','KILL'])
                    for _ in range(100):
                        result=subprocess.run(['/bin/ps','-p',str(child),'-o','stat='],capture_output=True,text=True)
                        if result.returncode!=0 or result.stdout.strip().startswith('Z'):break
                        time.sleep(.05)
                    else:self.fail('TERM-ignoring descendant is still running')
                finally:
                    failure_in_flight=sys.exc_info()[0] is not None
                    # Test-only recovery: verify the still-unreaped direct child
                    # through ps before ever signalling its group, including zombies.
                    if owner.process is not None and owner.process.returncode is None:
                        pid=owner.process.pid
                        try:
                            result=subprocess.run(['/bin/ps','-ww','-p',str(pid),'-o','pid=,ppid=,pgid=,stat=,command='],capture_output=True,text=True,timeout=10)
                            fields=result.stdout.strip().split(None,4)
                            safe=(result.returncode==0 and len(fields)==5 and fields[:3]==[str(pid),str(os.getpid()),str(pid)]
                                  and signal.getsignal(signal.SIGCHLD)==signal.SIG_DFL
                                  and (fields[3].startswith('Z') or fields[4]==' '.join(owner.argv)))
                            if not safe:raise AssertionError('fixture cleanup identity is unverified')
                            try:os.killpg(pid,signal.SIGKILL)
                            except ProcessLookupError:pass
                            owner.process.wait(timeout=10)
                        except (OSError,subprocess.SubprocessError,AssertionError) as cleanup_error:
                            print('fixture cleanup unverified: '+type(cleanup_error).__name__,file=sys.stderr)
                            if not failure_in_flight:raise

    def test_identity_evidence_write_failure_still_cleans_owned_child(self):
        import subprocess
        import owned_candidate
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp).resolve();candidate=root/'candidate';candidate.mkdir();(candidate/'assets').mkdir()
            source=root/'fixture.c';source.write_text('#include <unistd.h>\nint main(void){for(;;)pause();}\n')
            binary=candidate/'som';subprocess.run(['cc',str(source),'-o',str(binary)],check=True,timeout=30)
            preparer=root/'preparer.py';preparer.write_text('import subprocess,sys\nfrom pathlib import Path\nc=Path(sys.argv[1])\nsubprocess.Popen([str(c/"som"),"serve","--assets",str(c/"assets"),"--port","18919"],cwd=c,start_new_session=True)\n')
            owner=owned_candidate.CandidateOwner(root,candidate,18919)
            try:
                self.assertEqual(owner.run_preparer(preparer,[str(candidate)],dict(os.environ),timeout=10),0)
                (root/'candidate-signal-identity.stdout').write_bytes(b'existing')
                with self.assertRaises(owned_candidate.OwnershipError):owner.wait_after_signal(timeout=10,grace=.05)
                self.assertIsInstance(owner.process.returncode,int)
                self.assertEqual((root/'candidate-signal-identity.stdout').read_bytes(),b'existing')
                self.assertTrue(any(e['kind']=='candidate-evidence-error' for e in owner.events))
                self.assertTrue(any(e['kind']=='candidate-wait' and e['state']=='completed' for e in owner.events))
            finally:
                if owner.process is not None and owner.process.returncode is None:
                    owner.process.terminate();owner.process.wait(timeout=10)

    def test_preparer_child_remains_owned_and_has_real_wait(self):
        import subprocess
        import owned_candidate
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp).resolve();candidate=root/'candidate';candidate.mkdir();(candidate/'assets').mkdir()
            binary=candidate/'som';source=root/'fixture.c';source.write_text('#include <unistd.h>\nint main(void){for(;;)pause();}\n');subprocess.run(['cc',str(source),'-o',str(binary)],check=True,timeout=30)
            preparer=root/'preparer.py'
            preparer.write_text('import subprocess,sys\nfrom pathlib import Path\nc=Path(sys.argv[1])\np=subprocess.Popen([str(c/"som"),"serve","--assets",str(c/"assets"),"--port","18919"],cwd=c,start_new_session=True)\nif p.poll() is not None:raise SystemExit(4)\nprint("prepared")\n')
            owner=owned_candidate.CandidateOwner(root,candidate,18919)
            try:
                self.assertEqual(owner.run_preparer(preparer,[str(candidate)],dict(os.environ),timeout=10),0)
                pid=owner.process.pid
                parent=subprocess.check_output(['/bin/ps','-p',str(pid),'-o','ppid='],text=True).strip()
                self.assertEqual(parent,str(os.getpid()))
                self.assertEqual((root/'prepare.stdout').read_text(),'prepared\n')
                diag=subprocess.run(['/bin/ps','-ww','-p',str(pid),'-o','pid=,ppid=,pgid=,stat=,command='],capture_output=True,text=True)
                print('FAKE_SHEBANG_IDENTITY',repr(diag.stdout),repr(diag.stderr),diag.returncode,flush=True)
                result=owner.wait_after_signal(timeout=10)
                self.assertIsInstance(result,int);owner.save()
                receipt=json.loads((root/'candidate-owner.json').read_bytes())
                waits=[e for e in receipt['events'] if e['kind']=='candidate-wait']
                self.assertEqual(len(waits),1);self.assertEqual(waits[0]['pid'],pid)
                self.assertEqual(waits[0]['state'],'completed');self.assertEqual(waits[0]['exit_code'],result)
            finally:
                if owner.process is not None and owner.process.poll() is None:
                    owner.process.terminate();owner.process.wait(timeout=10)

class DerivedInstallTests(unittest.TestCase):
    def test_exclusive_install_rejects_collision_and_tampering(self):
        import hashlib
        import install_derived_inputs as installer
        import reconstruct_historical_responses as recon
        def h(data):return hashlib.sha256(data).hexdigest()
        for fault in ('none','collision','tamper'):
            with self.subTest(fault=fault), tempfile.TemporaryDirectory() as temp:
                root=Path(temp).resolve();inputs=root/'inputs';derived=root/'derived'
                for directory in (inputs/'data',derived/'data'):directory.mkdir(parents=True)
                (derived/'data/a.raw').write_bytes(b'raw');(derived/'data/a.json').write_bytes(b'json')
                row={'historical_raw_path':'data/a.raw','historical_raw_sha256':h(b'raw'),
                     'historical_json_path':'data/a.json','historical_json_sha256':h(b'json')}
                raw=json.dumps({'reconstructed_cases':[row]}).encode()
                if fault=='collision':(inputs/'data/a.json').write_bytes(b'existing')
                if fault=='tamper':(derived/'data/a.json').write_bytes(b'changed')
                if fault=='none':
                    self.assertEqual(installer.install(inputs,derived,raw,map_sha=h(raw),expected_pairs=1),2)
                    self.assertEqual((inputs/'data/a.raw').read_bytes(),b'raw')
                else:
                    with self.assertRaises(recon.Refused):installer.install(inputs,derived,raw,map_sha=h(raw),expected_pairs=1)
                    self.assertFalse((inputs/'data/a.raw').exists())

class SupplementProofTests(unittest.TestCase):
    def test_pair_exact_json_types_and_tamper(self):
        import base64
        import audit_supplement_provision as proof
        raw=b'{"n":1,"ok":true}'; encoded=base64.b64encode(raw)+b'\n'
        document=json.dumps({'http_status':200,'body':{'n':1,'ok':True}}).encode()
        proof.verify_pair(encoded,document)
        for altered in (json.dumps({'http_status':200,'body':{'n':True,'ok':True}}).encode(),
                        json.dumps({'http_status':201,'body':{'n':1,'ok':True}}).encode()):
            with self.assertRaises(ValueError):proof.verify_pair(encoded,altered)
        for altered in (encoded+b' ',encoded[:-1],b'%%%\n'):
            with self.assertRaises(ValueError):proof.verify_pair(altered,document)

class ProductHTTPBytesTests(unittest.TestCase):
    def test_real_suite_transport_seam(self):
        import importlib, types
        from unittest import mock
        import product_http_recorder
        behavior=Path(__file__).resolve().parents[2]/'behavior'
        sys.path.insert(0,str(behavior))
        try: suite=importlib.import_module('suite')
        finally:sys.path.pop(0)
        def transport(base,method,path,body,content_type):
            return 404,'application/json',b'{"detail":"no"}'
        def script(owner):
            owner.call('not_found','missing','GET',['/things/42'],status=404,expect={'detail':'no'})
        prompt={'version':'som.prompt.v1','resource':{'table':'things','path':'/things','fields':[]}}
        with tempfile.TemporaryDirectory() as temp, mock.patch.object(suite,'http_send',transport), \
             mock.patch.object(suite.Run,'script',script), mock.patch.object(suite.Run,'strict',script):
            out=Path(temp).resolve()/'http'; observer=product_http_recorder.ProductRecorder(out,suite,require_owner=False)
            def parity_case(case):
                python=suite.run(prompt,'http://127.0.0.1:19021')
                rust=suite.run(prompt,'http://127.0.0.1:19022'); rust.strict()
                return python,rust
            with mock.patch.object(suite,'http_send',observer.send), mock.patch.object(suite,'run',observer.run), \
                 mock.patch.object(suite.Run,'strict',lambda owner:observer.strict(owner)):
                python,rust=parity_case({'id':'real-suite'})
            self.assertTrue(observer.finish())
            rows=json.loads((out/'manifest.json').read_bytes())['calls']
            self.assertEqual([(r['case'],r['target'],r['sequence']) for r in rows],
                             [('real-suite','python',1),('real-suite','rust',1),('real-suite','rust',2)])
            self.assertEqual(python.raw[0]['response'],'{"detail":"no"}')
            self.assertEqual(len(rust.raw),2)

    def test_real_suite_fallback_put_capture(self):
        import importlib
        from unittest import mock
        import product_http_recorder
        behavior=Path(__file__).resolve().parents[2]/'behavior';sys.path.insert(0,str(behavior))
        try:suite=importlib.import_module('suite')
        finally:sys.path.pop(0)
        def send(base,method,path,body,content_type):
            status=404 if method=='GET' else 405
            return status,'application/json',json.dumps({'detail':'Not Found' if status==404 else 'Method Not Allowed'}).encode()
        def script(owner):
            table=owner.prof.tables[0]; token=owner.norm.token(table,12,True)
            owner.fallbacks(table,None,token,owner.coll(table,None))
        prompt={'version':'som.prompt.v1','resource':{'table':'things','path':'/things','fields':[]}}
        with tempfile.TemporaryDirectory() as temp, mock.patch.object(suite,'http_send',send), mock.patch.object(suite.Run,'script',script):
            out=Path(temp).resolve()/'http';observer=product_http_recorder.ProductRecorder(out,suite,require_owner=False)
            def parity_case(case):return suite.run(prompt,'http://127.0.0.1:19021')
            with mock.patch.object(suite,'http_send',observer.send), mock.patch.object(suite,'run',observer.run):
                result=parity_case({'id':'fallback'})
            self.assertTrue(observer.finish()); rows=json.loads((out/'manifest.json').read_bytes())['calls']
            put=next(row for row in rows if row['method']=='PUT')
            self.assertEqual(put['status'],405);self.assertEqual(put['step'],'things: put on the collection')
            self.assertEqual((out/put['request_file']).read_bytes(),b'{}')
            self.assertEqual(json.loads((out/put['response_file']).read_bytes()),{'detail':'Method Not Allowed'})
            self.assertEqual(result.raw[1]['method'],'PUT')

    def test_concurrent_case_sequences_and_exact_bytes(self):
        import concurrent.futures, hashlib, http.client, http.server, threading, types
        from unittest import mock
        import product_http_recorder
        class Handler(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                data=self.rfile.read(int(self.headers['Content-Length']))
                self.send_response(200); self.send_header('Content-Type','application/octet-stream'); self.end_headers()
                self.wfile.write(data[::-1])
            def log_message(self,*args): pass
        server=http.server.ThreadingHTTPServer(('127.0.0.1',0),Handler)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        base='http://127.0.0.1:'+str(server.server_port)
        def send(base,method,path,body,content_type):
            conn=http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=5)
            try:
                conn.request(method,path,body,{'Content-Type':content_type}); response=conn.getresponse()
                return response.status,response.getheader('content-type'),response.read()
            finally:conn.close()
        suite=types.SimpleNamespace()
        class Run:
            def call(self,step,category):
                return suite.http_send(base,'POST','/data',b'\x00\xff\x01','application/octet-stream')
            def strict(self): self.call('strict','bounds')
        def run(prompt,base,send=None):
            result=Run(); result.call('main','crud'); return result
        suite.Run=Run; suite.run=run; suite.http_send=send
        try:
            with tempfile.TemporaryDirectory() as temp:
                out=Path(temp).resolve()/'http'; observer=product_http_recorder.ProductRecorder(out,suite,require_owner=False)
                def parity_case(case):
                    python=suite.run({},base); rust=suite.run({},base); rust.strict()
                with mock.patch.object(suite,'http_send',observer.send), mock.patch.object(suite,'run',observer.run), \
                     mock.patch.object(Run,'strict',lambda owner:observer.strict(owner)):
                    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
                        list(pool.map(parity_case,[{'id':'case-'+str(i)} for i in range(4)]))
                self.assertTrue(observer.finish())
                result=json.loads((out/'manifest.json').read_bytes());self.assertEqual(len(result['calls']),12)
                groups={}
                for row in result['calls']:
                    key=(row['case'],row['target']);groups.setdefault(key,[]).append(row['sequence'])
                    request=(out/row['request_file']).read_bytes(); response=(out/row['response_file']).read_bytes()
                    self.assertEqual(request,b'\x00\xff\x01');self.assertEqual(response,b'\x01\xff\x00')
                    self.assertEqual(row['request_sha256'],hashlib.sha256(request).hexdigest())
                    self.assertEqual(row['response_sha256'],hashlib.sha256(response).hexdigest())
                self.assertEqual(len(groups),8)
                for (case,target),seq in groups.items():self.assertEqual(seq,[1] if target=='python' else [1,2])
                import audit_mac_parity as audit
                transcripts={}
                for row in result['calls']:
                    key=(row['case'],row['target'])
                    req=(out/row['request_file']).read_bytes().decode('utf-8','replace')
                    res=(out/row['response_file']).read_bytes().decode('utf-8','replace')
                    transcripts.setdefault(key,[]).append(({'step':row['step'],'method':row['method'],'path':row['path'],
                        'content_type':row['content_type'],'body':req,'status':row['status'],
                        'response_content_type':row['response_content_type'],'response':res},{'step':row['step'],'category':row['category']}))
                self.assertEqual(audit.audit_product_http(out,transcripts,{case:{} for case,_ in groups},require_owners=False,check_normalization=False)['calls'],12)
                first=result['calls'][0]; tamper=out/first['response_file']; original=tamper.read_bytes()
                tamper.write_bytes(original+b'!')
                with self.assertRaises(ValueError):audit.audit_product_http(out,transcripts,{case:{} for case,_ in groups},require_owners=False,check_normalization=False)
                tamper.write_bytes(original)
                key=(first['case'],first['target']);transcripts[key][0][0]['status']=201
                with self.assertRaises(ValueError):audit.audit_product_http(out,transcripts,{case:{} for case,_ in groups},require_owners=False,check_normalization=False)
        finally:
            server.shutdown();server.server_close();thread.join(timeout=5)

class ParityIndependentChecks(unittest.TestCase):
    def test_join_passes_exact_request_bytes_and_target_to_oracle(self):
        import hashlib
        from unittest import mock
        import audit_mac_parity as audit
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp).resolve();(root/'calls').mkdir(); calls=[];transcripts={}
            prompt={'version':'som.prompt.v1','resource':{'table':'things','path':'/things','fields':[]}}
            for i,target in enumerate(('python','rust'),1):
                request=b'{"x":1}';response=b'{"status":"ok"}';req=f'calls/{i:06d}.request.raw';res=f'calls/{i:06d}.response.raw'
                (root/req).write_bytes(request);(root/res).write_bytes(response)
                calls.append({'id':i,'case':'case','target':target,'sequence':1,'complete':True,'base':'http://127.0.0.1:19021',
                    'request_file':req,'response_file':res,'request_sha256':hashlib.sha256(request).hexdigest(),
                    'response_sha256':hashlib.sha256(response).hexdigest(),'body_present':True,'step':'health','category':'healthz',
                    'method':'POST','path':'/healthz','content_type':'application/json','status':200,'response_content_type':'application/json'})
                record={'step':'health','method':'POST','path':'/healthz','body':request.decode(),
                    'content_type':'application/json','status':200,'response_content_type':'application/json','response':response.decode()}
                normalized={'step':'health','category':'healthz','request':{'method':'POST','path':'/healthz','body':request.decode()},
                            'status':200,'media':'application/json','body':{'status':'ok'}}
                transcripts[('case',target)]=[(record,normalized)]
            (root/'manifest.json').write_text(json.dumps({'version':'som.m6-mac-product-http.v1','calls':calls,
                'recording_errors':[],'product_acceptance_passed':False,'bytes':sum(7+15 for _ in calls)}))
            # Byte totals include the exact UTF-8 request and response lengths.
            manifest=json.loads((root/'manifest.json').read_bytes());manifest['bytes']=sum(len((root/r['request_file']).read_bytes())+len((root/r['response_file']).read_bytes()) for r in calls)
            (root/'manifest.json').write_text(json.dumps(manifest))
            with mock.patch.object(audit,'audit_product_owners',return_value=2), mock.patch.object(audit.frozen_oracle,'check') as check:
                result=audit.audit_product_http(root,transcripts,{'case':prompt})
                self.assertEqual(result['oracle_case_targets_checked'],2)
                self.assertEqual([c.kwargs['include_strict'] for c in check.call_args_list],[False,True])
                self.assertTrue(all(c.args[1][0][1]==b'{"x":1}' for c in check.call_args_list))
                (root/calls[0]['request_file']).write_bytes(b'tamper')
                with self.assertRaises(ValueError):audit.audit_product_http(root,transcripts,{'case':prompt})

    def test_partial_crud_oracle_rejects_changed_submitted_or_stored_values(self):
        import audit_product_oracle as oracle
        prompt={'version':'som.prompt.v1','resource':{'table':'items','path':'/items',
                'fields':[{'name':'name','type':'str'},{'name':'amount','type':'int'}]}}
        model=oracle.PartialOracle(prompt)
        record={'status':201,'method':'POST','path':'/items'}
        self.assertEqual(model.check(record,b'{"name":"one","amount":1}',b'{"id":7,"name":"one","amount":1}'),1)
        record.update(status=200,method='PATCH',path='/items/7')
        with self.assertRaises(ValueError):model.check(record,b'{"name":"two"}',b'{"id":7,"name":"one","amount":1}')
        with self.assertRaises(ValueError):model.check(record,b'{"name":"two"}',b'{"id":7,"name":"two","amount":2}')
        self.assertEqual(model.check(record,b'{"name":"two"}',b'{"id":7,"name":"two","amount":1}'),1)
        record['method']='GET'
        with self.assertRaises(ValueError):model.check(record,b'',b'{"id":7,"name":"wrong","amount":1}')

    def test_recompute_success_row_ids_timestamps_and_number_types(self):
        import audit_product_normalization as norm
        prompt={'version':'som.prompt.v3','resource':{'table':'things','path':'/things',
                'fields':[{'name':'weight','type':'float'},{'name':'count','type':'int'}]}}
        reader=norm.Normalizer(prompt)
        row={'id':17,'weight':3,'count':4,'created_at':'2026-01-01T00:00:00Z','updated_at':'2026-01-01T00:00:01Z'}
        expected={'id':'id#1','weight':3.0,'count':4,'created_at':'<timestamp>','updated_at':'<timestamp>'}
        record={'status':200,'path':'/things/17','method':'POST','step':'create'}
        normalized={'body':expected,'request':{'path':'/things/17'}}
        self.assertTrue(reader.check(record,normalized,json.dumps(row).encode()))
        changed=dict(row,weight=4)
        with self.assertRaises(ValueError):reader.check(record,normalized,json.dumps(changed).encode())
        changed=dict(row,updated_at='2025-01-01T00:00:00Z')
        with self.assertRaises(ValueError):reader.check(record,normalized,json.dumps(changed).encode())
        with self.assertRaises(ValueError):reader.check(record,normalized,b'{"invalid":"\xff"}')
        record.update(method='PATCH',step='things: empty patch')
        normalized['request']['path']='/things/{id#1}'
        self.assertTrue(reader.check(record,normalized,json.dumps(row).encode()))
        row['updated_at']='2026-01-01T00:00:02Z'
        with self.assertRaises(ValueError):reader.check(record,normalized,json.dumps(row).encode())
        record['step']='things: patch weight'
        self.assertTrue(reader.check(record,normalized,json.dumps(row).encode()))
        with self.assertRaises(ValueError):reader.check(record,normalized,json.dumps(row).encode())

    def test_recompute_parent_child_batch_and_foreign_key(self):
        import audit_product_normalization as norm
        prompt={'version':'som.prompt.v5','resources':{'parent':{'table':'parents','path':'/parents','fields':[]},
                'child':{'table':'children','path':'/children','fields':[]}},'relation':{'foreign_key':'parent_id'}}
        reader=norm.Normalizer(prompt); stamp={'created_at':'2026-01-01T00:00:00Z','updated_at':'2026-01-01T00:00:00Z'}
        body={'parent':{'id':50,**stamp},'children':[{'id':80,'parent_id':50,**stamp}]}
        expected={'parent':{'id':'id#1','created_at':'<timestamp>','updated_at':'<timestamp>'},
                  'children':[{'id':'id#1','parent_id':'id#1','created_at':'<timestamp>','updated_at':'<timestamp>'}]}
        self.assertTrue(reader.check({'status':201,'path':'/parents/with-children','method':'POST','step':'batch'}, {'body':expected,'request':{'path':'/parents/with-children'}},json.dumps(body).encode()))
        body['children'][0]['parent_id']=99
        with self.assertRaises(ValueError):reader.check({'status':201,'path':'/parents/with-children','method':'POST','step':'batch'}, {'body':expected,'request':{'path':'/parents/with-children'}},json.dumps(body).encode())

    def test_validation_error_raw_response_and_request(self):
        import audit_mac_parity as audit
        raw={'step':'bad input','method':'POST','body':'{}','content_type':'application/json',
             'status':422,'response_content_type':'Application/JSON; charset=utf-8',
             'response':json.dumps({'detail':[{'type':'bad','loc':['body','name'],'msg':'invalid'}]})}
        normalized={'step':'bad input','request':{'method':'POST','body':'{}'},'status':422,
                    'media':'application/json','body':{'422_loc0':['body']}}
        self.assertEqual(audit.check_record(raw,normalized),'validation-error')
        for key, value in (('body','different'),('status',409),('media','text/plain')):
            clone=json.loads(json.dumps(normalized))
            if key=='body': clone['request']['body']=value
            else: clone[key]=value
            with self.subTest(key=key), self.assertRaises(ValueError): audit.check_record(raw,clone)
        clone=json.loads(json.dumps(normalized)); clone['body']={'422_loc0':['query']}
        with self.assertRaises(ValueError): audit.check_record(raw,clone)

    def test_product_owner_raw_listener_and_wait(self):
        import hashlib
        import audit_mac_parity as audit
        with tempfile.TemporaryDirectory() as temp:
            life=Path(temp).resolve(); http=life/'product-http';http.mkdir()
            number=[0]
            def command(argv,exit_code,stdout):
                number[0]+=1;stem='os-%04d'%number[0]; out=(stem+'.stdout');err=(stem+'.stderr')
                (life/out).write_bytes(stdout);(life/err).write_bytes(b'')
                return {'argv':argv,'exit_code':exit_code,'stdout':{'file':out,'sha256':hashlib.sha256(stdout).hexdigest()},
                        'stderr':{'file':err,'sha256':hashlib.sha256(b'').hexdigest()}}
            pid=1234;port=19021
            ps=['/bin/ps','-axo','pid=,ppid=,pgid=,command=']
            listener=['/usr/sbin/lsof','-nP',f'-iTCP:{port}','-sTCP:LISTEN','-t']
            start={'kind':'process','phase':'product-ready','pid':pid,'port':port,
                   'table':command(ps,0,b'1234 1 1234 python -m uvicorn\n1235 1234 1234 worker\n'),'listener':command(listener,0,b'1234\n'),
                   'cwd':command(['/usr/sbin/lsof','-a','-p','1234','-d','cwd','-Fn'],0,b'p1234\nn/tmp/py_example\n')}
            stop={'kind':'process','phase':'product-stopped','pid':pid,'port':port,
                  'table':command(ps,0,b'1 0 1 init\n'),'listener':command(listener,1,b'')}
            events=[{'kind':'child-start','identity':7,'pid':pid,'cwd':'/tmp/py_example',
                     'argv':['python','-m','uvicorn','app','--host','127.0.0.1','--port','19021']},start,
                    {'kind':'child-wait','identity':7,'pid':pid,'state':'completed','exit_code':-15},stop]
            (life/'receipt.json').write_text(json.dumps({'version':'som.m6-mac-parity-capture.v1','recording_errors':[],'events':events}))
            owner={'id':1,'case':'example','target':'python','base':'http://127.0.0.1:19021','pid':pid,'start_event':1,'stop_event':3}
            manifest={'owners':[owner]};groups={('example','python'):[{'owner':1,'base':owner['base']}]}
            self.assertEqual(audit.audit_product_owners(http,manifest,groups),1)
            (life/start['listener']['stdout']['file']).write_bytes(b'9999\n')
            with self.assertRaises(ValueError):audit.audit_product_owners(http,manifest,groups)
            (life/start['listener']['stdout']['file']).write_bytes(b'1234\n')
            table=life/stop['table']['stdout']['file']; original=table.read_bytes(); table.write_bytes(original+b'1235 1 1234 worker\n')
            stop['table']['stdout']['sha256']=hashlib.sha256(table.read_bytes()).hexdigest()
            (life/'receipt.json').write_text(json.dumps({'version':'som.m6-mac-parity-capture.v1','recording_errors':[],'events':events}))
            with self.assertRaises(ValueError):audit.audit_product_owners(http,manifest,groups)
            table.write_bytes(original);stop['table']['stdout']['sha256']=hashlib.sha256(original).hexdigest()
            events[2]['state']='unverified';(life/'receipt.json').write_text(json.dumps({'version':'som.m6-mac-parity-capture.v1','recording_errors':[],'events':events}))
            with self.assertRaises(ValueError):audit.audit_product_owners(http,manifest,groups)

    def test_macos_product_process_actual_listener_and_wait(self):
        import socket, subprocess, time
        from unittest import mock
        import audit_mac_parity as audit
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp).resolve(); life=root/'lifecycle';life.mkdir();http=life/'product-http';http.mkdir()
            cwd=root/'rs_live_case';cwd.mkdir(); executable=cwd/'som_app'
            with socket.socket() as probe:
                probe.bind(('127.0.0.1',0));port=probe.getsockname()[1]
            executable.write_text('#!'+sys.executable+'\nimport http.server\nhttp.server.HTTPServer(("127.0.0.1",'+str(port)+'),http.server.BaseHTTPRequestHandler).serve_forever()\n')
            executable.chmod(0o755)
            observer=parity.TrackingRecorder(life)
            with mock.patch.object(subprocess,'Popen',observer.popen):
                proc=observer.popen([str(executable)],cwd=str(cwd),stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
                try:
                    for _ in range(100):
                        try:
                            with socket.create_connection(('127.0.0.1',port),timeout=.1): break
                        except OSError:time.sleep(.05)
                    else:self.fail('fixture process did not listen')
                    observer.product_snapshot(proc.pid,'product-ready',port);start=len(observer.events)-1
                finally:
                    proc.terminate();proc.wait(timeout=10)
                observer.product_snapshot(proc.pid,'product-stopped',port);stop=len(observer.events)-1
            (life/'receipt.json').write_text(json.dumps({'version':'som.m6-mac-parity-capture.v1','recording_errors':observer.errors,'events':observer.events}))
            base='http://127.0.0.1:'+str(port)
            owner={'id':1,'case':'live-case','target':'rust','base':base,'pid':proc.pid,'start_event':start,'stop_event':stop}
            self.assertEqual(audit.audit_product_owners(http,{'owners':[owner]},{('live-case','rust'):[{'owner':1,'base':base}]}),1)

    def test_parity_imports_have_matching_source_inventory(self):
        import audit_mac_parity as audit
        expected={'som-rs/tests/rust-output-v1/rust_profile.py','som-rs/tests/rust-output-v1/rust_profile_phase2.py',
                  'som-rs/scripts/rust_output_acceptance/python_replay.py','som-rs/scripts/compiler_acceptance/qa_harness.py',
                  'som-rs/scripts/compiler_acceptance/paths.py'}
        self.assertTrue(expected.issubset(set(audit.PARITY_SOURCES)))
        self.assertEqual(audit.PARITY_SOURCES,parity.PARITY_SOURCES)
        import subprocess
        with tempfile.TemporaryDirectory() as temp:
            repo=Path(temp).resolve();subprocess.run(['git','init','-q',str(repo)],check=True)
            for name in expected:
                target=repo/name;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(b'original\n')
            subprocess.run(['git','-C',str(repo),'add','.'],check=True)
            subprocess.run(['git','-C',str(repo),'-c','user.name=Fixture','-c','user.email=fixture@example.invalid',
                            'commit','-qm','fixture'],check=True)
            revision=subprocess.check_output(['git','-C',str(repo),'rev-parse','HEAD'],text=True).strip()
            self.assertEqual(len(audit.verify_git_sources(repo,revision,sorted(expected))),5)
            for name in expected:
                target=repo/name;target.write_bytes(b'mutated\n')
                with self.subTest(name=name), self.assertRaises(ValueError):audit.verify_git_sources(repo,revision,sorted(expected))
                target.write_bytes(b'original\n')

    def test_same_candidate_snapshots_and_binary_are_bound(self):
        import hashlib
        from unittest import mock
        import audit_mac_rust006_extra as audit
        def h(data):return hashlib.sha256(data).hexdigest()
        def write(path,data):path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(data)
        with tempfile.TemporaryDirectory() as temp:
            repo=Path(temp).resolve();candidate=repo/'candidate';candidate.mkdir();run=repo/'run';(run/'logs').mkdir(parents=True)
            binary=candidate/'som';binary.write_bytes(b'binary');write(candidate/'assets/manifest.json',b'assets')
            write(repo/'src/source.rs',b'source');write(candidate/'source.tar',b'archive')
            revision='a'*40;source={'commit':revision,'archive_sha256':h(b'archive'),'files':{'src/source.rs':h(b'source')}}
            write(candidate/'source.json',json.dumps(source).encode()); source_sha=h((candidate/'source.json').read_bytes())
            build={'binary_sha256':h(b'binary'),'complete':True,'source_unchanged':True,'tree_before':{'head':revision},
                   'source_sha256':source_sha,'asset_manifest_sha256':h(b'assets')}
            argv=[str(binary),'serve','--assets',str(candidate/'assets'),'--port','18919'];health={'status':'ok'}
            runtime={'binary_sha256':h(b'binary'),'source_sha256':source_sha,'pid':1234,'port':18919,'accepted':False,'argv':argv,'health':health}
            write(candidate/'build.json',json.dumps(build).encode());write(candidate/'runtime.json',json.dumps(runtime).encode())
            def snapshot(phase):
                commands={'ps':['/bin/ps','-ww','-p','1234','-o','pid=,ppid=,pgid=,command='],
                          'cwd':['/usr/sbin/lsof','-a','-p','1234','-d','cwd','-Fn'],
                          'listener':['/usr/sbin/lsof','-nP','-iTCP:18919','-sTCP:LISTEN','-t']}
                values={'ps':('1234 12 1234 '+' '.join(argv)+'\n').encode(),
                        'cwd':('p1234\nn'+str(candidate)+'\n').encode(),'listener':b'1234\n'}
                result={'pid':1234,'port':18919,'commands':{}}
                for name,command in commands.items():
                    row={'argv':command,'exit_code':0}
                    for stream in ('stdout','stderr'):
                        rel='logs/'+phase+'-'+name+'.'+stream;data=values[name] if stream=='stdout' else b''
                        write(run/rel,data);row[stream]=rel;row[stream+'_sha256']=h(data)
                    result['commands'][name]=row
                data=json.dumps(health).encode();rel='logs/'+phase+'-health.raw';write(run/rel,data)
                result['health']={'status':200,'file':rel,'sha256':h(data)};return result
            report={'version':'som.m6-mac-rust006-extra.v1','candidate_revision':revision,'candidate_binary_sha256':h(b'binary'),
                    'product_acceptance_passed':False,'candidate_before':snapshot('before'),'candidate_after':snapshot('after')}
            write(run/'collector.json',json.dumps(report).encode())
            with mock.patch.object(audit,'REPO',repo):
                self.assertEqual(audit.audit_candidate(run,candidate,revision)['pid'],1234)
                (run/'logs/after-listener.stdout').write_bytes(b'9999\n')
                with self.assertRaises(ValueError):audit.audit_candidate(run,candidate,revision)
                (run/'logs/after-listener.stdout').write_bytes(b'1234\n');binary.write_bytes(b'mutated')
                with self.assertRaises(ValueError):audit.audit_candidate(run,candidate,revision)

    def test_parity_sources_match_selected_git_revision(self):
        import audit_mac_parity as audit
        import subprocess
        with tempfile.TemporaryDirectory() as temp:
            repo=Path(temp).resolve()
            subprocess.run(['git','init','-q',str(repo)],check=True)
            (repo/'source.py').write_bytes(b'original\n')
            subprocess.run(['git','-C',str(repo),'add','source.py'],check=True)
            subprocess.run(['git','-C',str(repo),'-c','user.name=Fixture','-c','user.email=fixture@example.invalid',
                            'commit','-qm','fixture'],check=True)
            revision=subprocess.check_output(['git','-C',str(repo),'rev-parse','HEAD'],text=True).strip()
            self.assertEqual(audit.verify_git_sources(repo,revision,['source.py'])['source.py'],
                             __import__('hashlib').sha256(b'original\n').hexdigest())
            (repo/'source.py').write_bytes(b'tampered\n')
            with self.assertRaises(ValueError):audit.verify_git_sources(repo,revision,['source.py'])
            with self.assertRaises(ValueError):audit.verify_git_sources(repo,'0'*40,['source.py'])

    def test_missing_product_capture_refused(self):
        import audit_mac_parity as audit
        from unittest import mock
        with mock.patch.object(sys,'argv',['audit','--raw','/a','--parity-out','/b','--public-run','/c','--legacy-run','/d','--out','/e']):
            with self.assertRaises(SystemExit): audit.main()

    def test_empty_and_full_error_bodies(self):
        import audit_mac_parity as audit
        raw={'step':'missing','method':'GET','body':None,'content_type':'application/json',
             'status':404,'response_content_type':'application/json','response':'{"detail":"Not Found"}'}
        normalized={'step':'missing','request':{'method':'GET'},'status':404,
                    'media':'application/json','body':{'detail':'Not Found'}}
        self.assertEqual(audit.check_record(raw,normalized),'error')
        normalized['body']={'detail':'other'}
        with self.assertRaises(ValueError): audit.check_record(raw,normalized)
        raw.update(status=204,response='',response_content_type=None)
        normalized.update(status=204,media=None,body=None)
        self.assertEqual(audit.check_record(raw,normalized),'empty')
        raw['response']='unexpected'
        with self.assertRaises(ValueError): audit.check_record(raw,normalized)

class HistoricalReconstructionTests(unittest.TestCase):
    def test_actual_source_name_formats(self):
        import reconstruct_historical_responses as recon
        self.assertTrue(recon.valid_manifest_name('package-009.manifest.json'))
        self.assertTrue(recon.valid_manifest_name('package-015.manifest.json'))
        for invalid in ('package-09.manifest.json', 'package-009Xmanifest.json', '../package-009.manifest.json', 'package-009.manifest.json/extra'):
            self.assertFalse(recon.valid_manifest_name(invalid))
        self.assertTrue(recon.valid_response_name('run/python-baseline-raw/calls/000009.response.raw'))
        for invalid in ('run/python-baseline-raw/calls/00009.response.raw', 'run/python-baseline-raw/calls/000009Xresponse.raw', '../run/python-baseline-raw/calls/000009.response.raw'):
            self.assertFalse(recon.valid_response_name(invalid))

    def test_later_source_request_status_and_response_join(self):
        import hashlib
        import verify_historical_response_provenance as proof
        import reconstruct_historical_responses as recon
        def h(raw): return hashlib.sha256(raw).hexdigest()
        request=b'{"bundle":"later"}'; response=b'{"files":{}}'
        root='run/python-baseline-raw/'; prefix='calls/000009'
        call={'id':9,'method':'POST','url':'http://127.0.0.1:18917/v1/assemble', 'status':200,'complete':True,'error':None,
              'request_file':prefix+'.request.raw','response_file':prefix+'.response.raw','request_sha256':h(request),'response_sha256':h(response)}
        files={root+prefix+'.json':json.dumps(call).encode(),root+prefix+'.request.raw':request,root+prefix+'.response.raw':response}
        recording={'version':'som.m6-http-recording.v1','exit_code':0,'recording_errors':[],'calls':[call]}
        report={'rows':[{'source':'public','id':'case','variant':v,'baseline_assemble_status':200,
                         'baseline_assemble_request_sha256':h(request),'baseline_assemble_response_sha256':h(response)} for v in ('none','python')]}
        mapping={'reconstructed_cases':[{'source':'public','id':'case','source_request_equal':False,'historical_request_sha256':h(b'old'),
                 'later_baseline_request_sha256':h(request),'decoded_response_sha256':h(response),
                 'source_candidates':[{'path':root+prefix+'.response.raw'}]}]}
        self.assertEqual(proof.join(mapping,recording,report,files.__getitem__,1)['derived_pairs'],1)
        for name in (root+prefix+'.request.raw',root+prefix+'.response.raw',root+prefix+'.json'):
            old=files[name]; files[name]=old+b'!'
            with self.subTest(name=name), self.assertRaises((recon.Refused,ValueError)): proof.join(mapping,recording,report,files.__getitem__,1)
            files[name]=old
        for field,bad in (('method','GET'),('status',201),('url','http://127.0.0.1:18917/v1/compile'),('complete',False)):
            old=call[field]; call[field]=bad; files[root+prefix+'.json']=json.dumps(call).encode()
            with self.subTest(field=field), self.assertRaises(recon.Refused): proof.join(mapping,recording,report,files.__getitem__,1)
            call[field]=old; files[root+prefix+'.json']=json.dumps(call).encode()

    def test_exact_bytes_and_source_tampering(self):
        import base64
        import hashlib
        import reconstruct_historical_responses as recon
        def h(data): return hashlib.sha256(data).hexdigest()
        def put(path, data):
            path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(data)
        def enc(obj): return json.dumps(obj, ensure_ascii=False, indent=2).encode() + b'\n'
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); inputs = root / 'inputs'; restored = root / 'restored'; sealed = root / 'sealed'; later = root / 'later'
            prefix = '.local/rust-compiler-goal/acceptance/public-products-003b/000'
            request = b'{"original":true}'; body = b'{"result":"test"}'
            wire = base64.b64encode(body) + b'\n'; wrapped = enc({'http_status': 200, 'body': {'result':'test'}})
            request_name = prefix + '/assemble.request.raw'; raw_name = 'run/python-baseline-raw/calls/000009.response.raw'
            put(restored / request_name, request); put(later / raw_name, body)
            def manifest(name, payload, cipher):
                return {'version':'som.m6-sealed-inputs-manifest.v1','cipher_sha256':h(cipher),'uncompressed_bytes':len(payload),
                        'total_files':1,'files':[{'path':name,'sha256':h(payload),'bytes':len(payload),'mode':420}]}
            old_manifest = enc(manifest(request_name, request, b'old'))
            new_cipher = b'authenticated ciphertext placeholder'; new_manifest = enc(manifest(raw_name, body, new_cipher))
            entry = {'archive':'package-000.sealed','manifest':'package-000.manifest.json','manifest_sha256':h(new_manifest),
                     'cipher_sha256':h(new_cipher),'total_files':1,'bytes':len(body)}
            put(inputs / 'package-000.manifest.json',old_manifest)
            old_index = enc({'version':'som.m6-input-index.v1','packages':[{'archive':'package-000.sealed','manifest':'package-000.manifest.json','manifest_sha256':h(old_manifest)}]})
            put(inputs / 'inputs-index.json',old_index)
            put(sealed / 'package-000.manifest.json',new_manifest); put(sealed / 'package-000.sealed',new_cipher)
            new_index = enc({'version':'som.m6-evidence-index.v1','packages':[entry]})
            put(sealed / 'evidence-index.json',new_index)
            mapping = {'artifact':{'artifact_id':12843589912,'run_id':40743738705,'head_sha':'40cffc13b3bff35e3ea5804b9f43a49cdef7b3c0','evidence_index_sha256':h(new_index)},
                       'historical_input_index_sha256':h(old_index),'reconstructed_cases':[{'source':'public','index':0,'status':200,'source_request_equal':False,
                       'historical_request_sha256':h(request),'later_baseline_request_sha256':h(b'other'),
                       'historical_raw_path':prefix+'/assemble.response.raw.b64','historical_json_path':prefix+'/assemble.response.json',
                       'historical_raw_sha256':h(wire),'historical_json_sha256':h(wrapped),'decoded_response_sha256':h(body),
                       'source_candidates':[{'path':raw_name,'size':len(body),'package':entry['archive'],'manifest':entry['manifest'],
                                             'manifest_sha256':entry['manifest_sha256'],'cipher_sha256':entry['cipher_sha256']}]}]}
            raw_map = enc(mapping)
            def run(): return recon.derive(raw_map, inputs, restored, sealed, later, map_sha=h(raw_map), input_sha=h(old_index), evidence_sha=h(new_index), expected_count=1)
            result = run()
            self.assertEqual(result[prefix+'/assemble.response.raw.b64'],wire)
            self.assertEqual(result[prefix+'/assemble.response.json'],wrapped)
            for target, original in ((restored / request_name, request), (later / raw_name,body), (sealed / 'package-000.sealed',new_cipher)):
                with self.subTest(target=str(target)):
                    target.write_bytes(original + b'!')
                    with self.assertRaises(recon.Refused): run()
                    target.write_bytes(original)
            # Legacy sources retain JSON requests, not raw request files. Bind the
            # approved JSON bytes before reproducing the compact request body.
            request_json = enc({'original': True})
            request_compact = b'{"original":true}'
            json_name = prefix + '/assemble.request.json'
            put(restored / json_name, request_json)
            fallback_manifest = enc(manifest(json_name, request_json, b'old'))
            put(inputs / 'package-000.manifest.json', fallback_manifest)
            old_index = enc({'version':'som.m6-input-index.v1','packages':[{'archive':'package-000.sealed','manifest':'package-000.manifest.json','manifest_sha256':h(fallback_manifest)}]})
            put(inputs / 'inputs-index.json', old_index)
            mapping['historical_input_index_sha256'] = h(old_index)
            mapping['reconstructed_cases'][0]['historical_request_sha256'] = h(request_compact)
            raw_map = enc(mapping)
            self.assertEqual(run()[prefix+'/assemble.response.json'], wrapped)
            (restored / json_name).write_bytes(request_json + b'!')
            with self.assertRaises(recon.Refused): run()
            (restored / json_name).write_bytes(request_json)
            changed = raw_map.replace(h(wire).encode(), b'0'*64)
            with self.assertRaises(recon.Refused):
                recon.derive(changed, inputs, restored, sealed, later, map_sha=h(raw_map), input_sha=h(old_index), evidence_sha=h(new_index), expected_count=1)


if __name__ == '__main__':
    unittest.main(verbosity=2)

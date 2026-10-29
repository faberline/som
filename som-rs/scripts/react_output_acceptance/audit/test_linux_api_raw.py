"""Synthetic Linux evidence checks. Execute only on a GitHub-hosted runner."""
import os
from pathlib import Path
import tempfile
import unittest

if os.environ.get('GITHUB_ACTIONS') != 'true' or os.environ.get('RUNNER_ENVIRONMENT') != 'github-hosted':
    raise RuntimeError('GitHub hosted runner required')

from test_api_raw import Fixture, audit, save


class LinuxFixture(Fixture):
    def __init__(self, repo):
        super().__init__(repo)
        linux = self.linux = {'api_profile_sha256':'1'*64, 'node_profile_sha256':'2'*64,
                'node':'/qa/node', 'python':'/qa/venv/bin/python', 'pg_bin':'/qa/postgresql/bin',
                'lsof':'/usr/bin/lsof', 'ps':'/bin/ps',
                'api_profile_path':'/qa/api-profile.json', 'node_profile_path':'/qa/node-profile.json',
                'full_before_path':str(repo/'.local/run/api-qa-evidence/full-before.json'),
                'os_tools': {'lsof': {'path':'/usr/bin/lsof','resolved_path':'/usr/bin/lsof', 'sha256':'3'*64, 'mode':0o755},
                             'ps': {'path':'/bin/ps','resolved_path':'/usr/bin/ps', 'sha256':'4'*64, 'mode':0o755}}}
        self.receipt['linux_qa'] = {k:v for k,v in linux.items() if k not in ('api_profile_path','node_profile_path','full_before_path')}
        self.receipt['linux_qa']['recheck_passed'] = True
        processes = self.api['processes']
        processes[0]['command'][0] = linux['pg_bin'] + '/pg_ctl'
        processes[1]['command'][0] = linux['python']
        processes[1]['exit_code'] = -15
        events = self.receipt['events']
        for n, event in enumerate(events):
            if event['kind'] in ('pg_ctl', 'initdb'):
                event['argv'][0] = linux['pg_bin'] + '/' + Path(event['argv'][0]).name
            if event['kind'] == 'probe':
                event['argv'][0] = linux['node']
            if event['kind'] == 'process':
                event['cwd']['argv'][0] = linux['lsof']
                if 'listener' in event:
                    event['listener']['argv'][0] = linux['lsof']
                if event['pid'] == 5432 and event['phase'] == 'cwd-check':
                    raw = ('5432 100 5432 ' + ' '.join(processes[1]['command']) + '\n').encode()
                    event['ps']['stdout'] = self.retain(f'linux-python-ps-{n}.stdout', raw)
                if event['pid'] == 5431 and event['phase'] == 'cwd-check':
                    raw = f"5431 100 5431 {linux['pg_bin']}/postgres -D {self.work/'pgdata'}\n".encode()
                    event['ps']['stdout'] = self.retain(f'linux-postgres-ps-{n}.stdout', raw)
        def start(pid, argv, cwd):
            return {'kind':'child-start','pid':pid,'argv':argv,'cwd':cwd}
        def wait(pid, code):
            return {'kind':'child-wait','pid':pid,'state':'completed','exit_code':code,'error_type':None}
        versions = []
        for name, option in [('lsof','-v'),('ps','--version')]:
            versions.append({'kind':'os-tool-version','tool':name,'error_type':None,
                             **self.command('linux-version-'+name,[linux[name],option], b'version\n')})
        out = versions
        pgcount = 0
        py_started = False
        py_waited = False
        for event in events:
            if event['kind'] in ('initdb','pg_ctl','probe'):
                if event['kind'] == 'initdb': pid=7001
                elif event['kind'] == 'probe': pid=6000
                else:
                    pgcount += 1
                    pid=7001+pgcount
                out.extend([start(pid, event['argv'], str(repo)), wait(pid,0)])
            if event.get('pid') == 5432 and event.get('phase') == 'cwd-check' and not py_started:
                migration=[linux['python'],str(self.work/'_alembic/migrate.py'),str(self.work/'_alembic'),
                           str(self.work/'python/alembic/versions')]
                out.extend([start(7004,migration,str(self.work/'python')),wait(7004,0),
                            start(5432,processes[1]['command'],str(self.work/'python'))])
                py_started = True
                out.append({'kind':'listener',**self.command('listen-before',
                    [linux['lsof'],'-nP','-a','-p','5432','-iTCP:20001','-sTCP:LISTEN','-t'],b'5432\n')})
            if event['kind'] == 'probe':
                out.append(event)
                out.append({'kind':'listener',**self.command('listen-after',
                    [linux['lsof'],'-nP','-a','-p','5432','-iTCP:20001','-sTCP:LISTEN','-t'],b'5432\n')})
                continue
            if event.get('pid') == 5432 and event.get('phase') == 'after-stop' and not py_waited:
                out.append(wait(5432,-15)); py_waited=True
            out.append(event)
        self.receipt['events'] = out
        command = [linux['python'],str(repo/'som-rs/scripts/react_output_acceptance/record_api.py'),
                   str(self.work.parent/'python'),str(self.product),str(self.work),'--raw-out',str(self.raw),
                   '--api-profile',linux['api_profile_path'],'--qa-profile',linux['node_profile_path'],
                   '--full-validation',linux['full_before_path']]
        (self.work.parent/'api.log').write_bytes(b'raw collector output\n')
        save(self.work.parent/'api-invocation.json', {'version':'som.react-api-invocation.v1','argv':command,
             'cwd':str(repo),'outcome':'exited','exit_code':0,'wait':{'state':'completed','exit_code':0},
             'log':'api.log','log_sha256':self.reader.hash(self.work.parent/'api.log'),'failure':None})
        self.refresh()

    def validate_linux(self):
        return audit.validate(self.reader,self.raw,self.work/'report.json',self.prompt,self.contract,self.product,linux=self.linux)


class LinuxApiRawTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)

    def test_complete_linux_wire_lifecycle_and_invocation(self):
        self.assertEqual(LinuxFixture(self.root/'ok').validate_linux(), {'steps':20,'exchanges':20,'processes':2})

    def test_linux_receipt_requires_external_context(self):
        f=LinuxFixture(self.root/'unbound')
        with self.assertRaisesRegex(audit.ApiRawError,'independently verified'):
            f.validate()

    def test_missing_or_fake_wait_never_passes(self):
        for mutation in ('missing','unverified','wrong_code'):
            f=LinuxFixture(self.root/mutation)
            waits=[e for e in f.receipt['events'] if e['kind']=='child-wait' and e['pid']==5432]
            if mutation=='missing': f.receipt['events'].remove(waits[0])
            elif mutation=='unverified': waits[0]['state']='unverified'
            else: waits[0]['exit_code']=0
            f.refresh()
            with self.assertRaises(audit.ApiRawError): f.validate_linux()

    def test_listener_raw_bytes_and_context_binding_are_checked(self):
        f=LinuxFixture(self.root/'listener')
        e=next(e for e in f.receipt['events'] if e['kind']=='listener')
        e['stdout']=f.retain('bad-listener',b'9999\n')
        f.refresh()
        with self.assertRaisesRegex(audit.ApiRawError,'listener PID'): f.validate_linux()
        f=LinuxFixture(self.root/'identity')
        f.receipt['linux_qa']['python']='/unrelated/python'
        f.refresh()
        with self.assertRaisesRegex(audit.ApiRawError,'identity differs'): f.validate_linux()

    def test_invocation_log_and_wait_are_checked(self):
        f=LinuxFixture(self.root/'invocation')
        path=f.work.parent/'api-invocation.json'
        doc=f.reader.json(path)
        doc['wait']['exit_code']=False
        save(path,doc)
        with self.assertRaisesRegex(audit.ApiRawError,'invocation or wait'): f.validate_linux()
        f=LinuxFixture(self.root/'log')
        (f.work.parent/'api.log').write_text('tampered')
        with self.assertRaisesRegex(audit.ApiRawError,'log differs'): f.validate_linux()

    def test_daemon_absence_is_independent_of_direct_child_waits(self):
        f=LinuxFixture(self.root/'daemon')
        after=next(e for e in f.receipt['events'] if e.get('pid')==5431 and e.get('phase')=='after-stop')
        after['table']['stdout']=f.retain('daemon-still-alive',b'5431 1 5431\n')
        f.refresh()
        with self.assertRaisesRegex(audit.ApiRawError,'remains alive'): f.validate_linux()


if __name__=='__main__':
    unittest.main()

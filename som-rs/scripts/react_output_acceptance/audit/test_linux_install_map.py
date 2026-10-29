"""Synthetic source-map tests. Execute only on a GitHub-hosted runner."""
import io
import json
import os
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest import mock
import zipfile

if os.environ.get('GITHUB_ACTIONS')!='true' or os.environ.get('RUNNER_ENVIRONMENT')!='github-hosted':
    raise RuntimeError('GitHub hosted runner required')
import linux_install_map as m

def encode(value): return json.dumps(value,sort_keys=True).encode()

def fixture(root):
    assets={name:{'text':'fixture '+name,'sha256':m.sha(('fixture '+name).encode())} for name in
            ('activate','activate.csh','activate.fish','activate.nu','activate.ps1','activate.bat','deactivate.bat','pydoc.bat','activate_this.py','_virtualenv.py')}
    installer=encode({'version':'som.uv099-installer-source-authority.v1','uv':'0.9.9','assets':assets})
    rows=[]; audits={}; wheels={}
    for i in range(40):
        name='demo'+str(i); dist=name+'-1.0.dist-info'; filename=name+'-1.0-py3-none-any.whl'
        module=name+'.py'; payload=('value = '+str(i)+'\n').encode()
        members=[{'path':module,'sha256':m.sha(payload),'size':len(payload),'record_binding':'sha256'},
                 {'path':dist+'/RECORD','sha256':'0'*64,'size':0,'record_binding':'self'}]
        entries=[[module,m.record_digest(payload),str(len(payload))],[dist+'/RECORD','','']]
        files={module:payload,dist+'/RECORD':b''}
        if i==0:
            script=b'[console_scripts]\nhello = demo0:main\n'
            files[dist+'/entry_points.txt']=script
            members.append({'path':dist+'/entry_points.txt','sha256':m.sha(script),'size':len(script),'record_binding':'sha256'})
            entries.append([dist+'/entry_points.txt',m.record_digest(script),str(len(script))])
        raw=io.BytesIO()
        with zipfile.ZipFile(raw,'w') as z:
            for path,data in files.items(): z.writestr(path,data)
        wheel=raw.getvalue()
        inventory=encode({'name':name,'filename':filename,'sha256':m.sha(wheel),'dist_info_dir':dist,
                          'data_dir':name+'-1.0.data','members':members,'record_entries':entries,'directories':[]})
        audits[name]=inventory; wheels[filename]=wheel
        rows.append({'name':name,'filename':filename,'sha256':m.sha(wheel),'audit_sha256':m.sha(inventory)})
    closure=encode({'wheels':rows})
    path=root/'closure.tar'
    with tarfile.open(path,'w') as tar:
        items={m.BASE+'/receipt.json':closure}
        items.update({m.BASE+'/wheel-audits/'+name+'/inventory.json':raw for name,raw in audits.items()})
        items.update({m.BASE+'/wheels/'+name:raw for name,raw in wheels.items()})
        for name,raw in items.items():
            info=tarfile.TarInfo(name); info.size=len(raw); tar.addfile(info,io.BytesIO(raw))
    return path,closure,audits,installer


class SourceMapTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)

    def derive(self):
        path,closure,audits,installer=fixture(self.root)
        with mock.patch.object(m,'TAR_SHA',m.sha(path.read_bytes())), mock.patch.object(m,'CLOSURE_SHA',m.sha(closure)), mock.patch.object(m,'INSTALLER_SHA',m.sha(installer)):
            return m.derive(path,closure,audits,installer,'/qa/venv','/qa/runtime/python')

    def test_wheel_and_generated_script_sources(self):
        expected,dirs=self.derive()
        self.assertEqual(expected[m.SITE+'/demo0.py']['sha256'],m.sha(b'value = 0\n'))
        self.assertEqual(expected['bin/hello']['sha256'],m.sha(m.script_bytes(Path('/qa/venv'),'demo0','main')))
        self.assertIn(m.SITE+'/demo0-1.0.dist-info/RECORD',expected)
        self.assertIn(m.SITE,dirs)
        self.assertEqual(expected['bin/python'],{'type':'symlink','target':'/qa/runtime/python'})

    def test_venv_detects_changed_file_and_extra_file(self):
        expected,dirs=self.derive()
        entries={name:{**row,'mode':0o777 if row['type']=='symlink' else 0o644} for name,row in expected.items()}
        entries.update({name:{'type':'directory','mode':0o755} for name in dirs})
        snapshot={'version':'som.react-linux-api-tree-snapshot.v1','root_mode':0o755,'entries':entries}
        installed={name:{**row,**({'mode':0o644} if row['type']=='file' else {})} for name,row in expected.items()}
        self.assertEqual(m.venv_check(snapshot,encode(installed),expected,dirs),len(expected))
        entries[m.SITE+'/demo0.py']['sha256']='0'*64
        with self.assertRaisesRegex(m.InputAuditError,'wheel or installer'): m.venv_check(snapshot,encode(installed),expected,dirs)
        entries[m.SITE+'/demo0.py']['sha256']=expected[m.SITE+'/demo0.py']['sha256']
        entries['extra']={'type':'file','mode':0o644,'size':0,'sha256':m.sha(b'')}
        with self.assertRaisesRegex(m.InputAuditError,'scope differs'): m.venv_check(snapshot,encode(installed),expected,dirs)

    def test_runtime_source_identity_and_executable_bit(self):
        original={'install/pg/postgres':{'kind':'regular_file','sha256':m.sha(b'pg'),'executable_bit':True},
                  'install/pg/alias':{'kind':'alias_not_admitted','target':'postgres','sha256':m.sha(b'postgres'),
                                      'qualification_probe_executed':False,'runtime_admitted':False}}
        raw=encode(original)
        snapshot={'version':'som.react-linux-api-tree-snapshot.v1','root_mode':0o755,'entries':{
            'pg':{'type':'directory','mode':0o755},'pg/postgres':{'type':'file','mode':0o755,'size':2,'sha256':m.sha(b'pg')},
            'pg/alias':{'type':'symlink','mode':0o777,'target':'postgres'}}}
        with mock.patch.object(m,'RUNTIME_INVENTORY_SHA',m.sha(raw)):
            self.assertEqual(m.runtime_check(snapshot,raw),2)
            snapshot['entries']['pg/postgres']['mode']=0o644
            with self.assertRaisesRegex(m.InputAuditError,'runtime file identity'): m.runtime_check(snapshot,raw)

    def test_changed_qualified_tar_is_refused(self):
        path,closure,audits,installer=fixture(self.root)
        with mock.patch.object(m,'INSTALLER_SHA',m.sha(installer)), mock.patch.object(m,'CLOSURE_SHA',m.sha(closure)):
            with self.assertRaisesRegex(m.InputAuditError,'qualified TAR'): m.derive(path,closure,audits,installer,'/qa/venv','/qa/runtime/python')

if __name__=='__main__': unittest.main()

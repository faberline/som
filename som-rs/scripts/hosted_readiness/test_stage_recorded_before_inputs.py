"""GitHub-only data stager mutation fixtures. No real AEAD/runtime admission."""
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
from types import ModuleType, SimpleNamespace
import unittest
from unittest import mock
import warnings
import zipfile

if os.environ.get('GITHUB_ACTIONS') != 'true' or sys.platform != 'linux':
    raise RuntimeError('GitHub Linux source fixtures only')
HERE = Path(__file__).absolute().parent
REPO = HERE.parents[2]
PATH = 'som-rs/scripts/hosted_readiness/stage_recorded_before_inputs.py'
raw = (REPO / PATH).read_bytes()
assert raw == subprocess.check_output(['/usr/bin/git', '--no-replace-objects', '-C', str(REPO),
    'cat-file', 'blob', os.environ['HELPER_REVISION'] + ':' + PATH])
m = ModuleType('before_input_stager_under_test'); m.__file__ = str(REPO / PATH)
exec(compile(raw, m.__file__, 'exec', dont_inherit=True), m.__dict__)


class BeforeInputStagerTests(unittest.TestCase):
    def fixture(self, root):
        metadata = root / 'metadata'; members = root / 'members'
        metadata.mkdir(); members.mkdir()
        packages = []
        for number in range(18):
            name = f'input-{number:03d}/original.raw'; body = ('original ' + name).encode()
            source = members / name; source.parent.mkdir(); source.write_bytes(body); source.chmod(0o644)
            manifest = {'version':'som.m6-sealed-inputs-manifest.v1', 'cipher_sha256':'1'*64,
                'uncompressed_bytes':len(body), 'total_files':1,
                'files':[{'path':name, 'sha256':m.sha(body), 'bytes':len(body), 'mode':0o644}]}
            raw = m.canonical(manifest); manifest_name = f'package-{number:03d}.manifest.json'
            (metadata / manifest_name).write_bytes(raw)
            packages.append({'archive':f'package-{number:03d}.sealed', 'manifest':manifest_name,
                'manifest_sha256':m.sha(raw)})
        raw = m.canonical({'version':'som.m6-input-index.v1','packages':packages})
        (metadata / 'inputs-index.json').write_bytes(raw)
        return metadata, members, m.sha(raw)

    def zip_fixture(self, root, *, extra=None, linked=False, duplicate=False):
        cipher = b'synthetic sealed bytes; not actual AEAD'
        manifest = {'version':'som.m6-sealed-inputs-manifest.v1', 'cipher_sha256':m.sha(cipher),
            'uncompressed_bytes':50, 'total_files':1,
            'files':[{'path':'run/synthetic.raw','sha256':m.sha(b'raw'),'bytes':3,'mode':0o644}]}
        manifest_raw = m.canonical(manifest)
        package = {'archive':'package-000.sealed','manifest':'package-000.manifest.json',
            'manifest_sha256':m.sha(manifest_raw), 'cipher_sha256':m.sha(cipher), 'total_files':1, 'bytes':3}
        index = m.canonical({'version':'som.m6-evidence-index.v1','key_id':'2'*64,'packages':[package],
            'status':'encrypted','product_acceptance':'read-summary'})
        path = root / 'original.zip'
        with warnings.catch_warnings():
            warnings.simplefilter('ignore', UserWarning)
            with zipfile.ZipFile(path,'x') as archive:
                archive.writestr('evidence-index.json', index)
                archive.writestr('package-000.manifest.json', manifest_raw)
                info = zipfile.ZipInfo('package-000.sealed')
                info.external_attr = (stat.S_IFLNK | 0o777) << 16 if linked else (stat.S_IFREG | 0o600) << 16
                archive.writestr(info, cipher)
                if extra: archive.writestr(extra,b'unlisted')
                if duplicate: archive.writestr('package-000.sealed',cipher)
        pin = {'id':1,'name':'synthetic-before','size_in_bytes':path.stat().st_size,
            'digest':'sha256:'+m.sha(path.read_bytes()),'run_id':2,'head_sha':'3'*40,'index_sha256':m.sha(index)}
        return path, pin

    def test_all_original18_inputs_copy_with_original_bytes_and_modes_without_later_extra(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d).resolve(); metadata,members,index = self.fixture(root)
            (members / 'later19.raw').write_bytes(b'later approved role is separate')
            with mock.patch.object(m,'APPROVED_INDEX_SHA',index):
                result = m.stage_approved(metadata,members,root/'out')
            self.assertEqual(len(result['complete_original_files']),18)
            self.assertFalse(result['later_extra_input_roles_copied'])
            self.assertFalse((root/'out/restored/later19.raw').exists())
            for name,row in result['complete_original_files'].items():
                self.assertEqual((root/'out/restored'/name).read_bytes(),(members/name).read_bytes())
                self.assertEqual((root/'out/restored'/name).stat().st_mode & 0o777,row['mode'])
            self.assertEqual((root/'out/inputs-index.json').read_bytes(),(metadata/'inputs-index.json').read_bytes())

    def test_changed_approved_payload_or_mode_is_refused_without_rewriting_original_metadata(self):
        for change in ('bytes','mode','link'):
            with self.subTest(change=change), tempfile.TemporaryDirectory() as d:
                root=Path(d).resolve(); metadata,members,index=self.fixture(root)
                source=members/'input-000/original.raw'; before=(metadata/'inputs-index.json').read_bytes()
                if change=='bytes': source.write_bytes(b'changed')
                elif change=='mode': source.chmod(0o755)
                else:
                    source.unlink(); source.symlink_to(members/'input-001/original.raw')
                with mock.patch.object(m,'APPROVED_INDEX_SHA',index),self.assertRaises(ValueError):
                    m.stage_approved(metadata,members,root/'out')
                self.assertEqual((metadata/'inputs-index.json').read_bytes(),before)

    def test_changed_approved_index_or_manifest_is_refused(self):
        for change in ('index','manifest'):
            with self.subTest(change=change),tempfile.TemporaryDirectory() as d:
                root=Path(d).resolve(); metadata,members,index=self.fixture(root)
                path=metadata/('inputs-index.json' if change=='index' else 'package-000.manifest.json')
                path.write_bytes(path.read_bytes()+b' ')
                with mock.patch.object(m,'APPROVED_INDEX_SHA',index),self.assertRaises(ValueError):
                    m.stage_approved(metadata,members,root/'out')

    def test_original_ciphertext_and_metadata_universe_is_retained_exactly_as_data(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d).resolve(); path,pin=self.zip_fixture(root)
            result=m.extract_sealed(path,root/'sealed',pin)
            self.assertEqual(result['encrypted_members'],3)
            with zipfile.ZipFile(path) as archive:
                self.assertEqual({p.name for p in (root/'sealed').iterdir()},set(archive.namelist()))
                for name in archive.namelist(): self.assertEqual((root/'sealed'/name).read_bytes(),archive.read(name))

    def test_extra_duplicate_traversal_or_alias_zip_member_is_refused(self):
        for mutation in ({'extra':'unlisted.raw'},{'extra':'../escape'},{'duplicate':True},{'linked':True}):
            with self.subTest(mutation=mutation),tempfile.TemporaryDirectory() as d:
                root=Path(d).resolve(); path,pin=self.zip_fixture(root,**mutation)
                with self.assertRaises(ValueError): m.extract_sealed(path,root/'sealed',pin)
                self.assertFalse((root/'escape').exists())

    def test_outer_hash_and_size_are_checked_before_a_copy_can_pass(self):
        for mutation in ('hash','size'):
            with self.subTest(mutation=mutation),tempfile.TemporaryDirectory() as d:
                root=Path(d).resolve(); path,pin=self.zip_fixture(root)
                expected=pin['digest'].removeprefix('sha256:') if mutation=='size' else '0'*64
                size=pin['size_in_bytes']+1 if mutation=='size' else pin['size_in_bytes']
                with self.assertRaises(ValueError): m.copy_exact(path,root/'copy.zip',expected,size)
                self.assertEqual(m.sha(path.read_bytes()),pin['digest'].removeprefix('sha256:'))

    def test_live_artifact_identity_refuses_wrong_source_run_size_digest_or_expiry(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d).resolve(); _,pin=self.zip_fixture(root)
            current={key:pin[key] for key in ('id','name','size_in_bytes','digest')}
            current.update(expired=False,workflow_run={'id':pin['run_id'],'head_sha':pin['head_sha'],'repository_id':1390374878})
            m.artifact_metadata(pin,current)
            for name,value in (('expired',True),('size_in_bytes',1),('digest','sha256:'+'0'*64)):
                with self.subTest(name=name),self.assertRaises(ValueError): m.artifact_metadata(pin,{**current,name:value})
            for name,value in (('id',99),('head_sha','4'*40),('repository_id',99)):
                changed={**current,'workflow_run':{**current['workflow_run'],name:value}}
                with self.subTest(origin=name),self.assertRaises(ValueError): m.artifact_metadata(pin,changed)

    def test_duplicate_json_keys_and_unsafe_relative_names_are_refused(self):
        with self.assertRaises(ValueError): m.parse(b'{"id":1,"id":2}')
        for name in ('../escape','/absolute','a//b','a/./b','a\\b','a\u0000b'):
            with self.subTest(name=name),self.assertRaises(ValueError): m.relative(name)


    def test_first_private_failure_keeps_actual_reason_phase_and_original_exception(self):
        for fail in ('node','metadata'):
            with self.subTest(fail=fail), tempfile.TemporaryDirectory() as d:
                root=Path(d).resolve()
                profile={'version':'som.linux-qa-recorded-before.v1','role':'recorded-same-request-before',
                    'admission_complete':False,'artifacts':{role:{'id':i+1} for i,role in enumerate(sorted(m.ROLES))}}
                sources=SimpleNamespace(read=lambda name:m.canonical(profile),revision='1'*40)
                original=ValueError('exact synthetic first refusal for '+fail)
                def refuse(*args): raise original
                transport=SimpleNamespace(checked_node=refuse if fail=='node' else lambda *args:{'sha256':'2'*64})
                graph=SimpleNamespace(artifact_metadata=refuse)
                with self.assertRaises(ValueError) as caught:
                    m.stage_inputs(sources,graph,transport,m.PROFILE,{role:root/(role+'.zip') for role in m.ROLES},
                        root/'metadata',root/'members',root/'node',root/'out',root/'evidence')
                self.assertIs(caught.exception,original)
                record=json.loads((root/'evidence/stage-result.json').read_bytes())
                self.assertFalse(record['complete']);self.assertFalse(record['product_acceptance_passed'])
                error=record['first_error']
                self.assertEqual(error['type'],'ValueError');self.assertEqual(error['message'],str(original))
                self.assertIn('ValueError: '+str(original),error['traceback'])
                self.assertEqual(error['phase'],'qualify-transport-node' if fail=='node' else 'historical-artifact-metadata')
                self.assertEqual(error['role'],None if fail=='node' else 'calibration')


    def test_failure_evidence_write_cannot_replace_original_stage_refusal(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d).resolve()
            profile={'version':'som.linux-qa-recorded-before.v1','role':'recorded-same-request-before',
                'admission_complete':False,'artifacts':{role:{'id':i+1} for i,role in enumerate(sorted(m.ROLES))}}
            sources=SimpleNamespace(read=lambda name:m.canonical(profile),revision='1'*40)
            original=ValueError('original synthetic node refusal')
            secondary=OSError('private evidence write failed')
            def refuse(*args): raise original
            transport=SimpleNamespace(checked_node=refuse)
            with mock.patch.object(m,'write',side_effect=secondary),self.assertRaises(ValueError) as caught:
                m.stage_inputs(sources,SimpleNamespace(),transport,m.PROFILE,
                    {role:root/(role+'.zip') for role in m.ROLES},root/'metadata',root/'members',
                    root/'node',root/'out',root/'evidence')
            self.assertIs(caught.exception,original)
            self.assertEqual(original.__som_stage_evidence_error__,
                {'type':'OSError','message':'private evidence write failed'})
            self.assertFalse((root/'evidence/stage-result.json').exists())


if __name__ == '__main__': unittest.main(verbosity=2)

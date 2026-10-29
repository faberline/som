"""Synthetic-only startup adapter fixture. This is not product acceptance."""
import hashlib
import os
from pathlib import Path
import shutil
import sys
import tempfile
from types import SimpleNamespace
import unittest

import react_api_python_payload as subject


class Fixture(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory(dir=os.environ['RUNNER_TEMP'])
        self.temp = Path(self.tempdir.name)
        os.environ['RUNNER_TEMP'] = str(self.temp)
        os.environ['GITHUB_SHA'] = 'a' * 40
        self.base = self.temp / 'som-product-runtime-qualification/install/python/bin/python3.12'
        self.base.parent.mkdir(parents=True)
        shutil.copyfile(sys.executable, self.base)
        self.base.chmod(0o755)
        self.venv = self.temp / 'som-react-python-closure/environments/react-api-qa/venv'
        (self.venv / 'bin').mkdir(parents=True)
        self.site = self.venv / 'lib/python3.12/site-packages'
        self.site.mkdir(parents=True)
        self.python = self.venv / 'bin/python'
        self.python.symlink_to(self.base)
        (self.site / 'side-effect.pth').write_text("import pathlib; pathlib.Path('/tmp/fixture-pth-ran').touch()\n")
        self.repo = Path(os.environ['GITHUB_WORKSPACE'])
        self.values = {subject.SELF: b'fixture provider bound by synthetic source authority',
                       subject.RETAINED: b'def sealed_files(repo): return {}\n',
                       subject.LIVE: b'def verify(profile):\n return {"status":"verified-live-inputs", "python":profile["venv"]+"/bin/python", "pg_bin":profile["runtime_root"]+"/install/postgresql/bin", "live_receipt_sha256":"b"*64, "runtime_receipt_sha256":"b"*64, "closure_receipt_sha256":"b"*64, "venv_inventory_sha256":"b"*64, "requirements_count":39, "wheel_count":40}\n'}
        source_hashes = {name:hashlib.sha256(raw).hexdigest() for name, raw in self.values.items()}
        # This fixture replaces API admission and the live verifier on purpose.
        # It tests adapter control flow only, never the real 39-pin payload.
        self.values[subject.API] = ("def inputs(path,repo):\n"
            " return repo,path,{'venv':" + repr(str(self.venv)) + ",'runtime_root':" + repr(str(self.base.parents[3])) + "},{'source_sha256':" + repr(source_hashes) + "},'b'*64,'b'*64,'b'*64\n").encode()
        source_hashes[subject.API] = hashlib.sha256(self.values[subject.API]).hexdigest()
        # The API fixture cannot embed its own hash without a cycle. Supply the
        # actual synthetic authority at call time through a tiny fixture module.
        self.values[subject.API] = ("def inputs(path,repo):\n"
            " from synthetic_authority import value\n"
            " return repo,path,{'venv':" + repr(str(self.venv)) + ",'runtime_root':" + repr(str(self.base.parents[3])) + "},{'source_sha256':value},'b'*64,'b'*64,'b'*64\n").encode()
        source_hashes[subject.API] = hashlib.sha256(self.values[subject.API]).hexdigest()
        sys.modules['synthetic_authority'] = SimpleNamespace(value=source_hashes)
        self.ctx = SimpleNamespace(qa_mode='native-qa-v1', repo=self.repo,
            helper_revision='a'*40, candidate_revision='a'*40,
            sources=SimpleNamespace(read=lambda path: self.values[path]),
            qa_import_controls=SimpleNamespace(verify_installed=lambda: None),
            qa_source_authority={'source_authority_active':True,
                'admitted_repository_python_sources':list(self.values)},
            data={'runtime_root':str(self.base.parents[3])},
            runtime=lambda: {'tools':{'python':str(self.base)}})

    def tearDown(self):
        del sys.modules['synthetic_authority']
        os.environ['RUNNER_TEMP'] = str(self.temp.parent)
        self.tempdir.cleanup()

    def test_success_is_immutable_and_does_not_process_pth(self):
        before = sys.path[:]
        payload = subject.admit(self.ctx, self.python)
        self.assertIs(type(payload), subject.AdmittedPayload)
        record = payload.record()
        self.assertEqual(record['site'], str(self.site))
        self.assertEqual(record['requirements_count'], 39)
        self.assertEqual(record['wheel_count'], 40)
        self.assertIs(record['product_acceptance_passed'], False)
        self.assertIs(record['pth_executed'], False)
        self.assertEqual(before, sys.path)
        self.assertNotIn('retained_evidence', sys.modules)
        self.assertFalse(Path('/tmp/fixture-pth-ran').exists())
        subject.admit(self.ctx, self.python)

    def test_regular_copy_refused_even_with_identical_bytes(self):
        self.python.unlink()
        shutil.copyfile(self.base, self.python)
        with self.assertRaises(subject.PayloadError):
            subject.admit(self.ctx, self.python)

    def test_link_to_different_python_refused(self):
        self.python.unlink()
        other = self.base.parent / 'other'
        shutil.copyfile(self.base, other)
        self.python.symlink_to(other)
        with self.assertRaises(subject.PayloadError):
            subject.admit(self.ctx, self.python)

    def test_site_symlink_refused(self):
        self.site.rmdir() if not list(self.site.iterdir()) else shutil.rmtree(self.site)
        self.site.symlink_to(self.base.parent, target_is_directory=True)
        with self.assertRaises(subject.PayloadError):
            subject.admit(self.ctx, self.python)

    def test_source_control_and_preloaded_module_refused(self):
        self.ctx.qa_source_authority['source_authority_active'] = False
        with self.assertRaises(subject.PayloadError):
            subject.admit(self.ctx, self.python)
        self.ctx.qa_source_authority['source_authority_active'] = True
        sys.modules['retained_evidence'] = SimpleNamespace()
        try:
            with self.assertRaises(subject.PayloadError):
                subject.admit(self.ctx, self.python)
        finally:
            del sys.modules['retained_evidence']

    def test_verifier_failure_is_not_accepted(self):
        self.values[subject.LIVE] = b'def verify(profile): raise ValueError("rejected live inputs")\n'
        sys.modules['synthetic_authority'].value[subject.LIVE] = hashlib.sha256(self.values[subject.LIVE]).hexdigest()
        with self.assertRaisesRegex(ValueError, 'rejected live inputs'):
            subject.admit(self.ctx, self.python)
        self.assertNotIn('retained_evidence', sys.modules)


if __name__ == '__main__':
    unittest.main(verbosity=2)

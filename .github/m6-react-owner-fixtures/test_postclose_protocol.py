"""Exercise the two-phase protocol with synthetic authority on GitHub only."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

if os.environ.get('GITHUB_ACTIONS') != 'true' or os.environ.get('RUNNER_ENVIRONMENT') != 'github-hosted':
    raise RuntimeError('GitHub-hosted runner required')

import m6_react_callback as subject


class PostCloseProtocolTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=os.environ['RUNNER_TEMP'])
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve() / 'owner'
        self.root.mkdir()
        self.rev = 'a' * 40
        self.capture = {'version': 'som.m6-react-graph-callback.v1', 'gate': subject.GATE,
                        'helper_revision': self.rev, 'candidate_revision': self.rev,
                        'product_acceptance_passed': False, 'owned_waits_complete': False,
                        'owned_cleanup_complete': False}
        (self.root / 'result.json').write_text(json.dumps(self.capture))
        self.domain = {'complete': True, 'all_required_passed': True, 'raw_logs_complete': True}
        raw = (self.root / 'result.json').read_bytes()
        self.proof = {'version': 'som.owner-shared-postclose-proof.v1', 'gate': subject.GATE,
                      'helper_revision': self.rev, 'candidate_revision': self.rev,
                      'capture_result_sha256': subject.sha(raw), 'context_sha256': 'a' * 64,
                      'artifact_inputs_sha256': 'b' * 64, 'services_manifest_sha256': 'c' * 64,
                      'layout_binding_sha256': 'd' * 64, 'source_admission_sha256': 'e' * 64,
                      'owner_adapter': {}, 'proof_files_sha256': 'f' * 64, 'bound_source_sha256': {},
                      'owned_waits_complete': True, 'owned_cleanup_complete': True,
                      'product_acceptance_passed': False}

    def verify(self):
        with mock.patch.object(subject, 'admit_owner_executables', return_value={'source_admission_sha256':'e'*64}), \
             mock.patch.object(subject, 'verify_react_raw_domain', return_value=self.domain), \
             mock.patch.object(subject, 'shared_postclose_proof', return_value=self.proof):
            return subject.verify_result(self.root, lambda _: b'synthetic', self.rev, self.rev,
                                         repo=self.root.parent, proof_roots={})

    def test_returned_verified_result_does_not_rewrite_capture(self):
        original = (self.root / 'result.json').read_bytes()
        result = self.verify()
        self.assertEqual((self.root / 'result.json').read_bytes(), original)
        self.assertFalse((self.root / 'verified-result.json').exists())
        self.assertEqual(result['capture_result_sha256'], subject.sha(original))
        self.assertEqual(result['shared_postclose_proof'], self.proof)
        self.assertTrue(all(result[k] for k in ('complete', 'all_required_passed',
                        'owned_waits_complete', 'owned_cleanup_complete', 'raw_logs_complete')))
        self.assertFalse(result['product_acceptance_passed'])
        (self.root / 'verified-result.json').write_text(json.dumps(result))
        self.assertEqual(self.verify(), result)
        (self.root / 'verified-result.json').write_text('{}')
        with self.assertRaisesRegex(ValueError, 'retained verified result differs'):
            self.verify()

    def test_incomplete_domain_or_shared_proof_is_refused(self):
        self.domain['raw_logs_complete'] = False
        with self.assertRaisesRegex(ValueError, 'domain incomplete'):
            self.verify()
        self.domain['raw_logs_complete'] = True
        self.proof['owned_waits_complete'] = False
        with self.assertRaisesRegex(ValueError, 'shared post-close proof differs'):
            self.verify()
        self.proof['owned_waits_complete'] = True
        self.proof['extra'] = 'not permitted'
        with self.assertRaisesRegex(ValueError, 'shared post-close proof differs'):
            self.verify()

    def test_source_bound_loader_rejects_preloaded_module(self):
        import sys
        name = '_som_m6_react_postclose_launcher'
        with mock.patch.dict(sys.modules, {name: object()}):
            with self.assertRaisesRegex(ValueError, 'preloaded'):
                subject.shared_postclose_proof(self.root, lambda _: b'', self.rev, self.rev,
                                               subject.GATE, repo=self.root.parent, proof_roots={})

    def test_source_bound_loader_removes_its_module(self):
        import sys
        raw = b'def verify_owner_postclose(*args, **kwargs):\n    return {"synthetic": True}\n'
        value = subject.shared_postclose_proof(self.root, lambda path: raw, self.rev, self.rev,
                                               subject.GATE, repo=self.root.parent, proof_roots={})
        self.assertEqual(value, {'synthetic': True})
        self.assertNotIn('_som_m6_react_postclose_launcher', sys.modules)

    def test_load_real_source_bound_auditor_modules(self):
        from pathlib import Path
        import sys
        repo=Path(os.environ['GITHUB_WORKSPACE']).resolve()
        def reader(path):
            return (repo / ('.github/m6-react-owner-fixtures/audit130.py' if path == subject.AUDITOR else path)).read_bytes()
        with subject.source_bound_auditor(reader, repo) as module:
            self.assertTrue(callable(module.run_audit))
            self.assertEqual(module.__som_raw_sha256__,subject.sha(reader(subject.AUDITOR)))
        self.assertTrue(all(name not in sys.modules for name in subject.AUDIT_MODULES))
    def test_owner_requires_every_executed_source_in_verified_admission(self):
        admission={'source_authority_active':True,'helper_revision':self.rev,'candidate_revision':self.rev,
                   'product_acceptance_passed':False,'compiler_acceptance_passed':False,
                   'whole_acceptance_complete':False,
                   'admitted_repository_python_sources':sorted(subject.EXECUTABLE_SOURCES)}
        context={'native_source_authority':admission}
        (self.root.parent/'context.input.json').write_text(json.dumps(context))
        authority=('def verify(*args):\n    return '+repr(admission)+'\n').encode()
        react_paths={subject.SELF,subject.AUDITOR,subject.INPUT_PROFILE,*subject.DERIVED_SOURCES}
        receipt={'amendment':3,'status':'amendment_sealed_after_independent_review',
                 'independent_review':{'reviewer':'synthetic'},'files':{name:subject.sha(b'source') for name in react_paths}}
        def reader(name):
            if name==subject.AUTHORITY:return authority
            if name==subject.REACT_RECEIPT:return json.dumps(receipt).encode()
            return b'source'
        def git(_, *args):
            return (self.rev+'\n').encode() if args[0]=='rev-parse' else b'fixture.py\0'
        with mock.patch.object(subject,'git',side_effect=git):
            result=subject.admit_owner_executables(self.root,reader,self.rev,self.rev,repo=self.root.parent)
        self.assertEqual(set(result['executed_source_sha256']),subject.EXECUTABLE_SOURCES)
        del receipt['files'][subject.INPUT_PROFILE]
        with mock.patch.object(subject,'git',side_effect=git):
            with self.assertRaisesRegex(ValueError,'absent from React003'):
                subject.admit_owner_executables(self.root,reader,self.rev,self.rev,repo=self.root.parent)
        receipt['files'][subject.INPUT_PROFILE]=subject.sha(b'source')
        admission['admitted_repository_python_sources'].remove(subject.LAUNCHER)
        (self.root.parent/'context.input.json').write_text(json.dumps(context))
        authority=('def verify(*args):\n    return '+repr(admission)+'\n').encode()
        with mock.patch.object(subject,'git',side_effect=git):
            with self.assertRaisesRegex(ValueError,'scope not admitted'):
                subject.admit_owner_executables(self.root,reader,self.rev,self.rev,repo=self.root.parent)


if __name__ == '__main__':
    unittest.main()

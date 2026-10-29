"""Synthetic transport only. No historical runtime or acceptance claims."""
import json, os, tempfile, unittest
from pathlib import Path
if os.environ.get('GITHUB_ACTIONS')!='true' or os.environ.get('RUNNER_ENVIRONMENT')!='github-hosted':
    raise RuntimeError('GitHub-hosted runner required')
import cross_job_restore as s

class RestoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(dir=os.environ['RUNNER_TEMP']);self.addCleanup(self.tmp.cleanup)
        top=Path(self.tmp.name).resolve();self.repo=top/'repo';self.retain=top/'retained'
        self.restore=self.repo/'.local/old-capture';self.restore.mkdir(parents=True);self.retain.mkdir()
        for root in (self.retain,self.restore):
            (root/'empty').mkdir();(root/'empty').chmod(0o700)
            (root/'f').write_bytes(b'synthetic evidence');(root/'f').chmod(0o640)
        self.snapshot=s.tree(self.retain);self.old='/home/runner/work/som/som'
        self.context={'repo':self.old,'m6_react_inputs':{'collector_out':self.old+'/.local/old-capture'}}
        self.capture={'input_paths':{'collector_out':self.old+'/.local/old-capture'},
            'collector_capture':{'source_stability_verified':True,'regular_file_modes_preserved':True,
              'empty_directories_preserved':True,'excluded':[],'errors':[],
              'source_snapshot':self.snapshot,'copied_sha256':{'f':s.sha(b'synthetic evidence')}}}
        self.archive=top/'artifact.zip';self.archive.write_bytes(b'synthetic artifact')
        self.receipt={'version':'som.m6-producer-artifact-source.v1','repository_id':12,'run_id':34,'run_attempt':2,
          'head_sha':'a'*40,'artifact_id':56,'artifact_name':'test-only','artifact_sha256':s.sha(self.archive.read_bytes()),
          'artifact_size':len(self.archive.read_bytes())}
        self.artifact={'id':56,'name':'test-only','size_in_bytes':self.receipt['artifact_size'],'expired':False,
          'digest':'sha256:'+self.receipt['artifact_sha256'],'workflow_run':{'id':34,'head_sha':'a'*40,'repository_id':12}}
        self.run={'id':34,'head_sha':'a'*40,'repository':{'id':12},'run_attempt':3}
        self.attempt={'id':34,'head_sha':'a'*40,'repository':{'id':12},'run_attempt':2}
    def enc(self,obj):return json.dumps(obj).encode()
    def check(self):
        capture,context=self.enc(self.capture),self.enc(self.context)
        return s.verify_collector_restore(capture,context,self.retain,self.repo,
                expected_capture_sha256=s.sha(capture),expected_context_sha256=s.sha(context))
    def producer(self):return s.verify_producer_metadata(self.enc(self.receipt),self.enc(self.artifact),self.enc(self.run),
                                                          self.enc(self.attempt),archive_path=self.archive)
    def test_exact_original_relative_path_and_identical_trees(self):
        proof=self.check();self.assertEqual(proof['relative_collector'],'.local/old-capture')
        self.assertEqual(proof['original_repo'],self.old);self.assertFalse(proof['complete'])
        self.assertEqual(proof['process_identity'],'historical_only')
    def test_changed_bytes_or_mode_rejected(self):
        (self.restore/'f').write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError,'bytes/modes/tree'):self.check()
        (self.restore/'f').write_bytes(b'synthetic evidence');(self.restore/'f').chmod(0o600)
        with self.assertRaisesRegex(ValueError,'bytes/modes/tree'):self.check()
    def test_empty_directory_or_extra_file_rejected(self):
        (self.restore/'empty').rmdir()
        with self.assertRaisesRegex(ValueError,'bytes/modes/tree'):self.check()
        (self.restore/'empty').mkdir();(self.restore/'empty').chmod(0o700);(self.restore/'extra').write_text('x')
        with self.assertRaisesRegex(ValueError,'bytes/modes/tree'):self.check()
    def test_symlink_or_hardlink_rejected(self):
        (self.restore/'link').symlink_to('f')
        with self.assertRaisesRegex(ValueError,'linked'):self.check()
        (self.restore/'link').unlink();os.link(self.restore/'f',self.restore/'link')
        with self.assertRaisesRegex(ValueError,'single-link'):self.check()
    def test_original_path_traversal_or_context_mismatch_rejected(self):
        self.context['repo']='/home/runner/work/../som'
        with self.assertRaisesRegex(ValueError,'canonical'):self.check()
        self.context['repo']=self.old;self.context['m6_react_inputs']['collector_out']='/other/path'
        with self.assertRaisesRegex(ValueError,'context collector'):self.check()
    def test_excluded_or_unstable_capture_rejected(self):
        self.capture['collector_capture']['excluded']=[{'path':'missing','reason':'symlink_not_followed'}]
        with self.assertRaisesRegex(ValueError,'snapshot incomplete'):self.check()
        self.capture['collector_capture']['excluded']=[];self.capture['collector_capture']['source_stability_verified']=False
        with self.assertRaisesRegex(ValueError,'snapshot incomplete'):self.check()
    def test_capture_hash_not_rewritten(self):
        with self.assertRaisesRegex(ValueError,'digest differs'):
            s.verify_collector_restore(self.enc(self.capture),self.enc(self.context),self.retain,self.repo,
                 expected_capture_sha256='0'*64,expected_context_sha256=s.sha(self.enc(self.context)))
    def test_archive_and_historical_attempt_match_but_no_origin_claim(self):
        proof=self.producer();self.assertEqual(proof['producer']['run_attempt'],2)
        self.assertEqual(proof['api_authentication'],'external_required')
        self.assertEqual(proof['artifact_to_attempt_attribution'],'external_required');self.assertFalse(proof['complete'])
    def test_wrong_attempt_artifact_or_bytes_rejected(self):
        self.attempt['run_attempt']=3
        with self.assertRaisesRegex(ValueError,'attempt differs'):self.producer()
        self.attempt['run_attempt']=2;self.artifact['workflow_run']['head_sha']='b'*40
        with self.assertRaisesRegex(ValueError,'artifact identity'):self.producer()
        self.artifact['workflow_run']['head_sha']='a'*40;self.archive.write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError,'artifact bytes'):self.producer()
    def test_producer_and_reviewer_attempts_may_differ(self):
        self.assertEqual(self.run['run_attempt'],3);self.assertEqual(self.producer()['producer']['run_attempt'],2)

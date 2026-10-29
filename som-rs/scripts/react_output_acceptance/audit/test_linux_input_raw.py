"""Synthetic run-input boundaries; execute only on a GitHub-hosted runner."""
import os
from pathlib import Path
import unittest
from unittest import mock

if os.environ.get('GITHUB_ACTIONS')!='true' or os.environ.get('RUNNER_ENVIRONMENT')!='github-hosted':
    raise RuntimeError('GitHub hosted runner required')
import audit130
import linux_input_raw as inputs
from test_qa_profile_audit import fixture


class InputBoundaries(unittest.TestCase):
    def test_unavailable_closure_is_unrun_before_reading_self_report(self):
        with self.assertRaises(inputs.LinuxInputError) as caught:
            inputs.audit(None,None,None,{'profile':'fake'},None)
        self.assertEqual(caught.exception.status,'unrun')

    def test_independent_reader_failure_does_not_install_case_identity(self):
        audit,root,run,*_=fixture()
        audit.qa_profile={'profile':'fake'}
        audit.api_qa_profile={'stale':'must clear'}
        audit.api_closure_tar=Path('/qualified.tar')
        with mock.patch.object(audit130,'audit_linux_inputs',side_effect=inputs.LinuxInputError('missing source','unrun')):
            with self.assertRaises(audit130.EvidenceError) as caught:
                audit.api_qa_context()
        self.assertEqual(caught.exception.status,'unrun')
        self.assertIsNone(audit.api_qa_profile)

    def test_input_context_is_passed_only_after_independent_audit(self):
        audit,root,run,*_=fixture()
        audit.qa_profile={'profile':'already checked'}
        expected={'node':'verified-only'}
        with mock.patch.object(audit130,'audit_linux_inputs',return_value=expected) as verify:
            self.assertEqual(audit.api_qa_context()[0],'passed')
        self.assertIs(audit.api_qa_profile,expected)
        verify.assert_called_once_with(audit.reader,audit.run,audit.latest_receipt,audit.qa_profile,None)

    def test_source_run_and_artifact_metadata_are_joined(self):
        run={'id':1,'head_sha':'head','status':'completed','conclusion':'success','repository':{'id':1390374878}}
        artifact={'id':2,'expired':False,'digest':'sha256:abc','workflow_run':{'id':1,'head_sha':'head','repository_id':1390374878}}
        inputs.metadata(run,artifact,1,'head',2,'abc')
        artifact['workflow_run']['head_sha']='other'
        with self.assertRaises(inputs.LinuxInputError): inputs.metadata(run,artifact,1,'head',2,'abc')


if __name__=='__main__': unittest.main()

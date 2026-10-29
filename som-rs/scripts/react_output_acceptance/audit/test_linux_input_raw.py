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

class SourceClosureSchemaTests(unittest.TestCase):
    def test_reader_matches_actual_context_authority_inventory(self):
        import ast
        context = Path(__file__).resolve().parent.parent / 'linux_api_context.py'
        tree = ast.parse(context.read_text())
        constants = {}
        for node in tree.body:
            if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
                key = node.targets[0].id
                if key in ('API_CHAIN', 'LIVE_DEPENDENCIES'):
                    constants[key] = set(ast.literal_eval(node.value.args[0]))
                elif key in ('CONTEXT_SOURCE', 'NODE_SOURCE', 'LIVE_SOURCE', 'RECORDER_SOURCE', 'HOST_SOURCE', 'EVIDENCE_SOURCE'):
                    constants[key] = ast.literal_eval(node.value)
        expected = constants['API_CHAIN'] | constants['LIVE_DEPENDENCIES'] | {
            constants[key] for key in ('CONTEXT_SOURCE', 'NODE_SOURCE', 'LIVE_SOURCE',
                                       'RECORDER_SOURCE', 'HOST_SOURCE', 'EVIDENCE_SOURCE')}
        self.assertEqual(len(expected), 48)
        self.assertEqual(set(inputs.REQUIRED_SOURCES), expected)
        self.assertIn('som-rs/behavior/typescript/__init__.py', expected)
        shas = {name: '0' * 64 for name in expected}
        inputs.source_inventory(shas)
        missing = dict(shas)
        del missing['som-rs/behavior/typescript/__init__.py']
        with self.assertRaisesRegex(inputs.LinuxInputError, 'source authority closure differs'):
            inputs.source_inventory(missing)
        extra = dict(shas, unreviewed='0' * 64)
        with self.assertRaisesRegex(inputs.LinuxInputError, 'source authority closure differs'):
            inputs.source_inventory(extra)

    def test_retained_static_input_names_match_the_host_writer(self):
        import ast
        source = Path(__file__).resolve().parent.parent / 'linux_api_evidence.py'
        tree = ast.parse(source.read_text())
        begin = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == 'begin')
        assignment = next(node for node in ast.walk(begin) if isinstance(node, ast.Assign)
                          and any(isinstance(target, ast.Name) and target.id == 'inputs' for target in node.targets))
        written = {ast.literal_eval(key) for key in assignment.value.keys}
        expected = {'runtime-receipt.json', 'runtime-artifact-origin.json', 'runtime-qualification-receipt.json',
                    'runtime-installed-files.json', 'runtime-source-pins.json', 'runtime-build-intermediates.json',
                    'closure-receipt.json', 'live-provision-receipt.json', 'live-installed-files.json',
                    'live-installed-metadata.json', 'selected.lock', 'offline.lock', 'pyproject.toml', 'uv.lock',
                    'requirements.in', 'probe-installed-metadata.py'}
        self.assertEqual(written, expected)


if __name__ == '__main__':
    unittest.main()

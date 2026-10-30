#!/usr/bin/env python3
"""Synthetic sealed-artifact anchor fixtures; GitHub-hosted runners only."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock
import zipfile

if not (os.environ.get('GITHUB_ACTIONS') == 'true'
        and os.environ.get('RUNNER_ENVIRONMENT') == 'github-hosted'
        and os.environ.get('GITHUB_REPOSITORY') == 'faberline/som'):
    raise RuntimeError('execute these fixtures only on a GitHub-hosted runner')

import rehearsal_anchor as anchor


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def log():
    t = '2026-10-29T00:00:00.000000Z '
    return (t + 'SHA256 digest of uploaded artifact zip is ' + anchor.ARCHIVE_SHA256 + '\n'
            + t + 'Artifact m6-release-evidence.zip successfully finalized. Artifact ID '
            + str(anchor.ARTIFACT_ID) + '\n'
            + t + 'Artifact download URL: https://github.com/faberline/som/actions/runs/'
            + str(anchor.RUN_ID) + '/artifacts/' + str(anchor.ARTIFACT_ID) + '\n').encode()


def metadata():
    run = {'id': anchor.RUN_ID, 'run_attempt': anchor.ATTEMPT, 'head_sha': anchor.HEAD,
           'path': '.github/workflows/m6-release.yml', 'workflow_id': anchor.WORKFLOW_ID,
           'event': 'pull_request', 'status': 'completed', 'conclusion': 'success',
           'repository': {'id': anchor.REPOSITORY_ID, 'full_name': anchor.REPO},
           'head_repository': {'id': anchor.REPOSITORY_ID}}
    steps = [{'name': name, 'conclusion': 'success'} for name in (
        'Record the tested source revision', 'Rehearse the four targets and five recovery scenarios offline',
        'Keep the encrypted raw rehearsal')]
    jobs = {'jobs': [{'id': anchor.JOB_ID, 'name': 'Four-target switch and rollback on QA ports',
                      'status': 'completed', 'conclusion': 'success', 'steps': steps}]}
    artifact = {'id': anchor.ARTIFACT_ID, 'name': 'm6-release-evidence', 'expired': False,
                'digest': 'sha256:' + anchor.ARCHIVE_SHA256, 'size_in_bytes': anchor.ARCHIVE_SIZE,
                'workflow_run': {'id': anchor.RUN_ID, 'head_sha': anchor.HEAD,
                                 'repository_id': anchor.REPOSITORY_ID,
                                 'head_repository_id': anchor.REPOSITORY_ID}}
    return run, jobs, artifact


class AnchorFixtures(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.archive = self.root / 'archive.zip'
        with self.archive.open('wb') as stream:
            stream.truncate(anchor.ARCHIVE_SIZE)
        self.run, self.jobs, self.artifact = metadata()

    def bind(self):
        with mock.patch.object(anchor, 'api', side_effect=[self.run, self.jobs, self.artifact]), \
             mock.patch.object(anchor, 'bounded_job_log', return_value=log()), \
             mock.patch.object(anchor.rc, 'digest', return_value=anchor.ARCHIVE_SHA256):
            anchor.bind_github('/synthetic/gh', self.archive)

    def test_metadata_and_job_upload_binding(self):
        self.bind()

    def test_wrong_repository_is_refused(self):
        self.run['repository']['id'] += 1
        with self.assertRaises(ValueError):
            self.bind()

    def test_wrong_head_or_attempt_is_refused(self):
        self.run['head_sha'] = 'a' * 40
        with self.assertRaises(ValueError):
            self.bind()

    def test_wrong_job_is_refused(self):
        self.jobs['jobs'][0]['id'] += 1
        with self.assertRaises(ValueError):
            self.bind()

    def test_failed_producer_step_is_refused(self):
        self.jobs['jobs'][0]['steps'][1]['conclusion'] = 'failure'
        with self.assertRaises(ValueError):
            self.bind()

    def test_wrong_artifact_digest_is_refused(self):
        self.artifact['digest'] = 'sha256:' + 'a' * 64
        with self.assertRaises(ValueError):
            self.bind()

    def test_changed_upload_log_is_refused(self):
        with self.assertRaises(ValueError):
            anchor.verify_upload_log(log().replace(str(anchor.ARTIFACT_ID).encode(), b'17'))

    def test_foreign_log_control_character_is_refused(self):
        with self.assertRaises(ValueError):
            anchor.verify_upload_log(log() + b'\x1b[2J')

    def test_github_metadata_process_does_not_receive_keyring(self):
        def fake_run(argv, **kwargs):
            self.assertNotIn('M6_INPUTS_KEYRING', kwargs['env'])
            self.assertEqual(kwargs['env']['GH_TOKEN'], 'synthetic-token')
            return subprocess.CompletedProcess(argv, 0, stdout=b'{}')
        with mock.patch.dict(os.environ, {'GH_TOKEN': 'synthetic-token',
                                         'M6_INPUTS_KEYRING': 'synthetic-keyring'}), \
             mock.patch.object(anchor.subprocess, 'run', side_effect=fake_run):
            self.assertEqual(anchor.api('/synthetic/gh', '/synthetic'), {})

    def test_github_log_process_does_not_receive_keyring(self):
        def fake_popen(_argv, **kwargs):
            self.assertNotIn('M6_INPUTS_KEYRING', kwargs['env'])
            self.assertEqual(kwargs['env']['GH_TOKEN'], 'synthetic-token')
            raise OSError('synthetic startup failure')
        with mock.patch.dict(os.environ, {'GH_TOKEN': 'synthetic-token',
                                         'M6_INPUTS_KEYRING': 'synthetic-keyring'}), \
             mock.patch.object(anchor.subprocess, 'Popen', side_effect=fake_popen):
            with self.assertRaises(OSError):
                anchor.bounded_job_log('/synthetic/gh', '/synthetic')

    def make_zip(self, *, extra=False, corrupt=False):
        packages, contents = [], {}
        for number in range(10):
            stem = f'package-{number:03d}'
            ciphertext = ('fake-cipher-' + str(number)).encode()
            manifest = json.dumps({'fixture': number}).encode()
            entry = {'archive': stem + '.sealed', 'manifest': stem + '.manifest.json',
                     'manifest_sha256': sha(manifest), 'cipher_sha256': sha(ciphertext),
                     'total_files': 1, 'bytes': 1}
            packages.append(entry)
            contents[entry['archive']] = ciphertext
            contents[entry['manifest']] = manifest
        if corrupt:
            contents[packages[0]['archive']] = b'changed'
        if extra:
            contents['../escape'] = b'not allowed'
        index = {'version': 'som.m6-evidence-index.v1', 'packages': packages}
        contents['evidence-index.json'] = json.dumps(index).encode()
        path = self.root / 'synthetic.zip'
        with zipfile.ZipFile(path, 'w') as bundle:
            for name, data in contents.items():
                bundle.writestr(name, data)
        return path

    def test_synthetic_sealed_zip_extracts_exact_members(self):
        archive = self.make_zip()
        root = self.root / 'sealed'
        result = anchor.unpack_sealed(archive, root)
        self.assertEqual(result, sha((root / 'evidence-index.json').read_bytes()))
        self.assertEqual(len(list(root.iterdir())), 21)

    def test_extra_zip_path_is_refused(self):
        with self.assertRaises(ValueError):
            anchor.unpack_sealed(self.make_zip(extra=True), self.root / 'sealed')
        self.assertFalse((self.root / 'escape').exists())

    def test_changed_cipher_is_refused(self):
        with self.assertRaises(ValueError):
            anchor.unpack_sealed(self.make_zip(corrupt=True), self.root / 'sealed')

    def exercise_restores(self, count):
        workspace = self.root / 'workspace'
        scripts = workspace / 'som-rs/scripts/react_output_acceptance'
        scripts.mkdir(parents=True)
        restorer = scripts / 'restore_hosted_evidence.mjs'
        sealed_inputs = scripts / 'sealed_inputs.mjs'
        node, libnode = self.root / 'node', self.root / 'libnode'
        for path in (restorer, sealed_inputs, node, libnode):
            path.write_bytes(path.name.encode())
        archive = self.root / 'small-archive'
        archive.write_bytes(b'synthetic archive')
        index_sha = 'e' * 64
        config = b'{"fixture":true}'
        def fake_run(argv, **kwargs):
            self.assertNotIn('GH_TOKEN', kwargs['env'])
            self.assertNotIn('GITHUB_TOKEN', kwargs['env'])
            self.assertEqual(kwargs['env']['M6_INPUTS_KEYRING'], 'synthetic-keyring')
            root = Path(argv[argv.index('--out') + 1])
            (root / 'run/inputs').mkdir(parents=True)
            (root / 'run/rehearsal').mkdir(parents=True)
            (root / 'run/inputs/prerequisites.json').write_bytes(config)
            rehearsal = {'passed': True, 'profile': 'compiler-only',
                         'prerequisites': {'sha256': sha(config)}}
            rehearsal_raw = json.dumps(rehearsal).encode()
            (root / 'run/rehearsal/rehearsal.json').write_bytes(rehearsal_raw)
            summary = {'passed': True, 'status': 'passed', 'source_commit': anchor.HEAD,
                       'rehearsal_sha256': sha(rehearsal_raw), 'prerequisites_sha256': sha(config)}
            (root / 'summary.json').write_text(json.dumps(summary))
            receipt = {'status': 'restored', 'index_sha256': index_sha,
                       'package_count': 10, 'file_count': 1996}
            return subprocess.CompletedProcess(argv, 0, stdout=json.dumps(receipt).encode())
        env = {'GH_TOKEN': 'synthetic-token', 'GITHUB_TOKEN': 'synthetic-token-2',
               'RUNNER_TEMP': str(self.root),
               'M6_INPUTS_KEYRING': 'synthetic-keyring'}
        with mock.patch.dict(os.environ, env), \
             mock.patch.object(anchor, 'reviewed_gh', return_value='/synthetic/gh'), \
             mock.patch.object(anchor, 'bind_github'), \
             mock.patch.object(anchor, 'unpack_sealed', return_value=index_sha), \
             mock.patch.object(anchor, 'NODE', node), mock.patch.object(anchor, 'LIBNODE', libnode), \
             mock.patch.object(anchor, 'NODE_SHA256', sha(node.read_bytes())), \
             mock.patch.object(anchor, 'LIBNODE_SHA256', sha(libnode.read_bytes())), \
             mock.patch.object(anchor, 'RESTORER_SHA256', sha(restorer.read_bytes())), \
             mock.patch.object(anchor, 'SEALED_INPUTS_SHA256', sha(sealed_inputs.read_bytes())), \
             mock.patch.object(anchor, 'ARCHIVE_SHA256', sha(archive.read_bytes())), \
             mock.patch.object(anchor.subprocess, 'run', side_effect=fake_run):
            results = [anchor.restore_verified_prior(archive, self.root, workspace) for _ in range(count)]
        for result in results:
            self.assertEqual(result['prior_sha256'], sha(config))
            self.assertEqual(result['archive_sha256'], sha(archive.read_bytes()))
            self.assertTrue(result['prior_path'].is_relative_to(self.root))
        return results

    def test_restorer_does_not_receive_github_token(self):
        self.exercise_restores(1)

    def test_two_sequential_restores_use_distinct_fresh_directories(self):
        first, second = self.exercise_restores(2)
        first_dir = first['prior_path'].parents[3]
        second_dir = second['prior_path'].parents[3]
        self.assertNotEqual(first_dir, second_dir)
        self.assertEqual(first_dir.parent, self.root)
        self.assertEqual(second_dir.parent, self.root)
        self.assertEqual(first_dir.stat().st_mode & 0o777, 0o700)
        self.assertEqual(second_dir.stat().st_mode & 0o777, 0o700)

    def test_github_cli_outside_runner_temp_is_refused(self):
        with tempfile.TemporaryDirectory() as outside:
            tool = Path(outside).resolve() / 'gh'
            tool.write_bytes(b'synthetic tool')
            with mock.patch.dict(os.environ, {'M6_AUDIT_GH': str(tool)}), \
                 mock.patch.object(anchor, 'GH_SHA256', sha(tool.read_bytes())):
                with self.assertRaises(ValueError):
                    anchor.reviewed_gh(self.root)

    def test_archive_outside_runner_temp_is_refused_before_github_access(self):
        with tempfile.TemporaryDirectory() as outside:
            archive = Path(outside).resolve() / 'prior.zip'
            archive.write_bytes(b'synthetic archive')
            with mock.patch.dict(os.environ, {'RUNNER_TEMP': str(self.root)}), \
                 mock.patch.object(anchor, 'bind_github') as github:
                with self.assertRaises(ValueError):
                    anchor.restore_verified_prior(archive, self.root, self.root)
            github.assert_not_called()

    def test_other_temp_root_is_refused_before_github_access(self):
        archive = self.root / 'synthetic.zip'
        archive.write_bytes(b'synthetic archive')
        other = self.root / 'other-temp'
        other.mkdir()
        with mock.patch.dict(os.environ, {'RUNNER_TEMP': str(self.root)}), \
             mock.patch.object(anchor, 'bind_github') as github:
            with self.assertRaises(ValueError):
                anchor.restore_verified_prior(archive, other, self.root)
        github.assert_not_called()


if __name__ == '__main__':
    unittest.main()

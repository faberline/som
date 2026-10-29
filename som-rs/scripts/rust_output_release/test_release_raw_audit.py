"""Synthetic raw audit fixtures. Execute only on GitHub-hosted runners."""
from __future__ import annotations

import hashlib
import base64
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock
import zipfile
import subprocess

import audit_release_raw as audit


def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


class UniverseTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.restored = self.root / 'restored'
        self.restored.mkdir()
        write(self.restored / 'dir/file', b'approved')
        (self.restored / 'dir/file').chmod(0o640)
        cipher = b'synthetic sealed bytes'
        manifest = {'version': 'synthetic', 'cipher_sha256': audit.sha(cipher), 'uncompressed_bytes': 8,
                    'total_files': 1, 'files': [{'path': 'dir/file', 'sha256': audit.sha(b'approved'),
                                                'bytes': 8, 'mode': 0o640}]}
        manifest_raw = json.dumps(manifest).encode()
        index = {'version': 'som.m6-evidence-index.v1', 'packages': [
            {'archive': 'package-000.sealed', 'manifest': 'package-000.manifest.json',
             'cipher_sha256': audit.sha(cipher), 'manifest_sha256': audit.sha(manifest_raw),
             'total_files': 1, 'bytes': 8}]}
        self.archive = self.root / 'raw.zip'
        with zipfile.ZipFile(self.archive, 'w') as z:
            z.writestr('evidence-index.json', json.dumps(index))
            z.writestr('package-000.manifest.json', manifest_raw)
            z.writestr('package-000.sealed', cipher)

    def test_exact_universe(self):
        result = audit.verify_universe(self.archive, self.restored)
        self.assertEqual((result['packages'], result['files']), (1, 1))

    def test_extra_file_refused(self):
        write(self.restored / 'extra', b'x')
        with self.assertRaisesRegex(ValueError, 'universe'):
            audit.verify_universe(self.archive, self.restored)

    def test_mode_or_content_change_refused(self):
        (self.restored / 'dir/file').chmod(0o600)
        with self.assertRaisesRegex(ValueError, 'file mismatch'):
            audit.verify_universe(self.archive, self.restored)

    def test_symlink_refused(self):
        (self.restored / 'dir/file').unlink()
        (self.restored / 'dir/file').symlink_to('/etc/passwd')
        with self.assertRaisesRegex(ValueError, 'symlink'):
            audit.verify_universe(self.archive, self.restored)

    def test_extra_zip_entry_refused(self):
        with zipfile.ZipFile(self.archive, 'a') as z:
            z.writestr('extra', b'x')
        with self.assertRaisesRegex(ValueError, 'ZIP universe'):
            audit.verify_universe(self.archive, self.restored)

    def test_unreadable_directory_fails_closed(self):
        def blocked(_root, *, followlinks, onerror):
            onerror(PermissionError('blocked'))
            yield None
        with mock.patch.object(audit.os, 'walk', side_effect=blocked):
            with self.assertRaises(PermissionError):
                audit.verify_universe(self.archive, self.restored)

    def test_cipher_change_refused(self):
        with zipfile.ZipFile(self.archive, 'r') as z:
            files = {i.filename: z.read(i.filename) for i in z.infolist()}
        files['package-000.sealed'] = b'changed ciphertext'
        with zipfile.ZipFile(self.archive, 'w') as z:
            for name, data in files.items(): z.writestr(name, data)
        with self.assertRaisesRegex(ValueError, 'cipher hash mismatch'):
            audit.verify_universe(self.archive, self.restored)


@unittest.skipUnless(os.environ.get('GITHUB_ACTIONS') == 'true', 'GitHub runner only')
class AuthenticatedRestoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='m6-audit-seal-', dir=os.environ['RUNNER_TEMP'])
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.source = Path(audit.__file__).resolve().parents[3]
        raw, sealed = self.root / 'input', self.root / 'sealed'
        raw.mkdir()
        write(raw / 'summary.json', b'{"synthetic":true}')
        key = base64.b64encode(bytes(range(32))).decode()
        self.ring = {'a' * 64: key}
        self.env_patch = mock.patch.dict(os.environ, {'M6_INPUTS_KEYRING': json.dumps(self.ring)})
        self.env_patch.start()
        self.addCleanup(self.env_patch.stop)
        sealer = self.source / 'som-rs/scripts/react_output_acceptance/seal_hosted_evidence.mjs'
        subprocess.run(['node', str(sealer), '--root', str(raw), '--out', str(sealed)], check=True,
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=120)
        self.archive = self.root / 'sealed.zip'
        with zipfile.ZipFile(self.archive, 'w') as z:
            for item in sealed.iterdir(): z.write(item, item.name)

    def test_authenticated_restore_and_manifest(self):
        destination = self.root / 'restored'
        receipt = audit.decrypt_original_zip(self.archive, destination, self.source)
        universe = audit.verify_universe(self.archive, destination)
        self.assertEqual(receipt['index_sha256'], universe['index_sha256'])
        self.assertEqual((receipt['package_count'], receipt['file_count']), (1, 1))
        self.assertEqual((destination / 'summary.json').read_bytes(), b'{"synthetic":true}')

    def test_wrong_key_refused(self):
        self.ring['a' * 64] = base64.b64encode(bytes(reversed(range(32)))).decode()
        os.environ['M6_INPUTS_KEYRING'] = json.dumps(self.ring)
        with self.assertRaisesRegex(ValueError, 'authenticated evidence restoration failed'):
            audit.decrypt_original_zip(self.archive, self.root / 'restored', self.source)

    def test_tampered_cipher_refused(self):
        with zipfile.ZipFile(self.archive, 'r') as z:
            files = {i.filename: z.read(i.filename) for i in z.infolist()}
        cipher = bytearray(files['package-000.sealed'])
        cipher[-1] ^= 1
        files['package-000.sealed'] = bytes(cipher)
        with zipfile.ZipFile(self.archive, 'w') as z:
            for name, data in files.items(): z.writestr(name, data)
        with self.assertRaisesRegex(ValueError, 'authenticated evidence restoration failed'):
            audit.decrypt_original_zip(self.archive, self.root / 'restored', self.source)


class ExchangeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.repo = '/original/source'
        self.original = self.repo + '/.local/rust-compiler-goal/acceptance/react-131/'
        self.candidate, self.previous = 'a' * 64, 'b' * 64
        self.config = {'repo': self.repo, 'candidate': {'binary': {'path': '/original/candidate'}},
                       'previous': {'binary': {'path': '/original/previous'}}, 'previous_canaries': {}}
        self.rows = []
        for role in ('previous', 'new'):
            rows = []
            for i in range(6 if role == 'previous' else 8):
                name = f'{role}-{i}'
                request, response, stdout, source = (name.encode() + suffix for suffix in (b'-req', b'-resp', b'-out', b'-input'))
                def binding(suffix, data):
                    path = 'run/inputs/' + name + suffix
                    write(self.root / path, data)
                    return {'path': self.original + path, 'sha256': audit.sha(data)}
                source_binding = binding('.source', source)
                row = {'name': name, 'steps': [
                    {'kind': 'http', 'path': '/v1/compile', 'request': binding('.req', request),
                     'response': binding('.resp', response)},
                    {'kind': 'cli', 'args': ['compile', source_binding['path']], 'inputs': [source_binding],
                     'binaries': [role], 'response': binding('.stdout', stdout)}]}
                rows.append(row)
            self.rows.append(rows)
        for family, rows in zip(('python', 'rust', 'typescript'), (self.rows[0][:2], self.rows[0][2:4], self.rows[0][4:])):
            self.config['previous_canaries'][family] = rows
        for family, rows in zip(('python', 'rust', 'typescript', 'react'),
                                (self.rows[1][:2], self.rows[1][2:4], self.rows[1][4:6], self.rows[1][6:])):
            self.config[family + '_canaries'] = rows
        self.report = {'scenarios': []}
        for name in audit.SCENARIOS:
            d = self.root / 'run/rehearsal' / name
            for unit in ('resume', 'switch', 'check') + (('restore', 'check-restored') if name == 'switch' else ()):
                (d / unit).mkdir(parents=True)
            self.report['scenarios'].append({'name': name, 'port': 18940, 'root': '/original/' + name})
        health_old, health_new = {'status': 'ok', 'version': 'previous'}, {'status': 'ok', 'version': 'candidate'}
        write(self.root / 'run/rehearsal/switch/root/som-rs/runtime/default.json',
              json.dumps({'health': health_old}).encode())
        for scenario in self.report['scenarios']:
            name = scenario['name']
            base = self.root / 'run/rehearsal' / name
            groups = {'resume': [('resumed-previous-default', 'previous')],
                      'switch': [('current-default', 'previous'), ('clone', 'previous'), ('clone', 'new')],
                      'check': [('post-check', 'new' if name == 'switch' else 'previous')]}
            if name in ('switch', 'after_pointer', 'after_commit'):
                groups['switch'].append(('new-default', 'new'))
            if name in ('switch', 'after_commit'):
                groups['switch'].append(('new-default-after-pointer', 'new'))
            if name != 'switch':
                groups['switch'].append(('restored-default', 'previous'))
            else:
                groups.update({'restore': [('restored-default', 'previous')], 'check-restored': [('post-check', 'previous')]})
            for unit, blocks in groups.items():
                target = base / unit
                index, events = 1, []
                for label, role in blocks:
                    port = 18941 if label == 'clone' and role == 'previous' else 18942 if label == 'clone' else 18940
                    pid = 4001 if port == 18941 else 4002
                    if label == 'clone':
                        events.append({'stage': 'started', 'pid': pid, 'argv': ['fake', '--port', str(port)]})
                    for row in self.rows[0 if role == 'previous' else 1]:
                        for step in row['steps']:
                            def source(binding):
                                return (self.root / binding['path'][len(self.original):]).read_bytes()
                            if step['kind'] == 'http':
                                write(target / f'{index:04d}-request.raw', source(step['request']))
                                write(target / f'{index+1:04d}-response.raw', source(step['response']))
                                index += 2
                            else:
                                binary = self.config[role if role == 'previous' else 'candidate']['binary']['path']
                                argv = [binary, *step['args'], '--url', f'http://127.0.0.1:{port}']
                                write(target / f'{index:04d}-cli-argv.json', json.dumps(argv).encode())
                                write(target / f'{index+1:04d}-cli-stdout.raw', source(step['response']))
                                write(target / f'{index+2:04d}-cli-stderr.raw', b'')
                                index += 3
                        events.append({'stage': 'canary_passed', 'label': label, 'name': row['name'], 'port': port})
                    if label == 'clone':
                        events.append({'stage': 'stopped', 'pid': pid})
                health_count = {'resume': 3, 'check': 1, 'restore': 2, 'check-restored': 1}.get(unit,
                    {'switch': 5, 'after_stop': 5, 'after_start': 6, 'after_pointer': 6, 'after_commit': 7}[name])
                for _ in range(health_count):
                    write(target / f'{index:04d}-response.raw', json.dumps(health_old).encode())
                    index += 1
                if name == 'switch' and unit == 'switch':
                    events.append({'stage': 'committed', 'receipt': {'health': health_new}})
                write(target / 'journal.json', json.dumps(events).encode())

    def inspect(self):
        return audit.compare_exchanges(self.root, self.config, self.report, self.candidate, self.previous)

    def test_all_byte_pairs(self):
        result = self.inspect()
        self.assertEqual((result['http_pairs'], result['cli_records']), (238, 238))
        self.assertEqual(result['http_route_per_exchange'], 'not_recorded')

    def test_changed_raw_response_refused(self):
        path = next((self.root / 'run/rehearsal/switch/switch').glob('*-response.raw'))
        path.write_bytes(b'tampered')
        with self.assertRaisesRegex(ValueError, 'HTTP request/reply sequence'):
            self.inspect()

    def test_changed_cli_stdout_refused(self):
        path = next((self.root / 'run/rehearsal/switch/switch').glob('*-cli-stdout.raw'))
        path.write_bytes(b'tampered')
        with self.assertRaisesRegex(ValueError, 'CLI argv/stdout'):
            self.inspect()

    def test_cli_stderr_is_counted_without_inventing_a_gate(self):
        path = next((self.root / 'run/rehearsal/switch/switch').glob('*-cli-stderr.raw'))
        path.write_bytes(b'warning')
        self.assertEqual(self.inspect()['cli_stderr_nonempty'], 1)

    def test_clone_port_must_match_its_recorded_owner(self):
        path = self.root / 'run/rehearsal/switch/switch/journal.json'
        events = json.loads(path.read_bytes())
        event = next(e for e in events if e['stage'] == 'canary_passed' and e['label'] == 'clone')
        event['port'] = 18943
        path.write_text(json.dumps(events))
        with self.assertRaisesRegex(ValueError, 'no active recorded owner'):
            self.inspect()

    def test_orphan_cli_output_refused(self):
        write(self.root / 'run/rehearsal/switch/switch/9998-cli-stdout.raw', b'orphan')
        with self.assertRaisesRegex(ValueError, 'orphan CLI'):
            self.inspect()

    def test_extra_health_response_refused(self):
        write(self.root / 'run/rehearsal/switch/switch/9998-response.raw', b'{"status":"ok"}')
        with self.assertRaisesRegex(ValueError, 'health response count'):
            self.inspect()

    def test_cross_scenario_exchange_swap_refused(self):
        old = next((self.root / 'run/rehearsal/after_stop/switch').glob('*-response.raw'))
        new = next((self.root / 'run/rehearsal/switch/check').glob('*-response.raw'))
        old_bytes, new_bytes = old.read_bytes(), new.read_bytes()
        self.assertNotEqual(old_bytes, new_bytes)
        old.write_bytes(new_bytes)
        new.write_bytes(old_bytes)
        with self.assertRaisesRegex(ValueError, 'HTTP request/reply sequence'):
            self.inspect()

    def test_unbound_input_path_refused(self):
        self.rows[0][0]['steps'][0]['request']['path'] = '/outside/request'
        with self.assertRaisesRegex(ValueError, 'outside captured root'):
            self.inspect()


class CompleteAuditTests(ExchangeTests):
    def setUp(self):
        super().setUp()
        self.head = 'c' * 40
        self.config['candidate']['binary']['sha256'] = audit.CANDIDATE_SHA256
        self.config['previous']['binary']['sha256'] = audit.PREVIOUS_SHA256
        self.config['base_controller'] = {'path': self.repo + '/som-rs/scripts/compiler_default_switch.py',
                                          'sha256': audit.BASE_CONTROLLER_SHA256}
        self.report.update({'version': 'som.rust-output-switch-rehearsal.v1', 'passed': True,
                            'profile': 'compiler-only', 'live_default_accessed': False, 'script_sha256': {}})
        config_raw = json.dumps(self.config).encode()
        write(self.root / 'run/inputs/prerequisites.json', config_raw)
        self.report['prerequisites'] = {'sha256': audit.sha(config_raw),
                                        'path': self.original + 'run/inputs/prerequisites.json'}
        self.report['prerequisites_receipt_sha256'] = audit.sha(json.dumps(
            self.config, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode())
        checks = {name: {'passed': True} for name in ('owner', 'health', 'pointers', 'previous_compiler_inputs',
                                                       'canaries', 'owner_after')}
        for i, row in enumerate(self.report['scenarios']):
            name, base = row['name'], self.root / 'run/rehearsal' / row['name']
            original_scenario = self.original + 'run/rehearsal/' + name
            row['root'] = original_scenario + '/root'
            pid, owner_pid = 1000 + i, 2000 + i
            identity = {'pid': owner_pid, 'port': row['port'], 'binary_sha256': audit.PREVIOUS_SHA256}
            seed_raw = json.dumps({'pid': pid, 'identity': {'pid': pid, 'port': row['port'],
                                                          'binary_sha256': audit.PREVIOUS_SHA256}}).encode()
            write(base / 'stand-in/stale-default.json', seed_raw)
            stdout, stderr = f'{pid}\n'.encode(), b''
            write(base / 'stand-in/seed.stdout', stdout)
            write(base / 'stand-in/seed.stderr', stderr)
            seed = {'pid': pid, 'old_pid_alive': False, 'exit_code': 0, 'stdout_file': 'stand-in/seed.stdout',
                    'stderr_file': 'stand-in/seed.stderr', 'stdout_sha256': audit.sha(stdout),
                    'stderr_sha256': audit.sha(stderr), 'receipt': 'stand-in/stale-default.json',
                    'default_sha256': audit.sha(seed_raw),
                    'argv': ['/opt/homebrew/Cellar/python@3.12/3.12.11_1/bin/python3.12',
                             '-c', 'import os; print(os.getpid())']}
            fresh = json.dumps({'pid': owner_pid, 'identity': identity}).encode()
            write(base / 'switch/0002-previous-default.json', fresh)
            resume = {'passed': True, 'status': 'passed', 'old_pid': pid, 'old_pid_alive': False,
                      'stale_snapshot_sha256': seed['default_sha256'], 'previous_binary_sha256': audit.PREVIOUS_SHA256,
                      'evidence_errors': [], 'cleanup_errors': [], 'default_sha256': audit.sha(fresh), 'owner': identity}
            write(base / 'resume/result.json', json.dumps(resume).encode())
            journal_path = base / 'resume/journal.json'
            journal = json.loads(journal_path.read_bytes())
            journal.append({'stage': 'resume_verified', 'owner': identity})
            journal_path.write_text(json.dumps(journal))
            journal_path = base / 'switch/journal.json'
            journal = json.loads(journal_path.read_bytes())
            if name != 'switch':
                journal.extend([{'stage': 'failed', 'error': 'injected failure at ' + name},
                                {'stage': 'rollback_verified'}])
            journal_path.write_text(json.dumps(journal))
            row['switch_stages'] = [event['stage'] for event in journal]
            for unit in ('restore',) if name == 'switch' else ():
                journal_path = base / unit / 'journal.json'
                journal = json.loads(journal_path.read_bytes())
                journal.append({'stage': 'restore_verified'})
                journal_path.write_text(json.dumps(journal))
            final_path = base / 'root/som-rs/runtime/default.json'
            final_health = {'status': 'ok', 'version': 'previous'}
            write(final_path, json.dumps({'pid': 3000 + i, 'identity': {'pid': 3000 + i, 'port': row['port'],
                                                                       'binary_sha256': audit.PREVIOUS_SHA256},
                                         'health': final_health}).encode())
            row['stopped_pid'], row['passed'], row['problems'], row['offline_seed'] = 3000 + i, True, [], seed
            names = ['resume', 'switch', 'check'] + (['restore', 'check-restored'] if name == 'switch' else [])
            calls = []
            for call in names:
                script = 'resume_default.py' if call == 'resume' else 'switch.py' if call in ('switch', 'restore') else 'check_default.py'
                argv = [seed['argv'][0], self.repo + '/som-rs/scripts/rust_output_release/' + script,
                        '--prerequisites', self.report['prerequisites']['path'], '--root', row['root'], '--port', str(row['port'])]
                if call in ('resume', 'switch'):
                    argv += ['--rehearsal', '--execute', '--output', original_scenario + '/' + call]
                    if call == 'switch' and name != 'switch': argv += ['--inject-failure', name]
                elif call == 'restore':
                    argv += ['--rehearsal', '--restore', original_scenario + '/switch', '--output', original_scenario + '/restore']
                else:
                    expected = audit.CANDIDATE_SHA256 if name == 'switch' and call == 'check' else audit.PREVIOUS_SHA256
                    argv += ['--expect-binary-sha256', expected, '--output', original_scenario + '/' + call]
                for stream in ('stdout', 'stderr'): write(base / (call + '.' + stream), b'')
                calls.append({'name': call, 'argv': argv, 'exit_code': 1 if call == 'switch' and name != 'switch' else 0,
                              'started_at': 1, 'ended_at': 2, 'stdout_sha256': audit.sha(b''), 'stderr_sha256': audit.sha(b'')})
            row['resume'], row['calls'] = calls[0], calls[1:]
            for unit in ('check',) + (('check-restored',) if name == 'switch' else ()):
                expected = audit.CANDIDATE_SHA256 if name == 'switch' and unit == 'check' else audit.PREVIOUS_SHA256
                write(base / unit / 'result.json', json.dumps({'passed': True, 'expect_binary_sha256': expected,
                                                              'checks': checks}).encode())
        report_raw = json.dumps(self.report).encode()
        write(self.root / 'run/rehearsal/rehearsal.json', report_raw)
        observations = [{'port': p, 'exit_code': 1, 'stdout_empty': True, 'stderr_empty': True} for p in (8765, 18765)]
        self.summary = {'version': 'som.m6-release-rehearsal.v1', 'passed': True, 'status': 'passed',
                        'source_commit': self.head, 'scenarios': {name: True for name in audit.SCENARIOS},
                        'offline_resume': {'passed': True, 'scenarios': 5}, 'rehearsal_sha256': audit.sha(report_raw),
                        'prerequisites_sha256': audit.sha(config_raw), 'candidate_binary_sha256': audit.CANDIDATE_SHA256,
                        'previous_binary_sha256': audit.PREVIOUS_SHA256, 'protected_ports_unused': [8765, 18765],
                        'all_scenario_owners_stopped': True, 'protected_port_check': {'before': observations, 'after': observations}}
        self.summary_path = self.root / 'summary.json'
        self.summary_path.write_text(json.dumps(self.summary))
        self.check_source = mock.patch.object(audit, 'check_source').start()
        self.addCleanup(mock.patch.stopall)

    def raw(self):
        return audit.check_raw(self.root, self.root, self.head)

    def test_complete_raw_and_limits(self):
        result = self.raw()
        self.assertEqual(result['counts']['scenarios'], 5)
        self.assertEqual(result['raw_exchanges']['cli_records'], 238)
        self.assertEqual(result['independent_wait_transcript'], 'not_recorded')

    def test_candidate_pin_mismatch_refused(self):
        self.summary['candidate_binary_sha256'] = '0' * 64
        self.summary_path.write_text(json.dumps(self.summary))
        with self.assertRaisesRegex(ValueError, 'binary hashes mismatch'): self.raw()

    def test_base_controller_pin_mismatch_refused(self):
        self.config['base_controller']['sha256'] = '0' * 64
        config_raw = json.dumps(self.config).encode()
        (self.root / 'run/inputs/prerequisites.json').write_bytes(config_raw)
        self.report['prerequisites']['sha256'] = audit.sha(config_raw)
        path = self.root / 'run/rehearsal/rehearsal.json'
        path.write_text(json.dumps(self.report))
        self.summary['rehearsal_sha256'] = audit.digest(path)
        self.summary_path.write_text(json.dumps(self.summary))
        with self.assertRaisesRegex(ValueError, 'base controller binding mismatch'): self.raw()

    def test_missing_scenario_refused(self):
        self.report['scenarios'].pop()
        path = self.root / 'run/rehearsal/rehearsal.json'
        path.write_text(json.dumps(self.report))
        self.summary['rehearsal_sha256'] = audit.digest(path)
        self.summary_path.write_text(json.dumps(self.summary))
        with self.assertRaisesRegex(ValueError, 'scenario inventory mismatch'): self.raw()

    def test_false_owner_attestation_refused(self):
        self.summary['all_scenario_owners_stopped'] = False
        self.summary_path.write_text(json.dumps(self.summary))
        with self.assertRaisesRegex(ValueError, 'producer cleanup'): self.raw()

    def test_protected_port_attestation_refused(self):
        self.summary['protected_port_check']['after'][0]['stdout_empty'] = False
        self.summary_path.write_text(json.dumps(self.summary))
        with self.assertRaisesRegex(ValueError, 'protected-port observation'): self.raw()

    def test_bad_final_receipt_refused(self):
        path = self.root / 'run/rehearsal/after_stop/root/som-rs/runtime/default.json'
        receipt = json.loads(path.read_bytes())
        receipt['pid'] = 99999
        path.write_text(json.dumps(receipt))
        with self.assertRaisesRegex(ValueError, 'final receipt mismatch'): self.raw()

    def test_fresh_previous_snapshot_changed_refused(self):
        path = self.root / 'run/rehearsal/after_start/switch/0002-previous-default.json'
        path.write_bytes(b'tampered')
        with self.assertRaisesRegex(ValueError, 'fresh previous snapshot mismatch'): self.raw()

    def test_resumed_pid_binding_changed_refused(self):
        path = self.root / 'run/rehearsal/after_pointer/resume/result.json'
        value = json.loads(path.read_bytes())
        value['owner']['pid'] += 1
        path.write_text(json.dumps(value))
        with self.assertRaisesRegex(ValueError, 'resumed owner binding differs'): self.raw()

    def test_modified_injection_argv_refused(self):
        self.report['scenarios'][1]['calls'][0]['argv'][-1] = 'after_start'
        path = self.root / 'run/rehearsal/rehearsal.json'
        path.write_text(json.dumps(self.report))
        self.summary['rehearsal_sha256'] = audit.digest(path)
        self.summary_path.write_text(json.dumps(self.summary))
        with self.assertRaisesRegex(ValueError, 'call argv mismatch'): self.raw()


class GithubIdentityTests(unittest.TestCase):
    def test_upload_attempt_and_exact_artifact_binding(self):
        head, digest = 'a' * 40, audit.sha(b'archive')
        artifact = {'id': 4, 'name': 'm6-release-evidence', 'expired': False,
                    'workflow_run': {'id': 1, 'head_sha': head, 'repository_id': audit.REPOSITORY_ID,
                                     'head_repository_id': audit.REPOSITORY_ID},
                    'digest': 'sha256:' + digest, 'size_in_bytes': 7}
        run = {'id': 1, 'run_attempt': 2, 'head_sha': head, 'status': 'completed', 'conclusion': 'success',
               'path': audit.WORKFLOW_PATH, 'workflow_id': audit.WORKFLOW_ID, 'event': 'workflow_dispatch',
               'repository': {'id': audit.REPOSITORY_ID, 'full_name': audit.REPO},
               'head_repository': {'id': audit.REPOSITORY_ID}}
        steps = [{'name': n, 'conclusion': 'success'} for n in
                 ('Record the tested source revision', 'Rehearse the four targets and five recovery scenarios offline',
                  'Keep the encrypted raw rehearsal')]
        job = {'id': 3, 'name': 'Four-target switch and rollback on QA ports', 'status': 'completed',
               'conclusion': 'success', 'steps': steps}
        def fake_api(path):
            if '/attempts/2/jobs?' in path: return {'jobs': [job]}
            if '/attempts/2' in path: return run
            return artifact
        with tempfile.TemporaryDirectory() as d:
            archive = write(Path(d) / 'a', b'archive')
            stamp = '2026-10-29T11:18:09.3530390Z '
            log = (stamp + f'SHA256 digest of uploaded artifact zip is {digest}\n'
                   + stamp + 'Artifact m6-release-evidence.zip successfully finalized. Artifact ID 4\n'
                   + stamp + 'Artifact download URL: https://github.com/faberline/som/actions/runs/1/artifacts/4\n').encode()
            with mock.patch.object(audit, 'api', side_effect=fake_api), \
                 mock.patch.object(audit, 'bounded_job_log', return_value=log):
                result = audit.bind_github(1, 2, 3, 4, head, archive)
                self.assertEqual(result['artifact_sha256'], digest)
                with self.assertRaisesRegex(ValueError, 'artifact ID log'):
                    with mock.patch.object(audit, 'bounded_job_log', return_value=b'different upload'):
                        audit.bind_github(1, 2, 3, 4, head, archive)
                with self.assertRaisesRegex(ValueError, 'unexpected log control byte'):
                    with mock.patch.object(audit, 'bounded_job_log', return_value=log + b'\x1b[2J'):
                        audit.bind_github(1, 2, 3, 4, head, archive)
                colored = b'\x1b[36m' + log + b'\x1b[0m'
                with mock.patch.object(audit, 'bounded_job_log', return_value=colored):
                    audit.bind_github(1, 2, 3, 4, head, archive)
                artifact['workflow_run']['head_repository_id'] = 0
                with self.assertRaisesRegex(ValueError, 'artifact metadata mismatch'):
                    audit.bind_github(1, 2, 3, 4, head, archive)
                artifact['workflow_run']['head_repository_id'] = audit.REPOSITORY_ID
                run['path'] = '.github/workflows/other.yml'
                with self.assertRaisesRegex(ValueError, 'run mismatch'):
                    audit.bind_github(1, 2, 3, 4, head, archive)


class ProducerReceiptTests(unittest.TestCase):
    def test_pinned_handoff_refuses_change_or_stale_archive(self):
        with tempfile.TemporaryDirectory() as d:
            archive = write(Path(d) / 'archive', b'cipher')
            identity = {'run_id': 1, 'attempt': 2, 'job_id': 3, 'artifact_id': 4,
                        'head_sha': 'a' * 40, 'artifact_sha256': audit.sha(b'cipher')}
            raw = (json.dumps(audit.producer_receipt(identity, archive), sort_keys=True,
                              separators=(',', ':')) + '\n').encode()
            receipt = write(Path(d) / 'receipt.json', raw)
            audit.verify_receipt(receipt, audit.sha(raw), identity, archive)
            with self.assertRaisesRegex(ValueError, 'receipt SHA'):
                audit.verify_receipt(receipt, '0' * 64, identity, archive)
            archive.write_bytes(b'changed')
            with self.assertRaisesRegex(ValueError, 'receipt differs'):
                audit.verify_receipt(receipt, audit.sha(raw), identity, archive)


if __name__ == '__main__':
    unittest.main()

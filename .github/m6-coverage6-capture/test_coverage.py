"""Synthetic checks. Execute only on a GitHub runner."""
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import capture as c
import coverage6 as coverage


def synthetic_request(method, endpoint, body, receive):
    status, raw = c.http(method, endpoint, body)
    receive(status, raw)
    return status, raw


class CoverageFixtures(unittest.TestCase):
    def test_frozen_six_inputs_and_wires(self):
        repo = Path(os.environ['GITHUB_WORKSPACE'])
        rows = coverage.inputs(repo)
        self.assertEqual(tuple(r[0] for r in rows), coverage.IDS)
        self.assertEqual(tuple(c.sha(r[2]) for r in rows), coverage.WIRES)
        self.assertEqual([len(r[3]) for r in rows], [5, 5, 12, 12, 12, 12])
        self.assertTrue(all('target' not in c.parse(r[2]) for r in rows))

    def test_source_tamper_is_refused(self):
        repo = Path(os.environ['GITHUB_WORKSPACE'])
        original = c.physical_file
        def altered(root, name):
            value = original(root, name)
            return value + b' ' if name.endswith('cov-v1-types.prompt.json') else value
        with patch.object(c, 'physical_file', side_effect=altered), self.assertRaises(c.Refusal):
            coverage.inputs(repo)

    def test_exact_frozen_files_and_status_are_required(self):
        prompt = {'synthetic': True, 'n': -0.0}
        bundle = {'version': 'som.dsl-bundle.v1', 'prompt': prompt}
        files = {'app.py': 'print(1)\n', 'README.md': 'fixture\n'}
        wire = c.compact({'format': 'som.prompt-json.v1', 'prompt': prompt})
        responses = [(200, c.compact({'status': 'ok', 'bundle': bundle})),
                     (200, c.compact({'status': 'ok', 'files': files}))]
        expected = {k: c.sha(v.encode()) for k, v in files.items()}
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for index, frozen in enumerate((expected, {'app.py': expected['app.py']})):
                folder = root / str(index)
                folder.mkdir()
                result = {'status': 'unrun'}
                with patch.object(c, 'http', side_effect=responses):
                    if index == 0:
                        coverage.execute(folder, prompt, wire, frozen, result, synthetic_request)
                        self.assertTrue(result['expected_frozen_files_matched'])
                        self.assertEqual(result['status'], 'captured_product')
                    else:
                        with self.assertRaises(c.Refusal):
                            coverage.execute(folder, prompt, wire, frozen, result, synthetic_request)
                        self.assertEqual(result['status'], 'failed')

    def test_all_cases_start_unrun_before_any_input_validation(self):
        summary = coverage.new_summary()
        self.assertEqual([r['id'] for r in summary['rows']], list(coverage.IDS))
        self.assertTrue(all(r['status'] == 'unrun' for r in summary['rows']))
        self.assertFalse(summary['product_acceptance_passed'])

    def test_bad_compile_is_never_assembled(self):
        prompt = {'synthetic': True}
        bad_bundles = [
            (500, c.compact({'status': 'ok', 'bundle': {}})),
            (200, c.compact({'status': 'error', 'bundle': {}})),
            (200, c.compact({'status': 'ok', 'bundle': {'version': 'wrong', 'prompt': prompt}})),
            (200, c.compact({'status': 'ok', 'bundle': {'version': 'som.dsl-bundle.v1', 'target': 'rust', 'prompt': prompt}})),
            (200, c.compact({'status': 'ok', 'bundle': {'version': 'som.dsl-bundle.v1', 'prompt': {'other': True}}})),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            for index, response in enumerate(bad_bundles):
                folder = Path(tmp) / str(index)
                folder.mkdir()
                request = Mock(return_value=response)
                def receive_request(method, endpoint, body, receive):
                    status, raw = request(method, endpoint, body)
                    receive(status, raw)
                    return status, raw
                row = {'status': 'unrun'}
                with self.subTest(index=index), self.assertRaises(c.Refusal):
                    coverage.execute(folder, prompt, b'{}', {}, row, receive_request)
                self.assertEqual(request.call_count, 1)
                self.assertIn('compile_response', row)
                self.assertEqual(row['status'], 'failed')
                self.assertFalse((folder / 'python.assemble.request.raw').exists())

    def test_start_refuses_other_listener_and_timeout_before_http(self):
        child = Mock()
        child.poll.return_value = None
        observed = []
        def other(_):
            observed.append({'exit_code': 0, 'stdout_sha256': c.sha(b'p99\nf6\n'), 'stderr_sha256': c.sha(b'')})
            raise c.Refusal('listener ownership differs')
        with patch.object(c, 'http') as http, self.assertRaises(c.Refusal):
            coverage.wait_owned_listener(child, other, observed, clock=Mock(return_value=0), sleep=Mock())
        http.assert_not_called()
        self.assertEqual(len(observed), 1)
        clock = Mock(side_effect=[0, 31])
        with patch.object(c, 'http') as http, self.assertRaises(c.Refusal):
            coverage.wait_owned_listener(child, Mock(), [], clock=clock, sleep=Mock())
        http.assert_not_called()

    def test_response_is_saved_before_after_probe_failure(self):
        received = Mock()
        probe = Mock(side_effect=[None, c.Refusal('synthetic')])
        with patch.object(c, 'http', return_value=(200, b'actual-response')) as http:
            with self.assertRaises(c.Refusal):
                coverage.guarded_request('POST', '/v1/compile', b'request', probe, received)
        http.assert_called_once()
        received.assert_called_once_with(200, b'actual-response')
        probe = Mock(side_effect=c.Refusal('synthetic'))
        received = Mock()
        with patch.object(c, 'http') as http:
            with self.assertRaises(c.Refusal):
                coverage.guarded_request('GET', '/healthz', None, probe, received)
        http.assert_not_called()
        received.assert_not_called()

    def test_execute_retains_response_when_after_probe_fails(self):
        prompt = {'synthetic': True}
        bundle = {'version': 'som.dsl-bundle.v1', 'prompt': prompt}
        raw = c.compact({'status': 'ok', 'bundle': bundle})
        probe = Mock(side_effect=[None, c.Refusal('ownership changed')])
        def request(method, endpoint, body, receive):
            return coverage.guarded_request(method, endpoint, body, probe, receive)
        with tempfile.TemporaryDirectory() as tmp, patch.object(c, 'http', return_value=(200, raw)):
            result = {'status': 'unrun'}
            with self.assertRaises(c.Refusal):
                coverage.execute(Path(tmp), prompt, b'{}', {}, result, request)
            self.assertEqual(result['status'], 'failed')
            self.assertEqual(result['compile_status'], 200)
            self.assertEqual((Path(tmp) / 'python.compile.response.raw').read_bytes(), raw)
            self.assertFalse((Path(tmp) / 'python.assemble.request.raw').exists())

    def test_all_port_probes_are_retained_even_if_one_is_unknown(self):
        ok = Mock(returncode=1, stdout=b'', stderr=b'')
        bad = Mock(returncode=0, stdout=b'p9\nf6\n', stderr=b'')
        with tempfile.TemporaryDirectory() as tmp:
            run = Path(tmp) / 'run'
            run.mkdir()
            obs = {}
            with patch.object(coverage.subprocess, 'run', side_effect=[ok, bad, ok]):
                self.assertFalse(coverage.empty_ports(run, obs, 'before'))
            self.assertEqual(set(obs), {'18917', '8765', '18765'})
            self.assertEqual((run / 'port-before-8765' / 'stdout.raw').read_bytes(), b'p9\nf6\n')
            self.assertEqual(obs['18765']['exit_code'], 1)

    def test_unexpected_bundle_target_fails(self):
        prompt = {'synthetic': True}
        bundle = {'version': 'som.dsl-bundle.v1', 'prompt': prompt, 'target': 'rust'}
        files = {'app.py': 'fixture'}
        responses = [(200, c.compact({'status': 'ok', 'bundle': bundle})),
                     (200, c.compact({'status': 'ok', 'files': files}))]
        with tempfile.TemporaryDirectory() as tmp, patch.object(c, 'http', side_effect=responses):
            result = {'status': 'unrun'}
            with self.assertRaises(c.Refusal):
                coverage.execute(Path(tmp), prompt, b'{}', {'app.py': c.sha(b'fixture')}, result, synthetic_request)
            self.assertEqual(result['status'], 'failed')


if __name__ == '__main__':
    unittest.main()

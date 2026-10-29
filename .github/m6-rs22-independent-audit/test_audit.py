"""Synthetic-only draft fixtures. Run on GitHub-hosted runners only."""
import json
import os
from pathlib import Path
import tempfile
import unittest

if os.environ.get('GITHUB_ACTIONS') != 'true' or os.environ.get('RUNNER_ENVIRONMENT') != 'github-hosted':
    raise RuntimeError('GitHub hosted runner required')
import audit


def raw(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':')).encode('utf-8')


class AuditFixtureTests(unittest.TestCase):
    def test_duplicate_keys_and_nonfinite_values_fail(self):
        for value in (b'{"x":1,"x":2}', b'{"x":NaN}', b'\xff'):
            with self.subTest(value=value), self.assertRaises(audit.AuditError):
                audit.parse(value)

    def test_signed_zero_and_unicode_survive(self):
        self.assertEqual(audit.canonical({'z': -0.0, 'x': '例'}), '{"x":"例","z":-0.0}'.encode())
        self.assertNotEqual(audit.digest(audit.canonical(-0.0)), audit.digest(audit.canonical(0.0)))
        audit.qualify_vectors((Path(os.environ['GITHUB_WORKSPACE']) / 'som-rs/contracts/compiler-v1/canonical-vectors.json').read_bytes())

    def test_raw_path_and_hash_are_both_required(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'one').write_bytes(b'synthetic')
            record = {'path': 'one', 'sha256': audit.digest(b'synthetic'), 'bytes': 9}
            self.assertEqual(audit.saved(root, record, 'one'), b'synthetic')
            with self.assertRaises(audit.AuditError):
                audit.saved(root, {**record, 'sha256': '0' * 64}, 'one')
            with self.assertRaises(audit.AuditError):
                audit.physical(root, '../one')
            (root / 'link').symlink_to(root / 'one')
            with self.assertRaises(audit.AuditError):
                audit.physical(root, 'link')

    def test_native_json_refusal_checks_all_frozen_fields_and_location(self):
        request = b'{"prompt":{"query":{"filter_field":"offset"}}}'
        start = request.index(b'"offset"')
        diagnostic = {'code': 'INVALID_VALUE', 'line': 1, 'column': start + 1,
                      'end_line': 1, 'end_column': start + 9, 'byte_start': start,
                      'byte_end': start + 8, 'prompt_pointer': '/prompt/query/filter_field',
                      'cause': 'synthetic cause', 'allowed_form': 'synthetic form'}
        case = {'id': 'RS13_JSON_V3_FILTER_OFFSET', 'span_text': '"offset"', 'expected': diagnostic}
        response = raw({'code': 'INVALID_VALUE', 'diagnostics': [diagnostic]})
        audit.native_diagnostic(response, case, request)
        for key in diagnostic:
            bad = dict(diagnostic)
            bad[key] = 'incorrect'
            with self.subTest(key=key), self.assertRaises(audit.AuditError):
                audit.native_diagnostic(raw({'code': 'INVALID_VALUE', 'diagnostics': [bad]}), case, request)
        with self.assertRaises(audit.AuditError):
            audit.native_diagnostic(raw({'code': 'INVALID_VALUE', 'diagnostics': [{**diagnostic, 'extra': 1}]}), case, request)

    def test_native_cnl_refusal_uses_decoded_source_span(self):
        source = 'hello limit\n'
        expected = {'code': 'INVALID_VALUE', 'byte_start': 6, 'byte_end': 11,
                    'line': 1, 'column': 7, 'end_line': 1, 'end_column': 12,
                    'cause': 'synthetic', 'allowed_form': 'synthetic', 'prompt_pointer': '/x'}
        case = {'id': 'RS12_CNL_V3_FILTER_LIMIT', 'expected': expected, 'span_text': 'limit'}
        audit.native_diagnostic(raw({'code': 'INVALID_VALUE', 'diagnostics': [expected]}), case, raw({'source': source}))
        with self.assertRaises(audit.AuditError):
            audit.native_diagnostic(raw({'code': 'INVALID_VALUE', 'diagnostics': [expected]}), case, raw({'source': 'hello wrong\n'}))

    def test_typed_diagnostic_is_shape_checked_but_not_proved(self):
        case = {'expected': {'code': 'INVALID_VALUE'}}
        audit.check_refusal(raw({'code': 'INVALID_VALUE', 'diagnostics': [{'synthetic': 'unproved'}]}), case)
        with self.assertRaises(audit.AuditError):
            audit.check_refusal(raw({'code': 'OTHER', 'diagnostics': [{}]}), case)
        with self.assertRaises(audit.AuditError):
            audit.check_refusal(raw({'code': 'INVALID_VALUE', 'diagnostics': []}), case)

    def test_missing_or_nonempty_guard_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            guard = root / 'run' / 'guards'
            guard.mkdir(parents=True)
            with self.assertRaises(audit.AuditError):
                audit.check_guard(root, 'before', 8765)
            for suffix, data in (('rc', b'1\n'), ('out', b''), ('err', b'')):
                (guard / ('before-8765.' + suffix)).write_bytes(data)
            audit.check_guard(root, 'before', 8765)
            (guard / 'before-8765.out').write_bytes(b'listener')
            with self.assertRaises(audit.AuditError):
                audit.check_guard(root, 'before', 8765)

    def test_synthetic_success_checks_complete_wire_and_product(self):
        cid = 'RS01_FIXTURE'
        prompt = {'name': 'fixture', 'sign': -0.0}
        source = raw({'format': 'som.prompt-json.v1', 'prompt': prompt, 'target': 'rust'})
        wire = raw({'format': 'som.prompt-json.v1', 'prompt': prompt})
        bundle = {'version': 'som.dsl-bundle.v1', 'prompt': prompt}
        comp = raw({'bundle': bundle})
        assembly = raw({'bundle': bundle})
        files = {'README.md': 'synthetic\n', 'src/main.py': 'print(1)\n'}
        assembled = raw({'files': files})
        sha = audit.digest(audit.canonical(prompt))
        row = {'id': cid, 'form': 'json', 'expected_control_status': 200,
               'frozen_prompt_sha256': sha, 'native_control_wire_sha256': audit.digest(wire),
               'typed_rust_source_path': 'fixture/source', 'typed_rust_source_sha256': audit.digest(source),
               'fresh_python_typed_request_sha256': audit.digest(wire)}
        case = {'form': 'json', 'python_control_body': wire.decode()}
        frozen = {'prompt_sha256': sha}
        supplied = {'expected_prompt_sha256': sha, 'requests': {'json': {'path': 'fixture/source', 'raw_sha256': audit.digest(source)}}}
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            inputs = root / 'inputs'
            (inputs / 'fixture').mkdir(parents=True)
            (inputs / 'fixture' / 'source').write_bytes(source)
            folder = root / 'run' / cid
            folder.mkdir(parents=True)
            def save(name, data):
                (folder / name).write_bytes(data)
                return {'path': f'run/{cid}/{name}', 'sha256': audit.digest(data), 'bytes': len(data)}
            result = {'prompt_sha256': sha, 'typed_rust_input_sha256': audit.digest(source),
                      'typed_rust_input': save('typed-rust.input.raw', source)}
            for form in ('native', 'typed'):
                result[form] = {'form': form, 'expected_status': 200, 'compile_status': 200,
                    'status': 'captured_product', 'assemble_status': 200,
                    'compile_request': save(form + '.compile.request.raw', wire),
                    'compile_response': save(form + '.compile.response.raw', comp),
                    'assemble_request': save(form + '.assemble.request.raw', assembly),
                    'assemble_response': save(form + '.assemble.response.raw', assembled),
                    'files_sha256': {k: audit.digest(v.encode()) for k, v in files.items()},
                    'files_map_sha256': audit.digest(audit.canonical(files))}
            audit.check_case(root, inputs, row, case, frozen, supplied, result)
            (inputs / 'fixture' / 'source').write_bytes(b'wrong')
            with self.assertRaises(audit.AuditError):
                audit.check_case(root, inputs, row, case, frozen, supplied, result)
            (inputs / 'fixture' / 'source').write_bytes(source)
            (folder / 'typed.assemble.response.raw').write_bytes(b'{}')
            with self.assertRaises(audit.AuditError):
                audit.check_case(root, inputs, row, case, frozen, supplied, result)

    def test_wait_and_owned_pid_are_required(self):
        pid = 42
        ports = {'before': {}, 'after': {}}
        for phase in ports:
            for port in (18917, 8765, 18765):
                ports[phase][str(port)] = {'port': port, 'exit_code': 1, 'stdout': '', 'stderr': '',
                    'stdout_sha256': audit.EMPTY, 'stderr_sha256': audit.EMPTY}
        ports['owned'] = {'port': 18917, 'pid': pid, 'exit_code': 0, 'stdout': 'p42\n', 'stderr': '',
                          'stdout_sha256': audit.digest(b'p42\n'), 'stderr_sha256': audit.EMPTY}
        cleanup = {'pid': pid, 'child_started': True, 'child_stopped': True, 'listeners_absent': True,
                   'escalated': False, 'wait_completed': True, 'wait_returncode': -15,
                   'post_wait_poll': -15, 'terminate_attempted': True, 'terminate_sent': True,
                   'kill_attempted': False, 'kill_sent': False}
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            folder = root / 'run' / 'guards'
            folder.mkdir(parents=True)
            for phase in ('before', 'after'):
                for port in (8765, 18765):
                    for suffix, data in (('rc', b'1\n'), ('out', b''), ('err', b'')):
                        (folder / f'{phase}-{port}.{suffix}').write_bytes(data)
            summary = {'ports': ports, 'child_pid': pid, 'cleanup': cleanup}
            audit.check_cleanup(summary, root)
            cleanup['terminate_sent'] = False
            with self.assertRaises(audit.AuditError):
                audit.check_cleanup(summary, root)
            cleanup['terminate_sent'] = True
            cleanup['wait_completed'] = False
            with self.assertRaises(audit.AuditError):
                audit.check_cleanup(summary, root)

    def test_duplicate_case_id_is_refused(self):
        with self.assertRaises(audit.AuditError):
            audit.unique([{'id': 'RS01'}, {'id': 'RS01'}])

if __name__ == '__main__':
    unittest.main()

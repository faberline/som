"""Named-command raw-record mutation fixtures. GitHub Linux only."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import ModuleType, SimpleNamespace
import unittest
from unittest import mock

if os.environ.get('GITHUB_ACTIONS') != 'true' or sys.platform != 'linux':
    raise RuntimeError('GitHub Linux fixtures only')
HERE = Path(__file__).absolute().parent
REPO = HERE.parents[2]
HELPER = os.environ['HELPER_REVISION']
PATH = 'som-rs/scripts/hosted_readiness/run_same_candidate_contracts.py'
raw = (REPO / PATH).read_bytes()
assert raw == subprocess.check_output(['/usr/bin/git', '--no-replace-objects', '-C', str(REPO),
                                      'cat-file', 'blob', HELPER + ':' + PATH])
m = ModuleType('named_contracts_under_test')
m.__file__, m.__cached__, m.__package__ = str(REPO / PATH), None, ''
exec(compile(raw, m.__file__, 'exec', dont_inherit=True), m.__dict__)


class NamedContractDataTests(unittest.TestCase):
    def fixture(self, root):
        repo = root / 'repo'; repo.mkdir()
        proofs = {name: root / name for name in ('assembly_root', 'python_inputs_root', 'rust_inputs_root', 'carrier_root')}
        data = {'version': 'som.full-product-context.v1', 'qa_mode': 'native-qa-v1', 'repo': str(repo),
                'helper_revision': '1' * 40, 'candidate_revision': '1' * 40,
                'observer_python': str(root / 'observer/.venv/bin/python'), **{key: str(value) for key, value in proofs.items()}}
        def save(name, value):
            path = root / name; path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(json.dumps(value).encode())
        save('context.json', data)
        sources = {m.SELF: b'wrapper source', m.BASE + 'run_full_products.py': b'entry source', m.CONTEXT: b'context source'}
        for _, script, _ in m.CASES: sources[script] = b'actual checker source fixture'
        report = {'gate': 'named-contract-acceptance', 'helper_revision': '1' * 40, 'candidate_revision': '1' * 40,
                  'product_acceptance_passed': False, 'complete': True, 'all_required_passed': True,
                  'owned_waits_complete': True, 'owned_cleanup_complete': True, 'raw_logs_complete': True,
                  'bound_source_sha256': {m.SELF: hashlib.sha256(sources[m.SELF]).hexdigest()}, 'rows': []}
        for index, (identity, script, arguments) in enumerate(m.CASES):
            command = {'argv': [data['observer_python'], '-I', '-S', '-B', str(repo / (m.BASE + 'run_full_products.py')),
                '--execute-tool', script, '--', *arguments, '--qa-mode', 'native-qa-v1'], 'cwd': str(repo),
                'exit_code': 0, 'wait_completed': True, 'owned_group_cleanup_passed': True, 'pid': 100 + index}
            row = {'id': identity, 'passed': True, 'command': command}
            for kind in ('stdout', 'stderr'):
                name = 'rows/' + str(index) + '.' + kind
                body = (identity + ' full raw ' + kind).encode(); path = root / name
                path.parent.mkdir(exist_ok=True); path.write_bytes(body)
                row[kind] = name; command[kind + '_sha256'] = hashlib.sha256(body).hexdigest()
            row['command_record'] = 'rows/' + str(index) + '.command.json'; save(row['command_record'], command)
            row['postcheck'] = 'rows/' + str(index) + '.postcheck.json'
            save(row['postcheck'], {'version': 'som.full-product-checker-postcheck.v1', 'tool': script,
                'helper_revision': '1' * 40, 'candidate_revision': '1' * 40, 'pid': command['pid'],
                'source_runtime_tools_inputs_unchanged': True, 'entry_executed': True, 'entry_exit_code': 0,
                'product_acceptance_passed': False, 'source_sha256': {name: hashlib.sha256(sources[name]).hexdigest()
                    for name in (script, m.BASE + 'run_full_products.py', m.CONTEXT)}})
            report['rows'].append(row)
        report['saved_files_sha256'] = m.inventory(root)
        # The fake repo is empty. Only actual regular saved evidence enters the inventory.
        save('result.json', report)
        return repo, proofs, sources, report, save

    def check(self, root, fixture):
        repo, proofs, sources, _, _ = fixture
        return m.verify_result(root, sources.__getitem__, '1' * 40, '1' * 40, repo=repo, proof_roots=proofs)

    def test_complete_original_command_and_raw_records_are_joined(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve(); fixture = self.fixture(root)
            self.assertEqual(len(self.check(root, fixture)['rows']), 2)

    def test_original_raw_command_cannot_disagree_with_summary_even_with_rehashed_inventory(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve(); fixture = self.fixture(root)
            _, _, _, report, save = fixture
            command = dict(report['rows'][0]['command']); command['exit_code'] = 1
            save(report['rows'][0]['command_record'], command)
            report['saved_files_sha256'] = m.inventory(root); report['saved_files_sha256'].pop('result.json')
            save('result.json', report)
            with self.assertRaisesRegex(ValueError, 'original raw record'): self.check(root, fixture)

    def test_changed_entry_prefix_is_refused_even_when_raw_command_and_summary_agree(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve(); fixture = self.fixture(root)
            _, _, _, report, save = fixture
            row = report['rows'][0]; row['command']['argv'][2] = '/foreign/driver.py'
            save(row['command_record'], row['command'])
            report['saved_files_sha256'] = m.inventory(root); report['saved_files_sha256'].pop('result.json')
            save('result.json', report)
            with self.assertRaisesRegex(ValueError, 'complete command'): self.check(root, fixture)

    def test_missing_required_context_source_postcheck_pin_is_refused(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve(); fixture = self.fixture(root)
            _, _, _, report, save = fixture
            path = report['rows'][0]['postcheck']; post = json.loads((root / path).read_bytes())
            del post['source_sha256'][m.CONTEXT]; save(path, post)
            report['saved_files_sha256'] = m.inventory(root); report['saved_files_sha256'].pop('result.json')
            save('result.json', report)
            with self.assertRaisesRegex(ValueError, 'postcheck'): self.check(root, fixture)

    def test_context_proof_root_and_unknown_saved_file_are_refused(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve(); fixture = self.fixture(root)
            fixture[1]['assembly_root'] = root / 'foreign-assembly'
            with self.assertRaisesRegex(ValueError, 'graph-chosen'): self.check(root, fixture)
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve(); fixture = self.fixture(root)
            (root / 'unlisted.raw').write_bytes(b'unlisted raw bytes')
            with self.assertRaisesRegex(ValueError, 'universe'): self.check(root, fixture)


    def test_real_current_tool_command_keeps_isolation_and_original_receipt_tail(self):
        # Exercise the actual source-owned command producer. Runtime and payload
        # admission are explicit doubles; this proves argv integration only.
        path = m.CONTEXT
        source = (REPO / path).read_bytes()
        self.assertEqual(source, subprocess.check_output(['/usr/bin/git', '--no-replace-objects', '-C', str(REPO),
            'cat-file', 'blob', HELPER + ':' + path]))
        context_module = ModuleType('named_command_context_source_fixture')
        context_module.__file__ = str(REPO / path)
        exec(compile(source, context_module.__file__, 'exec', dont_inherit=True), context_module.__dict__)
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve(); fixture = self.fixture(root)
            repo, _, _, report, _ = fixture
            observer = report['rows'][0]['command']['argv'][0]
            checked = []
            ctx = SimpleNamespace(repo=repo, qa_mode='native-qa-v1',
                qa_source_authority={'source_authority_active': True},
                sources=SimpleNamespace(read=lambda name: checked.append(name) or b'checked source'),
                python_payload=lambda executable: {'site': None})
            ctx.python_argv = lambda argv: context_module.Context.python_argv(ctx, argv)
            with mock.patch.object(context_module, 'context', return_value=ctx), \
                 mock.patch.object(context_module, 'observer_python', return_value=Path(observer)):
                for row, (_, script, arguments) in zip(report['rows'], m.CASES):
                    self.assertEqual(context_module.tool_command(repo / script, arguments), row['command']['argv'])
                    self.assertIn(script, checked)
            self.assertEqual(len(self.check(root, fixture)['rows']), 2)

    def test_lost_isolation_flag_is_refused_with_rehashed_command_and_inventory(self):
        for flag in ('-I', '-S', '-B'):
            with self.subTest(flag=flag), tempfile.TemporaryDirectory() as temp:
                root = Path(temp).resolve(); fixture = self.fixture(root)
                _, _, _, report, save = fixture
                row = report['rows'][0]
                row['command']['argv'].remove(flag)
                save(row['command_record'], row['command'])
                report['saved_files_sha256'] = m.inventory(root); report['saved_files_sha256'].pop('result.json')
                save('result.json', report)
                with self.assertRaisesRegex(ValueError, 'complete command'): self.check(root, fixture)

    def test_foreign_driver_with_all_isolation_flags_is_refused(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve(); fixture = self.fixture(root)
            _, _, _, report, save = fixture
            row = report['rows'][0]; row['command']['argv'][4] = '/foreign/driver.py'
            save(row['command_record'], row['command'])
            report['saved_files_sha256'] = m.inventory(root); report['saved_files_sha256'].pop('result.json')
            save('result.json', report)
            with self.assertRaisesRegex(ValueError, 'complete command'): self.check(root, fixture)


if __name__ == '__main__': unittest.main(verbosity=2)

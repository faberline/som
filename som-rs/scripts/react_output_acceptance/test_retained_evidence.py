"""Synthetic retained-evidence fixtures. Run only on GitHub-hosted runners."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

if os.environ.get('GITHUB_ACTIONS') != 'true' or os.environ.get('RUNNER_ENVIRONMENT') != 'github-hosted':
    raise RuntimeError('GitHub hosted runner required')

import retained_evidence as evidence
import test_profile_propagation as propagation
products = propagation.check_products

def digest(raw):
    return hashlib.sha256(raw).hexdigest()

def expected_tree(items):
    text = ''.join(name + '\0' + digest(data) + '\n' for name, data in sorted(items.items()))
    return digest(text.encode()), len(items), 0

def fixture():
    root = Path(tempfile.mkdtemp(prefix='som-retained-fixture-', dir=os.environ['RUNNER_TEMP'])).resolve()
    repo, inputs = root / 'repo', root / 'inputs'
    repo.mkdir()
    inputs.mkdir()
    for name, rel in evidence.SOURCES.items():
        path = repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(('source ' + name + '\n').encode())
    profile = inputs / 'profile.json'
    profile.write_bytes(b'{"sealed":"synthetic"}\n')
    node = inputs / 'node'
    node.write_bytes(b'synthetic node')
    node.chmod(0o755)
    npm = inputs / 'npm'
    npm.mkdir()
    (npm / 'npm.js').write_bytes(b'synthetic npm')
    cache = inputs / 'cache'
    cache.mkdir()
    (cache / 'item').write_bytes(b'synthetic cache')
    (cache / 'item').chmod(0o444)
    cache.chmod(0o555)
    qa = {'node': str(node), 'npm_root': npm, 'cache': cache, 'profile_path': profile,
          'profile_sha256': digest(profile.read_bytes()), 'npm_cli': str(npm / 'npm.js')}
    module = SimpleNamespace(validate_profile=mock.Mock(return_value=qa), recheck=mock.Mock(),
                             NODE_SHA256=digest(node.read_bytes()),
                             NPM_TREE=expected_tree({'npm.js': b'synthetic npm'}),
                             CACHE_TREE=expected_tree({'item': b'synthetic cache'}))
    return root, repo, profile, qa, module

class RetainedEvidenceTests(unittest.TestCase):
    def test_inventory_uses_frozen_path_order_when_lexical_order_differs(self):
        root = Path(tempfile.mkdtemp(prefix='som-retained-order-', dir=os.environ['RUNNER_TEMP']))
        (root / 'lib/cli').mkdir(parents=True)
        (root / 'lib/cli.js').write_bytes(b'outer')
        (root / 'lib/cli/entry.js').write_bytes(b'inner')
        rows = [('lib/cli/entry.js', b'inner'), ('lib/cli.js', b'outer')]
        stream = ''.join(name + '\0' + hashlib.sha256(data).hexdigest() + '\n' for name, data in rows).encode()
        result = evidence.tree_inventory(root)
        self.assertEqual(result['tree_sha256'], hashlib.sha256(stream).hexdigest())
        self.assertEqual(result['file_count'], 2)

    def test_retains_complete_shared_bytes_and_before_after_sources(self):
        root, repo, profile, qa, module = fixture()
        out = root / 'qa-profile-evidence'
        state, runtime = evidence.begin(out, repo, profile, module)
        self.assertEqual(state['status'], 'incomplete')
        cache = json.loads((out / 'cache-inventory.json').read_bytes())
        self.assertEqual(cache['entries'], {'item': {'kind': 'file', 'mode': 0o444, 'sha256': digest(b'synthetic cache')}})
        self.assertEqual(cache['root_mode'], 0o555)
        self.assertEqual(cache['tree_sha256'], module.CACHE_TREE[0])
        self.assertEqual((out / 'cache/item').read_bytes(), b'synthetic cache')
        self.assertEqual((out / 'node').read_bytes(), b'synthetic node')
        self.assertEqual((out / 'npm/npm.js').read_bytes(), b'synthetic npm')
        self.assertTrue(evidence.finish(out, repo, profile, module, state, runtime))
        final = json.loads((out / 'identity.json').read_bytes())
        self.assertEqual(final['status'], 'passed')
        self.assertEqual((out / 'profile-before.json').read_bytes(), (out / 'profile-after.json').read_bytes())
        for name in evidence.SOURCES:
            source = final['sources'][name]
            self.assertEqual(source['before']['sha256'], source['after']['sha256'])
            self.assertEqual((out / source['before']['path']).read_bytes(), (out / source['after']['path']).read_bytes())

    def test_changed_source_or_profile_is_failed(self):
        for field in ('source', 'profile'):
            with self.subTest(field=field):
                root, repo, profile, qa, module = fixture()
                out = root / 'qa-profile-evidence'
                state, runtime = evidence.begin(out, repo, profile, module)
                target = repo / evidence.SOURCES['run_gates'] if field == 'source' else profile
                target.write_bytes(b'changed')
                self.assertFalse(evidence.finish(out, repo, profile, module, state, runtime))
                self.assertEqual(json.loads((out / 'identity.json').read_bytes())['status'], 'failed')

    def test_link_in_tool_tree_is_refused(self):
        root, repo, profile, qa, module = fixture()
        (qa['npm_root'] / 'linked').symlink_to('npm.js')
        out = root / 'qa-profile-evidence'
        with self.assertRaises(evidence.EvidenceError):
            evidence.begin(out, repo, profile, module)
        self.assertEqual(json.loads((out / 'identity.json').read_bytes())['status'], 'failed')

    def test_tampered_retained_bytes_or_typed_inventory_fail(self):
        for field in ('profile', 'inventory'):
            with self.subTest(field=field):
                root, repo, profile, qa, module = fixture()
                out = root / 'qa-profile-evidence'
                state, runtime = evidence.begin(out, repo, profile, module)
                if field == 'profile':
                    (out / 'profile-before.json').write_bytes(b'tampered')
                else:
                    path = out / 'cache-inventory.json'
                    doc = json.loads(path.read_bytes())
                    doc['symlink_count'] = False
                    path.write_text(json.dumps(doc))
                self.assertFalse(evidence.finish(out, repo, profile, module, state, runtime))
                self.assertEqual(json.loads((out / 'identity.json').read_bytes())['status'], 'failed')

    def test_duplicate_inventory_json_is_refused(self):
        root = propagation.scratch()
        path = root / 'duplicate.json'
        path.write_bytes(b'{"entry":1,"entry":1}')
        with self.assertRaises(evidence.EvidenceError):
            evidence.read_json(path)

    def test_gate_invocation_records_argv_cwd_wait_and_log(self):
        root = propagation.scratch()
        log, receipt = root / 'gates.log', root / 'gates-invocation.json'
        observed = {}
        def fake(argv, *, cwd, stdout, stderr, timeout, check):
            observed.update(argv=argv, cwd=cwd, stderr=stderr, timeout=timeout, check=check)
            stdout.write('fixture output\n')
            return subprocess.CompletedProcess(argv, 0)
        with mock.patch.object(products.subprocess, 'run', side_effect=fake):
            result = products.gate_invocation(['gate.py', '--qa-profile', '/profile'], log, receipt)
        self.assertEqual(result['argv'], [products.PYTHON, 'gate.py', '--qa-profile', '/profile'])
        self.assertEqual(result['argv'], observed['argv'])
        self.assertEqual(result['cwd'], observed['cwd'])
        self.assertEqual(result['wait'], {'state': 'completed', 'exit_code': 0})
        self.assertEqual(result['log_sha256'], digest(b'fixture output\n'))
        self.assertEqual(json.loads(receipt.read_bytes()), result)

    def test_gate_unknown_wait_never_masquerades_as_completed(self):
        for failure in (subprocess.TimeoutExpired(['fixture'], 3600), OSError('unknown process state')):
            with self.subTest(failure=type(failure).__name__):
                root = propagation.scratch()
                with mock.patch.object(products.subprocess, 'run', side_effect=failure):
                    row = products.gate_invocation(['gate.py'], root / 'gates.log', root / 'gates-invocation.json')
                self.assertIn(row['outcome'], ('timed-out', 'process-error'))
                self.assertEqual(row['wait'], {'state': 'unverified', 'exit_code': None})

    def test_case_refuses_wrong_or_missing_cache_before_invocation(self):
        root, _, profile, qa, _ = fixture()
        out = root / 'run/products'
        out.mkdir(parents=True)
        entry = {'id': 'case-1', 'product': str(root / 'product')}
        with mock.patch.object(products, 'gate_invocation') as invocation:
            with self.assertRaises(evidence.EvidenceError):
                products.check_case('http://127.0.0.1:18919', entry, out, qa['cache'], {}, qa)
            invocation.assert_not_called()
        before = root / 'run/qa-profile-evidence'
        before.mkdir()
        (before / 'cache-inventory.json').write_text('{}')
        entry['id'] = 'case-2'
        with mock.patch.object(products, 'gate_invocation') as invocation:
            with self.assertRaises(evidence.EvidenceError):
                products.check_case('http://127.0.0.1:18919', entry, out, qa['cache'], {}, qa)
            invocation.assert_not_called()
        self.assertTrue((out / 'case-2/cache-evidence-before.json').is_file())

    def test_case_records_complete_matching_snapshots_and_refuses_changed_cache(self):
        for changed in (False, True):
            with self.subTest(changed=changed):
                root, _, profile, qa, _ = fixture()
                out = root / 'run/products'
                out.mkdir(parents=True)
                shared = root / 'run/qa-profile-evidence'
                shared.mkdir()
                expected = evidence.tree_inventory(qa['cache'])
                evidence.write_json(shared / 'cache-inventory.json', expected)
                entry = {'id': 'case-1', 'group': 'fixture', 'prompt': {'version': 'v1'},
                         'product': str(root / 'product')}
                def fake_gate(argv, log, destination):
                    log.write_bytes(b'gate output\n')
                    record = {'version': 'som.react-gates-invocation.v1', 'argv': [products.PYTHON, *argv],
                              'cwd': str(Path.cwd().resolve()), 'outcome': 'exited', 'exit_code': 0,
                              'wait': {'state': 'completed', 'exit_code': 0}, 'log': log.name,
                              'log_sha256': digest(b'gate output\n'), 'failure': None}
                    evidence.write_json(destination, record)
                    if changed:
                        path = qa['cache'] / 'item'
                        path.chmod(0o644)
                        path.write_bytes(b'changed cache')
                    return record
                with mock.patch.object(products, 'gate_invocation', side_effect=fake_gate), \
                     mock.patch.object(products, 'python_product', return_value='synthetic API unavailable'):
                    if changed:
                        with self.assertRaises(evidence.EvidenceError):
                            products.check_case('http://127.0.0.1:18919', entry, out, qa['cache'], {}, qa)
                    else:
                        with self.assertRaises(evidence.EvidenceError):
                            products.check_case('http://127.0.0.1:18919', entry, out, qa['cache'], {}, qa)
                case = out / 'case-1'
                before = json.loads((case / 'cache-evidence-before.json').read_bytes())
                after = json.loads((case / 'cache-evidence-after.json').read_bytes())
                self.assertEqual(before, expected)
                self.assertEqual(after == expected, not changed)
                self.assertTrue((case / 'gates-invocation.json').is_file())

    def test_case_unknown_wait_stops_before_api(self):
        root, _, profile, qa, _ = fixture()
        out = root / 'run/products'
        out.mkdir(parents=True)
        shared = root / 'run/qa-profile-evidence'
        shared.mkdir()
        evidence.write_json(shared / 'cache-inventory.json', evidence.tree_inventory(qa['cache']))
        entry = {'id': 'case-1', 'product': str(root / 'product')}
        def fake_gate(argv, log, destination):
            log.write_bytes(b'partial\n')
            record = {'version': 'som.react-gates-invocation.v1', 'argv': [products.PYTHON, *argv],
                      'cwd': str(Path.cwd().resolve()), 'outcome': 'process-error', 'exit_code': None,
                      'wait': {'state': 'unverified', 'exit_code': None}, 'log': log.name,
                      'log_sha256': digest(b'partial\n'), 'failure': 'OSError'}
            evidence.write_json(destination, record)
            return record
        with mock.patch.object(products, 'gate_invocation', side_effect=fake_gate), \
             mock.patch.object(products, 'python_product') as python_product:
            with self.assertRaisesRegex(evidence.EvidenceError, 'wait is unverified'):
                products.check_case('http://127.0.0.1:18919', entry, out, qa['cache'], {}, qa)
            python_product.assert_not_called()
        self.assertTrue((out / 'case-1/cache-evidence-after.json').is_file())

class SealedHelperAndGateFixtures(unittest.TestCase):
    def receipt_fixture(self):
        root = propagation.scratch()
        repo = root / 'repo'
        repo.mkdir()
        helper = repo / evidence.SOURCES['linux_qa_profile']
        helper.parent.mkdir(parents=True)
        helper.write_text('VALUE = "authenticated"\n')
        directory = repo / 'som-rs/evidence'
        directory.mkdir(parents=True)
        def map_hash(files):
            return digest(json.dumps(files, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode())
        old = {'som-rs/contracts/react-output-v1/synthetic': 'a' * 64}
        frozen = {'status': 'contract_frozen_before_implementation', 'implementation_started_before_freeze': False,
                  'files': old, 'target_contract': {'id': 'som.react-output-contract.v1', 'sha256': map_hash(old)}}
        freeze = directory / 'react-output-contract-freeze-2026-10-22.json'
        freeze.write_text(json.dumps(frozen))
        files = {**old, evidence.SOURCES['linux_qa_profile']: digest(helper.read_bytes())}
        target = {'id': 'som.react-output-contract.v1', 'sha256': map_hash(files), 'previous_sha256': map_hash(old)}
        latest = {'status': 'amendment_sealed_after_independent_review', 'implementation_already_in_progress': False,
                  'amendment': 1, 'previous_receipt': freeze.relative_to(repo).as_posix(),
                  'previous_receipt_sha256': digest(freeze.read_bytes()), 'files': files, 'target_contract': target,
                  'changed_paths': [], 'added_paths': [evidence.SOURCES['linux_qa_profile']], 'changes': [],
                  'independent_review': {'target_sha256': target['sha256'], 'rounds': [{'round': 1, 'verdict': 'APPROVED',
                    'reviewer': 'fixture', 'evidence': 'fixture', 'head': 'b' * 40}]}}
        receipt = directory / 'react-output-contract-amendment-001.json'
        receipt.write_text(json.dumps(latest))
        return root, repo, helper, freeze, receipt, latest

    def test_receipt_checks_precede_exact_helper_execution(self):
        for failure in ('none', 'helper-changed', 'preloaded', 'bad-target', 'bad-review', 'broken-chain', 'unsealed'):
            with self.subTest(failure=failure):
                root, repo, helper, freeze, receipt, latest = self.receipt_fixture()
                if failure == 'helper-changed':
                    helper.write_text('raise AssertionError("must never execute")\n')
                if failure == 'bad-target': latest['target_contract']['sha256'] = 'c' * 64
                if failure == 'bad-review': latest['independent_review']['rounds'][-1]['verdict'] = 'REJECTED'
                if failure == 'broken-chain': freeze.write_text('{}')
                if failure == 'unsealed': latest['status'] = 'draft'
                receipt.write_text(json.dumps(latest))
                with mock.patch.dict('sys.modules'):
                    import sys
                    sys.modules.pop('linux_qa_profile', None)
                    if failure == 'preloaded': sys.modules['linux_qa_profile'] = SimpleNamespace(VALUE='malicious')
                    if failure == 'none':
                        self.assertEqual(evidence.sealed_profile_helper(repo).VALUE, 'authenticated')
                    else:
                        with self.assertRaises(evidence.EvidenceError):
                            evidence.sealed_profile_helper(repo)

    def test_qa_output_path_rejects_escape_and_ancestor_link(self):
        root = propagation.scratch()
        repo = root / 'repo'
        repo.mkdir()
        outside = root / 'outside'
        outside.mkdir()
        (repo / 'linked').symlink_to(outside, target_is_directory=True)
        for path in (repo / '..' / 'outside' / 'out', outside / 'out', repo / 'linked' / 'out'):
            with self.subTest(path=path), self.assertRaises(evidence.EvidenceError):
                evidence.physical_repo_path(repo, path)
        self.assertEqual(evidence.physical_repo_path(repo, Path('new/out')), repo / 'new/out')
        self.assertFalse((repo / 'new').exists())

    def test_raw_gate_identity_and_strict_report(self):
        for scenario in ('pass', 'profile', 'node', 'npm_cli', 'cache', 'recheck_passed', 'missing', 'duplicate', 'linked', 'invocation'):
            with self.subTest(scenario=scenario):
                root, _, profile, qa, _ = fixture()
                out = root / 'run/products'
                out.mkdir(parents=True)
                shared = out.parent / 'qa-profile-evidence'
                shared.mkdir()
                evidence.write_json(shared / 'cache-inventory.json', evidence.tree_inventory(qa['cache']))
                entry = {'id': 'case-1', 'group': 'fixture', 'prompt': {'version': 'v1'}, 'product': str(root / 'product')}
                identity = {'sha256': qa['profile_sha256'], 'node': qa['node'], 'npm_cli': qa['npm_cli'],
                            'cache': str(qa['cache']), 'recheck_passed': True, 'failure_reason': None}
                if scenario in ('profile', 'node', 'npm_cli', 'cache', 'recheck_passed'):
                    identity['sha256' if scenario == 'profile' else scenario] = False if scenario == 'recheck_passed' else 'wrong'
                report = {'passed': True, 'qa_profile': identity, 'gates': [], 'builds_identical': True,
                          'lock_unchanged': True, 'product_unchanged': True, 'vendored_cache_tree_sha256_before': 'a',
                          'vendored_cache_tree_sha256_after': 'a', 'product_files': {}, 'dist_files': {}}
                def fake_gate(argv, log, destination):
                    log.write_bytes(b'gate')
                    record = {'version': 'som.react-gates-invocation.v1', 'argv': [products.PYTHON, *argv],
                              'cwd': str(Path.cwd().resolve()), 'outcome': 'exited', 'exit_code': 0,
                              'wait': {'state': 'completed', 'exit_code': 0}, 'log': log.name,
                              'log_sha256': digest(b'gate'), 'failure': None}
                    if scenario == 'invocation': record['argv'][-1] = '/different-profile'
                    evidence.write_json(destination, record)
                    gates = destination.parent / 'gates'
                    gates.mkdir()
                    target = gates / 'gates.json'
                    if scenario == 'duplicate': target.write_text('{"passed":true,"passed":true}')
                    elif scenario == 'linked':
                        external = root / 'report.json'
                        external.write_text(json.dumps(report))
                        target.symlink_to(external)
                    elif scenario != 'missing': target.write_text(json.dumps(report))
                    return record
                with mock.patch.object(products, 'gate_invocation', side_effect=fake_gate), \
                     mock.patch.object(products, 'python_product', return_value='fixture API unavailable') as api:
                    if scenario == 'pass':
                        row = products.check_case('http://127.0.0.1:18919', entry, out, qa['cache'], {}, qa)
                        self.assertEqual(row['qa_profile_sha256'], qa['profile_sha256'])
                        api.assert_called_once()
                    else:
                        with self.assertRaises(evidence.EvidenceError):
                            products.check_case('http://127.0.0.1:18919', entry, out, qa['cache'], {}, qa)
                        api.assert_not_called()

    def test_qa_case_path_rejects_traversal(self):
        root, _, _, qa, _ = fixture()
        out = root / 'run/products'
        out.mkdir(parents=True)
        for cid in ('..', '../escape', '/absolute', 'a/b', 'a\\b'):
            with self.subTest(cid=cid), self.assertRaises(evidence.EvidenceError):
                products.check_case('http://127.0.0.1:18919', {'id': cid}, out, qa['cache'], {}, qa)
        self.assertFalse((root / 'run/escape').exists())


if __name__ == '__main__':
    unittest.main()

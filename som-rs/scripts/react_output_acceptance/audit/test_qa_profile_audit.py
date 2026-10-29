"""Synthetic Linux QA auditor fixtures. Execute only on GitHub-hosted runners.

All small profiles, receipts, and files here are synthetic. They are not an
authority and do not run a product, compiler, package, server, or subprocess.
"""
import copy
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

if os.environ.get('GITHUB_ACTIONS') != 'true' or os.environ.get('RUNNER_ENVIRONMENT') != 'github-hosted':
    raise RuntimeError('GitHub hosted runner required')
sys.path.insert(0, str(Path(__file__).absolute().parent))
import audit130 as a


def put(path, raw):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)


def document(path, value):
    put(path, json.dumps(value, sort_keys=True, separators=(',', ':')).encode())


def tree_hash(files):
    return hashlib.sha256(b''.join(f'{name}\0{files[name]}\n'.encode() for name in sorted(files, key=Path))).hexdigest()


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def save_latest(audit, root):
    receipt = audit.latest_receipt
    target = digest(a.canonical(receipt['files']))
    receipt['target_contract']['sha256'] = target
    receipt['independent_review']['target_sha256'] = target
    document(root / 'som-rs/evidence/react-output-contract-amendment-001.json', receipt)


def fixture():
    root = Path(tempfile.mkdtemp(prefix='som-react-audit-fixture-')).resolve()
    run = root / 'run'
    run.mkdir()
    evidence = run / 'qa-profile-evidence'
    node_raw, npm_raw, cache_raw, prettier_raw = b'synthetic node', b'synthetic npm', b'synthetic cache', b'synthetic prettier'
    put(evidence / 'node', node_raw)
    put(evidence / 'npm/bin/npm-cli.js', npm_raw)
    put(evidence / 'npm/lib/cli.js', b'synthetic cli file')
    put(evidence / 'npm/lib/cli/entry.js', b'synthetic cli entry')
    put(evidence / 'cache/content/item', cache_raw)
    for path in (evidence / 'npm', evidence / 'npm/bin', evidence / 'npm/lib', evidence / 'npm/lib/cli'):
        path.chmod(0o755)
    for path in (evidence / 'npm/bin/npm-cli.js', evidence / 'npm/lib/cli.js', evidence / 'npm/lib/cli/entry.js'):
        path.chmod(0o644)
    (evidence / 'node').chmod(0o755)
    cache_root = evidence / 'cache'
    for path in (cache_root / 'content/item', cache_root / 'content', cache_root):
        path.chmod(0o555 if path.is_dir() else 0o444)
    npm_files = {'bin/npm-cli.js': digest(npm_raw), 'lib/cli.js': digest(b'synthetic cli file'),
                 'lib/cli/entry.js': digest(b'synthetic cli entry')}
    cache_files, prettier_files = {'content/item': digest(cache_raw)}, {'index.cjs': digest(prettier_raw)}
    formatter = '7' * 64
    profile = {'version': 'som.react-linux-qa-profile.v1', 'host': 'ubuntu-22.04-x86_64',
               'source': {'role': 'synthetic'}, 'node': {'path': 'node/bin/node', 'sha256': digest(node_raw)},
               'npm': {'root': 'node/lib/node_modules/npm', 'sha256': tree_hash(npm_files), 'files': 3, 'symlinks': 0},
               'cache': {'path': 'npm-cache-new', 'sha256': tree_hash(cache_files), 'files': 1, 'symlinks': 0},
               'prettier': {'sha256': tree_hash(prettier_files), 'files': 1, 'symlinks': 0},
               'reference_formatter_sha256': formatter}
    profile_raw = json.dumps(profile, sort_keys=True, separators=(',', ':')).encode()
    put(evidence / 'profile-before.json', profile_raw)
    put(evidence / 'profile-after.json', profile_raw)
    source_shas = {}
    for name in a.QA_SOURCES:
        raw = ('# synthetic ' + name).encode()
        put(root / name, raw)
        source_shas[name] = digest(raw)
    authority = {'version': 'som.react-linux-qa-audit-authority.v1', 'profile_sha256': digest(profile_raw),
                 'profile': profile, 'source_sha256': source_shas}
    retained_sources = {}
    for name, source_path in {'run_gates': 'som-rs/tests/react-output-v1/run_gates.py',
                              'linux_qa_profile': 'som-rs/tests/react-output-v1/linux_qa_profile.py',
                              'retained_evidence': 'som-rs/scripts/react_output_acceptance/retained_evidence.py'}.items():
        retained_sources[name] = {'source_path': source_path}
        for phase in ('before', 'after'):
            relative = f'source/{name}.{phase}.py'
            raw = (root / source_path).read_bytes()
            put(evidence / relative, raw)
            retained_sources[name][phase] = {'path': relative, 'sha256': digest(raw)}
    npm_inventory = {'version': 'som.react-qa-tree-inventory.v1', 'root_mode': 0o755,
                     'entries': {'bin': {'kind': 'directory', 'mode': 0o755},
                                 'bin/npm-cli.js': {'kind': 'file', 'mode': 0o644, 'sha256': digest(npm_raw)},
                                 'lib': {'kind': 'directory', 'mode': 0o755},
                                 'lib/cli': {'kind': 'directory', 'mode': 0o755},
                                 'lib/cli.js': {'kind': 'file', 'mode': 0o644, 'sha256': npm_files['lib/cli.js']},
                                 'lib/cli/entry.js': {'kind': 'file', 'mode': 0o644, 'sha256': npm_files['lib/cli/entry.js']}},
                     'tree_sha256': tree_hash(npm_files), 'file_count': 3, 'symlink_count': 0}
    cache_inventory = {'version': 'som.react-qa-tree-inventory.v1', 'root_mode': 0o555,
                       'entries': {'content': {'kind': 'directory', 'mode': 0o555},
                                   'content/item': {'kind': 'file', 'mode': 0o444, 'sha256': digest(cache_raw)}},
                       'tree_sha256': tree_hash(cache_files), 'file_count': 1, 'symlink_count': 0}
    document(evidence / 'npm-inventory.json', npm_inventory)
    document(evidence / 'cache-inventory.json', cache_inventory)
    document(evidence / 'identity.json', {'version': 'som.react-qa-profile-evidence.v1', 'status': 'passed', 'problems': [],
             'profile': {'before': {'path': 'profile-before.json', 'sha256': digest(profile_raw)},
                         'after': {'path': 'profile-after.json', 'sha256': digest(profile_raw)}},
             'sources': retained_sources,
             'assets': {'node': {'path': 'node', 'sha256': digest(node_raw), 'mode': 0o755},
                        'npm': {'path': 'npm', 'inventory': 'npm-inventory.json', 'tree_sha256': tree_hash(npm_files), 'file_count': 3},
                        'cache': {'path': 'cache', 'inventory': 'cache-inventory.json', 'tree_sha256': tree_hash(cache_files), 'file_count': 1}}})
    document(root / a.QA_AUTHORITY, authority)
    receipt_files = {path: source_shas[path] for path in a.QA_RECEIPT_SOURCES}
    receipt_files[a.QA_AUTHORITY] = digest((root / a.QA_AUTHORITY).read_bytes())
    (root / 'som-rs/evidence/react-output-v1').mkdir(parents=True)
    freeze_path = 'som-rs/evidence/react-output-contract-freeze-2026-10-22.json'
    changed_tool = 'som-rs/tests/react-output-v1/run_gates.py'
    archive_path = 'som-rs/evidence/react-output-contract-archive/synthetic-old-run-gates.py'
    archive_raw = b'# synthetic old run_gates source'
    put(root / archive_path, archive_raw)
    old_files = {changed_tool: digest(archive_raw)}
    old_target = digest(a.canonical(old_files))
    document(root / freeze_path, {'status': 'contract_frozen_before_implementation',
             'implementation_started_before_freeze': False, 'files': old_files,
             'target_contract': {'id': 'som.react-output-contract.v1', 'sha256': old_target}})
    target = digest(a.canonical(receipt_files))
    receipt = {'amendment': 1, 'status': 'amendment_sealed_after_independent_review',
               'implementation_already_in_progress': False, 'files': receipt_files,
               'changed_paths': [changed_tool], 'added_paths': sorted(set(receipt_files) - set(old_files)),
               'changes': [{'path': changed_tool, 'old_sha256': digest(archive_raw),
                            'new_sha256': receipt_files[changed_tool], 'archived_copy': archive_path,
                            'archived_copy_sha256': digest(archive_raw)}],
               'previous_receipt': freeze_path, 'previous_receipt_sha256': digest((root / freeze_path).read_bytes()),
               'target_contract': {'id': 'som.react-output-contract.v1', 'sha256': target, 'previous_sha256': old_target},
               'independent_review': {'target_sha256': target, 'rounds': [{'round': 1, 'verdict': 'APPROVED',
                                      'reviewer': 'synthetic reviewer', 'evidence': 'synthetic reference', 'head': 'a' * 40}]}}
    document(root / 'som-rs/evidence/react-output-contract-amendment-001.json', receipt)
    origin = '/synthetic-unreadable-runner-temp/som-react-linux-qa-profile.json'
    binding = {'path': origin, 'sha256': digest(profile_raw)}
    document(run / 'identity.json', {'qa_profile': {'before': binding, 'after': binding, 'unchanged': True},
             'qa_profile_evidence': {'path': 'qa-profile-evidence/identity.json', 'passed': True,
                                     'sha256': digest((evidence / 'identity.json').read_bytes())}})
    steps = []
    for index, name in enumerate(a.STEPS, 1):
        args = ['/synthetic/python', '/synthetic/' + name]
        if name == 'products':
            args += ['--qa-profile', origin]
        step_receipt = run / 'receipts' / f'{index:02d}-{name}.json'
        document(step_receipt, {'step': name, 'index': index, 'argv': args})
        steps.append({'step': name, 'receipt': str(step_receipt), 'receipt_sha256': digest(step_receipt.read_bytes())})
    document(run / 'run.json', {'qa_profile': binding, 'steps': steps})
    ids = [f'case-{index:03d}' for index in range(129)]
    groups = ('public_examples', 'coverage_cases', 'legacy_in_scope')
    cases = [{'id': cid, 'group': groups[index % len(groups)]} for index, cid in enumerate(ids)]
    manifest = [{'id': case['id'], 'group': case['group'],
                 'product': str(run / 'bundles/products' / case['id'])} for case in cases]
    document(run / 'bundles/products/manifest.json', {'products': manifest})
    origin_root = Path(origin).parent / 'som-react-linux-qa-closure'
    node = str(origin_root / 'node/bin/node')
    cli = str(origin_root / 'node/lib/node_modules/npm/bin/npm-cli.js')
    cache = str(origin_root / 'npm-cache-new')
    document(run / 'products/report.json', {'cases': [{**case, 'qa_profile_sha256': digest(profile_raw)} for case in cases], 'qa_profile': binding,
             'qa_profile_final_recheck': {'passed': True, 'failure_reason': None}, 'vendored_cache': cache})
    audit = a.Audit(root, run)
    audit.acceptance = cases
    audit.manifest = {entry['id']: entry for entry in manifest}
    audit.latest_receipt = receipt
    audit.contracts = {'toolchain.json': {'formatter': {'sha256': formatter, 'prettier_tree_sha256': tree_hash(prettier_files)}}}
    work = run / 'products' / ids[0] / 'gates'
    put(work / 'empty-npmrc', b'')
    put(work / 'prettier-evidence/index.cjs', prettier_raw)
    product = work / 'product'
    product.mkdir()
    commands = {'install': 'npm ci --offline --ignore-scripts --no-audit --no-fund',
                'typecheck': 'npm run typecheck', 'format_check': 'node node_modules/prettier/bin/prettier.cjs --check .',
                'test': 'npm run test', 'build': 'npm run build -- --outDir <dist dir outside the product>'}
    environment = {'PATH': f'{Path(node).parent}:/usr/bin:/bin', 'HOME': str(work), 'npm_config_cache': cache,
                   'npm_config_offline': 'true', 'npm_config_audit': 'false', 'npm_config_fund': 'false',
                   'npm_config_update_notifier': 'false', 'npm_config_logs_dir': str(work / 'npm-logs'),
                   'npm_config_userconfig': str(work / 'empty-npmrc')}
    rows = []
    for name in ('install', 'typecheck', 'format_check', 'test', 'build-1', 'build-2'):
        command = commands['build'].replace('<dist dir outside the product>', str(work / f'dist-{name[-1]}')) if name.startswith('build-') else commands[name]
        words = command.split()
        argv = [node, *([cli] if words[0] == 'npm' else []), *words[1:]]
        raw = ('synthetic ' + name + '\n').encode()
        put(work / f'{name}.log', raw)
        rows.append({'step': name, 'command': command, 'argv': argv, 'cwd': str(product), 'environment': environment.copy(),
                     'outcome': 'exited', 'exit_code': 0, 'wait': {'state': 'completed', 'exit_code': 0},
                     'failure': None, 'log': name + '.log', 'log_sha256': digest(raw)})
    gates = {'environment': {k: v for k, v in environment.items() if k != 'HOME'}, 'gates': rows,
             'passed': True, 'product_unchanged': True, 'lock_unchanged': True, 'builds_identical': True,
             'vendored_cache_tree_sha256_before': tree_hash(cache_files),
             'vendored_cache_tree_sha256_after': tree_hash(cache_files),
             'qa_profile': {'sha256': digest(profile_raw), 'node': node, 'npm_cli': cli, 'cache': cache,
                            'recheck_passed': True, 'failure_reason': None,
                            'prettier_evidence': {'path': 'prettier-evidence', 'sha256': tree_hash(prettier_files),
                                                  'files': 1, 'symlinks': 0}}}
    snapshot = {'version': 'som.m6-cache-snapshot.v1', 'status': 'passed', 'files': cache_files,
                'tree_sha256': tree_hash(cache_files), 'file_count': 1, 'symlink_count': 0}
    document(work.parent / 'cache-before.json', snapshot)
    document(work.parent / 'cache-after.json', snapshot)
    document(work.parent / 'cache-evidence-before.json', cache_inventory)
    document(work.parent / 'cache-evidence-after.json', cache_inventory)
    raw_invocation_log = b'synthetic gate invocation\n'
    put(work.parent / 'gates.log', raw_invocation_log)
    invocation = {'version': 'som.react-gates-invocation.v1',
                  'argv': [str(root / 'som-rs/runtime/acceptance-m3-venv/bin/python'),
                           str(root / 'som-rs/tests/react-output-v1/run_gates.py'),
                           audit.manifest[ids[0]]['product'], str(work), '--qa-profile', origin],
                  'cwd': str(root), 'outcome': 'exited', 'exit_code': 0,
                  'wait': {'state': 'completed', 'exit_code': 0}, 'log': 'gates.log',
                  'log_sha256': digest(raw_invocation_log), 'failure': None}
    document(work.parent / 'gates-invocation.json', invocation)
    return audit, root, run, profile, commands, gates, work


class LinuxQaAuditFixtures(unittest.TestCase):
    def test_sealed_synthetic_context_and_all_six_raw_children(self):
        audit, _, _, _, commands, gates, work = fixture()
        self.assertEqual(audit.qa_profile_context()[0], 'passed')
        audit.qa_product_evidence(work.parent, gates, commands)

    def test_npm_tree_uses_frozen_path_order_and_rejects_lexical_hash(self):
        audit, _, run, _, _, _, _ = fixture()
        root = run / 'qa-profile-evidence/npm'
        inventory = audit.qa_inventory(root)
        files = {name: row['sha256'] for name, row in inventory['entries'].items() if row['kind'] == 'file'}
        self.assertLess(sorted(files).index('lib/cli.js'), sorted(files).index('lib/cli/entry.js'))
        ordered_paths = sorted(files, key=Path)
        self.assertLess(ordered_paths.index('lib/cli/entry.js'), ordered_paths.index('lib/cli.js'))
        lexical_sha = digest(b''.join(f'{name}\0{files[name]}\n'.encode() for name in sorted(files)))
        self.assertNotEqual(lexical_sha, inventory['tree_sha256'])
        self.assertEqual(inventory['tree_sha256'], tree_hash(files))
        self.assertEqual(audit.qa_profile_context()[0], 'passed')
        path = run / 'qa-profile-evidence/npm-inventory.json'
        recorded = json.loads(path.read_bytes())
        recorded['tree_sha256'] = lexical_sha
        document(path, recorded)
        with self.assertRaises(a.EvidenceError):
            audit.qa_profile_context()

    def test_each_manifest_and_product_group_and_profile_match_frozen_case(self):
        for failure in ('manifest_group_missing', 'manifest_group_wrong', 'manifest_case_missing',
                        'product_group_missing', 'product_group_wrong',
                        'product_profile_missing', 'product_profile_wrong'):
            with self.subTest(failure=failure):
                audit, _, run, _, _, _, _ = fixture()
                manifest_path = run / 'bundles/products/manifest.json'
                report_path = run / 'products/report.json'
                manifest = json.loads(manifest_path.read_bytes())
                report = json.loads(report_path.read_bytes())
                if failure == 'manifest_group_missing':
                    del manifest['products'][-1]['group']
                elif failure == 'manifest_group_wrong':
                    manifest['products'][-1]['group'] = 'wrong'
                elif failure == 'manifest_case_missing':
                    manifest['products'].pop()
                elif failure == 'product_group_missing':
                    del report['cases'][-1]['group']
                elif failure == 'product_group_wrong':
                    report['cases'][-1]['group'] = 'wrong'
                elif failure == 'product_profile_missing':
                    del report['cases'][-1]['qa_profile_sha256']
                else:
                    report['cases'][-1]['qa_profile_sha256'] = '0' * 64
                document(manifest_path, manifest)
                document(report_path, report)
                with self.assertRaises(a.EvidenceError):
                    audit.qa_profile_context()

    def test_legacy_context_does_not_require_a_linux_authority(self):
        root = Path(tempfile.mkdtemp(prefix='som-react-legacy-fixture-')).resolve()
        run = root / 'run'
        run.mkdir()
        document(run / 'run.json', {'steps': []})
        audit = a.Audit(root, run)
        self.assertEqual(audit.qa_profile_context()[0], 'passed')
        self.assertFalse(audit.qa_requested)
        self.assertIsNone(audit.qa_profile)

    def test_script_sources_are_bound_by_authority_without_entering_receipt_map(self):
        for path in (name for name in a.QA_SOURCES if name not in a.QA_RECEIPT_SOURCES):
            with self.subTest(path=path):
                audit, root, _, _, _, _, _ = fixture()
                self.assertNotIn(path, audit.latest_receipt['files'])
                self.assertEqual(audit.qa_profile_context()[0], 'passed')
                audit, root, _, _, _, _, _ = fixture()
                (root / path).write_bytes(b'tampered synthetic script')
                with self.assertRaises(a.EvidenceError):
                    audit.qa_profile_context()

    def test_contract_tool_source_also_requires_direct_receipt_binding(self):
        audit, _, _, _, _, _, _ = fixture()
        audit.latest_receipt['files'].pop(next(iter(a.QA_RECEIPT_SOURCES)))
        with mock.patch.object(audit, 'qa_receipt_chain'):
            with self.assertRaisesRegex(a.EvidenceError, 'contract tool missing'):
                audit.qa_profile_context()

    def test_latest_review_and_full_receipt_chain_are_recomputed(self):
        failures = ('not_approved', 'review_target', 'reviewer_missing', 'evidence_missing', 'head_missing',
                    'implementation_started', 'target_map',
                    'previous_receipt_hash', 'previous_target_hash', 'older_map', 'unexpected_contract_file')
        for failure in failures:
            with self.subTest(failure=failure):
                audit, root, _, _, _, _, _ = fixture()
                receipt = audit.latest_receipt
                if failure == 'not_approved':
                    receipt['independent_review']['rounds'][-1]['verdict'] = 'CHANGES_REQUESTED'
                elif failure == 'review_target':
                    receipt['independent_review']['target_sha256'] = 'f' * 64
                elif failure in ('reviewer_missing', 'evidence_missing', 'head_missing'):
                    receipt['independent_review']['rounds'][-1].pop(failure.removesuffix('_missing'))
                elif failure == 'implementation_started':
                    receipt['implementation_already_in_progress'] = True
                elif failure == 'target_map':
                    receipt['target_contract']['sha256'] = 'f' * 64
                    receipt['independent_review']['target_sha256'] = 'f' * 64
                elif failure == 'previous_receipt_hash':
                    receipt['previous_receipt_sha256'] = 'f' * 64
                elif failure == 'previous_target_hash':
                    receipt['target_contract']['previous_sha256'] = 'f' * 64
                elif failure == 'older_map':
                    previous = root / receipt['previous_receipt']
                    old = json.loads(previous.read_bytes())
                    old['files'][next(iter(old['files']))] = 'f' * 64
                    document(previous, old)
                    receipt['previous_receipt_sha256'] = digest(previous.read_bytes())
                else:
                    put(root / 'som-rs/tests/react-output-v1/unexpected.py', b'# unexpected synthetic contract tool')
                document(root / 'som-rs/evidence/react-output-contract-amendment-001.json', receipt)
                with self.assertRaises(a.EvidenceError):
                    audit.qa_profile_context()

    def test_amendment_map_diff_and_archived_bytes_are_required(self):
        for failure in ('removed', 'changed_paths', 'added_paths', 'changes', 'old_sha', 'new_sha',
                        'archive_missing', 'archive_changed'):
            with self.subTest(failure=failure):
                audit, root, _, _, _, _, _ = fixture()
                receipt = audit.latest_receipt
                change = receipt['changes'][0]
                if failure == 'removed':
                    receipt['files'].pop(change['path'])
                    save_latest(audit, root)
                elif failure == 'changed_paths':
                    receipt['changed_paths'] = []
                elif failure == 'added_paths':
                    receipt['added_paths'] = []
                elif failure == 'changes':
                    receipt['changes'] = []
                elif failure == 'old_sha':
                    change['old_sha256'] = 'f' * 64
                elif failure == 'new_sha':
                    change['new_sha256'] = 'f' * 64
                elif failure == 'archive_missing':
                    change['archived_copy'] = 'som-rs/evidence/react-output-contract-archive/missing.py'
                else:
                    (root / change['archived_copy']).write_bytes(b'wrong archived source')
                document(root / 'som-rs/evidence/react-output-contract-amendment-001.json', receipt)
                with self.assertRaises(a.EvidenceError):
                    audit.qa_profile_context()

    def test_retained_node_requires_executable_mode_even_if_receipts_agree(self):
        audit, _, run, _, _, _, _ = fixture()
        evidence = run / 'qa-profile-evidence'
        (evidence / 'node').chmod(0o644)
        retained_identity = json.loads((evidence / 'identity.json').read_bytes())
        retained_identity['assets']['node']['mode'] = 0o644
        document(evidence / 'identity.json', retained_identity)
        run_identity = json.loads((run / 'identity.json').read_bytes())
        run_identity['qa_profile_evidence']['sha256'] = digest((evidence / 'identity.json').read_bytes())
        document(run / 'identity.json', run_identity)
        with self.assertRaisesRegex(a.EvidenceError, 'not executable'):
            audit.qa_profile_context()

    def test_missing_authority_or_retained_profile_is_not_accepted(self):
        audit, root, _, _, _, _, _ = fixture()
        audit.latest_receipt['files'].pop(a.QA_AUTHORITY)
        with self.assertRaises(a.EvidenceError) as caught:
            audit.qa_profile_context()
        self.assertIn(caught.exception.status, ('failed', 'unrun'))
        audit, root, run, _, _, _, _ = fixture()
        # Repoint one expected source to a missing file without deleting evidence.
        (run / 'qa-profile-evidence/profile-before.json').write_bytes(b'changed')
        with self.assertRaises(a.EvidenceError):
            audit.qa_profile_context()

    def test_qa_identity_cannot_be_hidden_by_removing_run_profile(self):
        audit, _, run, _, _, _, _ = fixture()
        run_doc = json.loads((run / 'run.json').read_bytes())
        run_doc.pop('qa_profile')
        document(run / 'run.json', run_doc)
        with self.assertRaises(a.EvidenceError):
            audit.qa_profile_context()

    def test_profile_origin_step_and_identity_are_independently_bound(self):
        for failure in ('source', 'identity', 'step', 'inventory', 'cache'):
            with self.subTest(failure=failure):
                audit, root, run, _, _, _, _ = fixture()
                if failure == 'source':
                    (root / a.QA_SOURCES[0]).write_bytes(b'changed')
                elif failure == 'identity':
                    document(run / 'identity.json', {'qa_profile': {'before': {}, 'after': {}}})
                elif failure == 'step':
                    run_doc = json.loads((run / 'run.json').read_bytes())
                    receipt = Path(run_doc['steps'][-1]['receipt'])
                    document(receipt, {'step': 'products', 'index': 12,
                                       'argv': ['/synthetic/python', '--qa-profile', 'wrong']})
                    run_doc['steps'][-1]['receipt_sha256'] = digest(receipt.read_bytes())
                    document(run / 'run.json', run_doc)
                elif failure == 'inventory':
                    report = json.loads((run / 'products/report.json').read_bytes())
                    report['cases'].pop()
                    document(run / 'products/report.json', report)
                else:
                    cache = run / 'qa-profile-evidence/cache/content/item'
                    cache.chmod(0o644)
                    cache.write_bytes(b'changed')
                    cache.chmod(0o444)
                with self.assertRaises(a.EvidenceError):
                    audit.qa_profile_context()
                self.assertIsNone(audit.qa_profile)

    def test_each_child_requires_matching_argv_env_cwd_wait_and_raw_log(self):
        mutations = ('argv', 'cwd', 'environment', 'outcome', 'exit_code', 'wait', 'log', 'log_sha256', 'missing', 'failure_missing')
        for field in mutations:
            with self.subTest(field=field):
                audit, _, _, _, commands, gates, work = fixture()
                audit.qa_profile_context()
                altered = copy.deepcopy(gates)
                if field == 'missing':
                    altered['gates'].pop()
                elif field == 'failure_missing':
                    altered['gates'][0].pop('failure')
                elif field == 'environment':
                    altered['gates'][0][field]['npm_config_offline'] = 'false'
                elif field == 'wait':
                    altered['gates'][0][field] = {'state': 'completed', 'exit_code': False}
                elif field == 'exit_code':
                    altered['gates'][0][field] = 1
                else:
                    altered['gates'][0][field] = 'changed'
                with self.assertRaises(a.EvidenceError):
                    audit.qa_product_evidence(work.parent, altered, commands)

    def test_cache_snapshot_is_recomputed_from_retained_tree(self):
        for failure in ('before', 'after', 'reported_hash'):
            with self.subTest(failure=failure):
                audit, _, _, _, commands, gates, work = fixture()
                audit.qa_profile_context()
                if failure == 'reported_hash':
                    gates['vendored_cache_tree_sha256_after'] = '0' * 64
                else:
                    snapshot_path = work.parent / f'cache-{failure}.json'
                    snapshot = json.loads(snapshot_path.read_bytes())
                    snapshot['files']['content/item'] = '0' * 64
                    document(snapshot_path, snapshot)
                with self.assertRaises(a.EvidenceError):
                    audit.qa_product_evidence(work.parent, gates, commands)

    def test_same_profile_and_six_records_are_checked_for_all_129_cases(self):
        audit, _, run, _, commands, first_gates, first_work = fixture()
        audit.qa_profile_context()
        for index in range(129):
            case_work = run / 'products' / f'case-{index:03d}'
            gate_work = case_work / 'gates'
            if index == 0:
                gates = first_gates
            else:
                put(gate_work / 'empty-npmrc', b'')
                put(gate_work / 'prettier-evidence/index.cjs', b'synthetic prettier')
                (gate_work / 'product').mkdir()
                for name in ('install', 'typecheck', 'format_check', 'test', 'build-1', 'build-2'):
                    put(gate_work / f'{name}.log', (first_work / f'{name}.log').read_bytes())
                for name in ('cache-before.json', 'cache-after.json', 'cache-evidence-before.json', 'cache-evidence-after.json', 'gates.log'):
                    put(case_work / name, (first_work.parent / name).read_bytes())
                invocation = json.loads((first_work.parent / 'gates-invocation.json').read_bytes())
                invocation['argv'][2] = audit.manifest[f'case-{index:03d}']['product']
                invocation['argv'][3] = str(gate_work)
                document(case_work / 'gates-invocation.json', invocation)
                text = json.dumps(first_gates).replace(str(first_work), str(gate_work))
                gates = json.loads(text)
            audit.qa_product_evidence(case_work, gates, commands)

    def test_gate_invocation_and_cache_mode_evidence_are_required(self):
        for failure in ('argv', 'cwd', 'wait', 'log_hash', 'failure_missing', 'cache_mode'):
            with self.subTest(failure=failure):
                audit, _, _, _, commands, gates, work = fixture()
                audit.qa_profile_context()
                if failure == 'cache_mode':
                    path = work.parent / 'cache-evidence-after.json'
                    inventory = json.loads(path.read_bytes())
                    inventory['entries']['content/item']['mode'] = 0o644
                    document(path, inventory)
                else:
                    path = work.parent / 'gates-invocation.json'
                    invocation = json.loads(path.read_bytes())
                    if failure == 'argv':
                        invocation['argv'][-1] = '/other/profile'
                    elif failure == 'cwd':
                        invocation['cwd'] = '/other'
                    elif failure == 'wait':
                        invocation['wait'] = {'state': 'unverified', 'exit_code': None}
                    elif failure == 'failure_missing':
                        invocation.pop('failure')
                    else:
                        invocation['log_sha256'] = '0' * 64
                    document(path, invocation)
                with self.assertRaises(a.EvidenceError):
                    audit.qa_product_evidence(work.parent, gates, commands)

    def test_contradictory_qa_or_postcheck_verdict_never_passes(self):
        for failure in ('recheck', 'reason', 'reason_missing', 'postcheck', 'passed', 'product', 'lock', 'build'):
            with self.subTest(failure=failure):
                audit, _, _, _, commands, gates, work = fixture()
                audit.qa_profile_context()
                if failure == 'recheck':
                    gates['qa_profile']['recheck_passed'] = False
                elif failure == 'reason':
                    gates['qa_profile']['failure_reason'] = 'synthetic evidence failure'
                elif failure == 'reason_missing':
                    gates['qa_profile'].pop('failure_reason')
                elif failure == 'postcheck':
                    gates['postcheck_errors'] = ['synthetic scan failure']
                else:
                    gates[{'passed': 'passed', 'product': 'product_unchanged',
                           'lock': 'lock_unchanged', 'build': 'builds_identical'}[failure]] = False
                with self.assertRaises(a.EvidenceError):
                    audit.qa_product_evidence(work.parent, gates, commands)

    def test_missing_final_recheck_failure_reason_is_rejected(self):
        audit, _, run, _, _, _, _ = fixture()
        report = json.loads((run / 'products/report.json').read_bytes())
        report['qa_profile_final_recheck'].pop('failure_reason')
        document(run / 'products/report.json', report)
        with self.assertRaises(a.EvidenceError):
            audit.qa_profile_context()

    def test_retained_tree_rejects_carriage_return_names(self):
        audit, _, run, _, _, _, _ = fixture()
        put(run / 'qa-profile-evidence/npm/bad\rname', b'synthetic')
        with self.assertRaisesRegex(a.EvidenceError, 'unsafe retained QA tree name'):
            audit.qa_profile_context()

    def test_nested_boolean_cannot_impersonate_numeric_zero_or_true(self):
        for failure in ('npm_inventory', 'cache_inventory', 'profile_authority', 'identity_binding',
                        'case_inventory', 'prettier_inventory'):
            with self.subTest(failure=failure):
                audit, root, run, _, commands, gates, work = fixture()
                if failure in ('npm_inventory', 'cache_inventory'):
                    path = run / 'qa-profile-evidence' / (failure.replace('_', '-') + '.json')
                    data = json.loads(path.read_bytes())
                    data['symlink_count'] = False
                    document(path, data)
                elif failure == 'profile_authority':
                    path = root / a.QA_AUTHORITY
                    data = json.loads(path.read_bytes())
                    data['profile']['cache']['symlinks'] = False
                    document(path, data)
                    audit.latest_receipt['files'][a.QA_AUTHORITY] = digest(path.read_bytes())
                    save_latest(audit, root)
                elif failure == 'identity_binding':
                    path = run / 'identity.json'
                    data = json.loads(path.read_bytes())
                    data['qa_profile_evidence']['passed'] = 1
                    document(path, data)
                if failure in ('npm_inventory', 'cache_inventory', 'profile_authority', 'identity_binding'):
                    with self.assertRaises(a.EvidenceError):
                        audit.qa_profile_context()
                else:
                    audit.qa_profile_context()
                    if failure == 'case_inventory':
                        path = work.parent / 'cache-evidence-before.json'
                        data = json.loads(path.read_bytes())
                        data['symlink_count'] = False
                        document(path, data)
                    else:
                        gates['qa_profile']['prettier_evidence']['symlinks'] = False
                    with self.assertRaises(a.EvidenceError):
                        audit.qa_product_evidence(work.parent, gates, commands)

    def test_qa_raw_json_rejects_duplicate_keys_and_nonfinite_numbers(self):
        for raw in (b'{"a":0,"a":1}', b'{"number":NaN}', b'{"number":Infinity}'):
            with self.subTest(raw=raw), self.assertRaises(a.EvidenceError):
                a.strict_qa_json(raw)
        audit, _, _, _, commands, gates, work = fixture()
        audit.qa_profile_context()
        put(work.parent / 'gates-invocation.json', b'{"version":1,"version":2}')
        with self.assertRaises(a.EvidenceError):
            audit.qa_product_evidence(work.parent, gates, commands)

    def test_boolean_false_is_not_a_successful_product_or_api_exit(self):
        for failure in ('gates', 'api_direct', 'api_collector'):
            with self.subTest(failure=failure):
                audit, _, _, _, commands, gates, work = fixture()
                cid = 'case-000'
                audit.qa_requested = False
                gates.pop('qa_profile')
                document(work / 'gates.json', gates)
                audit.contracts['toolchain.json']['commands'] = commands
                product_row = {'exit_codes': {'gates': False if failure == 'gates' else 0,
                                              'api_contract': False if failure == 'api_collector' else 0}}
                audit.manifest[cid]['prompt'] = {'version': 'som.prompt.v1', 'resource': {'fields': []}}
                if failure != 'gates':
                    facts = {'query': False, 'filter': False, 'before': False,
                             'not_nullable': False, 'required': False, 'unique': False}
                    document(work.parent / 'api/report.json', {'exit_code': False if failure == 'api_direct' else 0,
                             'result': {'steps': [], 'facts': facts}})
                    audit.contracts['api-contract.json'] = {'steps': [], 'skip_reasons': {}}
                with mock.patch.object(audit, 'find', return_value=product_row):
                    with self.assertRaisesRegex(a.EvidenceError, 'exit'):
                        audit.product_gate(cid, 1 if failure == 'gates' else 7)


if __name__ == '__main__':
    unittest.main()

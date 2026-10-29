"""Synthetic Linux API context checks. Execute only on GitHub hosted runners."""
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

if os.environ.get('GITHUB_ACTIONS') != 'true' or os.environ.get('RUNNER_ENVIRONMENT') != 'github-hosted':
    raise RuntimeError('GitHub hosted runner required')

import linux_api_context as api


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def fixture():
    root = Path(tempfile.mkdtemp(prefix='som-api-context-', dir=os.environ['RUNNER_TEMP'])).resolve()
    repo, runtime, closure = root / 'repo', root / 'runtime', root / 'closure'
    repo.mkdir()
    runtime.mkdir()
    closure.mkdir()
    venv = closure / 'venv'
    venv.mkdir()
    node_profile = root / 'node.json'
    node_profile.write_bytes(b'node-profile')
    profile = {'node_profile': str(node_profile), 'runtime_root': str(runtime), 'closure_root': str(closure),
               'venv': str(venv), 'live_receipt': str(closure / 'live.json')}
    (closure / 'live.json').write_bytes(b'live receipt')
    api_path = root / 'profile.json'
    api_path.write_bytes(b'api profile')
    auth = {'node_profile_sha256': digest(node_profile.read_bytes()),
            'source_sha256': {api.CONTEXT_SOURCE: 'a' * 64, api.NODE_SOURCE: 'b' * 64, api.LIVE_SOURCE: 'c' * 64,
                              api.RECORDER_SOURCE: '3' * 64, api.HOST_SOURCE: '4' * 64, api.EVIDENCE_SOURCE: '6' * 64,
                              **{name: '5' * 64 for name in api.LIVE_DEPENDENCIES | api.API_CHAIN}}}
    node = {'node': '/synthetic/node', 'npm_cli': '/synthetic/npm-cli.js', 'cache': Path('/synthetic/cache'),
            'profile_path': node_profile, 'profile_sha256': auth['node_profile_sha256']}
    node_module = SimpleNamespace(NPM_TREE=api.NPM_TREE, validate_profile=mock.Mock(return_value=node), recheck=mock.Mock())
    live = {'status': 'verified-live-inputs', 'python': str(venv) + '/bin/python',
            'pg_bin': str(runtime) + '/install/postgresql/bin', 'live_receipt_sha256': digest(b'live receipt'),
            'runtime_receipt_sha256': 'd' * 64, 'closure_receipt_sha256': 'e' * 64,
            'venv_inventory_sha256': 'f' * 64, 'requirements_count': 39, 'wheel_count': 40}
    live_module = SimpleNamespace(verify=mock.Mock(return_value=live))
    state = (repo, api_path, profile, auth, '1' * 64, digest(b'api profile'), digest(b'live receipt'))
    return state, node_module, live_module


class LinuxApiContextTests(unittest.TestCase):
    def admitted(self, state, node, live):
        return api.verify(*state, node, live)

    def test_source_bound_repository_imports_and_preload_refusal(self):
        # Real imports, direct-by-path builder load, and a poisoned cache file.
        # All source files here are synthetic and run only on GitHub.
        for role in ('host', 'contract'):
            with self.subTest(role=role), tempfile.TemporaryDirectory(dir=os.environ['RUNNER_TEMP']) as directory:
                root = Path(directory).resolve()
                tree = ('tree_sha', 'som-rs/tests/' + ('typescript' if role == 'host' else 'react') + '-output-v1/tree_sha.py')
                chain = ((api.IMPORT_CHAIN if role == 'host' else (('rust_profile', 'som-rs/tests/rust-output-v1/rust_profile.py'),))
                         + (tree,) + (api.HOST_CHAIN if role == 'host' else api.CONTRACT_CHAIN))
                values = {name: b'value = "sealed"\n' for name, _ in chain}
                values['common'] = (b'import importlib.util, pathlib\n'
                    b'p=pathlib.Path(__file__).parents[2] / "tests/rust-output-v1/build_negative_cases.py"\n'
                    b's=importlib.util.spec_from_file_location("rust_build_negative_cases", p)\n'
                    b'm=importlib.util.module_from_spec(s); s.loader.exec_module(m)\n'
                    b'value=m.value\n')
                values['check_contract'] = b'import common\nvalue=common.value\n'
                values['typescript.host'] = b'import hosts, tree_sha\nvalue=hosts.value + tree_sha.value\n'
                hashes = {}
                for name, rel in chain:
                    path = root / rel
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(values[name])
                    hashes[rel] = digest(values[name])
                    cache = path.parent / '__pycache__'
                    cache.mkdir(exist_ok=True)
                    (cache / (path.stem + '.cpython-310.pyc')).write_bytes(b'poisoned-bytecode')
                if role == 'contract':
                    builder = root / api.RUST_BUILDER
                    builder.parent.mkdir(parents=True, exist_ok=True)
                    builder.write_bytes(b'value = "builder"\n')
                    hashes[api.RUST_BUILDER] = digest(builder.read_bytes())
                names = {name for name, _ in chain} | {'rust_build_negative_cases'}
                with mock.patch.dict(sys.modules):
                    for name in names:
                        sys.modules.pop(name, None)
                    result = api.bind_repository_imports(root, hashes, role)
                    self.assertEqual(result.value, 'sealedsealed' if role == 'host' else 'builder')
                    self.assertEqual(result.__file__, str(root / chain[-1][1]))
                with mock.patch.dict(sys.modules):
                    for name in names:
                        sys.modules.pop(name, None)
                    sys.modules[chain[-1][0]] = SimpleNamespace()
                    with self.assertRaises(api.ContextError):
                        api.bind_repository_imports(root, hashes, role)
                with mock.patch.dict(sys.modules):
                    for name in names:
                        sys.modules.pop(name, None)
                    (root / chain[-1][1]).write_bytes(b'raise RuntimeError("must not run")\n')
                    with self.assertRaises(api.ContextError):
                        api.bind_repository_imports(root, hashes, role)
                    self.assertTrue(all(name not in sys.modules for name in names))

    def test_bound_context_identity_and_recheck(self):
        state, node, live = fixture()
        with mock.patch.object(api, 'tool', side_effect=lambda path: {'path': path, 'sha256': '2' * 64}), \
             mock.patch.object(api, 'case_evidence', return_value={'fixture': 'case'}):
            context = self.admitted(state, node, live)
            self.assertEqual(context['node_profile_path'], Path(state[2]['node_profile']))
            self.assertEqual(context['context_path'], state[1])
            self.assertEqual(context['profile_sha256'], state[3]['node_profile_sha256'])
            self.assertEqual(context['python'], state[2]['venv'] + '/bin/python')
            self.assertEqual(context['pg_bin'], state[2]['runtime_root'] + '/install/postgresql/bin')
            with mock.patch.object(api, 'inputs', return_value=state), mock.patch.object(api, 'source_bytes'):
                api.recheck(context)
                live.verify.return_value['venv_inventory_sha256'] = '0' * 64
                with self.assertRaises(api.ContextError):
                    api.recheck(context)

    def test_unreviewed_npm_and_live_input_missing_or_wrong_fail(self):
        state, node, live = fixture()
        with mock.patch.object(api, 'tool', side_effect=lambda path: {'path': path}):
            node.NPM_TREE = ('69965a8f2a75cad99018e333ff1819bdc8cbf4f17570e77f24ad5f95aa57e558', 1921, 0)
            with self.assertRaises(api.ContextError):
                self.admitted(state, node, live)
            node.NPM_TREE = api.NPM_TREE
            for field, value in [('status', 'unrun'), ('requirements_count', 38), ('wheel_count', 39),
                                 ('live_receipt_sha256', '0' * 64), ('python', '/other/python'),
                                 ('pg_bin', '/other/pg')]:
                with self.subTest(field=field):
                    saved = live.verify.return_value[field]
                    live.verify.return_value[field] = value
                    with self.assertRaises(api.ContextError):
                        self.admitted(state, node, live)
                    live.verify.return_value[field] = saved
            live.verify.return_value.pop('venv_inventory_sha256')
            with self.assertRaises(api.ContextError):
                self.admitted(state, node, live)

    def test_missing_unsealed_helper_and_preload_refuse_before_exec(self):
        state, node, _ = fixture()
        with mock.patch.object(api, 'inputs', return_value=state), \
             mock.patch.object(api, 'load_bound', side_effect=[node, api.ContextError('live verifier unavailable')]):
            with self.assertRaises(api.ContextError):
                api.validate(state[1], state[0])
        repo = state[0]
        rel = 'helper.py'
        (repo / rel).write_bytes(b'raise AssertionError("must never execute")\n')
        with mock.patch.dict(sys.modules, {'synthetic_api_helper': SimpleNamespace()}):
            with self.assertRaises(api.ContextError):
                api.load_bound(repo, rel, digest((repo / rel).read_bytes()), 'synthetic_api_helper')
        with self.assertRaises(api.ContextError):
            api.load_bound(repo, rel, '0' * 64, 'synthetic_api_helper')

    def test_only_explicit_caller_bound_node_helper_can_be_reused(self):
        state, node, live = fixture()
        node.__file__ = str(state[0] / api.NODE_SOURCE)
        with mock.patch.object(api, 'inputs', return_value=state), mock.patch.object(api, 'source_bytes'), \
             mock.patch.object(api, 'load_bound', return_value=live), \
             mock.patch.object(api, 'tool', side_effect=lambda path: {'path': path}), \
             mock.patch.object(api, 'case_evidence', return_value={'fixture': 'case'}), \
             mock.patch.dict(sys.modules, {'linux_qa_profile': node}):
            result = api.validate(state[1], state[0], bound_node_module=node)
            self.assertIs(result['_node_module'], node)
            with self.assertRaises(api.ContextError):
                api.validate(state[1], state[0], bound_node_module=SimpleNamespace())

    def test_raw_profiles_and_sources_reject_duplicate_json_and_symlinks(self):
        state, _, _ = fixture()
        root = state[0]
        with self.assertRaises(api.ContextError):
            api.parse(b'{"version":1,"version":2}')
        target = root / 'target'
        target.write_bytes(b'data')
        linked = root / 'link'
        linked.symlink_to(target)
        with self.assertRaises(api.ContextError):
            api.read(linked, 100)

    def test_authority_is_receipt_bound_and_requires_complete_source_inventory(self):
        state, _, _ = fixture()
        repo = state[0]
        names = (api.CONTEXT_SOURCE, api.NODE_SOURCE, api.LIVE_SOURCE, api.RECORDER_SOURCE, api.HOST_SOURCE, api.EVIDENCE_SOURCE, *api.LIVE_DEPENDENCIES, *api.API_CHAIN)
        for path in (repo / name for name in names):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(path.name.encode())
        sources = {name: digest((repo / name).read_bytes()) for name in names}
        document = {'version': 'som.react-linux-api-authority.v1', 'api_profile_sha256': 'a' * 64,
                    'node_profile_sha256': 'b' * 64, 'source_sha256': sources}
        path = repo / api.AUTHORITY
        path.parent.mkdir(parents=True, exist_ok=True)
        def bind(value):
            raw = json.dumps(value, sort_keys=True).encode()
            path.write_bytes(raw)
            return {api.AUTHORITY: digest(raw), api.NODE_SOURCE: sources[api.NODE_SOURCE],
                    'som-rs/tests/react-output-v1/run_api_contract.py': sources['som-rs/tests/react-output-v1/run_api_contract.py'],
                    'som-rs/tests/react-output-v1/api_contract.mjs': sources['som-rs/tests/react-output-v1/api_contract.mjs']}
        with mock.patch.object(api, 'receipt_files', return_value=bind(document)):
            actual, _ = api.authority(repo)
            self.assertEqual(actual, document)
            path.write_bytes(b'{}')
            with self.assertRaises(api.ContextError):
                api.authority(repo)
        for mutation in ('missing-verifier', 'unknown-member', 'unsealed-node'):
            with self.subTest(mutation=mutation):
                altered = json.loads(json.dumps(document))
                if mutation == 'missing-verifier':
                    del altered['source_sha256'][api.LIVE_SOURCE]
                if mutation == 'unknown-member':
                    altered['unexpected'] = True
                files = bind(altered)
                if mutation == 'unsealed-node':
                    files.pop(api.NODE_SOURCE)
                with mock.patch.object(api, 'receipt_files', return_value=files), self.assertRaises(api.ContextError):
                    api.authority(repo)

    def test_case_admission_joins_full_before_and_refuses_changed_identity(self):
        state, _, _ = fixture()
        repo, profile_path, profile, auth, auth_sha, profile_sha, live_sha = state
        run = repo / 'run'
        evidence = run / 'api-qa-evidence'
        evidence.mkdir(parents=True)
        before = evidence / 'full-before.json'
        baseline = {'version': 'som.react-linux-api-full-validation.v1', 'node': '/fixture/node',
                    'npm_cli': '/fixture/npm', 'cache': '/fixture/cache', 'python': '/fixture/python',
                    'pg_bin': '/fixture/pg', 'os_tools': {'fixture': True},
                    'runtime_receipt_sha256': 'd' * 64, 'closure_receipt_sha256': 'e' * 64,
                    'venv_inventory_sha256': 'f' * 64}
        before.write_text(json.dumps(baseline))
        with mock.patch.object(api, 'inputs', return_value=state), \
             mock.patch.object(api, 'case_evidence', return_value=baseline) as actual:
            context = api.validate_case(profile_path, repo, before)
            self.assertEqual(context['full_before_path'], before)
            self.assertEqual(context['full_before_sha256'], digest(before.read_bytes()))
            api.recheck_case(context)
            actual.return_value = {**baseline, 'python': '/different/python'}
            with self.assertRaises(api.ContextError):
                api.recheck_case(context)
            actual.return_value = baseline
            with self.assertRaises(api.ContextError):
                api.full_record(context)
            context['_node_module'], context['_live_module'] = object(), object()
            self.assertEqual(api.full_record(context), baseline)
            actual.return_value = {**baseline, 'python': '/different/python'}
            with self.assertRaises(api.ContextError):
                api.full_record(context)
        outside = repo.parent / 'api-qa-evidence/full-before.json'
        outside.parent.mkdir()
        outside.write_text(json.dumps(baseline))
        with mock.patch.object(api, 'inputs', return_value=state), self.assertRaises(api.ContextError):
            api.validate_case(profile_path, repo, outside)
        linked = evidence / 'linked.json'
        linked.symlink_to(before)
        with mock.patch.object(api, 'inputs', return_value=state), self.assertRaises(api.ContextError):
            api.validate_case(profile_path, repo, linked)


if __name__ == '__main__':
    unittest.main()

"""Source/data fixtures for current build provenance. Execute only on GitHub.

These fixtures never turn an invented command record into a successful build.
Positive tests cover real Git files/archives and isolated data rules only.
"""
from __future__ import annotations

from contextlib import contextmanager
import copy
import hashlib
import importlib.machinery
import importlib.util
import io
import json
import marshal
import os
from pathlib import Path
import struct
import subprocess
import sys
import tarfile
import tempfile
from types import ModuleType
import unittest
from unittest.mock import patch

if os.environ.get('GITHUB_ACTIONS') != 'true':
    raise RuntimeError('GitHub fixtures only')

subject = Path(__file__).with_name('current_candidate_layout.py')
p = ModuleType('_current_candidate_layout_source_test')
p.__file__ = str(subject)
exec(compile(subject.read_bytes(), str(subject), 'exec', dont_inherit=True), p.__dict__)


class CurrentCandidateLayout(unittest.TestCase):
    def git(self, root, *args):
        return subprocess.check_output(['/usr/bin/git', '-C', str(root), *args], stderr=subprocess.DEVNULL)

    @contextmanager
    def repository(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / 'repo'
            root.mkdir()
            self.git(root, 'init', '-q')
            self.git(root, 'config', 'user.name', 'SOM source fixture')
            self.git(root, 'config', 'user.email', 'som-source-fixture@example.invalid')
            files = {
                'som-rs/Cargo.toml': b'[package]\nname="fixture"\n',
                'som-rs/Cargo.lock': b'# source fixture\n',
                'som-rs/src/lib.rs': b'// no compiler execution\n',
                'som-rs/src/executable.sh': b'#!/bin/sh\nexit 98\n',
                'som-rs/contracts/contract.json': b'{}\n',
                'som-rs/som-targets/Cargo.toml': b'[package]\nname="fixture-target"\n',
                'som-rs/som-targets/Cargo.lock': b'# target source fixture\n',
                'som-rs/som-targets/src/main.rs': b'// tracked dependency fixture\n',
                'som-rs/som-targets/catalog/data.json': b'{}\n',
                'som-core/dep with space.txt': b'real tracked dependency outside roots\n',
                'tool.py': b'VALUE = "raw-source"\n',
                'executable.sh': b'#!/bin/sh\nexit 99\n',
            }
            for name, raw in files.items():
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(raw)
            (root / 'executable.sh').chmod(0o755)
            (root / 'som-rs/src/executable.sh').chmod(0o755)
            (root / 'tracked-link').symlink_to('som-core/dep with space.txt')
            self.git(root, 'add', '--all')
            self.git(root, 'commit', '-qm', 'fixed source fixture; no build')
            revision = self.git(root, 'rev-parse', 'HEAD').decode().strip()
            sources = p.Sources(root, revision)
            target = Path(directory) / 'target'
            config = {'repo': str(root), 'target_dir': str(target)}
            dep = (str(target / 'debug/som') + ': ' + str(root / 'som-rs/som-targets/src/main.rs') + ' \\\n ' + str(root / 'som-core/dep with space.txt').replace(' ', '\\ ') + '\n').encode()
            before = {'source_roots': p.SOURCE_ROOTS, 'tree_before': {'head': revision, 'porcelain': ''},
                      'all_tracked_before': sources.snapshot()}
            yield root, revision, sources, before, config, dep, Path(directory)

    def source_archive(self, sources, before, config, dep, directory):
        reads, external, files, modes = p.source_universe(sources, before, dep, config)
        path = directory / 'source.tar'
        raw = self.git(sources.root, '-c', 'tar.umask=0022', 'archive', '--format=tar', sources.revision, '--', *files)
        path.write_bytes(raw)
        source = {'version': 'som.compiler-candidate-source.v1', 'commit': sources.revision,
                  'roots': p.SOURCE_ROOTS, 'dep_info_files': reads, 'files': files, 'git_modes': modes,
                  'archive_sha256': p.sha(raw)}
        return path, source

    def rewrite_archive(self, path, change):
        members = []
        with tarfile.open(path, 'r:') as archive:
            for member in archive:
                raw = archive.extractfile(member).read() if member.isfile() else None
                member.pax_headers = {}
                members.append((member, raw))
        change(members)
        with tarfile.open(path, 'w', format=tarfile.USTAR_FORMAT) as archive:
            for member, raw in members:
                archive.addfile(member, io.BytesIO(raw) if raw is not None else None)

    def test_complete_actual_git_snapshot_keeps_regular_modes_and_symlink_text(self):
        with self.repository() as (root, revision, sources, before, config, dep, directory):
            snapshot = before['all_tracked_before']
            self.assertEqual(set(snapshot), sources.paths)
            self.assertEqual(snapshot['executable.sh']['git_mode'], '100755')
            self.assertEqual(snapshot['tracked-link']['git_mode'], '120000')
            self.assertEqual(snapshot['tracked-link']['working_sha256'], p.sha(os.readlink(root / 'tracked-link').encode()))
            with self.assertRaises(ValueError): sources.read('tracked-link')

    def test_raw_git_reader_rejects_changed_source_bytes_and_executable_mode(self):
        for change in ('bytes', 'mode'):
            with self.subTest(change=change), self.repository() as (root, revision, sources, before, config, dep, directory):
                if change == 'bytes': (root / 'tool.py').write_bytes(b'VALUE = "tampered"\n')
                else: (root / 'tool.py').chmod(0o755)
                with self.assertRaises(ValueError): sources.read('tool.py')

    def test_raw_module_ignores_real_matching_timestamp_pyc(self):
        with self.repository() as (root, revision, sources, before, config, dep, directory):
            source = root / 'tool.py'
            sentinel = directory / 'pyc-sentinel'
            cached = Path(importlib.util.cache_from_source(str(source)))
            cached.parent.mkdir()
            code = compile('from pathlib import Path\nPath(' + repr(str(sentinel)) + ').write_text("PYC")\nVALUE="poison"\n', str(source), 'exec')
            info = source.stat()
            cached.write_bytes(importlib.util.MAGIC_NUMBER + struct.pack('<III', 0, int(info.st_mtime), info.st_size) + marshal.dumps(code))
            ordinary = ModuleType('_matching_pyc_control')
            importlib.machinery.SourceFileLoader(ordinary.__name__, str(source)).exec_module(ordinary)
            self.assertEqual(ordinary.VALUE, 'poison')
            sentinel.write_text('control consumed')
            raw = p.raw_module(sources, '_raw_git_module', 'tool.py')
            self.assertEqual(raw.VALUE, 'raw-source')
            self.assertEqual(sentinel.read_text(), 'control consumed')
            self.assertIsNone(raw.__cached__)

    def test_actual_dep_info_escapes_and_repo_dependency_outside_roots(self):
        with self.repository() as (root, revision, sources, before, config, dep, directory):
            reads, external, files, modes = p.source_universe(sources, before, dep, config)
            self.assertEqual(reads, ['som-core/dep with space.txt', 'som-rs/som-targets/src/main.rs'])
            self.assertIn('som-core/dep with space.txt', files)
            self.assertEqual(external, [])
            self.assertNotIn('tool.py', files)
            self.assertEqual(set(files), set(modes))

    def test_untracked_actual_repo_dependency_refused(self):
        with self.repository() as (root, revision, sources, before, config, dep, directory):
            new = root / 'not-tracked.rs'
            new.write_text('// actual untracked source\n')
            dep = dep.rstrip() + b' ' + str(new).encode() + b'\n'
            with self.assertRaises(ValueError): p.source_universe(sources, before, dep, config)

    def test_dependency_changed_after_begin_refused(self):
        with self.repository() as (root, revision, sources, before, config, dep, directory):
            (root / 'som-core/dep with space.txt').write_bytes(b'changed after snapshot\n')
            with self.assertRaises(ValueError): p.source_universe(sources, before, dep, config)

    def test_missing_prebuild_tracked_member_cannot_hide_dependency(self):
        with self.repository() as (root, revision, sources, before, config, dep, directory):
            before['all_tracked_before'].pop('som-core/dep with space.txt')
            with self.assertRaises(ValueError): p.source_universe(sources, before, dep, config)

    def test_prebuild_head_and_source_root_state_are_actual(self):
        for change in ('head', 'porcelain'):
            with self.subTest(change=change), self.repository() as (root, revision, sources, before, config, dep, directory):
                before['tree_before'][change] = 'a' * 40 if change == 'head' else ' M som-rs/src/lib.rs\n'
                with self.assertRaises(ValueError): p.source_universe(sources, before, dep, config)

    def test_wrong_binary_target_empty_dep_or_unexpanded_make_variable_refused(self):
        with self.repository() as (root, revision, sources, before, config, dep, directory):
            for raw in (b'', dep.replace(b'/debug/som:', b'/release/som:'), dep.rstrip() + b' $(BAD)\n'):
                with self.subTest(raw=raw), self.assertRaises(ValueError):
                    p.dependency_files(raw, config['repo'], config['target_dir'], str(Path(config['target_dir']) / 'debug/som'))

    def test_real_git_archive_has_exact_full_source_universe_and_modes(self):
        with self.repository() as (root, revision, sources, before, config, dep, directory):
            path, source = self.source_archive(sources, before, config, dep, directory)
            p.verify_archive(path, source, sources)
            self.assertIn('som-core/dep with space.txt', source['files'])
            self.assertEqual(source['git_modes']['som-rs/src/executable.sh'], '100755')
            self.assertEqual(source['archive_sha256'], p.sha(path.read_bytes()))

    def test_source_archive_omission_extra_duplicate_mode_and_body_fail(self):
        for mutation in ('omit', 'extra', 'duplicate', 'mode', 'body', 'symlink'):
            with self.subTest(mutation=mutation), self.repository() as (root, revision, sources, before, config, dep, directory):
                path, source = self.source_archive(sources, before, config, dep, directory)
                def change(rows):
                    index = next(i for i, (member, raw) in enumerate(rows) if member.isfile())
                    member, raw = rows[index]
                    if mutation == 'omit': rows.pop(index)
                    elif mutation == 'duplicate': rows.append((copy.copy(member), raw))
                    elif mutation == 'extra':
                        member = tarfile.TarInfo('extra.txt'); member.size = 1; member.mode = 0o644
                        rows.append((member, b'x'))
                    elif mutation == 'mode': member.mode = 0o755 if member.mode == 0o644 else 0o644
                    elif mutation == 'body': rows[index] = (member, b'x' * len(raw))
                    else:
                        member.type, member.linkname, member.size = tarfile.SYMTYPE, '/outside', 0
                        rows[index] = (member, None)
                self.rewrite_archive(path, change)
                # Rehash the altered archive. The per-file universe/bytes/modes
                # must reject it, rather than only the top-level hash check.
                source['archive_sha256'] = p.sha(path.read_bytes())
                with self.assertRaises(ValueError): p.verify_archive(path, source, sources)

    def test_source_archive_raw_hash_tamper_refused(self):
        with self.repository() as (root, revision, sources, before, config, dep, directory):
            path, source = self.source_archive(sources, before, config, dep, directory)
            source['archive_sha256'] = '0' * 64
            with self.assertRaises(ValueError): p.verify_archive(path, source, sources)

    def test_new_current_pair_required(self):
        for helper, candidate in (('a' * 40, 'b' * 40), ('short', 'short'), (p.F59, p.F59)):
            with self.subTest(helper=helper), self.assertRaises(ValueError): p.pair(helper, candidate)
        p.pair('a' * 40, 'a' * 40)

    def test_only_actual_two_cargo_argv_shapes_are_exposed(self):
        config = {'cargo': '/tools/cargo', 'tools': {'SOM_RUFF': '/tools/ruff'}}
        self.assertEqual(p.command_argv(config, 'candidate'), ['/tools/cargo', 'build', '--locked', '--offline',
            '--manifest-path', 'som-rs/som-targets/Cargo.toml', '--bin', 'som'])
        self.assertNotIn('test', p.command_argv(config, 'candidate'))
        self.assertNotIn('--release', p.command_argv(config, 'candidate'))
        self.assertEqual(p.command_argv(config, 'assets')[-2:], ['--formatter', '/tools/ruff'])
        with self.assertRaises(ValueError): p.command_argv(config, 'rebuild')

    def test_sanitized_public_environment_excludes_tokens_and_custom_flags(self):
        config = {'cargo': '/tools/cargo', 'rustc': '/tools/rustc', 'target_dir': '/temp/target',
                  'tools': {key: '/tools/' + name for key, name in p.TOOL_ROLES.items()}}
        with patch.dict(os.environ, {'GITHUB_TOKEN': 'fixture-secret', 'AWS_SECRET_ACCESS_KEY': 'fixture-secret',
                                     'RUSTFLAGS': 'unexpected build flag'}):
            env = p.public_environment(config)
        self.assertNotIn('GITHUB_TOKEN', env)
        self.assertNotIn('AWS_SECRET_ACCESS_KEY', env)
        self.assertNotIn('RUSTFLAGS', env)
        self.assertNotIn('fixture-secret', json.dumps(env))
        self.assertEqual(env['CARGO_NET_OFFLINE'], 'true')
        p.verify_public_environment(env, config)
        for key, value in (('GITHUB_TOKEN', 'fixture-secret'), ('RUSTFLAGS', 'unexpected'), ('RUSTC', '/other/rustc')):
            with self.subTest(key=key), self.assertRaises(ValueError):
                p.verify_public_environment({**env, key: value}, config)

    def test_same_run_origin_refuses_stale_attempt_repo_and_candidate(self):
        env = {'GITHUB_ACTIONS': 'true', 'GITHUB_REPOSITORY': 'faberline/som', 'GITHUB_RUN_ID': '12',
               'GITHUB_RUN_ATTEMPT': '2', 'GITHUB_SHA': 'b' * 40, 'GITHUB_JOB': 'fixture'}
        catalog = {'version': 'som.same-candidate-artifact-inputs.v1', 'helper_revision': 'a' * 40,
                   'candidate_revision': 'a' * 40, 'repository': 'faberline/som', 'run_id': 12,
                   'run_attempt': 2, 'product_acceptance_passed': False}
        with patch.dict(os.environ, env):
            p.catalog_origin(catalog, env, 'a' * 40, 'a' * 40)
            for field, value in (('run_id', 11), ('run_attempt', 1), ('repository', 'other/repo'), ('candidate_revision', 'b' * 40)):
                bad = {**catalog, field: value}
                with self.subTest(field=field), self.assertRaises(ValueError):
                    p.catalog_origin(bad, env, 'a' * 40, 'a' * 40)
            for field in ('GITHUB_REPOSITORY', 'GITHUB_RUN_ID', 'GITHUB_RUN_ATTEMPT'):
                bad = {**env, field: 'foreign'}
                with self.subTest(producer_field=field), self.assertRaises(ValueError):
                    p.catalog_origin(catalog, bad, 'a' * 40, 'a' * 40)

    def test_missing_actual_command_record_never_yields_build_success(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'begin.json').write_text('{}')
            with self.assertRaises(FileNotFoundError): p.checked_commands(root, {})

    def test_forged_command_owner_wait_exit_logs_and_cleanup_are_rejected(self):
        # Reject-only saved-data mutations. No fixture creates an accepted
        # candidate build, and no Cargo/native build is run by these tests.
        mutations = (
            ('pid', lambda row: row['owned_start'].update(pid=0)),
            ('group', lambda row: row['owned_start'].update(pgrp=778)),
            ('stamp', lambda row: row['owned_start'].update(start_ticks=0)),
            ('executable', lambda row: row['owned_start'].update(executable_inode=12)),
            ('wait', lambda row: row['cleanup'].update(wait_completed=False)),
            ('nonterminal', lambda row: row['cleanup'].update(wait_exit_code=None, exit_code=None)),
            ('foreign_cleanup', lambda row: row['cleanup'].update(owned_pid=778)),
            ('remaining', lambda row: row['cleanup'].update(remaining_owned_session_members=[{'pid': 778}])),
            ('signal', lambda row: row['cleanup'].update(signals=['SIGKILL'])),
            ('exit', lambda row: row.update(exit_code=7)),
            ('argv', lambda row: row['command'].append('--release')),
            ('logs', lambda row: row['logs']['stdout'].update(sha256='0' * 64)),
        )
        for label, mutate in mutations:
            with self.subTest(mutation=label), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                before = {'config': {'cargo': '/source-pinned/cargo', 'tools': {'SOM_RUFF': '/source-pinned/ruff'}},
                          'public_environment': {'CARGO_NET_OFFLINE': 'true'}, 'github': {},
                          'tool_identities': {'cargo': {'device': 10, 'inode': 11}}}
                (root / 'begin.json').write_bytes(p.canonical(before))
                child = root / 'commands/00-assets'
                child.mkdir(parents=True)
                for name in ('stdout', 'stderr'): (child / (name + '.raw')).write_bytes(b'retained fixture log\n')
                row = {'version': p.COMMAND_VERSION, 'stage': 'assets', 'command': p.command_argv(before['config'], 'assets'),
                       'cwd': '.', 'environment': before['public_environment'], 'github': {},
                       'begin_sha256': p.sha((root / 'begin.json').read_bytes()),
                       'complete': True, 'exit_code': 0, 'identity_recheck_passed': True, 'interrupted_signal': None,
                       'tool_identities_before': before['tool_identities'], 'tool_identities_after': before['tool_identities'],
                       'source_pins_before': {'helper.py': 'a' * 64}, 'source_pins_after': {'helper.py': 'a' * 64},
                       'product_acceptance_passed': False,
                       'owned_start': {'pid': 777, 'pgrp': 777, 'session': 777, 'state': 'S', 'start_ticks': 99,
                                       'executable_device': 10, 'executable_inode': 11},
                       'cleanup': {'owned_pid': 777, 'signal_target': 'owned new process group only', 'signals': [],
                                   'remaining_owned_session_members': [], 'cleanup_passed': True,
                                   'wait_completed': True, 'wait_exit_code': 0, 'exit_code': 0},
                       'logs': {name: p.portable(p.read_file(child / (name + '.raw'))[1]) for name in ('stdout', 'stderr')}}
                mutate(row)
                (child / 'record.json').write_bytes(p.canonical(row))
                with self.assertRaises(ValueError): p.checked_commands(root, before)

    def test_symlink_hardlink_and_fifo_raw_inputs_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'file').write_bytes(b'raw')
            (root / 'alias').symlink_to('file')
            with self.assertRaises(ValueError): p.read(root / 'alias')
            os.link(root / 'file', root / 'hard')
            with self.assertRaises(ValueError): p.read(root / 'hard')
            os.mkfifo(root / 'fifo')
            with self.assertRaises(ValueError): p.read(root / 'fifo')

    def test_compatibility_envelope_preserves_real_original_fields_and_commands(self):
        original = {'commands': [{'command': ['/actual/cargo', 'build'], 'exit_code': 0}],
                    'assets_from': '/original/assets', 'som_ts_prettier': '/original/prettier',
                    'assets_packaged': {'formatter': '/original/ruff', 'formatter_sha256': 'a' * 64},
                    'profile': 'debug', 'binary_sha256': 'b' * 64}
        # Isolated path-normalization data only. This object has no complete
        # field, no source provenance and can never pass restore/finish.
        got = p.restored_build(original, Path('/callback/candidate'), Path('/callback/carrier'), 'c' * 64, 'd' * 64)
        self.assertEqual(got['commands'], original['commands'])
        self.assertEqual(got['profile'], 'debug')
        self.assertEqual(got['binary_sha256'], original['binary_sha256'])
        self.assertEqual(original['assets_from'], '/original/assets')
        self.assertEqual(got['build_role'], 'restored_same_build')
        self.assertFalse(got['restored_same_build']['compiler_rebuilt'])
        self.assertFalse(got['restored_same_build']['original_commands_reexecuted'])
        self.assertEqual(set(got['restored_same_build']['runtime_path_normalizations']),
                         {'assets_from', 'som_ts_prettier', 'assets_packaged.formatter'})


if __name__ == '__main__':
    unittest.main()

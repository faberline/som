"""Run three original legacy fixture files through the existing strict bootstrap.

This is a bounded GitHub source-test adapter. It grants no native authority.
"""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import ModuleType

SELF = '.github/actions/native-active-source-fixtures/legacy_runner.py'
RUNNER = 'som-rs/scripts/hosted_readiness/run_source_fixtures.py'
BASE = 'som-rs/scripts/hosted_readiness/'
ENTRIES = frozenset(BASE + name for name in (
    'test_experimental_native_compile.py',
    'test_reconstruct_public_inputs.py',
    'test_run_experimental_native_cnl.py',
))


def main():
    assert os.environ.get('GITHUB_ACTIONS') == 'true' and sys.platform == 'linux'
    assert sys.flags.isolated and sys.flags.no_site and sys.dont_write_bytecode
    assert len(sys.argv) == 2 and sys.argv[1] in ENTRIES
    entry = sys.argv[1]
    repo = Path(os.environ['GITHUB_WORKSPACE']).absolute()
    revision = os.environ['HELPER_REVISION']
    assert revision == os.environ['GITHUB_SHA']
    assert subprocess.check_output(['/usr/bin/git', '--no-replace-objects', '-C', str(repo),
        'rev-parse', 'HEAD']).decode().strip() == revision

    def source(name):
        path = repo / name
        assert not any(part.is_symlink() for part in (path, *path.parents))
        raw = path.read_bytes()
        assert raw == subprocess.check_output(['/usr/bin/git', '--no-replace-objects', '-C', str(repo),
            'cat-file', 'blob', revision + ':' + name])
        return raw

    assert Path(__file__).absolute() == repo / SELF
    source(SELF)
    raw = source(RUNNER)
    runner = ModuleType('_som_selected_legacy_source_fixture_runner')
    runner.__file__, runner.__cached__, runner.__package__ = str(repo / RUNNER), None, ''
    exec(compile(raw, runner.__file__, 'exec', dont_inherit=True), runner.__dict__)
    # These original functions check the complete physical Git source inventory.
    # Do not narrow or edit their source-directory or PYC predicates.
    expected = runner.source_inventory(repo, revision)
    entry_sha = expected[entry]['sha256']
    assert hashlib.sha256(source(entry)).hexdigest() == entry_sha
    primary, traceback = None, None
    with tempfile.TemporaryDirectory(prefix='som-selected-legacy-fixture-', dir=os.environ['RUNNER_TEMP']) as directory:
        root = Path(directory)
        site = root / 'site'
        site.mkdir()
        inventory = b'{}\n'
        (root / 'site-inventory.json').write_bytes(inventory)
        # The unchanged legacy files require only stdlib and checked repository
        # imports. The exact existing bootstrap verifies this empty site first.
        sys.argv = [str(repo / SELF), str(site), hashlib.sha256(inventory).hexdigest(),
                    entry_sha, str(repo / BASE), str(repo / entry), '-v']
        try:
            exec(compile(runner.BOOTSTRAP, runner.__file__ + ':BOOTSTRAP', 'exec', dont_inherit=True),
                 {'__name__': '__main__'})
        except SystemExit as terminal:
            if terminal.code not in (None, 0):
                primary, traceback = terminal, terminal.__traceback__
        except BaseException as error:
            primary, traceback = error, error.__traceback__
        finally:
            try:
                runner.verify_sources(repo, revision, expected)
            except BaseException as secondary:
                if primary is None:
                    raise
                primary.__som_fixture_source_postcheck_secondary__ = secondary
                print(json.dumps({'source_postcheck_secondary': type(secondary).__name__,
                                  'message': str(secondary), 'product_acceptance_passed': False}), file=sys.stderr)
    if primary is not None:
        raise primary.with_traceback(traceback)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

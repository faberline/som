"""Execute repository Python only from exact current Git bytes on GitHub."""
from __future__ import annotations
import argparse
import hashlib
import importlib.abc
import importlib.machinery
import importlib.util
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
from types import ModuleType

SELF = 'som-rs/scripts/hosted_readiness/bound_source.py'


def require(value, message):
    if not value: raise ValueError(message)


class GitBytes(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    def __init__(self, root, revision):
        self.root, self.revision, self.pins, self.pending = Path(root).absolute(), revision, {}, {}
        require(re.fullmatch('[0-9a-f]{40}', revision), 'exact source commit required')
        require(self.git('rev-parse', '--verify', 'HEAD^{commit}').decode().strip() == revision, 'HEAD differs')

    def git(self, *args):
        return subprocess.check_output(['/usr/bin/git', '--no-replace-objects', '-C', str(self.root), *args],
            env={'PATH': '/usr/bin:/bin', 'LANG': 'C.UTF-8', 'LC_ALL': 'C.UTF-8',
                 'GIT_CONFIG_NOSYSTEM': '1', 'GIT_CONFIG_GLOBAL': '/dev/null'}, stderr=subprocess.DEVNULL, timeout=30)

    def read(self, name):
        require(type(name) is str and name.endswith('.py') and not name.startswith('/') and '\\' not in name
                and all(part not in {'', '.', '..'} for part in name.split('/')), 'exact Python source required')
        entry = self.git('ls-tree', '-z', self.revision, '--', name)
        require(entry.count(b'\0') == 1 and entry.endswith(b'\t' + name.encode() + b'\0')
                and entry.startswith((b'100644 blob ', b'100755 blob ')), 'regular tracked Python required')
        path = self.root / name
        require(not any(item.is_symlink() for item in (path, *path.parents)), 'linked Python source refused')
        info = path.stat()
        require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and info.st_size <= 2**20,
                'bounded regular Python source required')
        raw = self.git('cat-file', 'blob', self.revision + ':' + name)
        require(len(raw) <= 2**20 and path.read_bytes() == raw, 'working Python source differs from Git')
        self.pins[name] = hashlib.sha256(raw).hexdigest()
        return raw

    def find_spec(self, fullname, path=None, target=None):
        found = importlib.machinery.PathFinder.find_spec(fullname, path)
        if found is None or not found.origin or found.origin in {'built-in', 'frozen'}: return None
        origin = Path(found.origin).absolute()
        if self.root not in origin.parents: return None
        require(origin.suffix == '.py' and target is None, 'repository PYC, extension or reload refused')
        self.pending[fullname] = origin, self.read(origin.relative_to(self.root).as_posix())
        return importlib.util.spec_from_file_location(fullname, origin, loader=self,
            submodule_search_locations=[str(origin.parent)] if found.submodule_search_locations is not None else None)

    def create_module(self, spec): return None

    def exec_module(self, module):
        origin, raw = self.pending.pop(module.__name__)
        module.__file__, module.__cached__, module.__som_raw_sha256__ = str(origin), None, hashlib.sha256(raw).hexdigest()
        exec(compile(raw, str(origin), 'exec', dont_inherit=True), module.__dict__)

    def verify(self):
        require(self.git('rev-parse', '--verify', 'HEAD^{commit}').decode().strip() == self.revision, 'HEAD changed')
        for name, pin in list(self.pins.items()): require(hashlib.sha256(self.read(name)).hexdigest() == pin, 'source changed')


def main():
    require(os.environ.get('GITHUB_ACTIONS') == 'true' and sys.platform == 'linux', 'GitHub Linux execution only')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', type=Path, required=True)
    parser.add_argument('--revision', required=True)
    parser.add_argument('--entry', required=True)
    parser.add_argument('arguments', nargs=argparse.REMAINDER)
    args = parser.parse_args()
    source = GitBytes(args.repo, args.revision)
    require(source.read(SELF) == Path(__file__).read_bytes(), 'launcher differs from exact source')
    raw = source.read(args.entry)
    for module in list(sys.modules.values()):
        file = getattr(module, '__file__', None)
        require(not isinstance(file, str) or source.root not in Path(file).absolute().parents
                or module is sys.modules['__main__'], 'preloaded repository module refused')
    target = ModuleType('__main__')
    target.__file__, target.__cached__, target.__package__ = str(source.root / args.entry), None, ''
    target.__som_raw_sha256__ = hashlib.sha256(raw).hexdigest()
    old_main, old_argv = sys.modules['__main__'], sys.argv
    sys.modules['__main__'] = target
    sys.argv = [target.__file__, *(args.arguments[1:] if args.arguments[:1] == ['--'] else args.arguments)]
    sys.path[:0] = [str((source.root / args.entry).parent), str(source.root / 'som-core/src')]
    sys.meta_path.insert(0, source)
    try:
        exec(compile(raw, target.__file__, 'exec', dont_inherit=True), target.__dict__)
    finally:
        source.verify()
        sys.meta_path.remove(source)
        sys.modules['__main__'], sys.argv = old_main, old_argv


if __name__ == '__main__': main()

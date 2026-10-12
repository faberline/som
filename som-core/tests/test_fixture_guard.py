"""Shared fixture fault controls must have one verifier owner."""

import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
from fixture_guard import reserve_fixture


class FixtureGuardTest(unittest.TestCase):
    def test_nested_checks_and_another_process(self):
        with tempfile.TemporaryDirectory() as directory:
            manifest = {"root": str(Path(directory).resolve())}
            code = """import sys
from fixture_guard import FixtureBusy, reserve_fixture
try:
    with reserve_fixture({'root': sys.argv[1]}):
        pass
except FixtureBusy:
    raise SystemExit(7)
"""
            args = [sys.executable, "-c", code, manifest["root"]]
            env = dict(os.environ, PYTHONPATH=str(SCRIPTS))
            with reserve_fixture(manifest), reserve_fixture(manifest):
                result = subprocess.run(args, env=env, check=False, timeout=5)
                self.assertEqual(result.returncode, 7)
            result = subprocess.run(args, env=env, check=False, timeout=5)
            self.assertEqual(result.returncode, 0)

    def test_exception_releases_the_lease(self):
        with tempfile.TemporaryDirectory() as directory:
            manifest = {"root": str(Path(directory).resolve())}
            with self.assertRaisesRegex(ValueError, "test failure"):
                with reserve_fixture(manifest):
                    raise ValueError("test failure")
            with reserve_fixture(manifest):
                self.assertEqual(
                    (Path(directory) / ".verification.lock").stat().st_mode & 0o777,
                    0o600,
                )


if __name__ == "__main__":
    unittest.main()

import os
from pathlib import Path
import tempfile
import unittest

from role_inventory import inventory


class RoleInventoryTests(unittest.TestCase):
    def test_unreadable_extra_directory_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            (root / 'expected').write_bytes(b'x')
            hidden = root / 'unreadable-extra'
            hidden.mkdir()
            (hidden / 'extra').write_bytes(b'x')
            hidden.chmod(0)
            try:
                self.assertFalse(os.access(hidden, os.R_OK), 'runner must not bypass directory permissions')
                with self.assertRaises(PermissionError):
                    inventory(root)
            finally:
                hidden.chmod(0o700)


if __name__ == '__main__':
    unittest.main()

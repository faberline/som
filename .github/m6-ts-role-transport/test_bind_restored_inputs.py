from datetime import datetime, timedelta, timezone
import unittest

from bind_restored_inputs import NAME, check_origin


class ArtifactOriginTests(unittest.TestCase):
    def records(self):
        meta = {'id': 9, 'name': NAME, 'expired': False,
                'expires_at': (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
                'digest': 'sha256:' + 'a' * 64,
                'workflow_run': {'id': 10, 'head_sha': 'b' * 40, 'repository_id': 11}}
        run = {'id': 10, 'run_attempt': 2, 'head_sha': 'b' * 40, 'repository': {'id': 11}}
        return meta, run

    def test_exact_origin_passes(self):
        meta, run = self.records()
        check_origin(meta, run, 9, 11, 10, 2, 'b' * 40)

    def test_changed_identity_attempt_and_expiry_fail(self):
        for target, key, value in [('meta', 'name', 'other'), ('meta', 'expired', True),
                                   ('meta', 'id', 12), ('run', 'run_attempt', 1),
                                   ('run', 'head_sha', 'c' * 40)]:
            meta, run = self.records()
            (meta if target == 'meta' else run)[key] = value
            with self.assertRaises(ValueError): check_origin(meta, run, 9, 11, 10, 2, 'b' * 40)


if __name__ == '__main__':
    unittest.main()

"""A partial update must not erase unrelated business state."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from audit_business_journeys import assert_partial_update


class PartialUpdateOracleTests(unittest.TestCase):
    def setUp(self):
        self.before = {"id": 1, "parent_id": 2, "quantity": 0.1, "unit": "kg", "note": "keep",
                       "created_at": "2030-01-01T00:00:00Z", "updated_at": "2030-01-01T00:00:00Z"}
        self.after = self.before | {"quantity": 0.0, "updated_at": "2030-01-01T00:00:01Z"}

    def test_zero_value_preserves_other_fields(self):
        assert_partial_update(self.before, self.after, "quantity", 0.0)

    def test_rejects_erased_or_reassigned_state(self):
        for mutation in ({"unit": None}, {"note": None}, {"id": 3}, {"parent_id": 3},
                         {"created_at": "2030-01-01T00:00:01Z"}, {"quantity": 0.1}):
            with self.subTest(mutation=mutation), self.assertRaises(AssertionError):
                assert_partial_update(self.before, self.after | mutation, "quantity", 0.0)

    def test_rejects_missing_field(self):
        del self.after["note"]
        with self.assertRaises(AssertionError):
            assert_partial_update(self.before, self.after, "quantity", 0.0)

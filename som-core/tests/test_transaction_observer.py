"""Reject split commits and missing database evidence in the batch observer."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from audit_business_transactions import assert_one_transaction

RESPONSE = {"parent": {"id": 1}, "children": [{"id": 2}, {"id": 3}]}


def events(transactions=("session-a:1",) * 3):
    return [{"table": table, "id": row_id, "operation": "insert", "transaction": transaction}
            for (table, row_id), transaction in zip([("parents", 1), ("children", 2), ("children", 3)], transactions, strict=True)]


class TransactionEvidenceTests(unittest.TestCase):
    def test_accepts_one_committed_transaction(self):
        assert_one_transaction(events(), "parents", "children", RESPONSE)

    def test_rejects_split_or_missing_transaction(self):
        for transactions in [
            (100, 101, 102),  # PostgreSQL commits each write separately.
            (None, None, None),  # Mongo has no multi-document transaction.
            ("session-a:1", "session-b:1", "session-b:1"),
            ("session-a:1", "session-a:2", "session-a:2"),
        ]:
            with self.subTest(transactions=transactions), self.assertRaises(AssertionError):
                assert_one_transaction(events(transactions), "parents", "children", RESPONSE)

    def test_rejects_incomplete_write_evidence(self):
        with self.assertRaises(AssertionError):
            assert_one_transaction(events()[:-1], "parents", "children", RESPONSE)

    def test_rejects_unreported_side_write(self):
        extra = {"table": "children", "id": 4, "operation": "insert", "transaction": "session-a:1"}
        with self.assertRaises(AssertionError):
            assert_one_transaction(events() + [extra], "parents", "children", RESPONSE)

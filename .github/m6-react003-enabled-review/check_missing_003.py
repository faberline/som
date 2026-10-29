"""Exercise actual enabled entries with the checked-out, unamended receipt chain."""
import json
from pathlib import Path
import sys
import unittest
from unittest import mock

repo=Path.cwd().resolve()
sys.path.insert(0,str(repo/'som-rs/scripts/react_output_acceptance'))
import retained_evidence
import run_acceptance
import check_products
import record_api

class UnsealedRefusal(unittest.TestCase):
    def test_002_does_not_admit_enabled_callers(self):
        self.assertTrue(run_acceptance.API_BOOTSTRAP_REVIEWED)
        self.assertTrue(run_acceptance.NATIVE_ROUTING_REVIEWED)
        self.assertTrue(check_products.API_BOOTSTRAP_REVIEWED)
        self.assertTrue(record_api.API_BOOTSTRAP_REVIEWED)
        self.assertFalse((repo/'som-rs/evidence/react-output-contract-amendment-003.json').exists())
        for invoke in (lambda:run_acceptance.load_api_recorder(repo),
                       lambda:check_products.load_api_recorder(repo),
                       lambda:record_api.load_linux_sources(repo,bound_retained=retained_evidence)):
            with self.subTest(invoke=invoke):
                with self.assertRaisesRegex(ValueError,'QA review metadata missing'):
                    invoke()
        self.assertNotIn('linux_api_context',sys.modules)

    def test_002_files_do_not_authorize_new_authority(self):
        # Synthetic seam only: exercise authority check with the actual 002 map,
        # after separately testing the real receipt-chain refusal above.
        previous=json.loads((repo/'som-rs/evidence/react-output-contract-amendment-002.json').read_bytes())['files']
        with mock.patch.object(retained_evidence,'sealed_files',return_value=previous):
            for invoke in (lambda:run_acceptance.load_api_recorder(repo),
                           lambda:check_products.load_api_recorder(repo),
                           lambda:record_api.load_linux_sources(repo,bound_retained=retained_evidence)):
                with self.subTest(invoke=invoke), self.assertRaises(ValueError):
                    invoke()
        self.assertNotIn('linux_api_context',sys.modules)

if __name__=='__main__': unittest.main(verbosity=2)

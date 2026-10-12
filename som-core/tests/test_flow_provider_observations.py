"""Provider evidence belongs to one phase, even when synthetic keys repeat."""

from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from flow_provider_fault_checks import phase_events


def test_reused_key_cannot_mix_an_earlier_service_version():
    previous = {"key": "flow_900000", "requested_version": ["earlier"]}
    current = {"key": "flow_900000", "requested_version": ["this-run"]}
    observed = {"events": [previous, current],
                "gcs": [{"key": "flow_900000", "generation": 1},
                        {"key": "flow_900000", "generation": 2}]}
    actual = phase_events(observed, {"events": 1, "gcs": 1})
    assert actual == {"events": [current],
                      "gcs": [{"key": "flow_900000", "generation": 2}]}
    assert observed["events"] == [previous, current]


def test_reset_provider_history_must_fail_evidence_collection():
    with pytest.raises(AssertionError, match="log was reset"):
        phase_events({"events": [], "gcs": []}, {"events": 1, "gcs": 0})

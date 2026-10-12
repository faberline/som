"""Unknown GCS metadata must never cause deletion or escape as a type error."""

from pathlib import Path
from types import ModuleType

from google.api_core import exceptions as gcs_errors
from pydantic import TypeAdapter, ValidationError
import pytest
import requests

ROOT = Path(__file__).resolve().parents[2]


class ObjectUnavailable(Exception):
    """The provider did not supply usable ownership proof."""


class Blob:
    """A provider boundary; no network or product state is used."""

    def __init__(self, generation: object, metadata: object):
        self.generation = generation
        self.metadata = metadata
        self.deleted = False
        self.condition = None

    def reload(self, **_options):
        pass

    def delete(self, *, if_generation_match, **_options):
        self.deleted = True
        self.condition = if_generation_match


class Bucket:
    def __init__(self, blob):
        self.selected = blob
        self.requested_version = None

    def blob(self, _key, *, generation=None):
        self.requested_version = generation
        return self.selected


def boundary(blob):
    """Load the reviewed template as a module, without an assembler fallback."""
    module = ModuleType("flow_provider_boundary")
    module.__dict__.update(TypeAdapter=TypeAdapter, ValidationError=ValidationError,
                           ObjectUnavailable=ObjectUnavailable, gcs_errors=gcs_errors,
                           requests=requests, gcs_bucket=Bucket(blob))
    source = (ROOT / "som-core/block_templates/flow/storage_gcs.py.tpl").read_text()
    exec(compile("from __future__ import annotations\n" + source,
                 "reviewed-storage-gcs-template", "exec"), module.__dict__)
    return module


@pytest.mark.parametrize("value", [None, True, False, 0, -1, 1.0, "", "bad", "-1", "1" * 21, [], {}])
def test_invalid_generation_never_reaches_delete(value):
    blob = Blob(value, {"som-run": "run", "som-step": "write"})
    module = boundary(blob)
    with pytest.raises(ObjectUnavailable):
        module.flow_object_reverse("object", "run", "write", None)
    assert not blob.deleted


@pytest.mark.parametrize("metadata", [None, {}, {"som-run": "other", "som-step": "write"}])
def test_absent_or_other_ownership_keeps_the_object(metadata):
    blob = Blob(123, metadata)
    assert boundary(blob).flow_object_reverse("object", "run", "write", None) is False
    assert not blob.deleted


def test_malformed_sdk_metadata_has_a_controlled_error():
    blob = Blob(123, {"som-run": 1, "som-step": []})
    with pytest.raises(ObjectUnavailable) as error:
        boundary(blob).flow_object_reverse("object", "run", "write", None)
    assert isinstance(error.value.__cause__, ValidationError)
    assert not blob.deleted


def test_exact_owned_generation_is_the_delete_condition():
    blob = Blob("123", {"som-run": "run", "som-step": "write"})
    module = boundary(blob)
    assert module.flow_object_reverse("object", "run", "write", "123") is True
    assert blob.deleted
    assert blob.condition == module.gcs_bucket.requested_version == 123

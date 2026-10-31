"""Expose the original serializer through the fixed native source loader."""
from pathlib import Path
from types import ModuleType

from _som_native_active_controls_binding import controls as _controls

_SELF = 'som-rs/scripts/compiler_acceptance/serializer.py'
_ROLE = 'qa.builder.compiler-serializer-import'
_NAMESPACE = '_som_qa_builder_compiler_serializer_import'
_ORIGINAL = 'som-rs/tests/compiler-v1/serializer.py'
_ORIGINAL_ROLE = 'qa.builder.compiler-serializer'
_ORIGINAL_NAMESPACE = '_som_qa_builder_compiler_serializer'

_controls.verify_installed()
if (_controls.role_sources.get(_ROLE) != _SELF
        or Path(__file__).absolute() != _controls.root / _SELF
        or __name__ != _NAMESPACE):
    raise RuntimeError('fixed native serializer import source required')
if _controls.role_sources.get(_ORIGINAL_ROLE) != _ORIGINAL:
    raise RuntimeError('fixed original compiler serializer source required')

_original = _controls.load_module(_ORIGINAL_ROLE)
if (type(_original) is not ModuleType
        or _original.__name__ != _ORIGINAL_NAMESPACE
        or _original.__file__ != str(_controls.root / _ORIGINAL)):
    raise RuntimeError('actual original compiler serializer module required')

serialize_prompt = _original.serialize_prompt
__all__ = ['serialize_prompt']

"""The compact registry reference must not weaken per-symbol waivers."""

import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
CONTRACT = ROOT / "som-rs/contracts/python-quality-v1"
SPEC = importlib.util.spec_from_file_location(
    "compact_quality_policy", ROOT / "som-core/scripts/quality_policy.py"
)
POLICY = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(POLICY)


def config(registry_line: str) -> str:
    """Make a valid fixed profile without loading a generated answer."""
    source = (CONTRACT / "pyproject.toml").read_text()
    source = source.replace('known-first-party = []', 'known-first-party = ["example"]')
    source += '\nknown_first_party = ["example"]\n'
    pins = {"ruff": "0.16.6", "mypy": "2.3.1", "pylint": "4.0.9", "isort": "9.0.2"}
    dependencies = [name + "==" + version for name, version in pins.items()]
    digests = {name: hashlib.sha256((CONTRACT / name).read_bytes()).hexdigest()
               for name in ("pyproject.toml", "contract.md", "exceptions.json")}
    return (
        '[project]\nname = "example"\ndependencies = []\n'
        '[dependency-groups]\ndev = ' + json.dumps(dependencies) + '\n'
        + source
        + '\n[tool.som.python-quality]\ncontract = "som.python-quality.v1"\n'
        + 'profile_sha256 = "' + digests["pyproject.toml"] + '"\n'
        + 'contract_sha256 = "' + digests["contract.md"] + '"\n'
        + 'exceptions_sha256 = "' + digests["exceptions.json"] + '"\n'
        + 'dependencies_sha256 = "' + hashlib.sha256(
            json.dumps(dependencies, separators=(",", ":")).encode()
        ).hexdigest() + '"\n'
        + 'tools = { ' + ', '.join(name + ' = "' + version + '"'
                                  for name, version in pins.items()) + ' }\n'
        + registry_line + '\n'
    )


def registry_reference() -> str:
    digest = hashlib.sha256((CONTRACT / "exceptions.json").read_bytes()).hexdigest()
    return 'exception_registry_ref = "sha256:' + digest + '"'


@pytest.mark.parametrize("inline", [False, True])
def test_only_the_exact_registry_or_its_hash_is_accepted(tmp_path, inline):
    registry = json.loads((CONTRACT / "exceptions.json").read_text())["exceptions"]
    line = "exception_registry = '''" + json.dumps(registry) + "'''" if inline else registry_reference()
    (tmp_path / "pyproject.toml").write_text(config(line))
    assert POLICY.inspect(tmp_path, []) == {
        "passed": True, "errors": [], "approved_exceptions": []
    }


@pytest.mark.parametrize("line", [
    "",
    'exception_registry_ref = "sha256:000000"',
    'exception_registry = "broken JSON"',
    'exception_registry = 1',
    "exception_registry = '[]'",
    registry_reference() + "\nexception_registry = '[]'",
])
def test_missing_changed_or_malformed_registries_fail_closed(tmp_path, line):
    (tmp_path / "pyproject.toml").write_text(config(line))
    result = POLICY.inspect(tmp_path, [])
    assert not result["passed"]
    assert {error["rule"] for error in result["errors"]} == {"exception-registry"}


@pytest.mark.parametrize("path,approved", [
    ("example/schema_v001.py", True),
    ("example/models.py", True),
    ("example/app.py", False),
])
def test_model_waiver_cannot_extend_to_business_code(tmp_path, path, approved):
    (tmp_path / "pyproject.toml").write_text(config(registry_reference()))
    target = tmp_path / path
    target.parent.mkdir()
    target.write_text(
        "# pylint: disable-next=too-many-ancestors\n"
        "class Resource(_SomBeanieDocument):\n"
        "    id: int = Field(alias='id')  # type: ignore[assignment]\n"
    )
    result = POLICY.inspect(tmp_path, [path])
    assert result["passed"] is approved
    assert len(result["approved_exceptions"] if approved else result["errors"]) == 2

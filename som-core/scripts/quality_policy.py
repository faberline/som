"""Check the fixed quality profile and the exact scope of every exception."""

import ast
import hashlib
import json
from pathlib import Path
import re
import tomllib

ROOT = Path(__file__).resolve().parents[2]
CONTRACT = ROOT / "som-rs/contracts/python-quality-v1"


def inspect(root: Path, files: list[str]) -> dict[str, object]:
    """Reject weakened rules, blanket skips and undocumented suppressions."""
    errors: list[dict[str, object]] = []
    exceptions: list[dict[str, object]] = []
    expected = tomllib.loads((CONTRACT / "pyproject.toml").read_text())["tool"]
    config = tomllib.loads((root / "pyproject.toml").read_text())
    actual = config.get("tool", {})
    package = config["project"]["name"]
    expected["ruff"]["lint"]["isort"]["known-first-party"] = [package]
    expected["isort"]["known_first_party"] = [package]
    for name, value in expected.items():
        if actual.get(name) != value:
            errors.append({"path": "pyproject.toml", "rule": "fixed-profile", "tool": name})
    metadata = actual.get("som", {}).get("python-quality", {})
    for key, name in (("profile_sha256", "pyproject.toml"),
                      ("contract_sha256", "contract.md"),
                      ("exceptions_sha256", "exceptions.json")):
        if metadata.get(key) != hashlib.sha256((CONTRACT / name).read_bytes()).hexdigest():
            errors.append({"path": "pyproject.toml", "rule": "asset-hash", "asset": key})
    registry = json.loads((CONTRACT / "exceptions.json").read_text())["exceptions"]
    try:
        full_registry = (
            "exception_registry_ref" not in metadata
            and json.loads(metadata.get("exception_registry", "[]")) == registry
        )
    except (TypeError, ValueError):
        full_registry = False
    registry_ref = metadata.get("exception_registry_ref")
    referenced_registry = (
        "exception_registry" not in metadata
        and registry_ref == "sha256:" + hashlib.sha256(
            (CONTRACT / "exceptions.json").read_bytes()
        ).hexdigest()
    )
    if metadata.get("contract") != "som.python-quality.v1" or not (
        full_registry or referenced_registry
    ):
        errors.append({"path": "pyproject.toml", "rule": "exception-registry"})
    dependencies = config["project"]["dependencies"] + config["dependency-groups"]["dev"]
    digest = hashlib.sha256(json.dumps(dependencies, separators=(",", ":")).encode()).hexdigest()
    if metadata.get("dependencies_sha256") != digest:
        errors.append({"path": "pyproject.toml", "rule": "dependencies-hash"})
    pins = {"ruff": "0.16.6", "mypy": "2.3.1", "pylint": "4.0.9", "isort": "9.0.2"}
    if metadata.get("tools") != pins or any(name + "==" + version not in dependencies
                                            for name, version in pins.items()):
        errors.append({"path": "pyproject.toml", "rule": "tool-pins"})

    sdk_modules = {"firebase_admin", "firebase_admin.auth", "firebase_admin.credentials",
                   "firebase_admin.exceptions", "google.cloud.storage"}
    for path in files:
        source = (root / path).read_text()
        lines = source.splitlines()
        tree = ast.parse(source, filename=path)
        nodes = list(ast.walk(tree))
        for index, line in enumerate(lines, 1):
            if not any(marker in line for marker in ("type: ignore", "pylint:", "noqa", "mypy:")):
                continue
            approved = False
            rule = line.split("#", 1)[-1].strip()
            ignore = re.search(r"# type: ignore\[([^\]]+)\]", line)
            if ignore:
                codes = set(ignore[1].split(","))
                if codes == {"import-untyped"}:
                    approved = any(isinstance(node, ast.Import) and node.lineno == index
                                   and all(alias.name in sdk_modules for alias in node.names)
                                   for node in nodes) and path.endswith("/integrations.py")
                elif codes == {"misc", "no-any-unimported"}:
                    approved = path.endswith("/integrations.py") and any(
                        isinstance(node, ast.ClassDef) and node.name == "LocalFirebaseCredential"
                        and node.lineno <= index <= min(child.lineno for child in node.body)
                        and "_fb_creds.Base" in line for node in nodes)
                elif codes == {"no-untyped-call"}:
                    approved = path.endswith("/integrations.py") and "return AnonymousCredentials()" in line
                elif codes == {"assignment"}:
                    approved = path.endswith(("/models.py", "/schema_v001.py")) and "id: int = Field(alias=" in line and any(
                        isinstance(node, ast.ClassDef) and node.lineno <= index <= node.end_lineno
                        and any(isinstance(base, ast.Name) and base.id == "_SomBeanieDocument"
                                for base in node.bases) for node in nodes)
            pylint = re.search(r"# pylint: (disable-next|disable)=([a-z,-]+)", line)
            if pylint:
                codes = set(pylint[2].split(","))
                following = next((node for node in nodes if getattr(node, "lineno", 0) == index + 1
                                  and isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))), None)
                if pylint[1] == "disable-next":
                    if codes == {"too-many-locals", "too-many-statements"}:
                        approved = path.endswith("/app.py") and following is not None and following.name == "create_router"
                    elif codes == {"too-many-ancestors"}:
                        approved = path.endswith(("/models.py", "/schema_v001.py")) and isinstance(following, ast.ClassDef) and any(
                            isinstance(base, ast.Name) and base.id == "_SomBeanieDocument" for base in following.bases)
                    elif codes == {"too-few-public-methods"}:
                        approved = path.endswith(("/models.py", "/schema_v001.py")) and isinstance(following, ast.ClassDef) and (
                            following.name in {"Base", "Settings"} or not any(
                                isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) for child in following.body))
                    elif codes == {"no-member"}:
                        approved = path.startswith("alembic/") and index < len(lines) and bool(re.search(
                            r"(?:context\.(?:configure|begin_transaction|run_migrations|is_offline_mode)|op\.(?:create_table|create_index|drop_table))\(", lines[index]))
                elif codes == {"invalid-name"}:
                    approved = path.startswith("alembic/versions/") and bool(re.match(
                        r"(?:revision|down_revision|branch_labels|depends_on) = ", line))
                elif codes == {"duplicate-code"}:
                    approved = path.endswith(("/models.py", "/schema_v001.py")) and any(
                        isinstance(node, ast.ClassDef) and node.lineno < index <= node.end_lineno
                        and any(isinstance(base, ast.Name) and base.id == "_SomBeanieDocument" for base in node.bases)
                        and not any(isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) for child in node.body)
                        for node in nodes)
            entry = {"path": path, "line": index, "rule": rule}
            (exceptions if approved else errors).append(entry)
    return {"passed": not errors, "errors": errors, "approved_exceptions": exceptions}

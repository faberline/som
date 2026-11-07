#!/usr/bin/env python3
"""Experimental, unsealed native metadata comparison. Never a product gate.

This checker covers one selected accepted Python or Rust compile reply. It does
not run a compiler, formatter, assembler or product test. TypeScript canaries do
not establish a full TypeScript baseline. Output paths are portable metadata.
"""
from __future__ import annotations

import argparse
import copy
from dataclasses import dataclass
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import posixpath
import re
import stat
import subprocess
import sys
from types import MappingProxyType, ModuleType


RAW_COMPAT_REVIEWED_SHA256 = "a025bd99b56dafc4de65c268318f583e117adfe16988d61bb9180cb5e8da0896"

_NATIVE_RAW_COMPAT_ROLES = {
    '_som_active_companions_entry_capture_experimental_native_compile': 'companions.entry.capture.experimental-native-compile.raw-compat',
    '_som_active_semantic_common_postclose_join_experimental_native_compile': 'semantic.common-postclose.join.experimental-native-compile.raw-compat',
    '_som_active_semantic_common_runtime_join_experimental_native_compile': 'semantic.common-runtime.join.experimental-native-compile.raw-compat',
    '_som_active_semantic_common_verify_join_experimental_native_compile': 'semantic.common-verify.join.experimental-native-compile.raw-compat',
    '_som_active_semantic_entry_join_experimental_native_compile': 'semantic.entry.join.experimental-native-compile.raw-compat',
    '_som_active_semantic_graph_join_experimental_native_compile': 'semantic.graph.join.experimental-native-compile.raw-compat',
    '_som_active_semantic_capture_driver_imports_experimental_native_compile': 'semantic.capture.driver.imports.experimental-native-compile.raw-compat',
    '_som_active_cnl_entry_helper_experimental_native_compile': 'cnl.entry.helper.experimental-native-compile.raw-compat',
    '_som_active_context_current_refusal_checker': 'context.current-refusal-checker.raw-compat',
    '_som_active_context_typescript_regression_native_factory': 'context.typescript-regression-native-factory.raw-compat',
    '_som_ts_domain_typescript_regression_native_factory': 'ts.domain.typescript-regression-native-factory.raw-compat'
}



def bootstrap_raw_compat(path):
    """Execute only the reviewed helper bytes before any comparison can start."""
    path = Path(path).absolute()
    suffix = ("som-rs", "scripts", "hosted_readiness", "raw_digest_compat.py")
    if ".." in path.parts or path.parts[-len(suffix):] != suffix:
        raise RuntimeError("fixed raw compatibility source path required")
    for member in (path, *path.parents):
        info = member.lstat()
        if stat.S_ISLNK(info.st_mode) or (member != path and not stat.S_ISDIR(info.st_mode)):
            raise RuntimeError("linked or nonregular raw compatibility source")
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
    with os.fdopen(os.open(path, flags), "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > 65536:
            raise RuntimeError("linked, nonregular, or oversized raw compatibility source")
        raw = stream.read(65537)
    if len(raw) > 65536 or hashlib.sha256(raw).hexdigest() != RAW_COMPAT_REVIEWED_SHA256:
        raise RuntimeError("raw compatibility source SHA differs before execution")
    # A fresh local module cannot be supplied by sys.path, sys.modules or a PYC.
    module = ModuleType("_som_reviewed_raw_digest_compat")
    module.__file__, module.__cached__, module.__package__ = str(path), None, ""
    exec(compile(raw, str(path), "exec", dont_inherit=True), module.__dict__)
    return module


def _native_raw_compat(path):
    from _som_native_active_controls_binding import controls
    controls.verify_installed()
    if type(__name__) is not str or type(_NATIVE_RAW_COMPAT_ROLES) is not dict:
        raise RuntimeError("fixed native raw compatibility table required")
    role = _NATIVE_RAW_COMPAT_ROLES.get(__name__)
    if type(role) is not str:
        raise RuntimeError("unlisted native raw compatibility namespace")
    path = Path(path).absolute()
    if path != controls.root / "som-rs/scripts/hosted_readiness/raw_digest_compat.py":
        raise RuntimeError("fixed native raw compatibility source path required")
    if controls.role_sources.get(role) != "som-rs/scripts/hosted_readiness/raw_digest_compat.py":
        raise RuntimeError("native raw compatibility role source differs")
    suffix = ("som-rs", "scripts", "hosted_readiness", "raw_digest_compat.py")
    if ".." in path.parts or path.parts[-len(suffix):] != suffix:
        raise RuntimeError("fixed raw compatibility source path required")
    for member in (path, *path.parents):
        info = member.lstat()
        if stat.S_ISLNK(info.st_mode) or (member != path and not stat.S_ISDIR(info.st_mode)):
            raise RuntimeError("linked or nonregular raw compatibility source")
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
    with os.fdopen(os.open(path, flags), "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > 65536:
            raise RuntimeError("linked, nonregular, or oversized raw compatibility source")
        raw = stream.read(65537)
    if len(raw) > 65536 or hashlib.sha256(raw).hexdigest() != RAW_COMPAT_REVIEWED_SHA256:
        raise RuntimeError("raw compatibility source SHA differs before execution")
    module = controls.load_module(role)
    if type(module) is not ModuleType or module.__file__ != str(path):
        raise RuntimeError("actual native raw compatibility module differs")
    if not (raw_compat is module and _native_mask_raw is module.mask_raw
            and _native_verify_frozen is module.verify_frozen
            and _native_RawDigestError is module.RawDigestError):
        raise RuntimeError("fixed native raw compatibility bindings differ")
    return module


if __name__ in {"experimental_native_compile", "__main__"} and "__som_native_active_controls__" not in globals():
    raw_compat = bootstrap_raw_compat(Path(__file__).absolute().with_name("raw_digest_compat.py"))
else:
    raw_compat = _native_raw_compat(Path(__file__).absolute().with_name("raw_digest_compat.py"))

BASELINE = "som-rs/scripts/hosted_readiness/inputs/ts065-baseline-hashes-v1.json"
BASELINE_SHA256 = "650c6d758519b92dc6bcc11678aa14e9942612a9ed0f1fd35202876065c425a9"
HOST = "x86_64-unknown-linux-gnu"
ARTIFACTS = "som-rs/contracts/native-tools-v1/artifacts.json"
COMPILER = "som-rs/src/compiler.rs"
RUST = "som-rs/som-targets/src/rust/mod.rs"
TYPESCRIPT = "som-rs/som-targets/src/typescript/mod.rs"
RUST_FORMAT = "som-rs/som-targets/src/rust/format.rs"
TS_FORMAT = "som-rs/som-targets/src/typescript/format.rs"
CHECKER = "som-rs/scripts/hosted_readiness/experimental_native_compile.py"
RAW_COMPAT = "som-rs/scripts/hosted_readiness/raw_digest_compat.py"
SHA = re.compile(r"[0-9a-f]{64}\Z")
OLD_RUFF_SHA256 = "cb0cdeba051c5c5bdc85c22dc31440ac1ea17651366bf188d4df1f8fe985933a"
BEFORE = {
    "python_backend": "581ce38f29702c300342cdefe8fe5f1735cefcd1869d6485b44f30063a866711",
    "python_render_only_formatter": "a6a6e62d4ad875f8aec308a0fd9c55e8ac576d8129d57fb935bdfc5bc4529b94",
    "python_formatted_formatter": "7c15579b81f82e6283e6afbb5d739e8cfebd26faa4a165cc8f0a1baab1b7d73c",
    "rust_backend": "bbfa2fa3d35f9534950bea04486017db99b1f26ff44a2211eda08f492050ea1a",
    "rust_formatter": "2c56fd066853c80c786a8a3a0e5434b7f808ace8b2cf66ae6e1a13566aa2815f",
    "rust_target_contract": "8c25078c84fdec3dea1b4cb15df499d682ccb9b7286d957598f2076e3a685d51",
    "typescript_backend": "ac93cc3c0f565a17c56e65810fc203000c5d9294d37cbf9ac59f441a86aa6f2e",
    "typescript_formatter": "636bd4a26884ea92873af81bf4fd5bbc0c065157e645c3919658a4d0bc29c0e8",
    "typescript_target_contract": "a1ed9978d7ddd0123e22a1e388f1a6e889ca63e43752563cd744733b84c103b4",
}
# Factory text pins make an unrecognized owning-factory change fail closed.
# Other files in each complete vector are hashed at the current committed source.
REGIONS = {
    "core_backend": (COMPILER, "fn backend_manifest() -> Value {", "\nfn formatter_config()", "2bb6c3aa9ad0e45bdb39bf11bfdfd0acb1ba4577b2a5e4b3755358959b09eb93"),
    "core_formatter": (COMPILER, "fn formatter_config() -> Value {", "\npub fn asset_manifest()", "3c2cff92c06b12ede482e3ed54b8169582e97009ad1fad5bbed26074f638ab8e"),
    "core_descriptors": (COMPILER, "    pub fn descriptors(&self, prompt: &Value) -> Value {", "\n    /// Read-only expected roles", "b09e245d492ce04e5574f03724113309e11991a904b75d5dca98e24ef94744e4"),
    "rust_vector": (RUST, "const BACKEND_FILES: &[(&str, &str)] = &[", "\n];", "e295bccab950935eeadd2af3b65a8a11b2221de434371a0355d1b88c0bff2a1f"),
    "rust_factory": (RUST, "fn rust_descriptors() ->", "\n/// The Rust target section", "b77f9a04f15f72ff67cb85d8d1e89bf8f4ae9c22b066bd6589ce3ae2b67dbea9"),
    "ts_vector": (TYPESCRIPT, "const BACKEND_FILES: &[(&str, &str)] = &[", "\n];", "ed6f98821ed132bde61db7d85be43bf9e53648b99b92177dee8167c832094812"),
    "ts_factory": (TYPESCRIPT, "fn backend_descriptor() -> Value {", "\n/// The 11 asset descriptors", "70e9b9751cc80b877190abffd00ec1514562286ebb1cc132e0979805917704e6"),
    "ts_formatter": (TS_FORMAT, "pub fn descriptor() -> Value {", "\nfn stamp(", "2191ec5df5b6915fc46157adc0e37e5115cb5ef1c81f75346a921b78ab462d8f"),
    "ts_libnode": (TS_FORMAT, "fn needs_libnode() -> bool {", "\n/// Verify the native Node", "171fff695480b94e214ca869ed03edc6ba19f075251489a5cfbca3959b234b96"),
    "canonical": ("som-rs/src/flow.rs", "pub fn canonical(value: &Value) -> String {", "\nfn capability(", "ae240afdfe50a0da4bae400f2d83747c0db19a95f5e8bd7417e4484bccbf39de"),
    "accepted_qa_canonical": ("som-rs/scripts/compiler_acceptance/qa_harness.py", "def canonical(value: Any) -> str:", "\ndef sha(", "59a396333110b8e1f701018fa2c532cc1bf7e577ce419a99825197918af5e611"),
}
ALIASES = {
    "composed": "som-rs/src/composed.rs", "flow": "som-rs/src/flow.rs",
    "quality": "som-rs/src/quality.rs", "flow_compact": "som-rs/src/flow_compact.rs",
    "prompt_schema": "som-rs/som-targets/src/rust/prompt_schema.rs",
}
IDS = {"python_backend": "som.compiler-backend.v1", "python_render_only_formatter": "som.compiler-render-only.v1",
       "python_formatted_formatter": "som.compiler-formatter.v1", "rust_backend": "som.rust-backend.v1",
       "rust_formatter": "som.rust-formatter.rustfmt-1.9.0.v1", "rust_target_contract": "som.rust-output-contract.v1",
       "typescript_backend": "som.typescript-backend.v1", "typescript_formatter": "som.typescript-formatter.prettier-3.9.10.v1",
       "typescript_target_contract": "som.typescript-output-contract.v1"}


class Refusal(ValueError):
    pass


def require(condition, reason):
    if not condition:
        raise Refusal(reason)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def digest(value):
    require(isinstance(value, str) and SHA.fullmatch(value), "invalid SHA-256")
    return value


def canonical(value):
    """Frozen Python primitive rule used by the accepted baseline's qa.sha."""
    if value is None:
        return "null"
    if type(value) is bool:
        return "true" if value else "false"
    if type(value) is int:
        return str(value)
    if type(value) is float:
        require(math.isfinite(value), "nonfinite JSON number")
        text = repr(value)
        if "e" in text:
            first, exponent = text.split("e", 1)
            sign = "-" if exponent.startswith("-") else "+" if exponent.startswith("+") else ""
            digits = exponent.lstrip("+-")
            text = first + "e" + sign + (digits.zfill(2) if sign == "-" else digits)
        return text
    if type(value) is str:
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    if type(value) is list:
        return "[" + ",".join(canonical(item) for item in value) + "]"
    require(type(value) is dict and all(type(key) is str for key in value), "unsupported JSON value")
    return "{" + ",".join(canonical(key) + ":" + canonical(value[key]) for key in sorted(value)) + "}"


def value_sha(value):
    return sha(canonical(value).encode("utf-8", "strict"))


def parse(raw):
    # The generic scanner validates UTF-8, all syntax, depth and decoded keys.
    raw_compat.mask_raw(raw, [], "after")
    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result, "duplicate JSON key")
            result[key] = value
        return result
    def number(text):
        result = float(text)
        require(math.isfinite(result), "nonfinite JSON number")
        return result
    return json.loads(raw.decode("utf-8", "strict"), object_pairs_hook=pairs, parse_float=number)


def relative(path):
    require(isinstance(path, str) and path and "\\" not in path and not path.startswith("/") and all(part not in {"", ".", ".."} for part in path.split("/")), "unsafe repository path")
    return path


class Repository:
    """Read only bytes that match the recorded committed Git object."""
    def __init__(self, root):
        self.root = root.resolve(strict=True)
        self.commit = self.git("rev-parse", "--verify", "HEAD").decode().strip()
        require(re.fullmatch(r"[0-9a-f]{40}", self.commit) is not None, "Git commit identity unavailable")
        self.pins, self.cache = {}, {}

    def git(self, *args):
        return subprocess.check_output(["git", "-C", str(self.root), *args], stderr=subprocess.DEVNULL)

    def read(self, path):
        path = relative(path)
        if path not in self.cache:
            file = self.root / path
            require(all(not item.is_symlink() for item in (file, *file.parents)), "symlink source refused")
            require(file.is_file(), "regular committed file required")
            raw = file.read_bytes()
            require(raw == self.git("show", self.commit + ":" + path), "working source differs from committed source")
            self.cache[path] = raw
            self.pins[path] = sha(raw)
        return self.cache[path]

    def text(self, path):
        return self.read(path).decode("utf-8", "strict")

    def paths(self):
        return self.git("ls-tree", "-r", "--name-only", self.commit).decode("utf-8").splitlines()


def region(repo, name):
    path, start, end, expected = REGIONS[name]
    text = repo.text(path)
    require(text.count(start) == 1 and end in text.split(start, 1)[1], "owning factory boundary differs")
    result = start + text.split(start, 1)[1].split(end, 1)[0]
    require(sha(result.encode()) == expected, "owning factory changed; independent review required")
    return result


def string_constant(repo, owner, name, linux=False):
    prefix = r'#\[cfg\(all\(target_os\s*=\s*"linux",\s*target_arch\s*=\s*"x86_64",\s*target_env\s*=\s*"gnu"\)\)\]\s*' if linux else ""
    pattern = prefix + r'(?:pub\s+)?const\s+' + re.escape(name) + r'\s*:\s*&str\s*=\s*"([^"\\]*)"\s*;'
    values = re.findall(pattern, repo.text(owner))
    require(len(values) == 1, "owning source constant is ambiguous")
    return values[0]


def mac_constant(repo, owner, name):
    prefix = r'#\[cfg\(all\(target_os\s*=\s*"macos",\s*target_arch\s*=\s*"aarch64"\)\)\]\s*'
    values = re.findall(prefix + r'(?:pub\s+)?const\s+' + re.escape(name) + r'\s*:\s*&str\s*=\s*"([^"\\]*)"\s*;', repo.text(owner))
    require(len(values) == 1, "accepted macOS source artifact pin is ambiguous")
    return digest(values[0])


def included_path(owner, value):
    path = posixpath.normpath(posixpath.join(posixpath.dirname(owner), value))
    return relative(path)


def alias_path(repo, expression):
    module, name = expression.split("::")
    require(module in ALIASES, "unknown owning source alias")
    owner = ALIASES[module]
    pattern = r'(?:pub\s+)?const\s+' + re.escape(name) + r'\s*:\s*&str\s*=\s*include_str!\(\s*"([^"\\]+)"\s*\)\s*;'
    values = re.findall(pattern, repo.text(owner))
    require(len(values) == 1, "owning source alias is not a literal include")
    return included_path(owner, values[0])


def backend(repo, kind):
    owner = COMPILER if kind == "python" else RUST if kind == "rust" else TYPESCRIPT
    require(kind == "python" or string_constant(repo, owner, "BACKEND_ID") == IDS[kind + "_backend"], "backend descriptor ID changed")
    body = region(repo, "core_backend" if kind == "python" else "rust_vector" if kind == "rust" else "ts_vector")
    body = body.split("let files = [" if kind == "python" else "= &[", 1)[1].split("];", 1)[0]
    token = re.compile(r'\(\s*"([^"\\]+)"\s*,\s*(?:include_str!\(\s*"([^"\\]+)"\s*\)|([a-z_]+::[A-Z_]+))\s*,?\s*\)\s*,?', re.S)
    files, previous = {}, 0
    for match in token.finditer(body):
        require(not body[previous:match.start()].strip(), "unrecognized backend vector entry")
        name, include, alias = match.groups()
        relative(name)
        require(name not in files, "duplicate backend vector name")
        path = included_path(owner, include) if include is not None else alias_path(repo, alias)
        raw = repo.read(path)
        raw.decode("utf-8", "strict")  # include_str! hashes the complete UTF-8 text.
        files[name] = sha(raw)
        previous = match.end()
    require(not body[previous:].strip() and len(files) == {"python": 29, "rust": 13, "typescript": 9}[kind], "incomplete backend vector")
    return value_sha({"version": IDS[kind + "_backend"], "files": files})


def target_contract(repo, target):
    prefix = "som-rs/evidence/" + target + "-output-contract-amendment-"
    candidates = [path for path in repo.paths() if re.fullmatch(re.escape(prefix) + r"[0-9]{3}\.json", path)]
    require(candidates, "sealed target receipt unavailable")
    path = max(candidates)
    receipt = parse(repo.read(path))
    require(receipt["status"] == "amendment_sealed_after_independent_review" and receipt["independent_review"]["final_verdict"] == "APPROVED", "active target receipt is unsealed")
    files = receipt["files"]
    require(type(files) is dict and files, "target receipt files map missing")
    for file, expected in files.items():
        require(sha(repo.read(relative(file))) == digest(expected), "sealed target receipt file differs")
    result = value_sha(files)
    owner = RUST if target == "rust" else TYPESCRIPT
    require(receipt["target_contract"]["id"] == string_constant(repo, owner, "TARGET_CONTRACT_ID") == IDS[target + "_target_contract"], "target contract ID differs")
    require(digest(receipt["target_contract"]["sha256"]) == digest(string_constant(repo, owner, "TARGET_CONTRACT_SHA256")) == result, "active receipt and owning target constant differ")
    return result, {"path": path, "raw_sha256": sha(repo.read(path)), "files_map_sha256": result, "amendment": receipt["amendment"]}


def after_pins(repo):
    for name in REGIONS:
        region(repo, name)
    vectors = parse(repo.read("som-rs/contracts/compiler-v1/canonical-vectors.json"))["vectors"]
    require(vectors and all(canonical(parse(item["input_json"].encode())) == item["canonical"] and sha(item["canonical"].encode()) == item["sha256"] for item in vectors), "frozen canonical qualification differs")
    tools = parse(repo.read(ARTIFACTS))["tools"]
    def tool(name, expected_version, host):
        matches = [item for item in tools if item["tool"] == name and item["host"] == host]
        require(len(matches) == 1 and matches[0]["version"] == expected_version, "native artifact pin is ambiguous")
        return matches[0]
    ruff = digest(tool("ruff", "0.16.6", HOST)["executable_sha256"])
    rustfmt = digest(tool("rustfmt", "1.9.0", HOST)["executable_sha256"])
    node = digest(tool("node", "26.5.0", HOST)["executable_sha256"])
    prettier = digest(tool("prettier", "3.9.10", "platform-independent JavaScript")["tree_sha256"])
    require(ruff == string_constant(repo, COMPILER, "FORMATTER_SHA256", True), "Linux Ruff source pin differs")
    require(mac_constant(repo, COMPILER, "FORMATTER_SHA256") == OLD_RUFF_SHA256, "accepted Ruff source pin changed")
    require(rustfmt == string_constant(repo, RUST_FORMAT, "RUSTFMT_SHA256", True) and IDS["rust_formatter"] == string_constant(repo, RUST_FORMAT, "FORMATTER_ID"), "Linux rustfmt source pin differs")
    require(mac_constant(repo, RUST_FORMAT, "RUSTFMT_SHA256") == BEFORE["rust_formatter"], "accepted rustfmt source pin changed")
    require(node == string_constant(repo, TS_FORMAT, "NODE_SHA256", True) and prettier == string_constant(repo, TS_FORMAT, "PRETTIER_TREE_SHA256"), "Linux Node or Prettier source pin differs")
    ts_formatter = value_sha({"node": node, "libnode": None, "prettier_tree_sha256": prettier})
    old_ts_formatter = value_sha({"node": mac_constant(repo, TS_FORMAT, "NODE_SHA256"), "libnode": digest(string_constant(repo, TS_FORMAT, "LIBNODE_SHA256")), "prettier_tree_sha256": prettier})
    require(old_ts_formatter == BEFORE["typescript_formatter"], "accepted Prettier tree or macOS artifact descriptor changed")
    require(ts_formatter == string_constant(repo, TS_FORMAT, "FORMATTER_SHA256", True) and IDS["typescript_formatter"] == string_constant(repo, TS_FORMAT, "FORMATTER_ID"), "Linux TypeScript formatter factory differs")
    quality = ALIASES["quality"]
    profile = repo.read(alias_path(repo, "quality::PROFILE_TEXT"))
    config = {"id": string_constant(repo, quality, "VERSION"), "formatter_version": string_constant(repo, quality, "FORMATTER_VERSION"), "formatter_sha256": OLD_RUFF_SHA256, "profile_sha256": sha(profile)}
    require(value_sha(config) == BEFORE["python_formatted_formatter"], "accepted Python formatter profile changed")
    config["formatter_sha256"] = ruff
    formatted = value_sha(config)
    render_only = value_sha({"version": "som.compiler-render-only.v1", "formatter": None})
    require(render_only == BEFORE["python_render_only_formatter"], "render-only formatter changed")
    result = {kind + "_backend": backend(repo, kind) for kind in ["python", "rust", "typescript"]}
    result.update({"python_render_only_formatter": render_only, "python_formatted_formatter": formatted, "rust_formatter": rustfmt, "typescript_formatter": ts_formatter})
    receipts = {}
    for target in ["rust", "typescript"]:
        result[target + "_target_contract"], receipts[target] = target_contract(repo, target)
        compatibility = parse(repo.read("som-rs/contracts/" + target + "-output-v1/native-tools.json"))
        require(compatibility["artifacts"]["path"] == ARTIFACTS and compatibility["artifacts"]["sha256"] == sha(repo.read(ARTIFACTS)), "sealed native artifact authority differs")
        require(sha(repo.read(compatibility["native_contract"]["path"])) == compatibility["native_contract"]["sha256"], "native contract pin differs")
    return MappingProxyType(result), receipts


def before_proof(baseline):
    require(baseline["accepted_descriptors"]["health"]["formatter_sha256"] == OLD_RUFF_SHA256 and baseline["accepted_descriptors"]["health"]["backend_sha256"] == BEFORE["python_backend"], "accepted Python before factory proof differs")
    observed = set()
    for case in baseline["accepted_canary_observations"]:
        target = case["target"]
        require(target in {"python", "rust", "typescript"}, "unknown accepted canary target")
        names = {"backend": target + "_backend", "formatter": ("python_formatted_formatter" if case["prompt_version"] in {"som.prompt.v6", "som.prompt.v7"} else "python_render_only_formatter") if target == "python" else target + "_formatter"}
        if target != "python":
            names["target_contract"] = target + "_target_contract"
        for asset, key in names.items():
            matches = [item for item in case["asset_descriptors"] if item["pointer"] == "/bundle/assets/" + asset]
            require(len(matches) == 1 and matches[0]["id"] == IDS[key] and matches[0]["sha256"] == BEFORE[key], "accepted before descriptor proof differs")
            observed.add(key)
    require(observed == BEFORE.keys(), "accepted before descriptor proof incomplete")


@dataclass(frozen=True)
class VerifiedReply:
    report: dict
    # This semantic copy is available only after the exact frozen raw hash passes.
    # It is for later old-bundle rejection/assembly checks, which remain unrun here.
    original_bundle: dict | None


class ExperimentalChecker:
    def __init__(self, root):
        require(platform.system() == "Linux" and platform.machine() == "x86_64", "only fixed Linux x86_64 native pins are admitted")
        self.repo = Repository(root)
        require(self.repo.read(CHECKER) == Path(__file__).read_bytes(), "running checker differs from committed source")
        require(Path(raw_compat.__file__).resolve() == Path(__file__).with_name("raw_digest_compat.py").resolve() and self.repo.read(RAW_COMPAT) == Path(raw_compat.__file__).read_bytes(), "raw digest helper differs from committed local source")
        raw = self.repo.read(BASELINE)
        require(sha(raw) == BASELINE_SHA256, "accepted baseline raw pin differs")
        baseline = parse(raw)
        require(baseline["version"] == "som.ts065-accepted-response-hashes.v1" and baseline["product_acceptance_passed"] is False, "wrong accepted baseline kind")
        require(len(baseline["baselines"]["python"]["rows"]) == 1342 and len(baseline["baselines"]["rust"]["rows"]) == 2093, "accepted named scope incomplete")
        before_proof(baseline)
        self.rows = {}
        for target in ["python", "rust"]:
            for row in baseline["baselines"][target]["rows"]:
                if target == "rust" and row["check"] != "compile_equal":
                    continue
                fields = ("source", "id", "variant") if target == "python" else ("phase", "id")
                key = (target, *(row[field] for field in fields))
                require(key not in self.rows and row["passed"] is True and row["status"] == "passed", "accepted compile identity duplicated or failed")
                self.rows[key] = (row["qa_compile_status"] if target == "python" else row["qa_status"], digest(row["qa_compile_response_sha256"] if target == "python" else row["qa_response_sha256"]), digest(row["baseline_compile_response_sha256"] if target == "python" else row["baseline_response_sha256"]))
        require(sum(key[0] == "python" for key in self.rows) == 1342 and sum(key[0] == "rust" for key in self.rows) == 699, "accepted compile result scope differs")
        self.after, self.receipts = after_pins(self.repo)

    def source_record(self):
        return {"commit": self.repo.commit, "host": HOST, "source_sha256": dict(sorted(self.repo.pins.items())), "after_descriptors": dict(self.after), "active_target_receipts": self.receipts}

    def verify_response(self, target, identity, status, raw):
        require(target in {"python", "rust"}, "TypeScript full-inventory compile baseline is not established")
        fields = ("source", "id", "variant") if target == "python" else ("phase", "id")
        require(type(identity) is dict and set(identity) == set(fields) and all(type(identity[field]) is str for field in fields), "compile identity fields differ")
        key = (target, *(identity[field] for field in fields))
        require(key in self.rows, "compile identity is outside accepted scope")
        expected_status, frozen, older_control = self.rows[key]
        require(type(status) is int and status == expected_status, "HTTP status differs from accepted QA reply")
        original = None
        if status != 200:
            raw_compat.verify_frozen(raw, frozen, [])
            leaves = []
        else:
            current = parse(raw)
            require(type(current) is dict and type(current.get("bundle")) is dict, "successful compile bundle missing")
            bundle = current["bundle"]
            members = {"version", "prompt", "dsl", "assets", "hashes"} | ({"target"} if target == "rust" else set())
            require(set(bundle) == members and bundle["version"] == ("som.dsl-bundle.v2" if target == "rust" else "som.dsl-bundle.v1"), "compile bundle scope differs")
            require(target != "rust" or bundle["target"] == "rust", "compile target differs")
            hashes = bundle["hashes"]
            require(type(hashes) is dict and set(hashes) == {"prompt_sha256", "dsl_sha256", "payload_sha256"}, "bundle hash schema differs")
            for field, value in [("prompt_sha256", bundle["prompt"]), ("dsl_sha256", bundle["dsl"]), ("payload_sha256", {key: value for key, value in bundle.items() if key != "hashes"})]:
                require(digest(hashes[field]) == value_sha(value), "current canonical bundle hash differs")
            version = bundle["prompt"].get("version")
            require(version in {"som.prompt.v" + str(i) for i in range(1, 8)}, "prompt version outside known compile scope")
            names = {"backend": target + "_backend"}
            if target == "python":
                formatter = "python_formatted_formatter" if version in {"som.prompt.v6", "som.prompt.v7"} else "python_render_only_formatter"
                if formatter == "python_formatted_formatter":
                    names["formatter"] = formatter
                else:
                    require(bundle["assets"]["formatter"] == {"id": IDS[formatter], "sha256": self.after[formatter]}, "render-only formatter changed")
            else:
                names.update({"formatter": "rust_formatter", "target_contract": "rust_target_contract"})
            original, pins, leaves = copy.deepcopy(bundle), [], []
            for asset, name in names.items():
                descriptor = bundle["assets"][asset]
                require(descriptor == {"id": IDS[name], "sha256": self.after[name]}, "current asset differs from independent source factory")
                leaf = "/bundle/assets/" + asset + "/sha256"
                pins.append((leaf, BEFORE[name], self.after[name]))
                leaves.append(leaf)
                original["assets"][asset]["sha256"] = BEFORE[name]
            old_payload = value_sha({key: value for key, value in original.items() if key != "hashes"})
            payload_leaf = "/bundle/hashes/payload_sha256"
            pins.append((payload_leaf, old_payload, hashes["payload_sha256"]))
            leaves.append(payload_leaf)
            raw_compat.verify_frozen(raw, frozen, pins)
            original["hashes"]["payload_sha256"] = old_payload
        report = {"state": "experimental-unsealed", "byte_compatibility_passed": True, "product_acceptance": False, "target": target, "identity": dict(identity), "http_status": status, "accepted_QA_raw_sha256": frozen, "older_comparison_control_raw_sha256": older_control, "current_raw_sha256": sha(raw), "admitted_digest_leaves": leaves, "source_pin_record": self.source_record(), "remaining": {"old_bundle_rejection": "unrun", "assembly_bytes_and_file_maps": "unrun", "behavior_and_full_product_gates": "unrun", "typescript_full_inventory_baseline": "not established"}}
        return VerifiedReply(report, original)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True, type=Path)
    parser.add_argument("--target", required=True, choices=["python", "rust", "typescript"])
    parser.add_argument("--identity", required=True, help="JSON object naming one accepted compile row")
    parser.add_argument("--http-status", required=True, type=int)
    parser.add_argument("--response", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        require(not args.response.is_symlink() and args.response.is_file(), "regular raw reply file required")
        result = ExperimentalChecker(args.repo).verify_response(args.target, parse(args.identity.encode()), args.http_status, args.response.read_bytes())
        print(json.dumps(result.report, ensure_ascii=False, sort_keys=True))
        return 0
    except (Refusal, raw_compat.RawDigestError, OSError, ValueError, KeyError, TypeError, IndexError, UnicodeError, subprocess.CalledProcessError):
        print(json.dumps({"state": "experimental-unsealed", "byte_compatibility_passed": False, "product_acceptance": False, "reason": "authority, scope, status, canonical hash, or exact raw-byte check failed"}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

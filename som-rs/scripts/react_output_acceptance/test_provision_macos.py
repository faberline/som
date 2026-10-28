"""GitHub-only provisioner fixtures. Run on GitHub, never on the local host."""
from __future__ import annotations

import contextlib
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import struct
import tarfile
import tempfile
import types
import unittest
from unittest import mock
import urllib.error
import urllib.parse
import urllib.request

HERE = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location("react_macos_provision_fixtures", HERE / "provision_macos.py")
provisioner = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(provisioner)


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def archive_bytes(root, members):
    result = io.BytesIO()
    with tarfile.open(fileobj=result, mode="w:gz") as bundle:
        for relative, content in members.items():
            member = tarfile.TarInfo(root + "/" + relative)
            member.mode = 0o755 if relative.startswith("bin/") else 0o644
            if isinstance(content, tuple):
                kind, target = content
                member.type = tarfile.SYMTYPE if kind == "symlink" else tarfile.LNKTYPE
                member.linkname = target
                bundle.addfile(member)
            else:
                member.size = len(content)
                bundle.addfile(member, io.BytesIO(content))
    return result.getvalue()


def entry(formula, version, raw, revision=0, rebuild=0, target="arm64_tahoe"):
    package = version + (f"_{revision}" if revision else "")
    sha = digest(raw)
    return {"formula": formula, "version": version, "revision": revision,
            "bottle_rebuild": rebuild, "target": target,
            "expected_keg_directory": f"/opt/homebrew/Cellar/{formula}/{package}",
            "opt_symlink": f"/opt/homebrew/opt/{formula}",
            "url": f"https://ghcr.io/v2/homebrew/core/{formula.replace('@', '/')}/blobs/sha256:{sha}",
            "archive_sha256": sha, "archive_size_bytes": len(raw),
            "non_system_dynamic_dependencies": []}


def macho_link_command(name, kind=0xC):
    fixed = 12 if kind == 0x8000001C else 24
    payload = name.encode() + b"\0"
    size = (fixed + len(payload) + 7) // 8 * 8
    header = struct.pack("<3I", kind, size, fixed)
    if fixed == 24:
        header += struct.pack("<3I", 2, 0x10000, 0x10000)
    return header + payload + b"\0" * (size - fixed - len(payload))


def macho_fixture(commands, body=b"unchanged body", padding=0, cpu=0x0100000C, subtype=0):
    region = b"".join(commands)
    return struct.pack("<8I", 0xFEEDFACF, cpu, subtype, 2, len(commands), len(region), 0, 0) + region + b"\0" * padding + body


def fat_fixture(slices, *, wide=False, endian=">"):
    width = 32 if wide else 20
    raw = bytearray(8 + width * len(slices))
    table = []
    for cpu, subtype, content in slices:
        offset = (len(raw) + 15) // 16 * 16
        raw.extend(b"\0" * (offset - len(raw)))
        raw.extend(content)
        values = (cpu, subtype, offset, len(content), 4, 0) if wide else (cpu, subtype, offset, len(content), 4)
        table.append(struct.pack(endian + ("IIQQII" if wide else "IIIII"), *values))
    header = struct.pack(endian + "II", 0xCAFEBABF if wide else 0xCAFEBABE, len(slices)) + b"".join(table)
    raw[:len(header)] = header
    return bytes(raw)


def signed_macho_fixture(commands, bits, cpu, subtype=0):
    directory = bytearray(40)
    struct.pack_into(">II", directory, 0, 0xFADE0C02, 40)
    directory[39] = bits
    signature = struct.pack(">IIIII", 0xFADE0CC0, 60, 1, 0, 20) + directory
    offset = 32 + sum(map(len, commands)) + 16
    signature_command = struct.pack("<IIII", 0x1D, 16, offset, len(signature))
    return macho_fixture([*commands, signature_command], signature, cpu=cpu, subtype=subtype)


def normalization_policy():
    return {"policy": "homebrew-6.0.6-ruby-macho-5.0.0-v1",
            "prefixes": {"@@HOMEBREW_PREFIX@@": "/opt/homebrew", "@@HOMEBREW_CELLAR@@": "/opt/homebrew/Cellar"},
            "codesign_arguments": ["--sign", "-", "--force", "--preserve-metadata=entitlements,requirements,flags,runtime"]}


class FakeOpener:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []

    def open(self, request, timeout):
        self.requests.append(request)
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return io.BytesIO(response)


class ProvisionMacosTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="som-macos-provision-fixture-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.out = self.root / "logs"
        self.out.mkdir()
        self.prefix = self.root / "homebrew"
        self.node_bytes = b"fixture node executable\n"
        self.libnode_bytes = b"fixture libnode bytes\n"
        self.npm_bytes = b"fixture npm source\n"
        self.node_archive = archive_bytes("node/1.0", {
            "bin/node": self.node_bytes,
            "lib/libnode.0.dylib": self.libnode_bytes,
            "libexec/lib/node_modules/npm/bin/npm-cli.js": self.npm_bytes,
            "libexec/lib/node_modules/npm/npm-link": ("symlink", "bin/npm-cli.js"),
        })
        self.node = entry("node", "1.0", self.node_archive)
        self.manifest = {"version": "som.react-output-macos-bottles.v1",
                         "bottle_count": 1, "bottles": [self.node]}
        npm_lines = (
            f"bin/npm-cli.js\0{digest(self.npm_bytes)}\n"
            "npm-link\0symlink:bin/npm-cli.js\n"
            f"npmrc\0{digest(b'prefix = /opt/homebrew' + bytes([10]))}\n"
        ).encode()
        self.contracts = {"fixture": {
            "node": {"binary": "/opt/homebrew/Cellar/node/1.0/bin/node",
                     "sha256": digest(self.node_bytes),
                     "libnode": {"path": "/opt/homebrew/Cellar/node/1.0/lib/libnode.0.dylib",
                                 "sha256": digest(self.libnode_bytes)},
                     "homebrew_libraries": {}},
            "npm": {"root": "/opt/homebrew/lib/node_modules/npm",
                    "tree_sha256": digest(npm_lines), "tree_files": 2, "tree_symlinks": 1},
        }}
        self.downloads = []

    def downloader(self, candidate, destination):
        self.downloads.append(candidate["archive_sha256"])
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(self.node_archive)
        return {"archive_sha256": digest(self.node_archive), "archive_size_bytes": len(self.node_archive)}

    def supplement_fixture(self):
        files = []
        blobs = {}
        pins = []
        bindings = []
        for index in range(11):
            installed = f"/opt/homebrew/Cellar/node/1.0/bin/tool{index}"
            raw = f"public frozen fixture {index}\n".encode()
            path = f"frozen-tools/files/node/1.0/bin/tool{index}"
            pin = {"label": f"fixture/{index}", "path": installed, "sha256": digest(raw)}
            pins.append(pin)
            bindings.append({"path": installed, "sha256": pin["sha256"], "page_size": 4096, "cms_reserve": 0})
            files.append({"path": path, "installed_path": installed, "sha256": pin["sha256"],
                          "formula": "node", "archive_sha256": self.node["archive_sha256"],
                          "license_path": "frozen-tools/licenses/node.txt", "bytes": len(raw), "mode": 0o755})
            blobs[path] = raw
        manifest = {"version": provisioner.SUPPLEMENT_VERSION, "repository": "faberline/som", "files": files}
        manifest_raw = json.dumps(manifest).encode()
        blobs["frozen-tools/manifest.json"] = manifest_raw
        specification = {"repository": "faberline/som", "commit": "a" * 40,
                         "manifest_path": "frozen-tools/manifest.json", "manifest_sha256": digest(manifest_raw)}
        policy = {**normalization_policy(), "installed_signing_page_sizes": bindings}
        return specification, manifest, blobs, pins, policy

    def test_public_supplement_verifies_all_eleven_blobs_before_use(self):
        specification, _, blobs, pins, policy = self.supplement_fixture()
        reader = mock.Mock(side_effect=lambda commit, path: blobs[path])
        report = {}
        verified = provisioner.load_frozen_file_supplement(specification, [self.node], pins, policy, report, blob_reader=reader)
        self.assertEqual(reader.call_count, 12)
        self.assertTrue(all(call.args[0] == "a" * 40 for call in reader.call_args_list))
        self.assertEqual(set(verified), {pin["path"] for pin in pins})
        self.assertEqual(report["status"], "verified")
        self.assertNotIn("data", json.dumps(report))

    def test_supplement_rejects_manifest_hash_unknown_paths_bottle_size_and_mode(self):
        changes = ({"installed_path": "/opt/homebrew/Cellar/node/1.0/bin/unknown"},
                   {"sha256": "0" * 64}, {"archive_sha256": "0" * 64},
                   {"path": "frozen-tools/files/node/1.0/../other"},
                   {"bytes": provisioner.SUPPLEMENT_FILE_LIMIT + 1}, {"mode": 0o777})
        for change in changes:
            with self.subTest(change=change):
                specification, manifest, blobs, pins, policy = self.supplement_fixture()
                manifest["files"][0].update(change)
                encoded = json.dumps(manifest).encode()
                specification["manifest_sha256"] = digest(encoded)
                blobs["frozen-tools/manifest.json"] = encoded
                with self.assertRaises(provisioner.ProvisionError):
                    provisioner.load_frozen_file_supplement(specification, [self.node], pins, policy, {},
                                                            blob_reader=lambda commit, path: blobs[path])
        specification, _, blobs, pins, policy = self.supplement_fixture()
        specification["manifest_sha256"] = "0" * 64
        reader = mock.Mock(side_effect=lambda commit, path: blobs[path])
        with self.assertRaises(provisioner.ProvisionError) as found:
            provisioner.load_frozen_file_supplement(specification, [self.node], pins, policy, {}, blob_reader=reader)
        self.assertEqual(found.exception.code, "supplement_manifest_hash_mismatch")
        self.assertEqual(reader.call_count, 1)

    def test_last_bad_supplement_blob_blocks_all_staging_and_installation(self):
        specification, manifest, blobs, pins, policy = self.supplement_fixture()
        blobs[manifest["files"][-1]["path"]] = b"wrong last blob"
        document = {**self.manifest, "macho_normalization": policy, "frozen_file_supplement": specification}
        with mock.patch.object(provisioner, "pin_specs", return_value=pins), \
                mock.patch.object(provisioner, "stage_package") as stage, \
                mock.patch.object(provisioner, "install_closure") as install:
            with self.assertRaises(provisioner.ProvisionError) as found:
                provisioner.provision(document, {}, self.out, provisioner.new_report(), prefix=self.prefix,
                                      expected_count=None, supplement_reader=lambda commit, path: blobs[path])
        self.assertEqual(found.exception.code, "supplement_file_hash_mismatch")
        stage.assert_not_called()
        install.assert_not_called()

    def test_verified_supplement_keeps_raw_relocated_and_final_hashes_and_skips_signing(self):
        specification, manifest, blobs, pins, policy = self.supplement_fixture()
        members = {f"bin/tool{index}": f"raw bottle fixture {index}".encode() for index in range(11)}
        raw_macho = macho_fixture([macho_link_command("@@HOMEBREW_PREFIX@@/opt/fixture/lib/libfixture.dylib")])
        members["bin/tool0"] = raw_macho
        raw_archive = archive_bytes("node/1.0", members)
        node = entry("node", "1.0", raw_archive)
        for row in manifest["files"]:
            row["archive_sha256"] = node["archive_sha256"]
        encoded = json.dumps(manifest).encode()
        specification["manifest_sha256"] = digest(encoded)
        blobs["frozen-tools/manifest.json"] = encoded
        verified = provisioner.load_frozen_file_supplement(specification, [node], pins, policy, {},
                                                           blob_reader=lambda commit, path: blobs[path])
        def download(candidate, destination):
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(raw_archive)
            return {"archive_sha256": digest(raw_archive)}
        cli, bridge = mock.Mock(), mock.Mock()
        record = {"attempts": []}
        _, staged = provisioner.stage_package(node, self.out, pins, record, downloader=download,
                                               normalization=policy, macho_runner=cli, security_runner=bridge,
                                               supplement=verified)
        cli.assert_not_called()
        bridge.assert_not_called()
        attempt = record["attempts"][0]
        self.assertEqual(attempt["raw_hash_checks"][0]["actual_sha256"], digest(raw_macho))
        relocated, _ = provisioner.relocate_macho_bytes(raw_macho)
        self.assertEqual(attempt["normalization"]["files"][0]["normalized_sha256"], digest(relocated))
        first = attempt["supplement_files"][0]
        self.assertEqual(first["pre_supplement_sha256"], digest(relocated))
        self.assertEqual(first["supplemented_sha256"], pins[0]["sha256"])
        self.assertEqual((staged / "bin/tool0").read_bytes(), blobs[manifest["files"][0]["path"]])
        self.assertTrue(all(check["status"] == "passed" for check in attempt["hash_checks"]))

    def test_runner_guard_requires_hosted_arm64_macos_26_and_all_flags(self):
        good = {"GITHUB_ACTIONS": "true", "RUNNER_ENVIRONMENT": "github-hosted", "RUNNER_OS": "macOS"}
        provisioner.require_github_runner(good, system="Darwin", machine="arm64", macos_version="26.1")
        for changed in ({}, {**good, "GITHUB_ACTIONS": "false"},
                        {**good, "RUNNER_ENVIRONMENT": "self-hosted"}, {**good, "RUNNER_OS": "Linux"}):
            with self.subTest(environment=changed):
                with self.assertRaises(provisioner.ProvisionError):
                    provisioner.require_github_runner(changed, system="Darwin", machine="arm64", macos_version="26.1")
        for system, machine, version in (("Linux", "arm64", "26.1"), ("Darwin", "x86_64", "26.1"),
                                         ("Darwin", "arm64", "15.7")):
            with self.subTest(system=system, machine=machine, version=version):
                with self.assertRaises(provisioner.ProvisionError):
                    provisioner.require_github_runner(good, system=system, machine=machine, macos_version=version)

    def test_cli_guard_refuses_before_output_download_or_install(self):
        output = self.root / "refused"
        stream = io.StringIO()
        with mock.patch.dict(provisioner.os.environ, {}, clear=True), \
                mock.patch.object(provisioner, "provision") as run, \
                contextlib.redirect_stdout(stream):
            code = provisioner.main(["--out", str(output)])
        self.assertEqual(code, 1)
        run.assert_not_called()
        self.assertFalse(output.exists())
        summary = json.loads(stream.getvalue())
        self.assertEqual(summary["status"], "failed")
        self.assertEqual(summary["code"], "runner_refused")
        self.assertEqual(summary["product_suites"], "not_run")

    def test_download_uses_exact_public_formula_scope_and_checks_archive_hash(self):
        raw = b"fixture archive"
        candidate = entry("openssl@3", "3.6.3", raw)
        opener = FakeOpener([b'{"token":"fake-public-scope-token"}', raw])
        destination = self.out / "bottle.tar.gz"
        result = provisioner.download_archive(candidate, destination, opener=opener)
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(opener.requests[0].full_url).query)
        self.assertEqual(query["scope"], ["repository:homebrew/core/openssl/3:pull"])
        self.assertEqual(opener.requests[1].full_url, candidate["url"])
        self.assertEqual(opener.requests[1].get_header("Authorization"), "Bearer fake-public-scope-token")
        self.assertEqual(destination.read_bytes(), raw)
        self.assertEqual(result["archive_sha256"], digest(raw))

    def test_download_errors_do_not_retain_signed_urls_or_tokens(self):
        opener = FakeOpener([urllib.error.URLError("https://signed.invalid/object?token=private-fixture")])
        with self.assertRaises(provisioner.ProvisionError) as found:
            provisioner.download_archive(self.node, self.out / "failed.tar.gz", opener=opener)
        self.assertEqual(found.exception.code, "download_failed")
        self.assertNotIn("signed", str(found.exception))
        self.assertNotIn("private-fixture", str(found.exception))

    def test_cross_host_redirect_removes_the_public_token(self):
        request = urllib.request.Request(self.node["url"], headers={"Authorization": "Bearer fake-fixture"})
        redirected = provisioner.PublicBottleRedirect().redirect_request(
            request, None, 302, "Found", {}, "https://storage.invalid/blob?signature=fixture")
        self.assertIsNone(redirected.get_header("Authorization"))

    def test_sha_mismatch_blocks_extraction_even_with_an_injected_downloader(self):
        def wrong_download(candidate, destination):
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(b"wrong bytes")
            return {"archive_sha256": candidate["archive_sha256"]}
        record = {"attempts": []}
        with mock.patch.object(provisioner, "extract_archive") as extract:
            with self.assertRaises(provisioner.ProvisionError) as found:
                provisioner.stage_package(self.node, self.out, [], record, downloader=wrong_download)
        self.assertEqual(found.exception.code, "archive_hash_mismatch")
        extract.assert_not_called()
        self.assertEqual(record["attempts"][0]["status"], "failed")

    def test_tar_traversal_outside_keg_hardlinks_and_external_symlinks_are_refused(self):
        cases = {
            "traversal": {"../../escape": b"bad"},
            "other-keg": {"../1.1/bad": b"bad"},
            "hardlink": {"hard": ("hardlink", "node/1.0/bin/node")},
            "relative-link": {"link": ("symlink", "../../../outside")},
            "absolute-link": {"link": ("symlink", "/tmp/outside")},
            "symlink-parent": {"link": ("symlink", "inside"), "link/child": b"bad"},
        }
        for name, members in cases.items():
            with self.subTest(name=name):
                archive = self.out / f"{name}.tar.gz"
                archive.write_bytes(archive_bytes("node/1.0", members))
                stage = self.out / ("stage-" + name)
                with self.assertRaises(provisioner.ProvisionError):
                    provisioner.extract_archive(archive, stage, self.node)
                self.assertFalse(stage.exists())

    def test_tar_absolute_member_is_refused_before_writing(self):
        archive = self.out / "absolute.tar.gz"
        with tarfile.open(archive, "w:gz") as bundle:
            member = tarfile.TarInfo("/opt/homebrew/Cellar/node/1.0/bin/node")
            member.size = 3
            bundle.addfile(member, io.BytesIO(b"bad"))
        stage = self.out / "absolute-stage"
        with self.assertRaises(provisioner.ProvisionError):
            provisioner.extract_archive(archive, stage, self.node)
        self.assertFalse(stage.exists())

    def test_internal_symlink_and_executable_mode_are_preserved(self):
        archive = self.out / "valid.tar.gz"
        archive.write_bytes(archive_bytes("node/1.0", {
            "bin/node": self.node_bytes, "bin/tool": ("symlink", "node"),
        }))
        staged = provisioner.extract_archive(archive, self.out / "valid-stage", self.node)
        self.assertEqual((staged / "bin/node").read_bytes(), self.node_bytes)
        self.assertEqual((staged / "bin/node").stat().st_mode & 0o777, 0o755)
        self.assertTrue((staged / "bin/tool").is_symlink())
        self.assertEqual(provisioner.os.readlink(staged / "bin/tool"), "node")

    def test_postgresql_timezone_hardlinks_become_independent_regular_copies(self):
        root = "postgresql@17/17.7_1"
        source = "share/postgresql/timezone/Etc/UTC"
        alias = "share/postgresql/timezone/UTC"
        raw = archive_bytes(root, {source: b"timezone bytes", alias: ("hardlink", root + "/" + source)})
        pg = entry("postgresql@17", "17.7", raw, revision=1)
        pg["hardlink_copy_prefixes"] = ["share/postgresql/timezone"]
        archive = self.out / "pg-timezone.tar.gz"
        archive.write_bytes(raw)
        staged = provisioner.extract_archive(archive, self.out / "pg-timezone-stage", pg)
        self.assertEqual((staged / alias).read_bytes(), b"timezone bytes")
        self.assertNotEqual((staged / alias).stat().st_ino, (staged / source).stat().st_ino)
        (staged / alias).write_bytes(b"independent mutation")
        self.assertEqual((staged / source).read_bytes(), b"timezone bytes")

    def test_timezone_hardlink_policy_refuses_outside_targets_and_link_chains(self):
        root = "postgresql@17/17.7_1"
        timezone = "share/postgresql/timezone/"
        cases = {
            "absolute": (timezone + "bad", "/tmp/outside"),
            "traversal": (timezone + "bad", root + "/" + timezone + "../outside"),
            "outside-keg": (timezone + "bad", "other/1.0/share/postgresql/timezone/regular"),
            "outside-timezone": (timezone + "bad", root + "/bin/tool"),
            "outside-member": ("bin/bad", root + "/" + timezone + "regular"),
            "chain": (timezone + "bad", root + "/" + timezone + "alias"),
            "symlink-source": (timezone + "bad", root + "/" + timezone + "symbolic"),
            "missing-source": (timezone + "bad", root + "/" + timezone + "missing"),
        }
        for name, (member, target) in cases.items():
            with self.subTest(name=name):
                raw = archive_bytes(root, {timezone + "regular": b"zone", "bin/tool": b"tool",
                    timezone + "alias": ("hardlink", root + "/" + timezone + "regular"),
                    timezone + "symbolic": ("symlink", "regular"), member: ("hardlink", target)})
                pg = entry("postgresql@17", "17.7", raw, revision=1)
                pg["hardlink_copy_prefixes"] = ["share/postgresql/timezone"]
                archive = self.out / (name + "-timezone.tar.gz")
                archive.write_bytes(raw)
                staging = self.out / (name + "-timezone-stage")
                with self.assertRaises(provisioner.ProvisionError):
                    provisioner.extract_archive(archive, staging, pg)
                self.assertFalse(staging.exists())

    def test_only_the_exact_llvm_global_link_is_allowed_and_cannot_have_children(self):
        declaration = {"etc/clang": {"link_target": "../../../../etc/clang", "resolved_target": "/opt/homebrew/etc/clang"}}
        for name, members in {
            "valid": {"etc/clang": ("symlink", "../../../../etc/clang")},
            "target-change": {"etc/clang": ("symlink", "../../../../etc/other")},
            "member-change": {"etc/other": ("symlink", "../../../../etc/clang")},
            "write-through": {"etc/clang": ("symlink", "../../../../etc/clang"), "etc/clang/file": b"bad"},
        }.items():
            with self.subTest(name=name):
                raw = archive_bytes("llvm/22.1.8", members)
                llvm = entry("llvm", "22.1.8", raw)
                llvm["global_symlinks"] = declaration
                archive = self.out / (name + "-llvm.tar.gz")
                archive.write_bytes(raw)
                staging = self.out / (name + "-llvm-stage")
                if name == "valid":
                    staged = provisioner.extract_archive(archive, staging, llvm)
                    self.assertEqual(provisioner.os.readlink(staged / "etc/clang"), "../../../../etc/clang")
                else:
                    with self.assertRaises(provisioner.ProvisionError):
                        provisioner.extract_archive(archive, staging, llvm)
                    self.assertFalse(staging.exists())

    def test_declared_cross_keg_links_resolve_from_the_public_cellar_path(self):
        node = json.loads(json.dumps(self.node))
        node["non_system_dynamic_dependencies"] = ["llvm"]
        llvm = entry("llvm", "22.1.8", b"fixture llvm archive")
        targets = {
            "bin/llvm-opt": "../../../../opt/llvm/bin/ld.lld",
            "bin/llvm-cellar": "../../../llvm/22.1.8/lib/libLLVM.dylib",
        }
        archive = self.out / "cross-keg.tar.gz"
        archive.write_bytes(archive_bytes("node/1.0", {
            name: ("symlink", target) for name, target in targets.items()
        }))
        staged = provisioner.extract_archive(archive, self.out / "cross-keg-stage", node,
                                             closure=[node, llvm])
        for name, target in targets.items():
            self.assertTrue((staged / name).is_symlink())
            self.assertEqual(provisioner.os.readlink(staged / name), target)

    def test_transitive_declared_dependency_link_is_allowed(self):
        node = json.loads(json.dumps(self.node))
        node["non_system_dynamic_dependencies"] = ["llvm"]
        llvm = entry("llvm", "22.1.8", b"fixture llvm archive")
        llvm["non_system_dynamic_dependencies"] = ["z3"]
        z3 = entry("z3", "4.16.0", b"fixture z3 archive")
        target = "/opt/homebrew/opt/z3/lib/libz3.dylib"
        archive = self.out / "transitive.tar.gz"
        archive.write_bytes(archive_bytes("node/1.0", {"lib/z3": ("symlink", target)}))
        staged = provisioner.extract_archive(archive, self.out / "transitive-stage", node,
                                             closure=[node, llvm, z3])
        self.assertEqual(provisioner.os.readlink(staged / "lib/z3"), target)

    def test_manifest_package_outside_this_dependency_closure_is_refused(self):
        llvm = entry("llvm", "22.1.8", b"fixture llvm archive")
        archive = self.out / "undeclared.tar.gz"
        target = "/opt/homebrew/opt/llvm/bin/ld.lld"
        archive.write_bytes(archive_bytes("node/1.0", {"bin/llvm": ("symlink", target)}))
        stage = self.out / "undeclared-stage"
        with self.assertRaises(provisioner.ProvisionError) as found:
            provisioner.extract_archive(archive, stage, self.node, closure=[self.node, llvm])
        self.assertEqual(found.exception.code, "archive_link_refused")
        self.assertEqual(found.exception.details, {
            "member": "node/1.0/bin/llvm", "link_target": target, "resolved_target": target,
        })
        self.assertFalse(stage.exists())

    def test_cross_keg_policy_still_refuses_an_unknown_version_and_outside_prefix(self):
        node = json.loads(json.dumps(self.node))
        node["non_system_dynamic_dependencies"] = ["llvm"]
        llvm = entry("llvm", "22.1.8", b"fixture llvm archive")
        for index, target in enumerate(("/opt/homebrew/Cellar/llvm/99.0/lib/libLLVM.dylib", "/tmp/outside")):
            with self.subTest(target=target):
                archive = self.out / f"wrong-closure-{index}.tar.gz"
                archive.write_bytes(archive_bytes("node/1.0", {"bin/tool": ("symlink", target)}))
                stage = self.out / f"wrong-closure-{index}-stage"
                with self.assertRaises(provisioner.ProvisionError) as found:
                    provisioner.extract_archive(archive, stage, node, closure=[node, llvm])
                self.assertEqual(found.exception.code, "archive_link_refused")
                self.assertEqual(found.exception.details["link_target"], target)
                self.assertFalse(stage.exists())

    def test_declared_cross_keg_link_cannot_be_a_write_through_parent(self):
        node = json.loads(json.dumps(self.node))
        node["non_system_dynamic_dependencies"] = ["llvm"]
        llvm = entry("llvm", "22.1.8", b"fixture llvm archive")
        archive = self.out / "cross-keg-parent.tar.gz"
        archive.write_bytes(archive_bytes("node/1.0", {
            "lib/llvm": ("symlink", "/opt/homebrew/opt/llvm/lib"),
            "lib/llvm/child": b"must not be written",
        }))
        stage = self.out / "cross-keg-parent-stage"
        with self.assertRaises(provisioner.ProvisionError) as found:
            provisioner.extract_archive(archive, stage, node, closure=[node, llvm])
        self.assertEqual(found.exception.code, "archive_link_refused")
        self.assertFalse(stage.exists())

    def test_unknown_global_link_keeps_public_details_in_the_failure_record(self):
        target = "/opt/homebrew/share/unknown-global-path"
        raw = archive_bytes("node/1.0", {"share/link": ("symlink", target)})
        node = entry("node", "1.0", raw)
        def download(candidate, destination):
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(raw)
            return {"archive_sha256": digest(raw)}
        record = {"attempts": []}
        with self.assertRaises(provisioner.ProvisionError) as found:
            provisioner.stage_package(node, self.out, [], record, downloader=download, closure=[node])
        details = record["attempts"][0]["error"]["details"]
        self.assertEqual(details["member"], "node/1.0/share/link")
        self.assertEqual(details["link_target"], target)
        self.assertEqual(provisioner.error_record(found.exception)["details"], details)

    def test_tree_hash_includes_literal_symlink_targets_and_both_counts(self):
        root = self.out / "tree"
        root.mkdir()
        (root / "empty").mkdir()
        (root / "a").write_bytes(b"content")
        (root / "b").symlink_to("a")
        expected = digest(f"a\0{digest(b'content')}\nb\0symlink:a\n".encode())
        self.assertEqual(provisioner.tree_sha256(root), (expected, 1, 1))

    def test_macho_port_changes_only_linkage_commands_and_keeps_body_offsets(self):
        old_names = ("@@HOMEBREW_PREFIX@@/opt/node/lib/libnode.dylib",
                     "@@HOMEBREW_PREFIX@@/opt/llvm/lib/libLLVM.dylib",
                     "@@HOMEBREW_CELLAR@@/rust/1.96.1/lib")
        new_names = ("/opt/homebrew/opt/node/lib/libnode.dylib",
                     "/opt/homebrew/opt/llvm/lib/libLLVM.dylib", "/opt/homebrew/Cellar/rust/1.96.1/lib")
        kinds = (0xD, 0xC, 0x8000001C)
        old_commands = [macho_link_command(name, kind) for name, kind in zip(old_names, kinds)]
        new_commands = [macho_link_command(name, kind) for name, kind in zip(new_names, kinds)]
        body = b"literal @@HOMEBREW_PREFIX@@ outside linkage must remain unchanged"
        # A real segment proves where header padding ends. The Cellar rpath
        # can grow by one alignment unit even when other commands shrink.
        body_offset = 1024
        segment = struct.pack("<II16sQQQQIIII", 0x19, 72, b"__DATA", 0, 4096,
                              body_offset, len(body), 7, 3, 0, 0)
        old_commands.insert(0, segment)
        new_commands.insert(0, segment)
        padding = body_offset - 32 - sum(map(len, old_commands))
        raw = macho_fixture(old_commands, body, padding=padding)
        expected = macho_fixture(new_commands, body,
                                 padding=padding + sum(map(len, old_commands)) - sum(map(len, new_commands)))
        relocated, operations = provisioner.relocate_macho_bytes(raw)
        self.assertEqual(relocated, expected)
        self.assertEqual(len(relocated), len(raw))
        self.assertEqual([(row["from"], row["to"]) for row in operations], list(zip(old_names, new_names)))
        self.assertEqual(relocated[-len(body):], body)

    def test_macho_growth_uses_verified_header_padding_and_keeps_sections(self):
        section_offset = 512
        segment = struct.pack("<II16sQQQQIIII", 0x19, 152, b"__TEXT", 0x100000000, 4096,
                              0, 520, 7, 5, 1, 0)
        segment += struct.pack("<16s16sQQ8I", b"__text", b"__TEXT", 0x100000200, 8,
                               section_offset, 2, 0, 0, 0, 0, 0, 0)
        old_command = macho_link_command("@@HOMEBREW_CELLAR@@/abc")
        new_command = macho_link_command("/opt/homebrew/Cellar/abc")
        self.assertGreater(len(new_command), len(old_command))
        raw = macho_fixture([segment, old_command], b"BODYDATA",
                            padding=section_offset - 32 - len(segment) - len(old_command))
        relocated, _ = provisioner.relocate_macho_bytes(raw)
        self.assertEqual(relocated[section_offset:], b"BODYDATA")
        self.assertEqual(struct.unpack_from("<I", relocated, 20)[0], len(segment) + len(new_command))
        self.assertEqual(len(relocated), len(raw))
        no_padding = macho_fixture([old_command])
        with self.assertRaises(provisioner.ProvisionError) as found:
            provisioner.relocate_macho_bytes(no_padding)
        self.assertEqual(found.exception.code, "macho_header_padding_missing")

    def test_source_signature_page_size_is_read_and_invalid_size_refused(self):
        for bits in (12, 14):
            directory = bytearray(40)
            struct.pack_into(">II", directory, 0, 0xFADE0C02, 40)
            directory[39] = bits
            signature = struct.pack(">IIIII", 0xFADE0CC0, 60, 1, 0, 20) + directory
            command = struct.pack("<IIII", 0x1D, 16, 48, len(signature))
            raw = macho_fixture([command], signature)
            self.assertEqual(provisioner.signing_page_size(raw), 1 << bits)
            broken = bytearray(raw)
            broken[-1] = 9
            with self.assertRaises(provisioner.ProvisionError):
                provisioner.signing_page_size(bytes(broken))
        self.assertIsNone(provisioner.signing_page_size(macho_fixture([macho_link_command("/usr/lib/a.dylib")])))

    def test_universal_relocation_preserves_table_slice_ranges_and_unchanged_payloads(self):
        old = macho_link_command("@@HOMEBREW_PREFIX@@/opt/fixture/lib/libfixture.dylib")
        unchanged = macho_fixture([macho_link_command("/usr/lib/unchanged.dylib")], cpu=0x01000007, subtype=8)
        parts = [(0x01000007, 3, macho_fixture([old], cpu=0x01000007, subtype=3)),
                 (0x01000007, 8, unchanged), (0x0100000C, 0, macho_fixture([old]))]
        for wide, endian in ((False, ">"), (True, ">"), (False, "<"), (True, "<")):
            with self.subTest(wide=wide, endian=endian):
                raw = fat_fixture(parts, wide=wide, endian=endian)
                relocated, operations = provisioner.relocate_macho_bytes(raw)
                slices = provisioner.fat_macho_slices(raw)
                self.assertEqual(provisioner.fat_macho_slices(relocated), slices)
                self.assertEqual(raw[:slices[0]["offset"]], relocated[:slices[0]["offset"]])
                self.assertEqual(len(raw), len(relocated))
                for part, original in zip(slices, parts):
                    offset, size = part["offset"], part["size"]
                    expected, _ = provisioner.relocate_macho_bytes(original[2])
                    self.assertEqual(relocated[offset:offset + size], expected)
                self.assertEqual([operation["slice_index"] for operation in operations], [0, 2])
                self.assertEqual(provisioner.macho_layout(relocated)["format"], "universal_macho")

    def test_universal_static_archives_are_byte_identical_and_never_signed(self):
        archive = b"!<arch>\n@@HOMEBREW_PREFIX@@ static object bytes"
        raw = fat_fixture([(7, 3, archive), (0x01000007, 3, archive), (0x0100000C, 0x80000002, archive)])
        self.assertEqual(provisioner.relocate_macho_bytes(raw), (raw, []))
        staged = self.out / "universal-archive-stage"
        staged.mkdir()
        (staged / "runtime.a").write_bytes(raw)
        sign = mock.Mock()
        report = {}
        provisioner.normalize_staged_macho(staged, normalization_policy(), report, command_runner=sign)
        sign.assert_not_called()
        self.assertEqual((staged / "runtime.a").read_bytes(), raw)

    def test_universal_bad_ranges_architectures_and_mixed_archives_are_refused(self):
        old = macho_link_command("@@HOMEBREW_PREFIX@@/opt/fixture/lib/libfixture.dylib")
        intel = macho_fixture([old], cpu=0x01000007, subtype=3)
        raw = fat_fixture([(0x01000007, 3, intel), (0x0100000C, 0, macho_fixture([old]))])
        overlap = bytearray(raw)
        struct.pack_into(">I", overlap, 36, struct.unpack_from(">I", raw, 16)[0])
        outside = bytearray(raw)
        struct.pack_into(">I", outside, 20, len(raw))
        wrong_cpu = bytearray(raw)
        struct.pack_into(">I", wrong_cpu, 8, 0x0100000C)
        mixed = fat_fixture([(0x01000007, 3, intel), (0x0100000C, 0, b"!<arch>\narchive")])
        for malformed in (raw[:30], bytes(overlap), bytes(outside), bytes(wrong_cpu), mixed):
            with self.subTest(sha=digest(malformed)), self.assertRaises(provisioner.ProvisionError):
                provisioner.relocate_macho_bytes(malformed)

    def test_universal_signing_keeps_per_slice_pages_and_never_forces_one_page_size(self):
        old = macho_link_command("@@HOMEBREW_PREFIX@@/opt/fixture/lib/libfixture.dylib")
        parts = [(0x01000007, 3, signed_macho_fixture([old], 12, 0x01000007, 3)),
                 (0x01000007, 8, macho_fixture([old], cpu=0x01000007, subtype=8)),
                 (0x0100000C, 0, signed_macho_fixture([old], 14, 0x0100000C))]
        raw = fat_fixture(parts)
        staged = self.out / "universal-signed-stage"
        staged.mkdir()
        path = staged / "runtime.dylib"
        path.write_bytes(raw)
        sign = mock.Mock(return_value=types.SimpleNamespace(returncode=0))
        report = {}
        provisioner.normalize_staged_macho(staged, normalization_policy(), report, command_runner=sign)
        item = report["files"][0]
        self.assertEqual([part["page_size"] for part in item["source_slice_signing_page_sizes"]], [4096, None, 16384])
        self.assertNotIn("--pagesize", sign.call_args.args[0])
        self.assertEqual(len(item["normalized_layout"]["slices"]), 3)
        changed = [(cpu, subtype, provisioner.relocate_macho_bytes(content)[0]) for cpu, subtype, content in parts]
        changed[-1] = (0x0100000C, 0, provisioner.relocate_macho_bytes(signed_macho_fixture([old], 12, 0x0100000C))[0])
        path.write_bytes(raw)
        def wrong_pages(command, **kwargs):
            Path(command[-1]).write_bytes(fat_fixture(changed))
            return types.SimpleNamespace(returncode=0)
        with self.assertRaises(provisioner.ProvisionError) as found:
            provisioner.normalize_staged_macho(staged, normalization_policy(), {}, command_runner=wrong_pages)
        self.assertEqual(found.exception.code, "macho_signing_page_size_mismatch")

    def test_installed_signing_size_uses_exact_frozen_binding_and_keeps_raw_size(self):
        old = macho_link_command("@@HOMEBREW_PREFIX@@/opt/fixture/lib/libfixture.dylib")
        directory = bytearray(40)
        struct.pack_into(">II", directory, 0, 0xFADE0C02, 40)
        directory[39] = 14
        signature = struct.pack(">IIIII", 0xFADE0CC0, 60, 1, 0, 20) + directory
        signature_offset = 32 + len(old) + 16
        command = struct.pack("<IIII", 0x1D, 16, signature_offset, len(signature))
        raw_binary = macho_fixture([old, command], signature)
        unsigned, _ = provisioner.relocate_macho_bytes(raw_binary)
        raw_archive = archive_bytes("node/1.0", {"bin/node": raw_binary})
        node = entry("node", "1.0", raw_archive)
        pin = {"label": "node/binary", "path": "/opt/homebrew/Cellar/node/1.0/bin/node",
               "sha256": digest(unsigned)}
        binding = {"path": pin["path"], "sha256": pin["sha256"], "page_size": 4096}
        policy = {**normalization_policy(), "installed_signing_page_sizes": [binding]}
        def download(candidate, destination):
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(raw_archive)
            return {"archive_sha256": digest(raw_archive)}
        sign = mock.Mock(return_value=types.SimpleNamespace(returncode=0, stdout="", stderr=""))
        record = {"attempts": []}
        provisioner.stage_package(node, self.out, [pin], record, downloader=download,
                                  normalization=policy, macho_runner=sign)
        item = record["attempts"][0]["normalization"]["files"][0]
        self.assertEqual(item["source_signing_page_size"], 16384)
        self.assertEqual(item["selected_signing_page_size"], 4096)
        self.assertEqual(item["installed_signing_pin"], binding)
        self.assertEqual(item["signing_page_size_source"], "frozen_installed_file")
        self.assertEqual(sign.call_args.args[0][-3:-1], ["--pagesize", "4096"])
        self.assertEqual(record["attempts"][0]["hash_checks"][0]["actual_sha256"], pin["sha256"])

    def test_installed_signing_size_refuses_an_unbound_path_hash_or_size(self):
        pin = {"path": "/opt/homebrew/Cellar/node/1.0/bin/node", "sha256": "a" * 64}
        valid = {**pin, "page_size": 4096}
        policy = {"installed_signing_page_sizes": [valid]}
        self.assertEqual(provisioner.installed_signing_targets(self.node, [pin], policy), {"bin/node": valid})
        for change in ({"sha256": "b" * 64}, {"path": pin["path"] + "-other"}, {"page_size": 8192},
                       {"cms_reserve": 1}):
            invalid = {"installed_signing_page_sizes": [{**valid, **change}]}
            with self.subTest(change=change), self.assertRaises(provisioner.ProvisionError) as found:
                provisioner.installed_signing_targets(self.node, [pin], invalid)
            self.assertEqual(found.exception.code, "manifest_invalid")

    def test_manifest_signing_sizes_match_the_exact_frozen_contract_inventory(self):
        manifest = json.loads((HERE / "macos_bottles.json").read_text())
        contracts = {path: json.loads((provisioner.REPO / path).read_text()) for path in provisioner.CONTRACTS}
        pins = provisioner.pin_specs(contracts)
        policy = manifest["macho_normalization"]
        assigned = sum(len(provisioner.installed_signing_targets(package, pins, policy))
                       for package in manifest["bottles"])
        self.assertEqual(assigned, len(policy["installed_signing_page_sizes"]))
        self.assertEqual(assigned, 32)

    def test_compact_signing_uses_only_the_bound_apple_api_recipe(self):
        staged = self.out / "compact-macho-stage"
        (staged / "bin").mkdir(parents=True)
        path = staged / "bin/node"
        raw = macho_fixture([macho_link_command("@@HOMEBREW_PREFIX@@/opt/fixture/lib/libfixture.dylib")])
        path.write_bytes(raw)
        unsigned, _ = provisioner.relocate_macho_bytes(raw)
        pin = {"path": "/opt/homebrew/Cellar/node/1.0/bin/node", "sha256": digest(unsigned)}
        binding = {**pin, "page_size": 4096, "cms_reserve": 0}
        policy = {**normalization_policy(), "installed_signing_page_sizes": [binding]}
        targets = provisioner.installed_signing_targets(self.node, [pin], policy)
        bridge = mock.Mock(return_value=0)
        cli = mock.Mock()
        report = {}
        provisioner.normalize_staged_macho(staged, policy, report, command_runner=cli,
                                           installed_signing=targets, security_runner=bridge)
        bridge.assert_called_once_with(path, 4096)
        cli.assert_not_called()
        item = report["files"][0]
        self.assertEqual(item["installed_signing_pin"], binding)
        self.assertEqual(item["signing_backend"], "apple_security_framework")
        recipe = item["security_signing"]
        self.assertEqual((recipe["cms_reserve"], recipe["page_size"], recipe["preserve_metadata"]), (0, 4096, 278))
        self.assertEqual((recipe["create_flags"], recipe["add_signature_flags"], recipe["osstatus"]), (128, 0, 0))
        self.assertEqual(item["normalized_sha256"], digest(unsigned))

    def test_compact_signing_failure_is_fatal_and_real_bridge_is_guarded(self):
        staged = self.out / "failed-compact-macho-stage"
        staged.mkdir()
        path = staged / "tool"
        raw = macho_fixture([macho_link_command("@@HOMEBREW_PREFIX@@/opt/fixture/lib/libfixture.dylib")])
        path.write_bytes(raw)
        binding = {"path": "/opt/homebrew/Cellar/fixture/1.0/tool", "sha256": "0" * 64,
                   "page_size": 4096, "cms_reserve": 0}
        report = {}
        with self.assertRaises(provisioner.ProvisionError) as found:
            provisioner.normalize_staged_macho(staged, normalization_policy(), report,
                                               installed_signing={"tool": binding}, security_runner=lambda *args: -50)
        self.assertEqual(found.exception.code, "macho_security_signing_failed")
        self.assertEqual(report["files"][0]["status"], "failed")
        with mock.patch.dict(provisioner.os.environ, {}, clear=True), mock.patch("ctypes.CDLL") as load:
            with self.assertRaises(provisioner.ProvisionError) as guarded:
                provisioner.sign_macho_with_security(path, 4096)
        self.assertEqual(guarded.exception.code, "runner_refused")
        load.assert_not_called()

    def test_macho_without_placeholder_linkage_is_byte_identical(self):
        raw = macho_fixture([macho_link_command("@rpath/libfixture.dylib")],
                            body=b"@@HOMEBREW_PREFIX@@ data is not a linkage command")
        self.assertEqual(provisioner.relocate_macho_bytes(raw), (raw, []))

    def test_macho_normalization_keeps_raw_unsigned_and_signed_hashes_separate(self):
        staged = self.out / "macho-stage"
        (staged / "bin").mkdir(parents=True)
        path = staged / "bin/tool"
        old = macho_link_command("@@HOMEBREW_PREFIX@@/opt/llvm/lib/libLLVM.dylib")
        new = macho_link_command("/opt/homebrew/opt/llvm/lib/libLLVM.dylib")
        raw = macho_fixture([old])
        unsigned = macho_fixture([new], padding=len(old) - len(new))
        path.write_bytes(raw)
        calls = []
        def sign(command, **kwargs):
            calls.append(command)
            target = Path(command[-1])
            target.write_bytes(target.read_bytes() + b"fixture ad-hoc signature")
            return types.SimpleNamespace(returncode=0, stdout="", stderr="")
        report = {}
        provisioner.normalize_staged_macho(staged, normalization_policy(), report, command_runner=sign)
        item = report["files"][0]
        self.assertEqual(item["raw_sha256"], digest(raw))
        self.assertEqual(item["unsigned_relocated_sha256"], digest(unsigned))
        self.assertEqual(item["normalized_sha256"], digest(unsigned + b"fixture ad-hoc signature"))
        self.assertEqual(calls[0][:-1], ["/usr/bin/codesign", "--sign", "-", "--force",
                                       "--preserve-metadata=entitlements,requirements,flags,runtime"])
        self.assertEqual(report["status"], "passed")

    def test_real_macho_signing_is_guarded_before_any_file_mutation(self):
        staged = self.out / "guarded-macho-stage"
        staged.mkdir()
        path = staged / "tool"
        raw = macho_fixture([macho_link_command("@@HOMEBREW_PREFIX@@/opt/llvm/lib/libLLVM.dylib")])
        path.write_bytes(raw)
        with mock.patch.dict(provisioner.os.environ, {}, clear=True), \
                mock.patch.object(provisioner.subprocess, "run") as run:
            with self.assertRaises(provisioner.ProvisionError) as found:
                provisioner.normalize_staged_macho(staged, normalization_policy(), {})
        self.assertEqual(found.exception.code, "runner_refused")
        self.assertEqual(path.read_bytes(), raw)
        run.assert_not_called()

    def test_normalized_file_still_must_match_the_unchanged_frozen_pin(self):
        old = macho_link_command("@@HOMEBREW_PREFIX@@/opt/llvm/lib/libLLVM.dylib")
        raw_binary = macho_fixture([old])
        raw_archive = archive_bytes("node/1.0", {"bin/node": raw_binary})
        node = entry("node", "1.0", raw_archive)
        node["raw_file_sha256"] = {"bin/node": digest(raw_binary)}
        def download(candidate, destination):
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(raw_archive)
            return {"archive_sha256": digest(raw_archive)}
        pin = {"label": "node/binary", "path": "/opt/homebrew/Cellar/node/1.0/bin/node", "sha256": "0" * 64}
        record = {"attempts": []}
        sign = mock.Mock(return_value=types.SimpleNamespace(returncode=0, stdout="", stderr=""))
        with self.assertRaises(provisioner.ProvisionError) as found:
            provisioner.stage_package(node, self.out, [pin], record, downloader=download,
                                      normalization=normalization_policy(), macho_runner=sign)
        self.assertEqual(found.exception.code, "frozen_bottle_bytes_mismatch")
        attempt = record["attempts"][0]
        self.assertEqual(attempt["raw_hash_checks"][0]["actual_sha256"], digest(raw_binary))
        self.assertEqual(attempt["hash_checks"][0]["expected_sha256"], "0" * 64)
        self.assertEqual(attempt["hash_checks"][0]["status"], "failed")
        self.assertNotEqual(attempt["hash_checks"][0]["actual_sha256"], digest(raw_binary))
        self.assertEqual(sign.call_count, 1)

    def test_wrong_recorded_raw_file_hash_blocks_normalization(self):
        node = json.loads(json.dumps(self.node))
        node["raw_file_sha256"] = {"bin/node": "0" * 64}
        record = {"attempts": []}
        with mock.patch.object(provisioner, "normalize_staged_macho") as normalize:
            with self.assertRaises(provisioner.ProvisionError) as found:
                provisioner.stage_package(node, self.out, [], record, downloader=self.downloader,
                                          normalization=normalization_policy())
        self.assertEqual(found.exception.code, "raw_bottle_file_mismatch")
        normalize.assert_not_called()

    def test_pin_inventory_includes_all_six_rust_tools_and_both_library_paths(self):
        document = json.loads(json.dumps(self.contracts["fixture"]))
        names = ("rustc", "cargo", "rustfmt", "cargo-fmt", "cargo-clippy", "clippy-driver")
        document["rust"] = {"bin_dir": "/opt/homebrew/Cellar/rust/1.96.1/bin",
                            **{name: {"sha256": digest(name.encode())} for name in names}}
        document["node"]["homebrew_libraries"] = {
            "/opt/homebrew/opt/fixture/lib/fixture.dylib": {
                "formula": "fixture", "resolved": "/opt/homebrew/Cellar/fixture/1.0/lib/fixture.dylib",
                "sha256": digest(b"library"),
            },
        }
        document["postgresql"] = {"bin_dir": "/opt/homebrew/opt/postgresql@17/bin",
                                  **{name: {"sha256": digest(name.encode())}
                                     for name in ("postgres", "initdb", "pg_ctl")}}
        pins = provisioner.pin_specs({"fixture": document})
        paths = {pin["path"] for pin in pins}
        self.assertTrue({f"/opt/homebrew/Cellar/rust/1.96.1/bin/{name}" for name in names} <= paths)
        self.assertIn("/opt/homebrew/opt/fixture/lib/fixture.dylib", paths)
        self.assertIn("/opt/homebrew/Cellar/fixture/1.0/lib/fixture.dylib", paths)
        self.assertTrue({f"/opt/homebrew/opt/postgresql@17/bin/{name}"
                         for name in ("postgres", "initdb", "pg_ctl")} <= paths)

    def test_fake_provision_preserves_pins_links_npm_bytes_and_reports_suites_unrun(self):
        report = provisioner.new_report()
        provisioner.provision(self.manifest, self.contracts, self.out, report,
                              prefix=self.prefix, downloader=self.downloader, expected_count=None)
        self.assertEqual(report["status"], "passed")
        self.assertEqual(report["product_suites"], "not_run")
        self.assertEqual(report["packages"][0]["selected_archive_sha256"], self.node["archive_sha256"])
        self.assertTrue(all(check["status"] == "passed" for check in report["hash_checks"]))
        keg = self.prefix / "Cellar/node/1.0"
        self.assertEqual((keg / "bin/node").read_bytes(), self.node_bytes)
        self.assertEqual((self.prefix / "opt/node").resolve(), keg.resolve())
        npm = self.prefix / "lib/node_modules/npm"
        self.assertEqual((npm / "bin/npm-cli.js").read_bytes(), self.npm_bytes)
        self.assertEqual((npm / "npmrc").read_bytes(), b"prefix = /opt/homebrew\n")
        self.assertTrue((npm / "npm-link").is_symlink())
        self.assertEqual(report["cleanup"]["status"], "passed")
        self.assertFalse((self.out / "archives").exists())
        self.assertFalse((self.out / "stage").exists())

    def test_success_cleanup_keeps_reports_and_never_follows_tool_symlinks(self):
        installed = self.prefix / "Cellar/fixture/1.0"
        installed.mkdir(parents=True)
        (installed / "tool").write_bytes(b"installed tool stays")
        (self.out / "report.json").write_bytes(b"provenance stays")
        (self.out / "archives").mkdir()
        (self.out / "archives/bottle.tar.gz").write_bytes(b"archive bytes")
        (self.out / "stage").mkdir()
        (self.out / "stage/tool-copy").write_bytes(b"duplicate bytes")
        (self.out / "stage/installed-link").symlink_to(installed, target_is_directory=True)
        result = provisioner.cleanup_provision_staging(self.out)
        self.assertEqual(result["file_bytes_removed"], len(b"archive bytesduplicate bytes"))
        self.assertEqual(result["removed_directories"], ["archives", "stage"])
        self.assertEqual((installed / "tool").read_bytes(), b"installed tool stays")
        self.assertEqual((self.out / "report.json").read_bytes(), b"provenance stays")

    def test_openssl_candidates_are_selected_by_both_frozen_library_hashes(self):
        bad = archive_bytes("openssl@3/3.6.3", {"lib/libcrypto.3.dylib": b"wrong", "lib/libssl.3.dylib": b"ssl"})
        good = archive_bytes("openssl@3/3.6.3", {"lib/libcrypto.3.dylib": b"crypto", "lib/libssl.3.dylib": b"ssl"})
        primary = entry("openssl@3", "3.6.3", bad, rebuild=1)
        alternative = entry("openssl@3", "3.6.3", good)
        primary["alternatives"] = [{key: alternative[key] for key in
                                    ("url", "archive_sha256", "archive_size_bytes", "target", "bottle_rebuild")}]
        raw = {primary["archive_sha256"]: bad, alternative["archive_sha256"]: good}
        def download(candidate, destination):
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(raw[candidate["archive_sha256"]])
            return {"archive_sha256": candidate["archive_sha256"]}
        pins = [{"label": name, "path": f"/opt/homebrew/Cellar/openssl@3/3.6.3/lib/{name}.3.dylib",
                 "sha256": digest(value)} for name, value in (("libcrypto", b"crypto"), ("libssl", b"ssl"))]
        record = {"attempts": []}
        selected, staged = provisioner.stage_package(primary, self.out, pins, record, downloader=download)
        self.assertEqual(selected["archive_sha256"], alternative["archive_sha256"])
        self.assertEqual((staged / "lib/libcrypto.3.dylib").read_bytes(), b"crypto")
        self.assertEqual([attempt["status"] for attempt in record["attempts"]], ["frozen_hash_mismatch", "passed"])
        self.assertEqual(record["selected_bottle_rebuild"], 0)
        self.assertEqual(len(record["attempts"][1]["hash_checks"]), 2)

    def test_frozen_stage_mismatch_fails_before_any_install(self):
        self.contracts["fixture"]["node"]["sha256"] = "0" * 64
        report = provisioner.new_report()
        with mock.patch.object(provisioner, "install_closure") as install:
            with self.assertRaises(provisioner.ProvisionError) as found:
                provisioner.provision(self.manifest, self.contracts, self.out, report,
                                      prefix=self.prefix, downloader=self.downloader, expected_count=None)
        self.assertEqual(found.exception.code, "package_staging_failed")
        self.assertEqual(report["packages"][0]["error"]["code"], "frozen_bottle_bytes_mismatch")
        install.assert_not_called()
        self.assertEqual(report["status"], "failed")
        self.assertEqual(report["product_suites"], "not_run")
        self.assertEqual(report["cleanup"]["status"], "not_run")
        self.assertTrue((self.out / "archives").is_dir())
        self.assertTrue((self.out / "stage").is_dir())

    def test_all_bottles_are_staged_and_errors_retained_before_install_is_refused(self):
        second = entry("fixture-two", "1.0", b"not downloaded")
        third = entry("fixture-three", "1.0", b"not downloaded")
        manifest = {**self.manifest, "bottle_count": 3, "bottles": [self.node, second, third]}
        errors = [provisioner.ProvisionError("archive_link_refused", "Fixture link refused.",
                                            {"member": "node/1.0/lib/link", "link_target": "/outside"}),
                  provisioner.ProvisionError("frozen_bottle_bytes_mismatch", "Fixture bytes differ.")]
        report = provisioner.new_report()
        with mock.patch.object(provisioner, "stage_package", side_effect=[errors[0], errors[1], (third, self.out)]) as stage, \
                mock.patch.object(provisioner, "install_closure") as install, \
                mock.patch.object(provisioner, "verify_python") as python:
            with self.assertRaises(provisioner.ProvisionError) as found:
                provisioner.provision(manifest, self.contracts, self.out, report,
                                      prefix=self.prefix, expected_count=None)
        self.assertEqual(found.exception.code, "package_staging_failed")
        self.assertEqual(stage.call_count, 3)
        self.assertEqual([package["status"] for package in report["packages"]], ["failed", "failed", "passed"])
        self.assertEqual(report["packages"][0]["error"]["details"]["link_target"], "/outside")
        self.assertEqual([item["error"]["code"] for item in found.exception.details["failed_packages"]],
                         ["archive_link_refused", "frozen_bottle_bytes_mismatch"])
        install.assert_not_called()
        python.assert_not_called()
        self.assertEqual(report["stage"], "download_and_stage")
        self.assertEqual(report["product_suites"], "not_run")

    def test_installed_hash_mismatch_is_fatal_and_does_not_claim_acceptance(self):
        report = provisioner.new_report()
        failed = [{"label": "fixture", "status": "failed", "expected_sha256": "0" * 64, "actual_sha256": "1" * 64}]
        with mock.patch.object(provisioner, "verify_installed", return_value=failed):
            with self.assertRaises(provisioner.ProvisionError) as found:
                provisioner.provision(self.manifest, self.contracts, self.out, report,
                                      prefix=self.prefix, downloader=self.downloader, expected_count=None)
        self.assertEqual(found.exception.code, "installed_frozen_hash_mismatch")
        self.assertEqual(report["status"], "failed")
        self.assertEqual(report["product_suites"], "not_run")
        self.assertEqual(report["hash_checks"], failed)

    def test_postgresql_data_and_library_links_use_the_temp_prefix(self):
        staged = self.out / "pg-keg"
        for relative in ("share/postgresql", "lib/postgresql"):
            (staged / relative).mkdir(parents=True)
            (staged / relative / "fixture").write_bytes(b"data")
        pg = entry("postgresql@17", "17.7", b"unused", revision=1)
        provisioner.install_closure([(pg, staged)], self.prefix)
        keg = self.prefix / "Cellar/postgresql@17/17.7_1"
        self.assertEqual((self.prefix / "share/postgresql@17").resolve(), (keg / "share/postgresql").resolve())
        self.assertEqual((self.prefix / "lib/postgresql@17").resolve(), (keg / "lib/postgresql").resolve())

    def test_python_framework_link_keeps_other_framework_versions_unchanged(self):
        staged = self.out / "python-keg"
        framework = staged / "Frameworks/Python.framework/Versions/3.12"
        framework.mkdir(parents=True)
        (framework / "Python").write_bytes(b"fixture framework")
        (staged / "bin").mkdir()
        (staged / "bin/python3.12").write_bytes(b"fixture interpreter")
        versions = self.prefix / "Frameworks/Python.framework/Versions"
        versions.mkdir(parents=True)
        (versions / "Current").symlink_to("3.13")
        python = entry("python@3.12", "3.12.11", b"unused", revision=1)
        provisioner.install_closure([(python, staged)], self.prefix)
        installed = self.prefix / "Cellar/python@3.12/3.12.11_1/Frameworks/Python.framework/Versions/3.12"
        self.assertEqual((versions / "3.12").resolve(), installed.resolve())
        self.assertEqual(provisioner.os.readlink(versions / "Current"), "3.13")
        self.assertEqual((self.prefix / "opt/python@3.12/bin/python3.12").read_bytes(), b"fixture interpreter")

    def test_python_version_and_bundled_stdlib_checks_use_only_a_fake_command_runner(self):
        binary = self.prefix / "opt/python@3.12/bin/python3.12"
        binary.parent.mkdir(parents=True)
        binary.write_bytes(b"never executed")
        commands = []
        def run(command, **kwargs):
            commands.append(command)
            output = "Python 3.12.11\n" if command[-1] == "--version" else "python-runtime-ok\n"
            return types.SimpleNamespace(returncode=0, stdout=output, stderr="")
        spec = {"binary": provisioner.PYTHON_BINARY, "version": "3.12.11"}
        contracts = {"fixture": {"python": {"version": "3.12.11"}}}
        report = provisioner.verify_python(spec, contracts, self.prefix, command_runner=run)
        self.assertEqual(report["status"], "passed")
        self.assertEqual(report["binary_sha256"], None)
        self.assertEqual(report["venv_creation"], "not_run")
        self.assertEqual(commands[0], [str(binary), "--version"])
        self.assertEqual(commands[1][:3], [str(binary), "-I", "-c"])
        self.assertIn("ensurepip", commands[1][3])
        self.assertTrue(all(check["status"] == "passed" for check in report["checks"]))

    def test_python_wrong_version_fails_before_standard_library_probe(self):
        binary = self.prefix / "opt/python@3.12/bin/python3.12"
        binary.parent.mkdir(parents=True)
        binary.write_bytes(b"never executed")
        run = mock.Mock(return_value=types.SimpleNamespace(returncode=0, stdout="Python 3.12.12\n", stderr=""))
        spec = {"binary": provisioner.PYTHON_BINARY, "version": "3.12.11"}
        contracts = {"fixture": {"python": {"version": "3.12.11"}}}
        report = provisioner.verify_python(spec, contracts, self.prefix, command_runner=run)
        self.assertEqual(report["status"], "failed")
        self.assertEqual(report["checks"][0]["actual_version"], "Python 3.12.12")
        self.assertEqual(run.call_count, 1)

    def test_python_probe_failure_does_not_retain_command_error_output(self):
        binary = self.prefix / "opt/python@3.12/bin/python3.12"
        binary.parent.mkdir(parents=True)
        binary.write_bytes(b"never executed")
        run = mock.Mock(side_effect=[
            types.SimpleNamespace(returncode=0, stdout="Python 3.12.11\n", stderr=""),
            types.SimpleNamespace(returncode=1, stdout="", stderr="https://signed.invalid/?token=private-fixture"),
        ])
        spec = {"binary": provisioner.PYTHON_BINARY, "version": "3.12.11"}
        contracts = {"fixture": {"python": {"version": "3.12.11"}}}
        report = provisioner.verify_python(spec, contracts, self.prefix, command_runner=run)
        self.assertEqual(report["status"], "failed")
        self.assertNotIn("signed.invalid", json.dumps(report))
        self.assertNotIn("private-fixture", json.dumps(report))

    def test_complete_fake_provision_keeps_suites_unrun_when_python_version_fails(self):
        python_raw = archive_bytes("python@3.12/3.12.11_1", {
            "bin/python3.12": b"never executed",
            "Frameworks/Python.framework/Versions/3.12/Python": b"fixture framework",
        })
        python = entry("python@3.12", "3.12.11", python_raw, revision=1)
        document = json.loads(json.dumps(self.manifest))
        document["bottles"].append(python)
        document["bottle_count"] = 2
        document["python"] = {"binary": provisioner.PYTHON_BINARY, "version": "3.12.11"}
        contracts = json.loads(json.dumps(self.contracts))
        contracts["fixture"]["python"] = {"version": "3.12.11"}
        archives = {self.node["archive_sha256"]: self.node_archive, python["archive_sha256"]: python_raw}
        def download(candidate, destination):
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(archives[candidate["archive_sha256"]])
            return {"archive_sha256": candidate["archive_sha256"]}
        run = mock.Mock(return_value=types.SimpleNamespace(returncode=0, stdout="Python 3.12.12\n", stderr=""))
        report = provisioner.new_report()
        with self.assertRaises(provisioner.ProvisionError) as found:
            provisioner.provision(document, contracts, self.out, report, prefix=self.prefix,
                                  downloader=download, expected_count=None, python_runner=run)
        self.assertEqual(found.exception.code, "python_runtime_mismatch")
        self.assertEqual(report["status"], "failed")
        self.assertEqual(report["product_suites"], "not_run")
        self.assertEqual(report["python"]["status"], "failed")

    def test_main_writes_failed_safe_report_for_a_frozen_hash_failure(self):
        manifest = self.out / "manifest.json"
        manifest.write_text(json.dumps(self.manifest))
        output = self.root / "main-logs"
        stream = io.StringIO()
        with mock.patch.object(provisioner, "require_github_runner"), \
                mock.patch.object(provisioner, "load_contracts", return_value={}), \
                mock.patch.object(provisioner, "CONTRACTS", ()), \
                mock.patch.object(provisioner, "provision", side_effect=provisioner.ProvisionError(
                    "installed_frozen_hash_mismatch", "Installed files differ from the frozen toolchain.")), \
                contextlib.redirect_stdout(stream):
            code = provisioner.main(["--out", str(output), "--manifest", str(manifest)])
        report = json.loads((output / "report.json").read_bytes())
        self.assertEqual(code, 1)
        self.assertEqual(report["status"], "failed")
        self.assertEqual(report["product_suites"], "not_run")
        self.assertEqual(report["error"]["code"], "installed_frozen_hash_mismatch")
        self.assertEqual(json.loads(stream.getvalue())["status"], "failed")

    def test_manifest_rejects_an_unofficial_blob_or_changed_alternative_identity(self):
        bad = json.loads(json.dumps(self.manifest))
        bad["bottles"][0]["url"] = "https://unofficial.invalid/bottle"
        with self.assertRaises(provisioner.ProvisionError):
            provisioner.validate_manifest(bad, expected_count=None)
        bad = json.loads(json.dumps(self.manifest))
        bad["bottles"][0]["alternatives"] = [{"formula": "other"}]
        with self.assertRaises(provisioner.ProvisionError):
            provisioner.validate_manifest(bad, expected_count=None)


if __name__ == "__main__":
    unittest.main()

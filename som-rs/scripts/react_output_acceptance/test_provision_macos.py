"""GitHub-only provisioner fixtures. Run on GitHub, never on the local host."""
from __future__ import annotations

import contextlib
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import tarfile
import tempfile
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

    def test_tree_hash_includes_literal_symlink_targets_and_both_counts(self):
        root = self.out / "tree"
        root.mkdir()
        (root / "empty").mkdir()
        (root / "a").write_bytes(b"content")
        (root / "b").symlink_to("a")
        expected = digest(f"a\0{digest(b'content')}\nb\0symlink:a\n".encode())
        self.assertEqual(provisioner.tree_sha256(root), (expected, 1, 1))

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
        self.assertEqual(found.exception.code, "frozen_bottle_bytes_mismatch")
        install.assert_not_called()
        self.assertEqual(report["status"], "failed")
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

"""Fake-process tests. No historical input, network, Node, or Git is executed."""
from __future__ import annotations

import base64
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock

SOURCE = Path(__file__).with_name("fetch_hosted_inputs.py")
SPEC = importlib.util.spec_from_file_location("m6_fetch_hosted_inputs", SOURCE)
helper = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(helper)


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


class Fixture:
    def __init__(self, root):
        self.root = root
        self.repo = root / "repo"
        self.repo.mkdir()
        carrier = self.repo / "som-rs/scripts/react_output_acceptance/sealed_inputs.mjs"
        carrier.parent.mkdir(parents=True)
        carrier.write_text("fake carrier: tests replace subprocess.run\n")
        self.config_path = root / "config.json"
        self.out = root / "output"
        self.commit = "1" * 40
        self.blobs = {}
        self.packages = []
        self.manifests = []
        self.data = {}
        self.keys = {}
        self.calls = []
        self.node_mode = "ok"
        self.add_package([(".local/fake/input.bin", b"\x00\xff\x80\n", 0o644)])
        self.add_package([(".local/fake/tool.sh", b"#!/bin/sh\nexit 99\n", 0o755)])
        self.refresh()

    def add_package(self, files):
        index = len(self.packages)
        archive_name = f"package-{index:03d}.sealed"
        manifest_name = f"package-{index:03d}.manifest.json"
        archive = f"fake opaque package {index}".encode()
        cipher_sha = helper.digest(archive)
        self.blobs[archive_name] = archive
        records = []
        for name, data, mode in files:
            self.data[name] = data
            records.append({"path": name, "sha256": helper.digest(data), "bytes": len(data), "mode": mode})
        self.manifests.append({
            "version": helper.MANIFEST_VERSION, "cipher_sha256": cipher_sha,
            "uncompressed_bytes": 100 + sum(len(data) * 2 for _, data, _ in files),
            "total_files": len(files), "files": records,
        })
        self.packages.append({"archive": archive_name, "manifest": manifest_name, "manifest_sha256": ""})
        self.keys[cipher_sha] = base64.b64encode(bytes([index + 1]) * 32).decode()

    def refresh(self):
        for package, manifest in zip(self.packages, self.manifests):
            raw = encoded(manifest)
            self.blobs[package["manifest"]] = raw
            package["manifest_sha256"] = helper.digest(raw)
        self.blobs["inputs-index.json"] = encoded({"version": helper.INDEX_VERSION, "packages": self.packages})
        self.config = {"version": helper.CONFIG_VERSION, "commit": self.commit,
                       "index_sha256": helper.digest(self.blobs["inputs-index.json"])}
        self.config_path.write_bytes(encoded(self.config))

    def environment(self):
        return {"M6_INPUTS_KEYRING": json.dumps(self.keys), "M6_INPUTS_KEY": "remove-this-parent-value", "PATH": "/fake-path"}

    def runner(self, args, **kwargs):
        self.calls.append((args, kwargs))
        assert kwargs.get("shell") is False
        assert kwargs.get("stdout") == subprocess.PIPE
        assert kwargs.get("stderr") == subprocess.PIPE
        assert "text" not in kwargs and "encoding" not in kwargs
        if args[0] == "git":
            assert args[:5] == ["git", "--no-pager", "-C", str(self.repo), "show"]
            commit, name = args[5].split(":", 1)
            assert commit == self.commit
            if name not in self.blobs:
                return subprocess.CompletedProcess(args, 1, b"", b"fake failure with sensitive output")
            return subprocess.CompletedProcess(args, 0, self.blobs[name], b"")
        assert args[0] == "fake-node" and args[2] == "restore"
        environment = kwargs["env"]
        assert "M6_INPUTS_KEYRING" not in environment
        assert environment["M6_INPUTS_KEY"] in self.keys.values()
        assert all(key not in " ".join(args) for key in self.keys.values())
        if self.node_mode == "fail":
            return subprocess.CompletedProcess(args, 1, b"fake-private-stdout", b"fake-private-stderr")
        manifest = json.loads(Path(args[args.index("--manifest") + 1]).read_bytes())
        if environment["M6_INPUTS_KEY"] != self.keys[manifest["cipher_sha256"]]:
            return subprocess.CompletedProcess(args, 1, b"", b"fake authentication failure")
        out = Path(args[args.index("--out") + 1])
        assert not out.exists()
        out.mkdir()
        for index, record in enumerate(manifest["files"]):
            if self.node_mode == "missing" and index == 0:
                continue
            destination = out.joinpath(*record["path"].split("/"))
            destination.parent.mkdir(parents=True, exist_ok=True)
            if self.node_mode == "symlink" and index == 0:
                target = self.root / "fake-symlink-target"
                target.write_bytes(self.data[record["path"]])
                destination.symlink_to(target)
            else:
                data = b"wrong fake bytes" if self.node_mode == "wrong-bytes" else self.data[record["path"]]
                destination.write_bytes(data)
                destination.chmod(0o600 if self.node_mode == "wrong-mode" else record["mode"])
        if self.node_mode == "extra":
            (out / "covert-extra.txt").write_text("fake extra file")
        if self.node_mode == "extra-dir":
            (out / "covert-extra-dir").mkdir()
        return subprocess.CompletedProcess(args, 0, b"", b"")

    def run(self, **kwargs):
        options = {"repo_root": self.repo, "config_file": self.config_path,
                   "out": self.out, "node": "fake-node", "environ": self.environment()}
        options.update(kwargs)
        with mock.patch.object(helper.subprocess, "run", side_effect=self.runner):
            return helper.provision(**options)

    def node_calls(self):
        return [args for args, _ in self.calls if args[0] == "fake-node"]

    def archive_calls(self):
        return [args for args, _ in self.calls if args[0] == "git" and args[-1].endswith(".sealed")]


class FetchHostedInputsTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="som-m6-fetch-test-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.fixture = Fixture(self.root)

    def refused(self, **kwargs):
        with self.assertRaisesRegex(helper.ProvisionError, f"^{helper.REFUSAL}$"):
            self.fixture.run(**kwargs)
        self.assertFalse(self.fixture.out.exists())
        self.assertEqual(list(self.root.glob("som-m6-input-stage-*")), [])

    def test_success_keeps_binary_bytes_modes_and_safe_receipt(self):
        receipt = self.fixture.run()
        self.assertEqual(receipt, {
            "version": helper.PROVISION_VERSION, "stage": "input_provision", "status": "passed",
            "file_count": 2, "bytes": sum(map(len, self.fixture.data.values())),
            "index_sha256": self.fixture.config["index_sha256"], "product_suites": "not_run",
        })
        self.assertEqual(json.loads((self.fixture.out / "provision.json").read_text()), receipt)
        for manifest in self.fixture.manifests:
            for record in manifest["files"]:
                destination = self.fixture.out / record["path"]
                self.assertEqual(destination.read_bytes(), self.fixture.data[record["path"]])
                self.assertEqual(destination.stat().st_mode & 0o777, record["mode"])
        self.assertEqual(len(self.fixture.node_calls()), 2)
        self.assertEqual(list(self.root.glob("som-m6-input-stage-*")), [])
        self.assertNotIn(str(self.root), json.dumps(receipt))
        for key in self.fixture.keys.values():
            self.assertNotIn(key, json.dumps(receipt))

    def test_existing_empty_output_is_allowed(self):
        self.fixture.out.mkdir()
        self.fixture.run()
        self.assertTrue((self.fixture.out / "provision.json").is_file())

    def test_corrupt_index_manifest_and_archive_fail(self):
        for name in ["inputs-index.json", "package-000.manifest.json", "package-000.sealed"]:
            with self.subTest(name=name):
                original = self.fixture.blobs[name]
                self.fixture.blobs[name] = original + b"corruption"
                self.refused()
                self.fixture.blobs[name] = original

    def test_config_and_index_are_strict_and_do_not_accept_shell_text(self):
        for mutation in [
            {"version": "unknown"}, {"commit": "main"}, {"commit": "1" * 40 + ";fake-command"},
            {"index_sha256": "0" * 64}, {"extra": "unwanted"},
        ]:
            with self.subTest(mutation=mutation):
                self.fixture.config_path.write_bytes(encoded({**self.fixture.config, **mutation}))
                self.refused()
        self.fixture.refresh()
        for index in [
            {"version": "unknown", "packages": self.fixture.packages},
            {"version": helper.INDEX_VERSION, "packages": []},
            {"version": helper.INDEX_VERSION, "packages": self.fixture.packages, "extra": True},
        ]:
            raw = encoded(index)
            self.fixture.blobs["inputs-index.json"] = raw
            self.fixture.config_path.write_bytes(encoded({**self.fixture.config, "index_sha256": helper.digest(raw)}))
            self.refused()

    def test_duplicate_json_keys_fail_even_when_hash_is_pinned(self):
        raw = b'{"version":"som.m6-input-index.v1","packages":[],"packages":[]}'
        self.fixture.blobs["inputs-index.json"] = raw
        self.fixture.config_path.write_bytes(encoded({**self.fixture.config, "index_sha256": helper.digest(raw)}))
        self.refused()

    def test_keys_must_match_every_package_once_and_be_canonical(self):
        first = next(iter(self.fixture.keys))
        variants = [{}, {**self.fixture.keys, "0" * 64: base64.b64encode(b"k" * 32).decode()},
                    {**self.fixture.keys, first: "bad-key"},
                    {**self.fixture.keys, first: self.fixture.keys[first] + "\n"},
                    {**self.fixture.keys, first: [self.fixture.keys[first]]}]
        for keyring in variants:
            with self.subTest(keyring_type=type(keyring).__name__):
                environment = self.fixture.environment()
                environment["M6_INPUTS_KEYRING"] = json.dumps(keyring)
                self.refused(environ=environment)
        self.assertEqual(self.fixture.archive_calls(), [])
        self.assertEqual(self.fixture.node_calls(), [])

    def test_package_names_and_pairs_are_exact_and_unique(self):
        for change in [
            {"archive": "../package-000.sealed"}, {"archive": "package-00.sealed"},
            {"manifest": "package-001.manifest.json"}, {"manifest": "/absolute.json"},
            {"extra": "field"},
        ]:
            with self.subTest(change=change):
                original = dict(self.fixture.packages[0])
                self.fixture.packages[0].update(change)
                self.fixture.refresh()
                self.refused()
                self.fixture.packages[0] = original
        self.fixture.packages[1] = dict(self.fixture.packages[0])
        # Do not refresh here: refresh would replace the same named manifest.
        raw = encoded({"version": helper.INDEX_VERSION, "packages": self.fixture.packages})
        self.fixture.blobs["inputs-index.json"] = raw
        self.fixture.config_path.write_bytes(encoded({**self.fixture.config, "index_sha256": helper.digest(raw)}))
        self.refused()

    def test_canonical_but_wrong_key_is_rejected_by_restore_without_publishing(self):
        environment = self.fixture.environment()
        keyring = dict(self.fixture.keys)
        first = next(iter(keyring))
        # The fake Node process models the carrier's authentication failure.
        keyring[first] = next(value for name, value in keyring.items() if name != first)
        environment["M6_INPUTS_KEYRING"] = json.dumps(keyring)
        self.refused(environ=environment)
        self.assertEqual(len(self.fixture.node_calls()), 1)

    def test_cross_package_duplicates_and_prefix_collisions_fail(self):
        for names in [(".local/same", ".local/same"), (".local/parent", ".local/parent/child")]:
            with self.subTest(names=names):
                self.fixture.manifests[0]["files"][0]["path"] = names[0]
                self.fixture.manifests[1]["files"][0]["path"] = names[1]
                self.fixture.refresh()
                self.refused()
        self.assertEqual(self.fixture.node_calls(), [])
        self.assertEqual(self.fixture.archive_calls(), [])

    def test_only_canonical_local_paths_are_allowed(self):
        for name in ["../escape", "/absolute", ".local/../escape", ".local/a//b", ".local/a/./b",
                     ".local/a\\b", ".local/a\0b", ".local/a/", "outside/file", ".local", ".local/a\nb"]:
            with self.subTest(name=name):
                self.fixture.manifests[1]["files"][0]["path"] = name
                self.fixture.refresh()
                self.refused()
        self.assertEqual(self.fixture.node_calls(), [])
        self.assertEqual(self.fixture.archive_calls(), [])

    def test_manifest_schema_count_hash_mode_and_carrier_limits_are_checked_first(self):
        original = json.loads(json.dumps(self.fixture.manifests[1]))
        changes = [
            lambda item: item.update(version="unknown"),
            lambda item: item.update(total_files=2),
            lambda item: item.update(uncompressed_bytes=helper.MAX_UNCOMPRESSED + 1),
            lambda item: item.update(extra="field"),
            lambda item: item["files"][0].update(bytes=helper.MAX_UNCOMPRESSED + 1),
            lambda item: item["files"][0].update(bytes=True),
            lambda item: item["files"][0].update(mode=0o777),
            lambda item: item["files"][0].update(sha256="invalid"),
            lambda item: item["files"][0].update(extra="field"),
        ]
        for change in changes:
            item = json.loads(json.dumps(original))
            change(item)
            self.fixture.manifests[1] = item
            self.fixture.refresh()
            self.refused()
        self.assertEqual(self.fixture.node_calls(), [])
        self.assertEqual(self.fixture.archive_calls(), [])

    def test_total_file_count_and_plaintext_size_are_bounded(self):
        original = json.loads(json.dumps(self.fixture.manifests))
        record = self.fixture.manifests[1]["files"][0]
        self.fixture.manifests[1]["files"] = [dict(record, path=f".local/fake/{index}", bytes=0) for index in range(helper.MAX_FILES)]
        self.fixture.manifests[1]["total_files"] = helper.MAX_FILES
        self.fixture.refresh()
        self.refused()
        self.fixture.manifests = original
        while len(self.fixture.packages) < 5:
            self.fixture.add_package([(f".local/large/{len(self.fixture.packages)}", b"fake", 0o644)])
        for manifest in self.fixture.manifests:
            manifest["files"][0]["bytes"] = helper.MAX_UNCOMPRESSED
        self.fixture.refresh()
        self.refused()
        self.assertEqual(self.fixture.node_calls(), [])
        self.assertEqual(self.fixture.archive_calls(), [])

    def test_node_failure_extra_missing_symlink_bytes_and_modes_never_publish(self):
        for mode in ["fail", "extra", "extra-dir", "missing", "symlink", "wrong-bytes", "wrong-mode"]:
            with self.subTest(mode=mode):
                self.fixture.node_mode = mode
                self.refused()

    def test_nonempty_file_and_symlink_outputs_are_untouched(self):
        self.fixture.out.mkdir()
        sentinel = self.fixture.out / "keep"
        sentinel.write_bytes(b"keep these fake bytes")
        with self.assertRaises(helper.ProvisionError):
            self.fixture.run()
        self.assertEqual(list(self.fixture.out.iterdir()), [sentinel])
        self.assertEqual(sentinel.read_bytes(), b"keep these fake bytes")
        with self.assertRaises(helper.ProvisionError):
            self.fixture.run(out=sentinel)
        self.assertEqual(self.fixture.calls, [])
        sentinel.unlink()
        self.fixture.out.rmdir()
        real = self.root / "real-empty"
        real.mkdir()
        self.fixture.out.symlink_to(real, target_is_directory=True)
        with self.assertRaises(helper.ProvisionError):
            self.fixture.run()
        with self.assertRaises(helper.ProvisionError):
            self.fixture.run(out=self.fixture.out / "child")
        self.assertEqual(list(real.iterdir()), [])
        self.assertEqual(self.fixture.calls, [])

    def test_noncanonical_output_and_symlink_parent_fail_before_subprocesses(self):
        real = self.root / "real"
        real.mkdir()
        link = self.root / "link"
        link.symlink_to(real, target_is_directory=True)
        for out in [str(link) + "/../output", link / "output", str(self.root) + "/./output"]:
            with self.assertRaises(helper.ProvisionError):
                self.fixture.run(out=out)
        self.assertEqual(list(real.iterdir()), [])
        self.assertEqual(self.fixture.calls, [])

    def test_publish_cannot_overwrite_a_destination_created_after_validation(self):
        original_link = helper.os.link
        marker = b"concurrent fake bytes must stay"
        raced = []

        def competing_link(source, destination, **kwargs):
            if not raced:
                Path(destination).write_bytes(marker)
                raced.append(Path(destination))
            return original_link(source, destination, **kwargs)

        with mock.patch.object(helper.os, "link", side_effect=competing_link):
            with self.assertRaises(helper.ProvisionError):
                self.fixture.run()
        self.assertEqual(raced[0].read_bytes(), marker)
        self.assertFalse((self.fixture.out / "provision.json").exists())

    def test_cli_prints_only_safe_receipt_or_generic_failure(self):
        stdout, stderr = io.StringIO(), io.StringIO()
        with mock.patch.object(helper.subprocess, "run", side_effect=self.fixture.runner), \
             mock.patch.dict(os.environ, self.fixture.environment(), clear=True), \
             contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            status = helper.main(["--repo-root", str(self.fixture.repo), "--config", str(self.fixture.config_path),
                                  "--out", str(self.fixture.out), "--node", "fake-node"])
        self.assertEqual(status, 0)
        self.assertEqual(stderr.getvalue(), "")
        self.assertEqual(json.loads(stdout.getvalue())["product_suites"], "not_run")
        stdout, stderr = io.StringIO(), io.StringIO()
        self.fixture.node_mode = "fail"
        with mock.patch.object(helper.subprocess, "run", side_effect=self.fixture.runner), \
             mock.patch.dict(os.environ, self.fixture.environment(), clear=True), \
             contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            status = helper.main(["--repo-root", str(self.fixture.repo), "--config", str(self.fixture.config_path),
                                  "--out", str(self.root / "failed-output"), "--node", "fake-node"])
        self.assertEqual(status, 1)
        self.assertEqual(stdout.getvalue(), "")
        self.assertEqual(stderr.getvalue(), helper.REFUSAL + "\n")
        for secret in [str(self.root), "fake-private-stdout", "fake-private-stderr", *self.fixture.keys.values()]:
            self.assertNotIn(secret, stderr.getvalue())
        self.assertFalse((self.root / "failed-output").exists())
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            self.assertEqual(helper.main(["--fake-secret-argument", "private-text"]), 1)
        self.assertEqual(stdout.getvalue(), "")
        self.assertEqual(stderr.getvalue(), helper.REFUSAL + "\n")


if __name__ == "__main__":
    unittest.main()

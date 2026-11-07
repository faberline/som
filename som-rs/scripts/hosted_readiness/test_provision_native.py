"""Fixture-only provisioning checks. Run on GitHub, not the developer host."""

import copy
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import tarfile
import tempfile
import unittest
from unittest.mock import patch
import zipfile

SPEC = importlib.util.spec_from_file_location("provision_native", Path(__file__).with_name("provision_native.py"))
p = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(p)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def archive(files, zipped=False, link=None):
    stream = io.BytesIO()
    if zipped:
        with zipfile.ZipFile(stream, "w") as output:
            for name, data in files.items():
                output.writestr(name, data)
    else:
        with tarfile.open(fileobj=stream, mode="w:gz") as output:
            for name, data in files.items():
                member = tarfile.TarInfo(name)
                member.size = len(data)
                output.addfile(member, io.BytesIO(data))
            if link:
                member = tarfile.TarInfo(link)
                member.type = tarfile.SYMTYPE
                member.linkname = "/etc/passwd"
                output.addfile(member)
    return stream.getvalue()


class ProvisionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.repo = self.root / "repo"
        self.repo.mkdir()
        self.out = self.root / "tools"
        self.rustfmt = self.root / "rustfmt"
        self.rustfmt.write_bytes(b"rustfmt")
        self.manifest = self.repo / "artifacts.json"
        self.blobs = {}
        rows = []
        for name in ("ruff", "rustfmt", "node", "prettier"):
            files = {"LICENSE": b"license"}
            row = {"tool": name, "version": "1.2.3", "host": p.HOST, "url": "https://nodejs.org/fixture", "licenses": {"LICENSE": sha(b"license")}}
            if name == "prettier":
                files = {"package/LICENSE": b"license", "package/bin/prettier.cjs": b"prettier"}
                row["host"] = "platform-independent JavaScript"
                row["licenses"] = {"package/LICENSE": sha(b"license")}
                lines = "LICENSE\0" + sha(b"license") + "\nbin/prettier.cjs\0" + sha(b"prettier") + "\n"
                row["tree_sha256"] = sha(lines.encode())
            else:
                files["bin/" + name] = name.encode()
                row.update(executable_member="bin/" + name, executable_sha256=sha(name.encode()))
            blob = archive(files, name == "ruff")
            row["archive_sha256"] = sha(blob)
            rows.append(row)
            self.blobs[name] = blob
        self.data = {"version": "som.native-tools.v1", "tools": rows}
        self.manifest.write_text(json.dumps(self.data))
        self.env = patch.dict(os.environ, {"GITHUB_ACTIONS": "true"})
        self.env.start()
        self.addCleanup(self.env.stop)

    def invoke(self, runner=None):
        def version(command, **kwargs):
            tool = "prettier" if command[-2].endswith("prettier.cjs") else Path(command[0]).name
            value = {"ruff": "ruff 1.2.3", "rustfmt": "rustfmt 1.2.3-stable (fixture)", "node": "v1.2.3", "prettier": "1.2.3"}[tool]
            return subprocess.CompletedProcess(command, 0, value + "\n", "")
        with patch.object(p.platform, "system", return_value="Linux"), patch.object(p.platform, "machine", return_value="x86_64"), patch.object(p, "download", side_effect=lambda row: self.blobs[row["tool"]]), patch.object(p.subprocess, "run", side_effect=runner or version) as process:
            self.process = process
            report = p.provision(self.repo, self.manifest, self.out, self.rustfmt)
        return report, process

    def test_pass_keeps_receipts_licenses_and_only_fixed_env_keys(self):
        report, process = self.invoke()
        self.assertEqual(report["status"], "ready")
        self.assertFalse(report["product_acceptance_passed"])
        self.assertEqual(process.call_count, 4)
        self.assertEqual((self.out / "licenses/node/LICENSE").read_bytes(), b"license")
        self.assertEqual((self.out / "prettier/LICENSE").read_bytes(), b"license")
        self.assertEqual({line.split("=")[0] for line in (self.out / "github.env").read_text().splitlines()}, {"SOM_RUFF", "SOM_RUSTFMT", "SOM_TS_NODE", "SOM_TS_PRETTIER"})
        self.assertEqual(json.loads((self.out / "receipt.json").read_text())["status"], "ready")

    def test_archive_mismatch_runs_no_program_and_keeps_failure(self):
        self.blobs["ruff"] += b"changed"
        with self.assertRaisesRegex(p.Refusal, "archive digest"):
            self.invoke()
        self.assertEqual(self.process.call_count, 0)
        self.assertEqual(json.loads((self.out / "receipt.json").read_text())["status"], "failed")
        self.assertFalse((self.out / "github.env").exists())

    def test_missing_record_or_license_refused_before_writes(self):
        for mutation in ("tool", "license"):
            with self.subTest(mutation=mutation):
                data = copy.deepcopy(self.data)
                if mutation == "tool":
                    data["tools"].pop()
                else:
                    data["tools"][0]["licenses"] = {}
                self.manifest.write_text(json.dumps(data))
                with self.assertRaises(p.Refusal):
                    self.invoke()
                self.assertFalse(self.out.exists())

    def test_installed_rustfmt_digest_mismatch_refused_before_writes(self):
        self.rustfmt.write_bytes(b"changed")
        with self.assertRaisesRegex(p.Refusal, "rustfmt digest"):
            self.invoke()
        self.assertFalse(self.out.exists())

    def test_existing_inside_repo_and_symlink_outputs_refused(self):
        for out in (self.repo / "tools", self.repo, self.root / "existing"):
            with self.subTest(out=out):
                if out.name == "existing":
                    out.mkdir()
                self.out = out
                with self.assertRaises(p.Refusal):
                    self.invoke()
        linked = self.root / "linked"
        linked.symlink_to(self.repo, target_is_directory=True)
        self.out = linked / "tools"
        with self.assertRaisesRegex(p.Refusal, "symlink"):
            self.invoke()

    def test_unsafe_manifest_and_archive_paths_refused(self):
        for path in ("../escape", "/absolute", "a//b", "a/./b", "a\\b", "a\nb"):
            with self.subTest(path=path):
                with self.assertRaises(p.Refusal):
                    p.member_path(path)
        self.manifest = self.root / "outside.json"
        self.manifest.write_text(json.dumps(self.data))
        with self.assertRaisesRegex(p.Refusal, "inside the repository"):
            self.invoke()

    def test_selected_link_and_unsafe_unused_member_refused(self):
        for blob in (archive({"LICENSE": b"license"}, link="bin/node"), archive({"../escape": b"bad", "LICENSE": b"license", "bin/node": b"node"})):
            with self.subTest(blob_sha=sha(blob)):
                row = copy.deepcopy(self.data["tools"][2])
                row["archive_sha256"] = sha(blob)
                with self.assertRaises(p.Refusal):
                    p.unpack(row, blob, self.root / ("extract-" + sha(blob)))

    def test_unselected_node_link_is_never_extracted(self):
        blob = archive({"LICENSE": b"license", "bin/node": b"node"}, link="bin/npm")
        row = copy.deepcopy(self.data["tools"][2])
        row["archive_sha256"] = sha(blob)
        out = self.root / "node-extract"
        p.unpack(row, blob, out)
        self.assertFalse((out / "bin/npm").exists())
        self.assertEqual((out / "bin/node").read_bytes(), b"node")

    def test_version_failure_is_recorded_and_no_env_is_written(self):
        def failed(command, **kwargs):
            return subprocess.CompletedProcess(command, 1, "", "fixture failure")
        with self.assertRaisesRegex(p.Refusal, "version command"):
            self.invoke(failed)
        receipt = json.loads((self.out / "receipt.json").read_text())
        self.assertEqual(receipt["tools"]["ruff"]["version"]["exit_code"], 1)
        self.assertEqual(receipt["status"], "failed")
        self.assertFalse((self.out / "github.env").exists())

    def test_executable_license_and_tree_mismatches_run_no_program(self):
        for field in ("executable_sha256", "licenses", "tree_sha256"):
            with self.subTest(field=field):
                data = copy.deepcopy(self.data)
                self.out = self.root / ("tools-" + field)
                row = data["tools"][3 if field == "tree_sha256" else 0]
                if field == "licenses":
                    row[field] = {"LICENSE": "0" * 64}
                else:
                    row[field] = "0" * 64
                self.manifest.write_text(json.dumps(data))
                with self.assertRaises(p.Refusal):
                    self.invoke()
                self.assertEqual(self.process.call_count, 0)
                self.assertFalse((self.out / "github.env").exists())

    def test_archive_limits_apply_before_extraction(self):
        with patch.object(p, "MAX_FILE", 4):
            with self.assertRaisesRegex(p.Refusal, "size limit"):
                self.invoke()
        self.assertEqual(self.process.call_count, 0)
        self.assertFalse((self.out / "github.env").exists())

    def test_unknown_host_refused(self):
        with patch.object(p.platform, "system", return_value="Darwin"):
            with self.assertRaisesRegex(p.Refusal, "unsupported native host"):
                p.provision(self.repo, self.manifest, self.out, self.rustfmt)
        self.assertFalse(self.out.exists())


if __name__ == "__main__":
    unittest.main()

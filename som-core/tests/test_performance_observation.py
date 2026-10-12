"""Performance observations must not mask correctness failures or impose limits."""

import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import bench_service_budget as benchmark


class PerformanceObservationTests(unittest.TestCase):
    def invoke(self, *, enforce=False, refusal=False):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            model = root / "model"
            model.mkdir()
            manifest = model / "manifest.json"
            manifest.write_text("{}")
            cases = root / "cases.jsonl"
            cases.write_text('{"prompt": {}}\n')
            output = root / "result.json"
            argv = ["benchmark", "--model", str(model), "--cases", str(cases), "--output", str(output),
                    "--warmup", "0", "--max-model-mb", "0.000001", "--max-rss-mb", "0.000001", "--max-p95-ms", "0.000001"]
            if enforce:
                argv.append("--enforce-limits")

            def get_json(url, data=None):
                if url.endswith("/healthz"):
                    return {"status": "ok", "model_loaded": True, "blocks_loaded": True,
                            "model_manifest_sha256": hashlib.sha256(manifest.read_bytes()).hexdigest(), "blocks_sha256": "test-blocks"}
                return {"status": "error"} if refusal else {"status": "ok", "files": {"app.py": "pass\n"}}

            with (patch.object(sys, "argv", argv), patch.object(benchmark, "get_json", get_json),
                  patch.object(benchmark, "listener_pid", return_value=123),
                  patch.object(benchmark, "rss_bytes", return_value=2_000_000),
                  patch.object(benchmark.time, "perf_counter", side_effect=[100, 100.002])):
                benchmark.main()
            return json.loads(output.read_text())

    def test_exceeded_references_are_observations_by_default(self):
        result = self.invoke()
        self.assertFalse(result["reference_values_met"])
        self.assertFalse(result["limits_enforced"])
        self.assertTrue(result["measurement_complete"])
        self.assertIsNone(result["passed"])

    def test_explicit_threshold_experiment_can_fail(self):
        with self.assertRaises(SystemExit) as error:
            self.invoke(enforce=True)
        self.assertEqual(error.exception.code, 1)

    def test_supported_refusal_still_fails_measurement(self):
        with self.assertRaisesRegex(ValueError, "generation failed"):
            self.invoke(refusal=True)

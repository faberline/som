"""Confusable Python source must retain the exact public string value."""

import ast
import json
from pathlib import Path
import subprocess

from som_core import flow_compact

ROOT = Path(__file__).resolve().parents[2]


def test_every_pinned_confusable_keeps_its_runtime_value(tmp_path):
    policy = json.loads(flow_compact.LITERAL_POLICY.read_text())
    points = policy["codepoints"]
    assert len(points) == len(set(points)) == 1582
    assert 0xFF1A in points and 0x0391 in points and 0x1D400 in points
    lines = []
    for point in points:
        value = "a" + chr(point) + "a"
        encoded = flow_compact._python_literal(value)
        assert ast.literal_eval(encoded) == value
        assert chr(point) not in encoded
        lines.append("value = " + encoded)
    binary = ROOT / "som-rs/models/composed-quality-candidate/ruff"
    checked = subprocess.run([str(binary), "check", "--isolated",
        "--select", "RUF001", "--no-cache", "-"],
        input="\n".join(lines) + "\n", capture_output=True,
        text=True, check=False, timeout=30)
    assert checked.returncode == 0, checked.stdout + checked.stderr


def test_long_escaped_unicode_and_source_quotes_preserve_values():
    for value in ("：" * 80, "aΑ\"\\\n" * 40,
                  "a\U0001d400a" * 40, "中文字：" * 30):
        encoded = flow_compact._python_literal(value)
        assert ast.literal_eval(encoded) == value
        assert all(len(line) <= 42 for line in encoded.splitlines())


def test_plain_ascii_and_non_confusable_unicode_bytes_are_unchanged():
    for value in ("title", "合法中文字", "simple\nvalue", "x" * 61, "語" * 61):
        expected = ("(\n" + "\n".join(json.dumps(value[index:index + 40],
                    ensure_ascii=False) for index in range(0, len(value), 40))
                    + "\n)" if len(value) > 60 else json.dumps(value, ensure_ascii=False))
        assert flow_compact._python_literal(value) == expected

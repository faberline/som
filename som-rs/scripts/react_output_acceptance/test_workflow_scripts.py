"""Parse the actual inline Python in the hosted workflow on a GitHub runner."""

from pathlib import Path
import textwrap
import unittest


class WorkflowScriptsTests(unittest.TestCase):
    def test_hosted_inline_python_is_valid_before_any_conditional_stage_runs(self):
        workflow = Path(__file__).resolve().parents[3] / ".github/workflows/m6-acceptance.yml"
        lines = workflow.read_text().splitlines(keepends=True)
        scripts = 0
        for start, line in enumerate(lines):
            if not line.strip().endswith(" - <<'PY'"):
                continue
            indent = line[:len(line) - len(line.lstrip())]
            end = next(index for index in range(start + 1, len(lines))
                       if lines[index].rstrip("\r\n") == indent + "PY")
            source = textwrap.dedent("".join(lines[start + 1:end]))
            compile(source, f"{workflow}:{start + 2}", "exec")
            scripts += 1
        self.assertEqual(scripts, 3, "Keep each actual hosted Python heredoc covered.")


if __name__ == "__main__":
    unittest.main()

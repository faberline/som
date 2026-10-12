"""A failed batched check must keep the full response and fail the tool call."""
import json
from pathlib import Path
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from eval_transport import observed_ranges


def test_failed_check_keeps_visible_response_and_unchanged_product(tmp_path):
    cli = tmp_path / 'fake-som'
    cli.write_text('#!' + sys.executable + '\nimport json\nprint(json.dumps({"status":"ok","files":{"app.py":"value = 1\\n"}}))\n')
    cli.chmod(0o755)
    checker = tmp_path / 'checker.txt'
    checker.write_text('import sys\nfrom pathlib import Path\nassert sys.argv[1:] == ["job", "--defer-source-visibility"]\nassert Path("app.py").read_text() == "value = 1\\n"\nraise SystemExit(1)\n')
    (tmp_path / 'prompt.json').write_text('{}')
    script = Path(__file__).resolve().parents[1] / 'scripts/eval_transport.py'
    result = subprocess.run([sys.executable, str(script), 'generate', '--cli', str(cli),
        '--check-python', sys.executable, '--check-script', str(checker), '--check-job', 'job'],
        cwd=tmp_path, capture_output=True, text=True)
    response = tmp_path / 'som-results/0001/response.json'
    assert result.returncode == 1
    assert observed_ranges(response.read_bytes(), [result.stdout], str(response))['complete']
    assert (tmp_path / 'app.py').read_text() == json.loads(response.read_text())['files']['app.py']
    assert (response.parent / 'prompt.json').read_text() == '{}'

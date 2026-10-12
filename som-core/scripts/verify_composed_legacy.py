"""Run the original 100 isolated PostgreSQL cases against saved Rust responses."""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time

ROOT = Path(__file__).resolve().parents[2]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--responses', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    cases = ROOT / 'som-core/data/service-regression-v4/heldout.jsonl'
    python = ROOT / 'som-code-python/.venv/bin/python'
    verifier = ROOT / 'som-core/scripts/verify_service_v3.py'

    def check(index):
        started = time.monotonic()
        response = args.responses / f'{index:04d}.json'
        result = subprocess.run([str(python), str(verifier), '--cases', str(cases),
                                 '--heldout-index', str(index), '--files-json', str(response)],
                                capture_output=True, text=True, timeout=180,
                                env=os.environ | {'PYTHONPATH': str(ROOT / 'som-core/src')})
        log = args.output / f'{index:04d}.log'
        log.write_text(result.stdout + result.stderr)
        return {'index': index, 'passed': result.returncode == 0,
                'seconds': time.monotonic() - started, 'log': str(log),
                'log_sha256': hashlib.sha256(log.read_bytes()).hexdigest(),
                'response_sha256': hashlib.sha256(response.read_bytes()).hexdigest()}

    rows = []
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = {pool.submit(check, i): i for i in range(100)}
        for future in as_completed(futures):
            try:
                rows.append(future.result())
            except Exception as exc:
                rows.append({'index': futures[future], 'passed': False, 'error': str(exc)})
            if len(rows) % 10 == 0:
                print(json.dumps({'done': len(rows), 'passed': sum(row['passed'] for row in rows)}), flush=True)
    rows.sort(key=lambda row: row['index'])
    report = {'cases_sha256': hashlib.sha256(cases.read_bytes()).hexdigest(),
              'verifier_sha256': hashlib.sha256(verifier.read_bytes()).hexdigest(),
              'results': rows, 'passed': len(rows) == 100 and all(row['passed'] for row in rows)}
    (args.output / 'result.json').write_text(json.dumps(report, indent=2) + '\n')
    raise SystemExit(0 if report['passed'] else 1)


if __name__ == '__main__':
    main()

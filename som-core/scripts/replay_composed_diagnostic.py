"""Replay the saved failing assertion without contacting a service."""
import hashlib
import json
from pathlib import Path
import argparse

from composed_diagnostics_recorded import failure_details
import composed_diagnostics_recorded as diagnostic_module

ROOT = Path(__file__).resolve().parents[2]
EVAL = ROOT / 'som-rs/evals/composable-2026-10-01'


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, default=EVAL / 'diagnostic-replay')
    args = parser.parse_args()
    failed = Path('/private/tmp/som-composable-codex-2026-10-01-r2/codex-04-direct/check-results/0001.json')
    receipt = json.loads(failed.read_text())
    original = Path(receipt['evidence_directory']) / 'events.jsonl'
    records = [json.loads(line) for line in original.read_text().splitlines()]
    http = [row for row in records if row.get('kind') == 'http']
    created = next(row['json'] for row in http
                   if row['method'] == 'POST' and row['path'] == '/customers' and row['status'] == 201)
    item = '/customers/' + str(created['id'])
    actual = next(row['json'] for row in reversed(http)
                  if row['method'] == 'GET' and row['path'] == item)
    assert http[-1]['path'] == '/__control' and actual != created
    replay = args.output
    evidence = replay / 'check-results/evidence-original'
    evidence.mkdir(parents=True, exist_ok=False)
    (evidence / 'events.jsonl').write_bytes(original.read_bytes())
    calls = []
    def request(method, path, status):
        calls.append((method, path, status))
        return actual
    try:
        assert request('GET', item, 200) == created
    except AssertionError as error:
        result = failure_details(error, replay)
    assert calls == [('GET', item, 200)]
    assert result['expected'] == created and result['actual'] == actual
    assert result['request']['path'] == item
    assert {row['path'] for row in result['differences']} == {'$.created_at', '$.updated_at'}
    output = {'passed': True, 'kind': 'saved-assertion replay; no network requests',
              'diagnostic_module_sha256': hashlib.sha256(Path(diagnostic_module.__file__).read_bytes()).hexdigest(),
              'original_check': str(failed), 'original_check_sha256': hashlib.sha256(failed.read_bytes()).hexdigest(),
              'original_evidence_sha256': hashlib.sha256(original.read_bytes()).hexdigest(),
              'original_source_sha256': receipt['source_sha256'],
              'old_failure': receipt['failure'], 'corrected_failure': result}
    (replay / 'result.json').write_text(json.dumps(output, indent=2) + '\n')
    print(json.dumps({'passed': True, 'differences': result['differences']}))


if __name__ == '__main__':
    main()

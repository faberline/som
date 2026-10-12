"""Keep tool-overhead observations separate from the formal token gate."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[2]
KIT = ROOT / 'som-rs/evals/composable-2026-10-01/codex'
sys.path.insert(0, str(KIT))
from check import sessions


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--kit', choices=('codex', 'codex-r2', 'codex-r3'), default='codex')
    args = parser.parse_args()
    kit = KIT.parent / args.kit
    output = kit / 'protocol-observations.json'
    if output.exists():
        raise ValueError('preserve the existing audit')
    report = {'purpose': 'Matched call text is diagnostic only. It is not execution or cost attribution proof.',
              'round': args.kit,
              'jobs': []}
    for job in json.loads((kit / 'jobs.json').read_text())['jobs']:
        item = {'job': job['id'], 'mode': job['mode'], 'calls': [], 'guard_rejections': [], 'sessions': []}
        for path in sessions(Path(job['directory'])):
            item['sessions'].append({'path': str(path), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()})
            for number, line in enumerate(path.read_text().splitlines(), 1):
                row = json.loads(line)
                payload = row.get('payload', {})
                if row.get('type') != 'response_item':
                    continue
                kind = payload.get('type')
                if kind in {'function_call', 'custom_tool_call'}:
                    text = payload.get('input', payload.get('arguments', ''))
                    if not isinstance(text, str):
                        text = json.dumps(text)
                    tags = []
                    if re.search(r'\bls(?:\s|\")|\bpwd\b|rg --files', text):
                        tags.append('directory_inspection')
                    if 'cat instruction.txt' in text or 'cat som_io.sh' in text:
                        tags.append('prepared_instruction_or_helper_read')
                    if 'bash som_io.sh' in text:
                        tags.append('som_generation_and_check')
                    if 'run_checks.sh' in text and 'cat ' not in text:
                        tags.append('standalone_check')
                    if 'write_stdin' in text or payload.get('name') == 'wait':
                        tags.append('wait')
                    if tags:
                        item['calls'].append({'session': path.name, 'line': number,
                            'call_id': payload.get('call_id'), 'tags': tags,
                            'input_sha256': hashlib.sha256(text.encode()).hexdigest()})
                elif kind in {'function_call_output', 'custom_tool_call_output'}:
                    text = json.dumps(payload.get('output', ''), ensure_ascii=False)
                    if 'Command blocked by PreToolUse hook: cap guard' in text:
                        item['guard_rejections'].append({'session': path.name, 'line': number,
                                                        'call_id': payload.get('call_id')})
        item['counts'] = {tag: sum(tag in call['tags'] for call in item['calls']) for tag in
            ('directory_inspection', 'prepared_instruction_or_helper_read',
             'som_generation_and_check', 'standalone_check', 'wait')}
        item['counts']['guard_rejections'] = len(item['guard_rejections'])
        report['jobs'].append(item)
    output.write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps({'jobs': len(report['jobs']), 'guard_rejections': sum(
        row['counts']['guard_rejections'] for row in report['jobs'])}))


if __name__ == '__main__':
    main()

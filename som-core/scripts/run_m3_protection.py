"""Recheck the protected M2 products and legacy services on one M3 source."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import subprocess
import sys
import time
import traceback

import httpx

from audit_business_transactions import source_hashes
from composed_evidence import failure_details
from m3_local_fixture import M3Fixture, close_native, interruption
from run_m3_flow_acceptance import candidate_hashes, checkpoint
from prepare_flow_training import cases as m2_cases
from som_core import composed, flow_compact, quality
from som_core.flow_m3_evidence import ROOT, digest, hashes, verify_runtime, verify_seal
from som_core.train_flow import BLOCKS, LEGACY
from som_core.v1 import load_blocks
from switch_flow_default import cli, start, stop
from verify_composed_services import verify_all
from verify_flow_service import verify as verify_flow
from verify_python_quality import verify as verify_quality

M2 = ROOT / 'som-rs/evals/m2-flow-2026-10-04/r31'


def jobs() -> list[dict]:
    result = []
    for group, name in [('services14', 'composed/regression.jsonl'),
                        ('compositions120', 'composed/heldout.jsonl'),
                        ('blind80', 'composed/blind.jsonl'),
                        ('legacy100', 'service-regression-v4/heldout.jsonl')]:
        for index, line in enumerate((ROOT / 'som-core/data' / name).read_text().splitlines()):
            row = json.loads(line)
            result.append({'id': f'{group}-{index:04d}', 'group': group, 'index': index,
                'prompt': row['prompt'], 'gold': M2 / f'legacy-generation-002/comparison/model/{group}/{index:04d}.json'})
    behavior_ids = {json.loads(line)['id'] for split in ('dev', 'formal')
                    for line in (ROOT / f'som-core/data/flow-v7-r10/{split}.jsonl').read_text().splitlines()}
    for identity, prompt, variant in m2_cases():
        result.append({'id': 'm2-' + identity, 'group': 'm2-quality218', 'prompt': prompt,
            'variant': variant, 'behavior': identity in behavior_ids,
            'gold': M2 / f'training-prerequisite-001/{identity}/response.json'})
    for source in sorted((M2 / 'supplementary-acceptance-003').iterdir()):
        if not source.is_dir() or not (source / 'dsl.json').is_file():
            continue
        row = json.loads((source / 'requirement.json').read_text())
        result.append({'id': 'supplement-' + row['id'], 'group': 'm2-supplement12', 'prompt': row['prompt'],
            'dsl': json.loads((source / 'dsl.json').read_text()), 'behavior': True,
            'gold': source / 'response.json'})
    if len(result) != 544 or len({row['id'] for row in result}) != 544:
        raise ValueError('M3 protection must include 314 legacy, 218 quality and 12 supplementary products')
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    for name in ('data', 'source-seal', 'model', 'runtime', 'output'):
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--shards', type=int, default=1)
    parser.add_argument('--shard-index', type=int, default=0)
    args = parser.parse_args()
    args.data, args.output, args.runtime = args.data.resolve(), args.output.resolve(), args.runtime.resolve()
    args.model = args.model.resolve()
    if not 1 <= args.shards <= 4 or not 0 <= args.shard_index < args.shards:
        raise ValueError('invalid protection shard')
    seal = verify_seal(args.source_seal, args.data)
    verify_runtime(seal, {name: args.runtime / name
                          for name in ("som", "som-flow-compact-verify")})
    checkpoint(args.model, args.data, args.source_seal)
    fixed_model = candidate_hashes(args.model)
    before = hashes(args.data)
    selected = [row for index, row in enumerate(jobs()) if index % args.shards == args.shard_index]
    args.output.mkdir(parents=True, exist_ok=False)
    library = load_blocks(BLOCKS.parent)
    server = assembler = None
    records = []
    report = {}
    try:
        server, address = start(args.runtime / 'som', args.model, BLOCKS, args.output / 'server')
        assembler = subprocess.Popen([str(args.runtime / 'som-flow-compact-verify'), str(BLOCKS)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        with M3Fixture(args.output / 'fixture') as fixture:
            for job in selected:
                directory = args.output / job['id']
                directory.mkdir()
                prompt = job['prompt']
                (directory / 'prompt.json').write_text(composed.canonical(prompt) + '\n')
                record = {'id': job['id'], 'group': job['group'], 'passed': False}
                begun = time.monotonic()
                try:
                    golden = json.loads(job['gold'].read_text())['files']
                    if prompt['version'] == 'som.prompt.v7':
                        plan = job.get('dsl') or flow_compact.reference(prompt, library, job['variant'])
                        files = flow_compact.assemble(prompt, plan, library, LEGACY / 'ruff')
                        (directory / 'dsl.json').write_text(composed.canonical(plan) + '\n')
                        assembler.stdin.write(composed.canonical({'prompt': prompt, 'dsl': plan, 'formatter_model': str(LEGACY)}) + '\n')
                        assembler.stdin.flush()
                        response = json.loads(assembler.stdout.readline())
                        if response.get('status') != 'ok' or response.get('files') != files:
                            raise ValueError('M2 protected Python/Rust final bytes differ')
                    else:
                        offset = (args.output / 'server/stderr.log').stat().st_size
                        response = httpx.post(address + '/v1/generate', json=prompt, timeout=120).json()
                        with (args.output / 'server/stderr.log').open('rb') as stream:
                            stream.seek(offset)
                            (directory / 'native-trace.jsonl').write_bytes(stream.read())
                        if response.get('status') != 'ok':
                            raise ValueError('legacy model generation failed: ' + str(response.get('code')))
                        files = response['files']
                    (directory / 'response.json').write_text(composed.canonical(response) + '\n')
                    if files != golden:
                        raise ValueError('protected final output changed from M2 archive')
                    record.update(archive_byte_parity=True, formatted_bytes=sum(len(text.encode()) for text in files.values()))
                    product = directory / 'product'
                    product.mkdir()
                    for path, source in files.items():
                        target = product / path
                        target.parent.mkdir(parents=True, exist_ok=True)
                        target.write_text(source)
                    original = source_hashes(product)
                    if job['group'] != 'legacy100':
                        record['quality'] = verify_quality(product, directory / 'quality')
                        if not record['quality']['passed']:
                            raise ValueError('protected strict quality failed')
                    if prompt['version'] == 'som.prompt.v7':
                        record['format_idempotent'] = all(not path.endswith('.py') or
                            quality.format_source(path, text, LEGACY / 'ruff', package=prompt['package']) == text
                            for path, text in files.items())
                        if not record['format_idempotent']:
                            raise ValueError('protected formatting changed on a second pass')
                        if job['behavior']:
                            record['behavior'] = verify_flow(prompt, product, fixture)
                            if not record['behavior']['full_m2_acceptance']:
                                raise ValueError('protected flow behavior failed')
                    elif job['group'] == 'legacy100':
                        checked = subprocess.run([sys.executable, str(Path(__file__).with_name('verify_service_v3.py')),
                            '--cases', str(ROOT / 'som-core/data/service-regression-v4/heldout.jsonl'),
                            '--heldout-index', str(job['index']), '--files-json', str(directory / 'response.json')],
                            capture_output=True, text=True, check=False, timeout=180)
                        (directory / 'behavior.stdout').write_text(checked.stdout)
                        (directory / 'behavior.stderr').write_text(checked.stderr)
                        if checked.returncode:
                            raise ValueError('legacy PostgreSQL service behavior failed')
                        record['behavior_exit_code'] = 0
                    else:
                        record['behavior'] = verify_all(prompt, product, fixture, standard=job['group'] == 'services14')
                        if record['behavior']['status'] != 'passed':
                            raise ValueError('legacy composed service behavior failed')
                    record['source_unchanged'] = source_hashes(product) == original
                    record['passed'] = record['source_unchanged']
                except Exception as error:
                    record.update(error=traceback.format_exc(), failure=failure_details(error), rejection=getattr(error, 'code', None))
                record['seconds'] = time.monotonic() - begun
                (directory / 'result.json').write_text(json.dumps(record, indent=2, default=str) + '\n')
                records.append(record)
                unchanged = hashes(args.data) == before
                model_unchanged = candidate_hashes(args.model) == fixed_model
                report = {'required': 544, 'records': records, 'source_sha256': before,
                    'model_sha256': fixed_model, 'model_unchanged': model_unchanged,
                    'source_unchanged': unchanged, 'shards': args.shards, 'shard_index': args.shard_index,
                    'shard_passed': unchanged and model_unchanged and len(records) == len(selected) and all(row['passed'] for row in records)}
                pending = args.output / 'result.pending.json'
                pending.write_text(json.dumps(report, indent=2, default=str) + '\n')
                pending.replace(args.output / 'result.json')
                print(composed.canonical({key: record.get(key) for key in ('id', 'passed', 'seconds', 'rejection', 'failure')}), flush=True)
                for disposable in (directory / 'product/.venv', directory / 'quality/mypy-cache', directory / 'quality/pylint-cache'):
                    if disposable.is_dir(): shutil.rmtree(disposable)
                if not unchanged or not model_unchanged:
                    raise ValueError('protection source changed during the frozen round')
    finally:
        if server is not None: stop(server)
        close_native(assembler)
    raise SystemExit(not report.get('shard_passed'))


if __name__ == '__main__':
    with interruption():
        main()

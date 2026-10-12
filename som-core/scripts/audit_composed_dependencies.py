"""Record provided pins and the locked versions used by each completed job."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import tomllib

ROOT = Path(__file__).resolve().parents[2]
EVAL = ROOT / 'som-rs/evals/composable-2026-10-01'


def normalized(name):
    return re.sub(r'[-_.]+', '-', name).lower()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--kit', choices=('codex', 'codex-r2', 'codex-r3'), required=True)
    args = parser.parse_args()
    kit = EVAL / args.kit
    output = kit / 'dependency-observations.json'
    if output.exists():
        raise ValueError('preserve the existing observation')
    results = json.loads((kit / 'results.json').read_text())
    assert len(results['results']) == 20 and all(row['passed'] for row in results['results'])
    jobs = json.loads((kit / 'jobs.json').read_text())['jobs']
    rows = []
    for job in jobs:
        root = Path(job['directory'])
        contract = root / 'dependency-contract.txt'
        pins = {}
        for line in contract.read_text().splitlines():
            name, version = line.split('==')
            pins[normalized(name.split('[')[0])] = version
        lock_path = root / 'uv.lock'
        locked = tomllib.loads(lock_path.read_text())
        installed = {}
        for package in locked['package']:
            installed.setdefault(normalized(package['name']), set()).add(package['version'])
        used = {name: sorted(installed[name]) for name in pins if name in installed}
        wrong = {name: versions for name, versions in used.items() if versions != [pins[name]]}
        rows.append({'job': job['id'], 'contract_sha256': hashlib.sha256(contract.read_bytes()).hexdigest(),
                     'lock_sha256': hashlib.sha256(lock_path.read_bytes()).hexdigest(),
                     'provided_pins': pins, 'locked_contract_packages': used,
                     'contract_packages_not_in_lock': sorted(set(pins) - set(used)),
                     'version_mismatches': wrong})
    result = {'jobs': rows,
              'same_provided_pins': len({row['contract_sha256'] for row in rows}) == 1,
              'all_present_contract_versions_match': not any(row['version_mismatches'] for row in rows),
              'scope': 'Both methods received the same pins. Some direct jobs declared a subset of the libraries. '
                       'The SOM block declares the complete library list. This observation reports those differences; '
                       'it does not claim identical installed dependency sets or add a new acceptance rule.'}
    output.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps({key: value for key, value in result.items() if key != 'jobs'}))


if __name__ == '__main__':
    main()

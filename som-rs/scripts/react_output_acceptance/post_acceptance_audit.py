#!/usr/bin/env python3
"""Recheck the separately observable Mac evidence after the driver exits.

A positive result requires every independently rechecked evidence row.
Missing evidence keeps the final verdict unverified.
"""
import argparse
import json
import os
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / 'audit'))
import audit_mac_rust006_extra as extra
import audit_mac_parity as parity
import audit_candidate_owner as owner
import audit_baseline_cleanup as baseline
import audit_workflow_origin as origin
import audit130
import run_hosted_acceptance as hosted


def audit_original_gate(repo, out, candidate, run, revision):
    summary=extra.json_file(out/'summary.json')
    if summary.get('passed') is not False or summary.get('status')!='awaiting-post-audit' or summary.get('pre_origin_passed') is not True:
        raise ValueError('driver did not finish the pre-origin checks')
    if summary.get('head_before')!=revision or summary.get('head_after')!=revision:
        raise ValueError('driver revision differs')
    bootstrap=extra.json_file(out/'bootstrap-assets.json')
    assets=hosted.asset_files(candidate/'assets')
    if not (assets==bootstrap.get('source_files')==bootstrap.get('staged_files')==hosted.asset_files(repo/'.local/m6-hosted/baseline/assets')==hosted.asset_files(repo/'som-rs/compiler-assets/current')):
        raise ValueError('candidate assets differ')
    if hosted.sha(out/'bootstrap-assets.json')!=summary.get('bootstrap_assets_receipt_sha256'):
        raise ValueError('bootstrap receipt differs')
    fresh=audit130.run_audit(repo,run)
    if fresh!=extra.json_file(out/'audit.json'):
        raise ValueError('fresh original audit differs')
    checked=dict(summary);checked['candidate_assets_match_bootstrap']=True
    if not hosted.verdict(out,checked):
        raise ValueError('original 12-step gate differs')
    return {'steps':checked['step_count'],'audit_totals':fresh['totals'],'candidate_binary_sha256':checked['candidate_binary_sha256']}


def collect(out, config, provenance, run_info, jobs_info, env):
    repo = HERE.parents[2]
    public = repo / '.local/rust-compiler-goal/acceptance/public-products-003b'
    legacy = repo / '.local/rust-compiler-goal/acceptance/legacy544/run-005'
    candidate = out / 'candidate'
    run = out / 'run'
    rust = run / 'rust006-extra'
    raw = run / 'rust006-parity-http'
    lifecycle = run / 'rust006-parity-lifecycle'
    parity_out = run / 'rust006-parity'
    parity_file = parity_out / 'report-parity.json'
    revision = env.get('GITHUB_SHA', '')
    report = {'version': 'som.m6-post-acceptance-observations.v1',
              'product_acceptance_passed': False, 'final_verdict': 'unverified', 'rows': []}
    bound = None
    def row(name, fn):
        try:
            result = fn()
            report['rows'].append({'id': name, 'status': 'passed', 'evidence': result})
            return result
        except Exception as exc:
            report['rows'].append({'id': name, 'status': 'unverified', 'error_type': type(exc).__name__})
            return None
    bound = row('approved-inputs', lambda: extra.approved_inputs(public, legacy, config, provenance))
    # The bound authority object is private and is not JSON evidence.
    if bound is not None:
        report['rows'][-1]['evidence'] = {'authority': 'verified'}
    def require_bound():
        if bound is None:
            raise ValueError('approved inputs unverified')
        return bound
    row('parity-sources', lambda: parity.audit_sources(revision, lifecycle, raw, parity_out))
    parity_candidate=row('parity-candidate', lambda: extra.audit_candidate(lifecycle/'candidate-probe', candidate, revision))
    row('parity-postgres-observed-stop', lambda: parity.audit_parity_postgres(lifecycle))
    row('parity-raw-and-observed-product-cleanup', lambda: parity.audit_wire(raw, parity_out, legacy, require_bound(), lifecycle/'product-http'))
    row('python-replay', lambda: extra.audit_replay(rust/'raw-python-replay', public, legacy, require_bound()))
    row('validate-cases', lambda: extra.audit_cases(rust/'raw-validate-cases'))
    row('phase2-rust-refusals', lambda: extra.audit_refusals(rust/'raw-rust-refusals', legacy, require_bound()))
    rust_candidate=row('rust-candidate', lambda: extra.audit_candidate(rust, candidate, revision))
    row('postgres-observed-stop', lambda: extra.audit_cluster(rust/'cluster-lifecycle'))
    row('rust-command-records', lambda: extra.audit_command_records(rust, candidate, public, legacy, parity_file))
    candidate_cleanup=row('candidate-observed-group-cleanup', lambda: owner.audit(out, candidate))
    def candidate_join():
        if not (parity_candidate and rust_candidate and candidate_cleanup and
                parity_candidate.get('pid')==rust_candidate.get('pid')==candidate_cleanup.get('candidate_pid') and
                parity_candidate.get('binary_sha256')==rust_candidate.get('binary_sha256')):
            raise ValueError('candidate identity join differs')
        return {'pid':parity_candidate['pid'],'binary_sha256':parity_candidate['binary_sha256']}
    row('candidate-identity-join', candidate_join)
    row('baseline-observed-cleanup', lambda: baseline.audit(out, repo/'.local/m6-hosted/baseline'))
    row('original-12-step-gate', lambda: audit_original_gate(repo,out,candidate,run,revision))
    row('workflow-origin', lambda: origin.verify(repo, env, json.loads(run_info.read_bytes()), json.loads(jobs_info.read_bytes()),
        workflow='.github/workflows/m6-mac-final-acceptance.yml', job_name='Frozen M6 tools and optional full acceptance',
        completed_step='Run one candidate and the independent audit', event='workflow_dispatch'))
    complete=all(x['status'] == 'passed' for x in report['rows'])
    report['status']='passed' if complete else 'incomplete'
    report['final_verdict']='passed' if complete else 'unverified'
    report['product_acceptance_passed']=complete
    return report


def main():
    p = argparse.ArgumentParser()
    for name in ('out', 'approved-input-config', 'provenance-root', 'run-info', 'jobs-info', 'report'):
        p.add_argument('--'+name, type=Path, required=True)
    a = p.parse_args()
    if a.report.exists() or a.report.is_symlink():
        raise ValueError('new report required')
    result = collect(a.out, a.approved_input_config, a.provenance_root, a.run_info, a.jobs_info, os.environ)
    with a.report.open('x') as stream:
        json.dump(result, stream, sort_keys=True, indent=2)
        stream.write('\n')
    print(json.dumps({'status': result['status'], 'final_verdict': result['final_verdict'],
                      'product_acceptance_passed': result['product_acceptance_passed'],
                      'rows': [{'id': x['id'], 'status': x['status']} for x in result['rows']]}))
    return 0 if result['status'] == 'passed' else 1

if __name__ == '__main__':
    raise SystemExit(main())

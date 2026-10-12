"""List the actual service checks with their bound requirements and evidence."""
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
EVAL = ROOT / 'som-rs/evals/composable-2026-10-01'
LABELS = {
    'empty_directory_uv_sync': 'Install pinned dependencies in an empty directory.',
    'missing_database_configuration': 'Reject startup when the database setting is missing.',
    'migration_upgrade': 'Apply the database migration.',
    'database_indexes_and_storage_constraints': 'Check database indexes and storage constraints.',
    'missing_identity_and_reader_write_denial': 'Reject missing identity and reader writes.',
    'jwt_signature_algorithm_issuer_audience_expiry_and_required_claims': 'Check JWT signature, algorithm, issuer, audience, expiry and required claims.',
    'firebase_project_expiry_revocation_disabled_user_and_recovery': 'Check Firebase project, expiry, revocation, disabled users and recovery.',
    'oauth_pkce_state_replay_browser_binding_nonce_csrf_logout_expiry_redis_failure_and_recovery': 'Check OAuth proof, state replay, browser binding, nonce, CSRF, logout, expiry and Redis recovery.',
    'generated_unittest': 'Run the generated unit tests.',
    'parent_child_crud_cross_parent_isolation_and_delete_conflict': 'Check parent and child CRUD, parent isolation and delete conflicts.',
    'defaults_types_nullability_readonly_fields_partial_patch_dates_and_filters': 'Check defaults, types, nulls, read-only fields, partial updates, dates and filters.',
    'default_page_limit_offset_and_id_order': 'Check default pages, limits, offsets and ID order.',
    'batch_one_transaction_success_and_input_boundaries': 'Check successful batch writes and input boundaries.',
    'late_database_unique_conflict_rolls_back_parent_and_earlier_child_then_recovers': 'Check rollback after a late unique conflict and subsequent recovery.',
    'concurrent_parent_delete_and_child_create_no_orphans': 'Race parent deletion against child creation. Check for orphan rows.',
    'bounded_string_integer_and_finite_float_inputs': 'Check string and integer bounds and finite floats.',
    'real_process_restart_persists_rows': 'Restart the real service process and check saved rows.',
    'delete_children_then_parent': 'Delete children, then delete their parent.',
    'migration_downgrade_and_upgrade_again': 'Reverse the migration, then apply it again.',
    'unavailable_database_health_503': 'Return unhealthy status when the database is unavailable.',
    'successful_parent_and_children_share_one_database_transaction': 'Observe one transaction for the parent and all children.',
    'forced_database_conflict_after_parent_and_child_writes_rolls_back_all_rows': 'Force a database conflict after actual writes. Check complete rollback.',
    'same_valid_payload_succeeds_after_removing_database_fault': 'Remove the database fault. Repeat the valid request successfully.',
    'nonunique_values_can_repeat_within_and_across_parents': 'Permit repeated nonunique values within and across parents.',
    'every_field_patch_preserves_all_unsubmitted_values_and_other_parent_rows': 'Update each field. Preserve every omitted field and other parent row.',
    'pending_items_query_excludes_date_boundary_and_other_parent_rows': 'Exclude the date boundary and other parents from the pending query.',
    'failed_parent_delete_preserves_its_business_records': 'Keep business records after a rejected parent deletion.',
    'csv_import_validation_late_conflict_atomic_rollback_and_recovery': 'Check CSV import validation, late conflict, complete rollback and recovery.',
}
for kind in ('json_export', 'csv', 'gzip_export', 'decimal_summary', 'statistics'):
    LABELS[f'business_{kind}_content_and_numeric_or_null_semantics'] = (
        'Check ' + kind.replace('_', ' ') + ' content, numbers and null values.')
for kind in ('s3', 'gcs'):
    LABELS[f'{kind}_upload_download_checksum_metadata_idempotence_size_key_failure_and_recovery'] = (
        'Check ' + kind.upper() + ' object bytes, checksum, metadata, repeat requests, size, keys, failure and recovery.')


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    output = EVAL / 'APP-CHECKLIST.md'
    if output.exists():
        raise ValueError('preserve the existing checklist')
    gate = json.loads((EVAL / 'candidate-correctness.json').read_text())
    report = EVAL / 'rust-services.json'
    assert digest(report) == gate['reports_sha256']['rust-services.json']
    rows = json.loads(report.read_text())['results']
    prompts = [json.loads(line)['prompt'] for line in
               (ROOT / 'som-core/data/composed/regression.jsonl').read_text().splitlines()]
    lines = ['# Complete service checklist', '',
             'These checks ran against the exact candidate source.',
             'The first ten entries are the standard apps. The last four are boundary services.',
             'Identity and object services use local isolated providers.',
             'Only the identity and storage checks selected by each service apply.',
             'Every checked item below shares its entry\'s requirement, source and evidence hashes.',
             'The raw report records the original check IDs and full transaction observations.', '',
             f'Raw report SHA-256: `{digest(report)}`.',
             'Binding receipt: [candidate-correctness.json](candidate-correctness.json).', '']
    for row, prompt in zip(rows, prompts, strict=True):
        assert row['status'] == row['transactions']['status'] == row['business_journeys']['status'] == 'passed'
        assert row['source_sha256'] == row['transactions']['source_sha256'] == row['business_journeys']['source_sha256']
        evidence = Path(row['evidence_directory'])
        for name, expected in row['evidence_sha256'].items():
            assert digest(evidence / name) == expected
        capabilities = ', '.join(f'{name}: `{value}`' for name, value in prompt['capabilities'].items())
        lines += [f'## {row["index"] + 1}. {row["package"]}', '', capabilities + '.', '',
                  f'Requirement SHA-256: `{row["requirement_sha256"]}`.',
                  f'Source hashes: [rust-services.json](rust-services.json), result {row["index"]}.',
                  f'Check evidence: [{evidence.name}]({evidence}/events.jsonl).',
                  f'Evidence SHA-256: `{row["evidence_sha256"]["events.jsonl"]}`.', '']
        for checks in (row['checks'], row['transactions']['checks'], row['business_journeys']['checks']):
            for check in checks:
                lines.append('- [x] ' + LABELS[check])
        lines.append('')
    output.write_text('\n'.join(lines) + '\n')
    print(json.dumps({'checklist': str(output), 'services': len(rows)}))


if __name__ == '__main__':
    main()

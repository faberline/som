"""Inject faults in disposable databases and observe real HTTP compensation.

All triggers live in an explicitly named test database. The product does not
import this module, expose test routes, or change its authentication behavior.
"""

from concurrent.futures import ThreadPoolExecutor
import time
import uuid

import sqlalchemy as sa

from som_core.flow import MUTATIONS
from verify_related import payload


def wait_for(predicate, description, timeout=15):
    """Fail when an expected observation never arrives."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = predicate()
        if result:
            return result
        time.sleep(0.02)
    raise AssertionError('missing observation: ' + description)


class PostgresGate:
    """Pause a checkpoint only after the business transaction commits."""

    def __init__(self, backend, branch, container='branches'):
        assert backend.name.startswith('som_test_flow_'), backend.name
        assert branch is None or branch.isascii() and branch.isidentifier() and not branch.startswith('_')
        assert container in {'branches', 'values'}
        self.backend, self.branch = backend, branch
        self.container = container
        self.identifier = uuid.uuid4().int % (2 ** 31)
        self.holder = backend.engine.connect()

    def install(self):
        self.holder.execute(sa.text('SELECT pg_advisory_lock(:gate)'), {'gate': self.identifier})
        self.holder.commit()
        condition = ("NEW.record->>'state' = 'succeeded'" if self.branch is None else
                     f"NEW.record->>'state' = 'running' AND NEW.record->'{self.container}' ? '{self.branch}'")
        with self.backend.engine.begin() as connection:
            connection.exec_driver_sql('CREATE SEQUENCE _som_probe_once')
            connection.exec_driver_sql(f'''
CREATE FUNCTION _som_probe_checkpoint() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF {condition}
       AND nextval('_som_probe_once') = 1 THEN
        PERFORM pg_advisory_xact_lock({self.identifier});
        RAISE EXCEPTION 'isolated checkpoint failure' USING ERRCODE = 'P0001';
    END IF;
    RETURN NEW;
END;
$$''')
            connection.exec_driver_sql('CREATE TRIGGER _som_probe_checkpoint BEFORE UPDATE ON _som_flow_runs FOR EACH ROW EXECUTE FUNCTION _som_probe_checkpoint()')

    def waiting(self):
        with self.backend.engine.connect() as connection:
            return connection.scalar(sa.text("SELECT count(*) FROM pg_stat_activity WHERE datname=:name AND wait_event='advisory'"), {'name': self.backend.name})

    def release(self):
        self.holder.execute(sa.text('SELECT pg_advisory_unlock(:gate)'), {'gate': self.identifier})
        self.holder.commit()

    def close(self):
        self.release()
        self.holder.close()
        with self.backend.engine.begin() as connection:
            connection.exec_driver_sql('DROP TRIGGER IF EXISTS _som_probe_checkpoint ON _som_flow_runs')
            connection.exec_driver_sql('DROP FUNCTION IF EXISTS _som_probe_checkpoint()')
            connection.exec_driver_sql('DROP SEQUENCE IF EXISTS _som_probe_once')


class MongoGate:
    """Target one app and namespace on the task-owned replica set."""

    def __init__(self, backend, prompt, collection, command, mode, milliseconds, error=None):
        assert backend.name.startswith('som_test_flow_'), backend.name
        self.backend = backend
        self.data = {'appName': prompt['package'], 'namespace': backend.name + '.' + collection,
                     'failCommands': [command], 'blockConnection': True,
                     'blockTimeMS': milliseconds}
        if error is not None:
            self.data.update(errorCode=error, errorLabels=[])
        self.mode = mode

    def install(self):
        result = self.backend.client.admin.command({'configureFailPoint': 'failCommand', 'mode': self.mode, 'data': self.data})
        self.count = result['count']

    def waiting(self):
        result = self.backend.client.admin.command({'waitForFailPoint': 'failCommand', 'timesEntered': self.count + 1, 'maxTimeMS': 10000})
        assert result['ok'] == 1, result
        return True

    def release(self):
        self.backend.client.admin.command({'configureFailPoint': 'failCommand', 'mode': 'off'})

    def close(self):
        self.release()


class PostgresWriteGate(PostgresGate):
    """Hold an uncommitted insert without changing the product."""

    def __init__(self, backend, prompt):
        super().__init__(backend, 'unused')
        role, method = first_database_write(prompt['flow']['steps'])
        table = prompt['resources'][role]['table']
        assert table.isascii() and table.isidentifier() and not table.startswith('_')
        self.table = table
        self.method = 'UPDATE' if method == 'patch' else 'INSERT'

    def install(self):
        self.holder.execute(sa.text('SELECT pg_advisory_lock(:gate)'), {'gate': self.identifier})
        self.holder.commit()
        with self.backend.engine.begin() as connection:
            connection.exec_driver_sql('CREATE SEQUENCE _som_probe_once')
            connection.exec_driver_sql(f'''
CREATE FUNCTION _som_probe_checkpoint() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF nextval('_som_probe_once') = 1 THEN
        PERFORM pg_advisory_xact_lock({self.identifier});
    END IF;
    RETURN NEW;
END;
$$''')
            connection.exec_driver_sql(f'CREATE TRIGGER _som_probe_checkpoint BEFORE {self.method} ON "{self.table}" FOR EACH ROW EXECUTE FUNCTION _som_probe_checkpoint()')

    def close(self):
        self.release()
        self.holder.close()
        with self.backend.engine.begin() as connection:
            connection.exec_driver_sql(f'DROP TRIGGER IF EXISTS _som_probe_checkpoint ON "{self.table}"')
            connection.exec_driver_sql('DROP FUNCTION IF EXISTS _som_probe_checkpoint()')
            connection.exec_driver_sql('DROP SEQUENCE IF EXISTS _som_probe_once')


class PostgresReadGate:
    """Pause a resource read while leaving the lease table available."""

    def __init__(self, backend, prompt):
        assert backend.name.startswith('som_test_flow_'), backend.name
        table = prompt['resources']['parent']['table']
        assert table.isascii() and table.isidentifier() and not table.startswith('_')
        self.backend, self.table = backend, table
        self.holder = backend.engine.connect()

    def install(self):
        self.holder.exec_driver_sql(f'LOCK TABLE "{self.table}" IN ACCESS EXCLUSIVE MODE')

    def waiting(self):
        with self.backend.engine.connect() as connection:
            return connection.scalar(sa.text(
                "SELECT count(*) FROM pg_stat_activity WHERE datname=:name "
                "AND wait_event_type='Lock' AND wait_event='relation' "
                "AND query ILIKE :table"
            ), {'name': self.backend.name, 'table': '%' + self.table + '%'})

    def release(self):
        self.holder.rollback()

    def close(self):
        self.release()
        self.holder.close()


def resource_write_operations(nodes):
    """Read mutation requirements from the public flow, including both arms."""
    result = set()
    for node in nodes:
        if node['op'] in MUTATIONS:
            result.add(node['op'])
        elif node['op'] == 'transaction':
            result.update(resource_write_operations(node['steps']))
        elif node['op'] == 'if':
            result.update(resource_write_operations(node['then']))
            result.update(resource_write_operations(node['else']))
    return result


class PostgresHeartbeatFault:
    """Fail lease renewal once while allowing release and recovery."""

    def __init__(self, backend):
        assert backend.name.startswith('som_test_flow_'), backend.name
        self.backend = backend

    def install(self):
        with self.backend.engine.begin() as connection:
            connection.exec_driver_sql('CREATE SEQUENCE _som_probe_heartbeat_once')
            connection.exec_driver_sql('''
CREATE FUNCTION _som_probe_heartbeat() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.until > OLD.until
       AND nextval('_som_probe_heartbeat_once') = 1 THEN
        RAISE EXCEPTION 'isolated heartbeat failure' USING ERRCODE = 'P0001';
    END IF;
    RETURN NEW;
END;
$$''')
            connection.exec_driver_sql('CREATE TRIGGER _som_probe_heartbeat BEFORE UPDATE ON _som_flow_leases FOR EACH ROW EXECUTE FUNCTION _som_probe_heartbeat()')

    def close(self):
        with self.backend.engine.begin() as connection:
            connection.exec_driver_sql('DROP TRIGGER IF EXISTS _som_probe_heartbeat ON _som_flow_leases')
            connection.exec_driver_sql('DROP FUNCTION IF EXISTS _som_probe_heartbeat()')
            connection.exec_driver_sql('DROP SEQUENCE IF EXISTS _som_probe_heartbeat_once')


class MongoHeartbeatFault:
    """Refuse the old epoch's renewal while permitting recovery."""

    def __init__(self, backend, lease):
        assert backend.name.startswith('som_test_flow_'), backend.name
        self.backend, self.lease = backend, lease
        self.options = backend.db['_som_flow_leases'].options()

    def install(self):
        # Reconfiguring failCommand waits for its blocked insert. A validator
        # on this separate collection can reject renewal while that insert
        # remains in flight. Advancing the epoch still permits compensation.
        result = self.backend.db.command({
            'collMod': '_som_flow_leases',
            'validator': {'$or': [{'epoch': {'$gt': self.lease['epoch']}},
                                   {'until': {'$lte': self.lease['until']}}]},
            'validationLevel': 'strict', 'validationAction': 'error',
        })
        assert result['ok'] == 1, result

    def close(self):
        self.backend.db.command({
            'collMod': '_som_flow_leases',
            'validator': self.options.get('validator', {}),
            'validationLevel': self.options.get('validationLevel', 'strict'),
            'validationAction': self.options.get('validationAction', 'error'),
        })


def preceding_checkpoints(nodes):
    """Count public committed scopes and read results before a branch."""
    count = 0
    for node in nodes:
        if node['op'] == 'if':
            return count
        count += 1
    raise AssertionError('checkpoint test requires a branch')


def first_branch(nodes):
    """Use only the branch named in the public requirement."""
    for node in nodes:
        if node['op'] == 'if':
            return node['name']
        if node['op'] == 'transaction':
            branch = first_branch(node['steps'])
            if branch:
                return branch
    return None


def first_database_write(nodes):
    """Find a resource write from the public operation graph."""
    for node in nodes:
        operation = node['op']
        if operation == 'batch.create':
            return 'parent', 'create'
        if operation == 'workflow.import':
            return 'child', 'create'
        if operation in {'parent.create', 'child.create', 'parent.patch', 'child.patch'}:
            return tuple(operation.split('.'))
        for key in ('steps', 'then', 'else'):
            selected = first_database_write(node.get(key, []))
            if selected is not None:
                return selected
    return None


def checkpoint_target(nodes):
    """Pause the first control checkpoint after a committed business scope.

    A branch inside a transaction is not a committed checkpoint. Linear
    flows can use a following read result or the final success checkpoint.
    The physical row and journal checks must still prove the selected pause.
    """
    committed = False
    for index, node in enumerate(nodes):
        if committed and node['op'] != 'transaction':
            return {'name': node['name'], 'container': 'branches' if node['op'] == 'if' else 'values',
                    'skip': index}
        if node['op'] == 'transaction' and first_database_write(node['steps']) is not None:
            committed = True
    return {'name': None, 'container': 'values', 'skip': len(nodes)} if committed else None


def committed_changes(before, after):
    """Require a physical insert or update, including changes to existing rows."""
    result = []
    for role in ('parent', 'child'):
        def identity(row):
            return row['id'] if 'id' in row else row['_id']
        old = {identity(row): row for row in before[role]}
        for row in after[role]:
            identifier = identity(row)
            if identifier not in old or row != old[identifier]:
                result.append({'role': role, 'id': identifier, 'created': identifier not in old, 'row': row})
    assert result, ('no committed business write before the injected fault', before, after)
    return result


def require_restored(before, after):
    """Business fields and creation times must return to their old values.

    A compensated PATCH is another write. Its private revision and update
    timestamp can advance. Every other field and the exact row set must match.
    """
    def projected(rows):
        return [{key: value for key, value in row.items()
                 if key not in {'_som_revision', '_som_guard', 'updated_at'}} for row in rows]
    for role in ('parent', 'child'):
        assert projected(before[role]) == projected(after[role]), (role, before, after)
        old = {row.get('id', row.get('_id')): row for row in before[role]}
        for row in after[role]:
            previous = old[row.get('id', row.get('_id'))]
            assert row['_som_revision'] >= previous['_som_revision']
            if '_som_guard' in previous:
                assert row['_som_guard'] >= previous['_som_guard']
            assert row['updated_at'] >= previous['updated_at']


def run_id(response):
    body = response.json()
    if 'run_id' in body:
        return body['run_id']
    assert isinstance(body.get('detail'), dict) and 'run_id' in body['detail'], body
    return body['detail']['run_id']


def database_rows(backend):
    """Read the physical rows without using the app's journal as an answer."""
    return {role: backend.rows(role) for role in ('parent', 'child')}


def require_owner_routes(prompt, client, providers, result):
    """Check authentication and owner isolation on every flow route."""
    import httpx
    route = '/flows/' + prompt['flow']['name'] + '/runs'
    identifier = result['run_id']
    with httpx.Client(base_url=str(client.base_url), timeout=20) as outsider:
        for method, path, body, headers in (
                ('POST', route, {}, {'Idempotency-Key': 'unauthorized'}),
                ('GET', '/flow-runs/' + identifier, None, {}),
                ('POST', '/flow-runs/' + identifier + '/recover', None, {})):
            response = outsider.request(method, path, json=body, headers=headers)
            assert response.status_code == 401, response.text
        providers.login_other_owner(outsider)
        for method, path in (('GET', '/flow-runs/' + identifier),
                             ('POST', '/flow-runs/' + identifier + '/recover')):
            response = outsider.request(method, path)
            assert response.status_code == 404, response.text
    if prompt['capabilities']['auth'] == 'google_oauth':
        headers = dict(client.headers)
        headers.pop('x-csrf-token', None)
        with httpx.Client(base_url=str(client.base_url), headers=headers,
                         cookies=client.cookies, timeout=20) as browser:
            for path, body, extra in ((route, {}, {'Idempotency-Key': 'csrf-missing'}),
                                      ('/flow-runs/' + identifier + '/recover', None, {})):
                response = browser.post(path, json=body, headers=extra)
                assert response.status_code == 403, response.text
    return {'passed': True, 'checks': ['all_run_routes_require_identity', 'owner_status_and_recovery_isolation', 'applicable_oauth_csrf']}


def checkpoint_failures(prompt, backend, client, make_inputs):
    """Prove committed-write compensation and preservation of external edits."""
    if not resource_write_operations(prompt['flow']['steps']):
        return {'passed': True, 'applicable': False,
                'reason': 'the public flow has no business writes to compensate'}
    target_checkpoint = checkpoint_target(prompt['flow']['steps'])
    if target_checkpoint is None:
        return {'passed': False, 'pending': ['no_checkpoint_branch_in_this_structure']}
    route = '/flows/' + prompt['flow']['name'] + '/runs'
    records = []
    for phase in ('late_failure', 'revision_conflict'):
        before = database_rows(backend)
        key = 'fault-' + uuid.uuid4().hex
        inputs = make_inputs(phase)
        gate = (PostgresGate(backend, target_checkpoint['name'], target_checkpoint['container']) if prompt['capabilities']['database'] == 'postgresql'
                else MongoGate(backend, prompt, '_som_flow_runs', 'findAndModify',
                               {'skip': target_checkpoint['skip']}, 5000, 8))
        gate.install()
        try:
            with ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(client.post, route, json=inputs, headers={'Idempotency-Key': key})
                try:
                    wait_for(gate.waiting, 'post-commit checkpoint pause')
                    committed = database_rows(backend)
                    changed_rows = committed_changes(before, committed)
                    independent = None
                    if phase == 'revision_conflict':
                        def identifier(row):
                            return row['id'] if 'id' in row else row['_id']
                        changed = next((row for row in reversed(changed_rows) if row['created']), changed_rows[-1])
                        role, target = changed['role'], changed['row']
                        field = prompt['resources'][role]['fields'][0]
                        value = payload({'fields': [field]}, 987650)[field['name']]
                        path = (prompt['resources']['parent']['path'] + '/' + str(identifier(target)) if role == 'parent' else
                                prompt['resources']['parent']['path'] + '/' + str(target[prompt['relation']['foreign_key']])
                                + prompt['resources']['child']['path'] + '/' + str(identifier(target)))
                        response = client.patch(path, json={field['name']: value})
                        assert response.status_code == 200, response.text
                        independent = {'role': role, 'id': identifier(target), 'field': field['name'], 'value': value}
                finally:
                    gate.release()
                response = future.result(timeout=45)
            assert response.status_code == 503, response.text
            run_identifier = run_id(response)
            journal = backend.journal(key)
            assert journal['run_id'] == run_identifier
            after = database_rows(backend)
            if phase == 'late_failure':
                assert journal['state'] == 'compensated' and journal['effects'] == [], journal
                require_restored(before, after)
            else:
                assert journal['state'] == 'recovery_required', journal
                row = next(row for row in after[independent['role']] if (row['id'] if 'id' in row else row['_id']) == independent['id'])
                assert row[independent['field']] == independent['value'], row
                again = client.post('/flow-runs/' + run_identifier + '/recover')
                assert again.status_code == 503, again.text
                assert database_rows(backend) == after
            backend.no_orphans()
            records.append({'phase': phase, 'passed': True, 'inputs': inputs, 'key': key,
                            'run_id': run_identifier, 'http_status': response.status_code,
                            'before': before, 'committed_before_failure': committed, 'changed_rows': changed_rows,
                            'public_checkpoint': target_checkpoint,
                            'independent_write': independent, 'after': after, 'journal': journal})
        finally:
            gate.close()
    return {'passed': True, 'records': records}


def lease_checks(prompt, backend, client, make_inputs):
    """Keep normal writes alive and prevent an expired executor's commit."""
    route = '/flows/' + prompt['flow']['name'] + '/runs'
    records = []
    is_mongo = prompt['capabilities']['database'] == 'mongodb'
    first_write = first_database_write(prompt['flow']['steps'])
    read_only = first_write is None

    def lease_row(identifier):
        if is_mongo:
            return backend.db['_som_flow_leases'].find_one({'_id': identifier})
        with backend.engine.connect() as connection:
            row = connection.execute(sa.select(backend.leases).where(backend.leases.c.run_id == identifier)).mappings().one()
            return dict(row)

    for phase in ('heartbeat_transaction', 'stale_executor', 'heartbeat_failure'):
        key = 'lease-' + uuid.uuid4().hex
        before = database_rows(backend)
        role, method = ('parent', 'get') if read_only else first_write
        gate = (MongoGate(backend, prompt, prompt['resources'][role]['table'],
                          'find' if read_only else 'findAndModify' if method == 'patch' else 'insert', {'times': 1}, 8000) if is_mongo
                else (PostgresReadGate(backend, prompt) if read_only
                      else PostgresWriteGate(backend, prompt)))
        heartbeat_fault = None
        gate.install()
        try:
            with ThreadPoolExecutor(max_workers=3) as executor:
                future = executor.submit(client.post, route, json=make_inputs(phase), headers={'Idempotency-Key': key})
                try:
                    wait_for(gate.waiting, 'blocked resource read' if read_only else 'blocked business insert')
                    run = backend.journal(key)
                    identifier = run['run_id']
                    lease = lease_row(identifier)
                    assert lease is not None and lease['epoch'] == run['epoch']
                    claim_responses = []
                    if phase == 'heartbeat_failure':
                        heartbeat_fault = (MongoHeartbeatFault(backend, lease) if is_mongo
                                           else PostgresHeartbeatFault(backend))
                        heartbeat_fault.install()
                        response = future.result(timeout=12)
                        renewed = lease_row(identifier)
                        assert response.status_code == 503, response.text
                    else:
                        renewed = wait_for(lambda: (latest if (latest := lease_row(identifier))
                                                   and latest['until'] > lease['until'] + 4 else None),
                                           'independent five-second heartbeat', timeout=7)
                        assert renewed['epoch'] == lease['epoch']
                    if phase == 'stale_executor':
                        backend.expire(identifier)
                        attempts = [executor.submit(client.post, '/flow-runs/' + identifier + '/recover') for _ in range(2)]
                        for attempt in attempts:
                            reply = attempt.result(timeout=20)
                            assert reply.status_code in {202, 503}, reply.text
                            assert run_id(reply) == identifier
                            claim_responses.append({'status': reply.status_code, 'body': reply.json()})
                finally:
                    gate.release()
                response = future.result(timeout=45)
            journal = backend.journal(key)
            after = database_rows(backend)
            if phase == 'heartbeat_transaction':
                assert response.status_code == 200 and journal['state'] == 'succeeded', response.text
                if read_only:
                    assert after == before, (before, after)
                else:
                    committed_changes(before, after)
            else:
                assert response.status_code == 503 and journal['state'] == 'compensated', response.text
                assert journal['epoch'] == run['epoch'] + 1
                require_restored(before, after)
                assert journal['effects'] == [], journal
            backend.no_orphans()
            records.append({'phase': phase, 'passed': True, 'key': key, 'run_id': identifier,
                            'read_only': read_only,
                            'lease_before': lease, 'lease_after_heartbeat': renewed,
                            'concurrent_recovery': claim_responses, 'journal': journal,
                            'http_status': response.status_code, 'before': before, 'after': after})
        finally:
            if heartbeat_fault is not None:
                heartbeat_fault.close()
            gate.close()
    return {'passed': True, 'records': records}


def interrupted_forward(prompt, backend, client, server, make_inputs):
    """Kill only the owned app after a write, then recover without replay."""
    import httpx

    if not resource_write_operations(prompt['flow']['steps']):
        return {'passed': True, 'applicable': False,
                'reason': 'no committed business writes or compensation to interrupt'}
    target_checkpoint = checkpoint_target(prompt['flow']['steps'])
    if target_checkpoint is None:
        return {'passed': False, 'pending': ['no_committed_checkpoint_in_this_structure']}
    key = 'crash-' + uuid.uuid4().hex
    inputs = make_inputs()
    before = database_rows(backend)
    route = '/flows/' + prompt['flow']['name'] + '/runs'
    gate = (PostgresGate(backend, target_checkpoint['name'], target_checkpoint['container'])
            if prompt['capabilities']['database'] == 'postgresql'
            else MongoGate(backend, prompt, '_som_flow_runs', 'findAndModify',
                           {'skip': target_checkpoint['skip']},
                           5000, 8))
    gate.install()
    gate_closed = False
    try:
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(client.post, route, json=inputs,
                                     headers={'Idempotency-Key': key})
            try:
                wait_for(gate.waiting, 'committed write before process interruption')
                committed = database_rows(backend)
                changed_rows = committed_changes(before, committed)
                original = backend.journal(key)
                assert original['state'] == 'running' and original['effects'], original
                process = server.process
                assert process is not None and process.poll() is None
                assert process.args[0] == str(server.root / '.venv/bin/python')
                assert process.args[3] == prompt['package'] + '.app:app'
                process.kill()
                process.wait(timeout=10)
                gate.close()
                gate_closed = True
            finally:
                gate.release() if not gate_closed else None
            try:
                response = future.result(timeout=15)
            except httpx.HTTPError:
                disconnected = True
            else:
                raise AssertionError('killed process unexpectedly returned ' + response.text)
        durable = backend.journal(key)
        assert durable == original, (durable, original)
        assert database_rows(backend) == committed
        backend.expire(original['run_id'])
        server.start()
        unchanged = database_rows(backend)
        assert unchanged == committed
        repeated = client.post(route, json=inputs, headers={'Idempotency-Key': key})
        assert repeated.status_code == 202, repeated.text
        assert run_id(repeated) == original['run_id']
        assert database_rows(backend) == unchanged
        inverse_interruption = interrupt_compensation(prompt, backend, client, server, original, unchanged)
        recovered = client.post('/flow-runs/' + original['run_id'] + '/recover')
        assert run_id(recovered) == original['run_id'], recovered.text
        latest = backend.journal(key)
        recovery_attempts = [{'status': recovered.status_code, 'body': recovered.json(), 'journal': latest}]
        # MongoDB can retain a dead client's native transaction until its
        # lifetime expires. A 503 must retain the journal, then a later API
        # request must complete the same inverse without replaying forward.
        if prompt['capabilities']['database'] == 'mongodb':
            lifetime = backend.client.admin.command({'getParameter': 1, 'transactionLifetimeLimitSeconds': 1})['transactionLifetimeLimitSeconds']
            assert type(lifetime) is int and 0 < lifetime <= 120
            deadline = time.monotonic() + 2 * lifetime + 15
            while latest['state'] == 'recovery_required' and time.monotonic() < deadline:
                assert recovered.status_code == 503 and latest['effects'], recovered.text
                assert latest['inputs'] == original['inputs'] and latest['values'] == original['values']
                assert latest['branches'] == original['branches']
                time.sleep(5)
                recovered = client.post('/flow-runs/' + original['run_id'] + '/recover')
                assert run_id(recovered) == original['run_id'], recovered.text
                latest = backend.journal(key)
                recovery_attempts.append({'status': recovered.status_code, 'body': recovered.json(), 'journal': latest})
        assert latest['state'] == 'compensated' and latest['effects'] == [], latest
        assert latest['epoch'] == inverse_interruption['journal']['epoch'] + len(recovery_attempts)
        assert latest['values'] == original['values']
        assert latest['branches'] == original['branches']
        restored = database_rows(backend)
        require_restored(before, restored)
        again = client.post('/flow-runs/' + original['run_id'] + '/recover')
        assert again.json() == recovered.json()
        assert database_rows(backend) == restored
        status = client.get('/flow-runs/' + original['run_id'])
        assert status.status_code == 200 and status.json()['state'] == 'compensated'
        backend.no_orphans()
        return {'passed': True, 'key': key, 'run_id': original['run_id'],
                'process_disconnected': disconnected, 'inputs': inputs,
                'before': before, 'committed_before_kill': committed, 'changed_rows': changed_rows,
                'public_checkpoint': target_checkpoint,
                'journal_before': original, 'journal_after': latest,
                'compensation_interruption': inverse_interruption,
                'recovery_attempts': recovery_attempts,
                'recover_status': recovered.status_code,
                'checks': ['committed_rows_and_journal_survive_sigkill',
                           'restart_does_not_replay_forward',
                           'duplicate_key_returns_original_running_run',
                           'recovery_only_reverses_committed_effects',
                           'uncommitted_inverse_group_rolls_back_after_sigkill',
                           'repeated_recovery_does_not_replay']}
    finally:
        if not gate_closed:
            gate.close()
        if server.process is not None and server.process.poll() is not None:
            server.start()


class PostgresInverseGate(PostgresGate):
    """Hold a native inverse inside the selected resource transaction."""

    def __init__(self, backend, prompt, effect):
        super().__init__(backend, 'unused')
        self.table = prompt['resources'][effect['role']]['table']
        self.row_id = effect['row_id']
        assert self.table.isascii() and self.table.isidentifier()
        assert type(self.row_id) is int and self.row_id > 0

    def install(self):
        self.holder.execute(sa.text('SELECT pg_advisory_lock(:gate)'), {'gate': self.identifier})
        self.holder.commit()
        with self.backend.engine.begin() as connection:
            connection.exec_driver_sql(f'''
CREATE FUNCTION _som_probe_inverse() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF OLD.id = {self.row_id} THEN
        PERFORM pg_advisory_xact_lock({self.identifier});
    END IF;
    IF TG_OP = 'DELETE' THEN RETURN OLD; END IF;
    RETURN NEW;
END;
$$''')
            connection.exec_driver_sql(f'CREATE TRIGGER _som_probe_inverse BEFORE DELETE OR UPDATE ON "{self.table}" FOR EACH ROW EXECUTE FUNCTION _som_probe_inverse()')

    def close(self):
        self.release()
        self.holder.close()
        with self.backend.engine.begin() as connection:
            connection.exec_driver_sql(f'DROP TRIGGER IF EXISTS _som_probe_inverse ON "{self.table}"')
            connection.exec_driver_sql('DROP FUNCTION IF EXISTS _som_probe_inverse()')


def interrupt_compensation(prompt, backend, client, server, original, committed):
    """Kill an owned recovery before the inverse group and journal commit."""
    import httpx

    group = original['effects'][-1]['group']
    effects = [effect for effect in original['effects'] if effect['group'] == group]
    target = effects[0]
    table = prompt['resources'][target['role']]['table']
    command = 'findAndModify' if target['before'] else 'delete'
    gate = (PostgresInverseGate(backend, prompt, target)
            if prompt['capabilities']['database'] == 'postgresql'
            else MongoGate(backend, prompt, table, command, {'times': 1}, 10000))
    gate.install()
    try:
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(client.post, '/flow-runs/' + original['run_id'] + '/recover')
            try:
                wait_for(gate.waiting, 'native compensation transaction before commit')
                journal = backend.journal(original['key'])
                assert journal['state'] == 'compensating'
                assert journal['effects'] == original['effects'], (journal, original)
                process = server.process
                assert process is not None and process.poll() is None
                assert process.args[0] == str(server.root / '.venv/bin/python')
                assert process.args[3] == prompt['package'] + '.app:app'
                process.kill()
                process.wait(timeout=10)
            finally:
                gate.release()
            try:
                response = future.result(timeout=15)
            except httpx.HTTPError:
                disconnected = True
            else:
                raise AssertionError('killed recovery returned ' + response.text)
        wait_for(lambda: database_rows(backend) == committed, 'native inverse rollback after lost app')
        assert backend.journal(original['key']) == journal
        backend.expire(original['run_id'])
        server.start()
        assert database_rows(backend) == committed
        return {'passed': True, 'process_disconnected': disconnected, 'journal': journal,
                'transaction_group': group, 'target': target,
                'committed_before_inverse': committed, 'after_rollback': database_rows(backend)}
    finally:
        gate.close()
        if server.process is not None and server.process.poll() is not None:
            server.start()

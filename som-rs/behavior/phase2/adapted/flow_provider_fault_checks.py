# Adapted by som-rs/behavior/phase2/adapt.py from som-core/scripts/flow_provider_fault_checks.py (sha256 27b814508cc3cfc35f5f020fcd02f831f7fcfb40bc20fc4d8c74772993d6a58a). Do not edit.
"""Observe owned object versions after real app interruption and recovery.

Fault controls belong to the isolated provider process. This module reads
only public requirements, HTTP replies, journals and provider observations.
It never reads blocks, model outputs or expected product source.
"""

import base64
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import hashlib
import uuid

import boto3
from google.auth.credentials import AnonymousCredentials
from google.cloud import storage
import httpx

from flow_fault_checks import database_rows, require_restored, run_id, wait_for
from phase2_host import host_for


class Versions:
    """Read exact immutable versions through the local provider's SDK API."""

    def __init__(self, providers):
        self.kind = providers.caps['storage']
        self.bucket = providers.bucket
        self.manifest = providers.manifest
        if self.kind == 's3':
            self.client = boto3.client('s3', endpoint_url=self.manifest['s3_url'],
                                       region_name='us-east-1', aws_access_key_id='test',
                                       aws_secret_access_key='test')
        else:
            self.client = storage.Client(project='demo-som', credentials=AnonymousCredentials(),
                                         client_options={'api_endpoint': self.manifest['base_url']})

    def all(self, key):
        result = []
        if self.kind == 's3':
            pages = self.client.get_paginator('list_object_versions').paginate(Bucket=self.bucket, Prefix=key)
            for page in pages:
                for item in page.get('Versions', []):
                    if item['Key'] != key:
                        continue
                    version = item['VersionId']
                    response = self.client.get_object(Bucket=self.bucket, Key=key, VersionId=version)
                    with closing(response['Body']) as stream:
                        body = stream.read()
                    result.append({'version': version, 'metadata': response.get('Metadata', {}),
                                   'body': base64.b64encode(body).decode(),
                                   'sha256': hashlib.sha256(body).hexdigest()})
        else:
            for blob in self.client.list_blobs(self.bucket, prefix=key, versions=True, retry=None, timeout=5):
                if blob.name != key:
                    continue
                body = blob.download_as_bytes(retry=None, timeout=5)
                result.append({'version': str(blob.generation), 'metadata': blob.metadata or {},
                               'body': base64.b64encode(body).decode(),
                               'sha256': hashlib.sha256(body).hexdigest()})
        return result

    def independent(self, key, body=b'independent-new-version'):
        if self.kind == 's3':
            response = self.client.put_object(Bucket=self.bucket, Key=key, Body=body)
            return response['VersionId']
        blob = self.client.bucket(self.bucket).blob(key)
        blob.upload_from_string(body, retry=None, timeout=5)
        return str(blob.generation)

    def close(self):
        self.client.close()


def object_key(prompt, inputs):
    """Resolve the provider key from the public requirement, never a block."""
    keys = set()
    def visit(nodes):
        for node in nodes:
            if node['op'] == 'object.put':
                ref = node['args']['key']
                if 'input' in ref:
                    keys.add(inputs[ref['input']])
                elif 'literal' in ref:
                    keys.add(ref['literal'])
                else:
                    raise AssertionError('provider key requires a runtime observation')
            for arm in ('steps', 'then', 'else'):
                visit(node.get(arm, []))
    visit(prompt['flow']['steps'])
    assert len(keys) == 1, ('exactly one observed provider key required', keys)
    return next(iter(keys))


def existing_object_check(prompt, backend, client, observer, providers, make_inputs):
    """A later business failure must preserve a pre-existing equal object."""
    inputs = make_inputs('existing_equal_object')
    key = 'existing-key-' + uuid.uuid4().hex
    provider_key = object_key(prompt, inputs)
    gate = 'existing-' + uuid.uuid4().hex
    before = database_rows(backend)
    is_mongo = prompt['capabilities']['database'] == 'mongodb'
    old_validator = None
    if not is_mongo:
        with backend.engine.begin() as connection:
            connection.exec_driver_sql('CREATE SEQUENCE _som_probe_success_once')
            connection.exec_driver_sql('''
CREATE FUNCTION _som_probe_success() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.record->>'state' = 'succeeded'
       AND nextval('_som_probe_success_once') = 1 THEN
        RAISE EXCEPTION 'isolated final checkpoint failure' USING ERRCODE = 'P0001';
    END IF;
    RETURN NEW;
END;
$$''')
            connection.exec_driver_sql('CREATE TRIGGER _som_probe_success BEFORE UPDATE ON _som_flow_runs FOR EACH ROW EXECUTE FUNCTION _som_probe_success()')
    try:
        route = '/flows/' + prompt['flow']['name'] + '/runs'
        control(providers.manifest, faults=[{'provider': observer.kind, 'operation': 'put',
                                            'key': provider_key, 'when': 'before_commit', 'gate': gate}])
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(client.post, route, json=inputs,
                                     headers={'Idempotency-Key': key})
            try:
                observed = wait_for(lambda: next((item for item in events(providers.manifest)['events']
                                                  if item.get('gate') == gate
                                                  and item.get('event') == 'paused_before_commit'), None),
                                    'actual provider bytes before conditional create', timeout=5)
                body = base64.b64decode(observed['body'], validate=True)
                existing = observer.independent(provider_key, body)
                initial_versions = observer.all(provider_key)
                if is_mongo:
                    # Fail the actual success checkpoint, regardless of the
                    # number of branches, transactions, or prior checkpoints.
                    identifier = backend.journal(key)['run_id']
                    old_validator = backend.db['_som_flow_runs'].options().get('validator', {})
                    result = backend.db.command({'collMod': '_som_flow_runs',
                                                 'validator': {'$or': [{'_id': {'$ne': identifier}},
                                                                       {'record.state': {'$ne': 'succeeded'}}]},
                                                 'validationLevel': 'strict', 'validationAction': 'error'})
                    assert result['ok'] == 1, result
            finally:
                control(providers.manifest, release=[gate], faults=[])
            response = future.result(timeout=45)
        assert response.status_code == 503, response.text
        journal = backend.journal(key)
        assert journal['state'] == 'compensated' and not journal['effects'], journal
        require_restored(before, database_rows(backend))
        assert observer.all(provider_key) == initial_versions
        backend.no_orphans()
        return {'phase': 'existing_equal_object', 'passed': True, 'inputs': inputs,
                'key': key, 'existing_version': existing, 'initial_versions': initial_versions,
                'provider_request': observed,
                'final_versions': observer.all(provider_key), 'final_journal': journal,
                'before': before, 'after': database_rows(backend), 'response': response.json()}
    finally:
        control(providers.manifest, release=[gate], faults=[])
        if is_mongo:
            if old_validator is not None:
                backend.db.command({'collMod': '_som_flow_runs', 'validator': old_validator})
        else:
            with backend.engine.begin() as connection:
                connection.exec_driver_sql('DROP TRIGGER _som_probe_success ON _som_flow_runs')
                connection.exec_driver_sql('DROP FUNCTION _som_probe_success()')
                connection.exec_driver_sql('DROP SEQUENCE _som_probe_success_once')


def control(manifest, **action):
    response = httpx.post(manifest['base_url'] + '/__flow/control', json=action, timeout=15)
    response.raise_for_status()


def events(manifest):
    response = httpx.get(manifest['base_url'] + '/__flow/events', timeout=15)
    response.raise_for_status()
    return response.json()


def phase_events(observed, cursor):
    """Keep only this phase's native events, even when object keys repeat."""
    result = {}
    for kind in ('events', 'gcs'):
        assert len(observed[kind]) >= cursor[kind], 'provider observation log was reset'
        result[kind] = observed[kind][cursor[kind]:]
    return result


def kill_owned(server, prompt):
    """Terminate only the exact Uvicorn process started by this checker."""
    process = server.process
    assert process is not None and process.poll() is None
    assert host_for(server.root).is_product_process(process, prompt['package'])
    process.kill()
    process.wait(timeout=10)


def lost_connection(future):
    try:
        response = future.result(timeout=15)
    except httpx.HTTPError:
        return True
    raise AssertionError('interrupted app returned an unexpected reply: ' + response.text)


def pause_after_commit(prompt, backend, providers, client, server, inputs, key, operation):
    """Hold a provider acknowledgement, then kill the owned app."""
    provider = prompt['capabilities']['storage']
    provider_key = object_key(prompt, inputs)
    gate = 'provider-' + uuid.uuid4().hex
    control(providers.manifest, faults=[{'provider': provider, 'operation': operation,
                                       'key': provider_key, 'when': 'after_commit', 'gate': gate}])
    path = '/flows/' + prompt['flow']['name'] + '/runs'
    if operation == 'delete':
        path = '/flow-runs/' + backend.journal(key)['run_id'] + '/recover'
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(client.post, path, **({'json': inputs, 'headers': {'Idempotency-Key': key}}
                                                      if operation == 'put' else {}))
        try:
            observed = wait_for(lambda: next((item for item in events(providers.manifest)['events']
                                             if item.get('gate') == gate and item.get('event') == 'paused'), None),
                                'provider commit before lost acknowledgement', timeout=10)
            journal = backend.journal(key)
            committed = database_rows(backend)
            kill_owned(server, prompt)
        finally:
            control(providers.manifest, release=[gate], faults=[])
        disconnected = lost_connection(future)
    return {'event': observed, 'journal': journal, 'database': committed,
            'connection_lost': disconnected}


def provider_fault_checks(prompt, backend, client, server, providers, make_inputs):
    """Recover uncertain creates and deleted exact versions without replay."""
    if prompt['capabilities']['storage'] == 'none':
        return {'applicable': False, 'passed': True, 'records': []}
    from phase2_host import object_put_after_database_write
    if not object_put_after_database_write(prompt):
        return {'applicable': False, 'passed': True, 'records': [],
                'reason': 'the flow puts no object after a database write'}
    observer = Versions(providers)
    records = []
    try:
        for phase in ('create_reply_lost', 'new_independent_version',
                      'delete_reply_lost', 'provider_unavailable', 'concurrent_recovery'):
            cursor = {kind: len(records) for kind, records in events(providers.manifest).items()}
            before = database_rows(backend)
            inputs = make_inputs(phase)
            provider_key = object_key(prompt, inputs)
            key = 'provider-key-' + uuid.uuid4().hex
            first = pause_after_commit(prompt, backend, providers, client, server, inputs, key, 'put')
            journal = first['journal']
            identifier = journal['run_id']
            assert journal['state'] == 'running' and journal['effects'], journal
            effect = journal['effects'][-1]
            assert effect['key'] == provider_key and effect['uncertain'] and effect['version'] is None
            owned = [item for item in observer.all(provider_key)
                     if item['metadata'].get('som-run') == identifier
                     and item['metadata'].get('som-step') == effect['step']]
            assert len(owned) == 1, owned
            proven = owned[0]['version']
            assert first['database'] != before, 'this fault must follow a committed database write'
            assert backend.journal(key) == journal
            independent = observer.independent(provider_key) if phase == 'new_independent_version' else None
            backend.expire(identifier)
            server.start()
            assert database_rows(backend) == first['database']
            duplicate = client.post('/flows/' + prompt['flow']['name'] + '/runs', json=inputs,
                                    headers={'Idempotency-Key': key})
            assert duplicate.status_code == 202 and run_id(duplicate) == identifier, duplicate.text
            assert database_rows(backend) == first['database']
            interrupted = None
            unavailable = None
            if phase == 'delete_reply_lost':
                interrupted = pause_after_commit(prompt, backend, providers, client, server, inputs, key, 'delete')
                saved = interrupted['journal']['effects'][-1]
                assert saved['version'] == proven and saved['owned'] and not saved['uncertain'], saved
                assert not any(item['version'] == proven for item in observer.all(provider_key))
                assert interrupted['database'] == first['database']
                backend.expire(identifier)
                server.start()
            if phase == 'provider_unavailable':
                providers.control(faults={prompt['capabilities']['storage']: True})
                try:
                    reply = client.post('/flow-runs/' + identifier + '/recover')
                    assert reply.status_code == 503 and run_id(reply) == identifier, reply.text
                    unavailable = backend.journal(key)
                    assert unavailable['state'] == 'recovery_required' and unavailable['effects'][-1]['uncertain']
                    assert database_rows(backend) == first['database']
                finally:
                    providers.control(faults={})
            concurrent = None
            if phase == 'concurrent_recovery':
                with ThreadPoolExecutor(max_workers=4) as executor:
                    futures = [executor.submit(client.post, '/flow-runs/' + identifier + '/recover')
                               for _ in range(4)]
                    replies = [future.result(timeout=45) for future in futures]
                assert all(reply.status_code in {202, 503} for reply in replies), [reply.text for reply in replies]
                assert all(run_id(reply) == identifier for reply in replies)
                concurrent = [{'status': reply.status_code, 'body': reply.json()} for reply in replies]
            response = client.post('/flow-runs/' + identifier + '/recover')
            assert response.status_code == 503 and run_id(response) == identifier, response.text
            after = backend.journal(key)
            assert after['state'] == 'compensated' and after['effects'] == [], after
            assert after['epoch'] > journal['epoch']
            require_restored(before, database_rows(backend))
            remaining = observer.all(provider_key)
            assert not any(item['version'] == proven for item in remaining), remaining
            if independent:
                assert len(remaining) == 1 and remaining[0]['version'] == independent, remaining
                assert remaining[0]['body'] == base64.b64encode(b'independent-new-version').decode()
            else:
                assert remaining == [], remaining
            observed = phase_events(events(providers.manifest), cursor)
            if observer.kind == 's3':
                deletes = [item for item in observed['events'] if item.get('provider') == 's3'
                           and item.get('key') == provider_key and item.get('operation') == 'delete'
                           and item.get('event') is None]
                assert deletes and all(item['requested_version'] == [proven] for item in deletes), deletes
            else:
                deletes = [item for item in observed['gcs'] if item.get('key') == provider_key
                           and item.get('operation') == 'delete']
                assert deletes and all(str(item['generation']) == proven and item['condition'] == [proven]
                                       for item in deletes), deletes
            stable = database_rows(backend)
            repeated = client.post('/flow-runs/' + identifier + '/recover')
            assert repeated.status_code == 503 and repeated.json() == response.json(), repeated.text
            assert database_rows(backend) == stable and observer.all(provider_key) == remaining
            backend.no_orphans()
            records.append({'phase': phase, 'passed': True, 'key': key, 'inputs': inputs,
                            'before': before, 'create_interruption': first,
                            'proven_version': owned[0], 'independent_version': independent,
                            'delete_interruption': interrupted, 'unavailable_journal': unavailable,
                            'concurrent_replies': concurrent,
                            'final_journal': after, 'remaining_versions': remaining,
                            'exact_version_deletes': deletes, 'response': response.json()})
        records.append(existing_object_check(prompt, backend, client, observer, providers, make_inputs))
        return {'applicable': True, 'passed': True, 'records': records}
    finally:
        control(providers.manifest, faults=[])
        providers.control(faults={})
        observer.close()

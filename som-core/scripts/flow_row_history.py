"""Observe committed resource values independently of product journals.

SQL triggers and MongoDB change streams belong to disposable test databases.
No product source, block, DSL or model result supplies the expected row data.
"""

from copy import deepcopy
import time

import sqlalchemy as sa


class PostgresHistory:
    """Record exact resource images and transaction IDs in the database."""

    def __init__(self, backend):
        assert backend.name.startswith('som_test_flow_')
        self.backend = backend
        self.roles = {backend.prompt['resources'][role]['table']: role
                      for role in ('parent', 'child')}
        quote = backend.engine.dialect.identifier_preparer.quote
        with backend.engine.begin() as connection:
            connection.exec_driver_sql('''
CREATE TABLE _som_probe_row_history (
    ordinal bigserial PRIMARY KEY, resource text NOT NULL,
    operation text NOT NULL, image jsonb NOT NULL, xid bigint NOT NULL
)''')
            connection.exec_driver_sql('''
CREATE FUNCTION _som_probe_row_history() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    INSERT INTO _som_probe_row_history(resource, operation, image, xid)
    VALUES (TG_TABLE_NAME, lower(TG_OP), to_jsonb(NEW), txid_current());
    RETURN NEW;
END;
$$''')
            for table in self.roles:
                connection.exec_driver_sql(
                    f'CREATE TRIGGER _som_probe_row_history AFTER INSERT OR UPDATE ON {quote(table)} '
                    'FOR EACH ROW EXECUTE FUNCTION _som_probe_row_history()')

    def capture(self, request):
        with self.backend.engine.connect() as connection:
            start = connection.scalar(sa.text('SELECT coalesce(max(ordinal), 0) FROM _som_probe_row_history'))
        initial = {role: self.backend.rows(role) for role in ('parent', 'child')}
        response = request()
        with self.backend.engine.connect() as connection:
            rows = connection.execute(sa.text('''
SELECT resource, operation, image, xid FROM _som_probe_row_history
WHERE ordinal > :start ORDER BY ordinal'''), {'start': start}).mappings().all()
        events = [{'role': self.roles[row['resource']], 'operation': row['operation'],
                   'row': row['image'], 'transaction': str(row['xid'])} for row in rows]
        return response, initial, events

    def close(self):
        quote = self.backend.engine.dialect.identifier_preparer.quote
        with self.backend.engine.begin() as connection:
            for table in self.roles:
                connection.exec_driver_sql(f'DROP TRIGGER _som_probe_row_history ON {quote(table)}')
            connection.exec_driver_sql('DROP FUNCTION _som_probe_row_history()')
            connection.exec_driver_sql('DROP TABLE _som_probe_row_history')


class MongoHistory:
    """Reconstruct exact images from native inserts and scalar updates."""

    def __init__(self, backend):
        assert backend.name.startswith('som_test_flow_')
        self.backend = backend
        self.roles = {backend.prompt['resources'][role]['table']: role
                      for role in ('parent', 'child')}

    def capture(self, request):
        initial = {role: self.backend.rows(role) for role in ('parent', 'child')}
        images = {(role, row['_id']): deepcopy(row)
                  for role, rows in initial.items() for row in rows}
        pipeline = [{'$match': {'ns.coll': {'$in': list(self.roles)},
                               'operationType': {'$in': ['insert', 'update', 'replace', 'delete']}}}]
        events = []
        with self.backend.db.watch(pipeline, max_await_time_ms=100) as stream:
            response = request()
            quiet = 0
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                event = stream.try_next()
                if event is None:
                    quiet += 1
                    if quiet == 3:
                        break
                    continue
                quiet = 0
                role = self.roles[event['ns']['coll']]
                key = role, event['documentKey']['_id']
                operation = event['operationType']
                if operation in {'insert', 'replace'}:
                    images[key] = deepcopy(event['fullDocument'])
                elif operation == 'update':
                    changes = event['updateDescription']
                    # Resource fields are scalar. Nested SDK or journal data
                    # is never used to reconstruct a resource observation.
                    assert not changes.get('truncatedArrays')
                    assert all('.' not in name for name in changes['updatedFields'])
                    images[key].update(changes['updatedFields'])
                    for name in changes['removedFields']:
                        images[key].pop(name)
                    if set(changes['updatedFields']) <= {'_som_guard'} and not changes['removedFields']:
                        continue
                else:
                    raise AssertionError('successful flow unexpectedly deleted a resource')
                session, number = event.get('lsid', {}).get('id'), event.get('txnNumber')
                assert session is not None and number is not None, event
                events.append({'role': role, 'operation': 'update' if operation == 'replace' else operation,
                               'row': deepcopy(images[key]),
                               'transaction': bytes(session).hex() + ':' + str(number)})
        return response, initial, events

    def close(self):
        """Change stream closes after each observed request."""


def observe_rows(backend):
    """Select the actual isolated database's observation mechanism."""
    return (MongoHistory if backend.prompt['capabilities']['database'] == 'mongodb'
            else PostgresHistory)(backend)

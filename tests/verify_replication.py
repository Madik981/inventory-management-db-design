from contextlib import closing
import json
from pathlib import Path
import socket
import sys
import threading
import time
import uuid

import psycopg2
from psycopg2 import sql

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from inventory_client import InventoryClient, connection_settings, lsn_number


ROOT = Path(__file__).resolve().parents[1]
checks = 0


def connect(settings):
    connection = psycopg2.connect(**settings, connect_timeout=3, options='-c statement_timeout=5000')
    connection.autocommit = True
    return connection


def query(connection, statement, parameters=None):
    with connection.cursor() as cursor:
        cursor.execute(statement, parameters)
        return cursor.fetchone() if cursor.description else None


def check(condition, label):
    global checks
    if not condition:
        raise AssertionError(label)
    checks += 1
    print('PASS ' + label, flush=True)


def wait_until(predicate, timeout=15):
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() >= deadline:
            raise AssertionError('Replication condition did not become true before timeout')
        time.sleep(0.03)


def main():
    primary_settings = connection_settings()
    replica_settings = connection_settings(replica=True)
    database = 'replication_test_' + uuid.uuid4().hex
    with closing(connect(dict(primary_settings, dbname='postgres'))) as primary_admin, \
            closing(connect(dict(replica_settings, dbname='postgres'))) as replica_admin:
        check(not query(primary_admin, 'SELECT pg_is_in_recovery()')[0]
              and query(replica_admin, 'SELECT pg_is_in_recovery()')[0],
              'Separate primary and physical standby are running')
        print(query(primary_admin, 'SELECT version()')[0], flush=True)
        if query(replica_admin, 'SELECT pg_is_wal_replay_paused()')[0]:
            raise RuntimeError('Resume existing replica replay before running this suite')
        query(primary_admin, sql.SQL('CREATE DATABASE {}').format(sql.Identifier(database)))
        paused = False
        clients = []
        def new_client(*args, **kwargs):
            item = InventoryClient(*args, **kwargs)
            clients.append(item)
            return item
        try:
            primary_settings['dbname'] = database
            replica_settings['dbname'] = database
            with closing(connect(primary_settings)) as primary:
                for name in ('01_schema.sql', '02_operations.sql', '06_transfers.sql'):
                    query(primary, (ROOT / 'sql' / name).read_text(encoding='utf-8'))
                query(primary, '''
                    INSERT INTO inventory_app.products VALUES (17, 'IPHONE-17', 'iPhone 17');
                    INSERT INTO inventory_app.warehouses VALUES (1, 'Almaty', 'Almaty'), (2, 'Astana', 'Astana');
                    INSERT INTO inventory_app.inventory (warehouse_id, product_id, on_hand)
                    VALUES (1, 17, 10), (2, 17, 0);
                ''')
                target = query(primary, 'SELECT pg_current_wal_flush_lsn()::text')[0]
                wait_until(lambda: query(replica_admin, 'SELECT pg_last_wal_replay_lsn() >= %s::pg_lsn', (target,))[0])

                client = new_client(primary_settings, replica_settings, replica_wait_seconds=0.2)
                read = client.read_inventory(1, 17)
                check(read['source'] == 'replica' and read['stock']['available'] == 10,
                      'Caught-up inventory reads are served by the replica')
                with closing(connect(replica_settings)) as replica:
                    try:
                        query(replica, 'UPDATE inventory_app.inventory SET on_hand=0 WHERE warehouse_id=1')
                    except psycopg2.errors.ReadOnlySqlTransaction:
                        check(True, 'The replica rejects inventory writes')
                    else:
                        raise AssertionError('Replica accepted a write')

                query(replica_admin, 'SELECT pg_wal_replay_pause()')
                paused = True
                wait_until(lambda: query(replica_admin, "SELECT pg_get_wal_replay_pause_state() = 'paused'")[0])
                order = str(uuid.uuid4())
                held = client.reserve_order(order, [{'warehouse_id': 1, 'product_id': 17, 'quantity': 3}])
                with closing(connect(replica_settings)) as replica:
                    stale = query(replica, 'SELECT available FROM inventory_app.inventory WHERE warehouse_id=1 AND product_id=17')[0]
                check(held['status'] == 'active' and stale == 10,
                      'Primary commits a reservation while paused replica still displays old stock')
                read = client.read_inventory(1, 17, after=held['token'])
                check(read['source'] == 'primary' and read['reason'] == 'replica_lag'
                      and read['lag_bytes'] > 0 and read['stock']['available'] == 7,
                      'Post-commit read token falls back to primary during replay lag')
                print(json.dumps(read), flush=True)
                read = client.read_inventory(1, 17)
                check(read['source'] == 'primary' and read['stock']['available'] == 7,
                      'Ordinary browsing also detects lag without a caller token')
                read = client.read_inventory(1, 17, authoritative=True)
                check(read['source'] == 'primary' and read['reason'] == 'authoritative',
                      'Authoritative reads always use primary')
                try:
                    client.reserve_order(str(uuid.uuid4()), [{'warehouse_id': 1, 'product_id': 17, 'quantity': 8}])
                except psycopg2.errors.RaiseException:
                    check(True, 'Stale replica availability cannot authorize an overselling purchase')
                else:
                    raise AssertionError('Overselling request unexpectedly succeeded')

                timer = threading.Timer(0.15, query, (replica_admin, 'SELECT pg_wal_replay_resume()'))
                timer.start()
                try:
                    waiting_client = new_client(primary_settings, replica_settings, replica_wait_seconds=3)
                    read = waiting_client.read_inventory(1, 17, after=held['token'])
                finally:
                    timer.join(timeout=6)
                paused = query(replica_admin, 'SELECT pg_is_wal_replay_paused()')[0]
                check(not paused and read['source'] == 'replica' and read['stock']['available'] == 7,
                      'Bounded read wait uses replica once replay catches up')
                check(client.read_inventory(2, 999)['stock'] is None,
                      'Missing inventory is reported consistently')

                invalid = dict(held['token'], timeline=held['token']['timeline'] + 1)
                try:
                    client.read_inventory(1, 17, after=invalid)
                except ValueError:
                    check(True, 'Read tokens from another timeline are rejected')
                else:
                    raise AssertionError('Invalid timeline was accepted')

                wrong_replica = new_client(primary_settings, primary_settings)
                read = wrong_replica.read_inventory(1, 17)
                check(read['source'] == 'primary' and read['reason'] == 'replica_identity_mismatch',
                      'A writable server configured as replica is excluded')
                with socket.socket() as unavailable:
                    unavailable.bind(('127.0.0.1', 0))
                    offline_settings = dict(replica_settings, host='127.0.0.1', port=unavailable.getsockname()[1])
                    offline = new_client(primary_settings, offline_settings)
                    read = offline.read_inventory(1, 17)
                check(read['source'] == 'primary' and read['reason'] == 'replica_unavailable'
                      and read['stock']['available'] == 7,
                      'An unreachable replica falls back to primary')

                client.finish_reservation(order, 'release')
                transfer = str(uuid.uuid4())
                check(client.request_transfer(transfer, 1, 2, 17, 4)['status'] == 'requested',
                      'Transfer command runs through the primary client')
                sent = client.advance_transfer(transfer, 'dispatch')
                read = waiting_client.read_inventory(1, 17, after=sent['token'])
                destination = waiting_client.read_inventory(2, 17, after=sent['token'])
                check(read['source'] == 'replica' and read['stock']['available'] == 6
                      and destination['stock']['available'] == 0,
                      'Replica shows source deduction while goods are in transit')
                received = client.advance_transfer(transfer, 'receive')
                client.advance_transfer(transfer, 'receive')
                read = waiting_client.read_inventory(2, 17, after=received['token'])
                check(read['source'] == 'replica' and read['stock']['available'] == 4,
                      'Verified receipt reaches replica exactly once after duplicate callback')
                with closing(connect(replica_settings)) as replica:
                    events = query(replica, '''SELECT count(*) FROM inventory_app.inventory_events
                        WHERE operation_id=%s AND event_type LIKE 'transfer_%%' ''', (transfer,))[0]
                    status = query(replica, 'SELECT status FROM inventory_app.warehouse_transfers WHERE transfer_id=%s', (transfer,))[0]
                check(events == 2 and status == 'received',
                      'Transfer ledger and both movement events replicate together')
                check(lsn_number(received['token']['lsn']) >= lsn_number(sent['token']['lsn']),
                      'Write tokens are captured after durable commits in WAL order')
                print(f'All {checks} replication and routing checks passed.', flush=True)
        finally:
            for client in clients:
                client.close()
            if paused:
                query(replica_admin, 'SELECT pg_wal_replay_resume()')
            query(primary_admin, sql.SQL('DROP DATABASE {}').format(sql.Identifier(database)))


if __name__ == '__main__':
    main()

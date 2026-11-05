import argparse
from contextlib import contextmanager
from dataclasses import asdict, dataclass
import json
import math
import os
import random
import threading
import time

import psycopg2
from psycopg2.pool import ThreadedConnectionPool


def connection_settings(replica=False):
    prefix = 'REPLICA_' if replica else ''
    return {
        'host': os.getenv(prefix + 'PGHOST', '127.0.0.1'),
        'port': os.getenv(prefix + 'PGPORT', '55435' if replica else '55434'),
        'dbname': os.getenv('PGDATABASE', 'inventory_case2'),
        'user': os.getenv('PGUSER', 'postgres'),
        'password': os.getenv('PGPASSWORD', 'inventory_demo'),
    }


def lsn_number(lsn):
    high, low = lsn.split('/')
    return (int(high, 16) << 32) + int(low, 16)


@dataclass(frozen=True)
class ReadToken:
    system_id: str
    timeline: int
    lsn: str


class InventoryClient:
    def __init__(self, primary=None, replica=None, replica_wait_seconds=0.25,
                 pool_size=8, max_attempts=3):
        if not math.isfinite(replica_wait_seconds) or replica_wait_seconds < 0:
            raise ValueError('Replica wait must be a finite nonnegative number')
        self.primary = connection_settings() if primary is None else primary
        self.replica = connection_settings(replica=True) if replica is None else replica
        self.replica_wait_seconds = replica_wait_seconds
        if pool_size < 1 or max_attempts < 1:
            raise ValueError('Pool size and retry attempts must be positive')
        self.pool_size = pool_size
        self.max_attempts = max_attempts
        self._pools = {}
        self._pool_lock = threading.Lock()

    @contextmanager
    def _connection(self, settings):
        key = tuple(sorted(settings.items()))
        with self._pool_lock:
            if key not in self._pools:
                pool = ThreadedConnectionPool(
                    1, self.pool_size, **settings, connect_timeout=2,
                    options='-c statement_timeout=2000 -c lock_timeout=1000'
                )
                self._pools[key] = (pool, threading.BoundedSemaphore(self.pool_size))
            pool, slots = self._pools[key]
        if not slots.acquire(timeout=2):
            raise TimeoutError('Inventory connection pool is busy')
        connection = None
        discard = False
        try:
            connection = pool.getconn()
            connection.set_session(isolation_level='READ COMMITTED', autocommit=True)
            yield connection
        except (psycopg2.OperationalError, psycopg2.InterfaceError):
            discard = True
            raise
        finally:
            if connection is not None:
                try:
                    if not connection.closed:
                        connection.rollback()
                        connection.autocommit = True
                except psycopg2.Error:
                    discard = True
                pool.putconn(connection, close=discard or bool(connection.closed))
            slots.release()

    def close(self):
        with self._pool_lock:
            for pool, _ in self._pools.values():
                pool.closeall()
            self._pools.clear()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def _primary_token(self, connection):
        with connection.cursor() as cursor:
            cursor.execute('SELECT pg_is_in_recovery()')
            if cursor.fetchone()[0]:
                raise RuntimeError('The configured primary is a standby')
            cursor.execute('''
                SELECT (pg_control_system()).system_identifier::text,
                       (pg_control_checkpoint()).timeline_id,
                       pg_current_wal_flush_lsn()::text
            ''')
            return ReadToken(*cursor.fetchone())

    def _write(self, statement, parameters):
        for attempt in range(self.max_attempts):
            try:
                return self._write_once(statement, parameters)
            except psycopg2.Error as error:
                transient = error.pgcode in ('40001', '40P01', '55P03', '57014')
                transient = transient or isinstance(error, (psycopg2.OperationalError, psycopg2.InterfaceError))
                if not transient or attempt + 1 == self.max_attempts:
                    raise
                time.sleep(min(0.5, 0.05 * 2 ** attempt) + random.uniform(0, 0.025))

    def _write_once(self, statement, parameters):
        with self._connection(self.primary) as connection:
            connection.autocommit = False
            with connection:
                with connection.cursor() as cursor:
                    cursor.execute('SET LOCAL synchronous_commit = on')
                    cursor.execute('SELECT pg_is_in_recovery()')
                    if cursor.fetchone()[0]:
                        raise RuntimeError('The configured primary is a standby')
                    cursor.execute(statement, parameters)
                    status = cursor.fetchone()[0]
            connection.autocommit = True
            return {'status': status, 'token': asdict(self._primary_token(connection))}

    def reserve_order(self, order_id, items):
        return self._write('SELECT inventory_app.reserve_order(%s, %s::jsonb)',
                           (str(order_id), json.dumps(items)))

    def finish_reservation(self, order_id, action):
        return self._write('SELECT inventory_app.finish_reservation(%s, %s)',
                           (str(order_id), action))

    def adjust_stock(self, operation_id, warehouse_id, product_id, delta, reason):
        return self._write('SELECT inventory_app.adjust_stock(%s, %s, %s, %s, %s)',
                           (str(operation_id), warehouse_id, product_id, delta, reason))

    def adjust_stocks(self, operations):
        if not isinstance(operations, list) or not 1 <= len(operations) <= 100:
            raise ValueError('A stock batch must contain between 1 and 100 operations')
        return self._write('''
            SELECT jsonb_agg(inventory_app.adjust_stock(
                x.operation_id, x.warehouse_id, x.product_id, x.delta, x.reason))
            FROM (SELECT * FROM jsonb_to_recordset(%s::jsonb)
                  AS item(operation_id uuid, warehouse_id integer, product_id bigint, delta bigint, reason text)
                  ORDER BY warehouse_id, product_id, operation_id) x
        ''', (json.dumps(operations),))

    def request_transfer(self, transfer_id, source, destination, product_id, quantity):
        return self._write('SELECT inventory_app.request_transfer(%s, %s, %s, %s, %s)',
                           (str(transfer_id), source, destination, product_id, quantity))

    def advance_transfer(self, transfer_id, action):
        return self._write('SELECT inventory_app.advance_transfer(%s, %s)',
                           (str(transfer_id), action))

    def _stock(self, connection, warehouse_id, product_id):
        with connection.cursor() as cursor:
            cursor.execute('''
                SELECT row_to_json(stock) FROM (
                    SELECT warehouse_id, product_id, on_hand, reserved, available, version
                    FROM inventory_app.inventory
                    WHERE warehouse_id = %s AND product_id = %s
                ) AS stock
            ''', (warehouse_id, product_id))
            row = cursor.fetchone()
            return row[0] if row else None

    def read_inventory(self, warehouse_id, product_id, authoritative=False, after=None,
                       allow_stale=False):
        if allow_stale:
            if authoritative or after is not None:
                raise ValueError('Advisory browsing cannot provide an authoritative or token-bound read')
            with self._connection(self.replica) as replica:
                with replica.cursor() as cursor:
                    cursor.execute('SELECT pg_is_in_recovery()')
                    if not cursor.fetchone()[0]:
                        raise RuntimeError('Advisory endpoint is not a standby')
                return {'source': 'replica', 'reason': 'advisory_stale_allowed',
                        'lag_bytes': None, 'freshness': 'not_guaranteed',
                        'stock': self._stock(replica, warehouse_id, product_id)}
        with self._connection(self.primary) as primary:
            target = self._primary_token(primary)
            if after is not None:
                previous = ReadToken(**after)
                if (previous.system_id, previous.timeline) != (target.system_id, target.timeline):
                    raise ValueError('Read token belongs to a different cluster or timeline')
                if lsn_number(previous.lsn) > lsn_number(target.lsn):
                    raise ValueError('Primary has not durably reached the read token')
            reason = 'authoritative'
            lag_bytes = None
            if not authoritative:
                reason = 'replica_lag'
                try:
                    with self._connection(self.replica) as replica:
                        deadline = time.monotonic() + self.replica_wait_seconds
                        with replica.cursor() as cursor:
                            while True:
                                cursor.execute('''
                                    SELECT pg_is_in_recovery(),
                                           (pg_control_system()).system_identifier::text,
                                           (pg_control_checkpoint()).timeline_id,
                                           pg_last_wal_replay_lsn()::text
                                ''')
                                recovery, system_id, timeline, replay = cursor.fetchone()
                                if not recovery or (system_id, timeline) != (target.system_id, target.timeline):
                                    reason = 'replica_identity_mismatch'
                                    break
                                if replay is not None:
                                    lag_bytes = max(0, lsn_number(target.lsn) - lsn_number(replay))
                                    if lag_bytes == 0:
                                        stock = self._stock(replica, warehouse_id, product_id)
                                        return {'source': 'replica', 'reason': 'caught_up',
                                                'lag_bytes': 0, 'stock': stock}
                                remaining = deadline - time.monotonic()
                                if remaining <= 0:
                                    break
                                time.sleep(min(0.02, remaining))
                except psycopg2.Error:
                    reason = 'replica_unavailable'
            return {'source': 'primary', 'reason': reason, 'lag_bytes': lag_bytes,
                    'stock': self._stock(primary, warehouse_id, product_id)}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('warehouse_id', type=int)
    parser.add_argument('product_id', type=int)
    parser.add_argument('--authoritative', action='store_true')
    parser.add_argument('--wait', type=float, default=0.25)
    parser.add_argument('--after', type=json.loads)
    parser.add_argument('--allow-stale', action='store_true')
    args = parser.parse_args()
    with InventoryClient(replica_wait_seconds=args.wait) as client:
        result = client.read_inventory(args.warehouse_id, args.product_id,
                                       authoritative=args.authoritative, after=args.after,
                                       allow_stale=args.allow_stale)
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()

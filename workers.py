import argparse
import json
import os
import signal
import threading
import uuid

import psycopg2
from psycopg2.extras import RealDictCursor

from inventory_client import InventoryClient


def sink_settings():
    return {
        'host': os.getenv('SINK_PGHOST', '127.0.0.1'),
        'port': os.getenv('SINK_PGPORT', '55436'),
        'dbname': os.getenv('SINK_PGDATABASE', 'inventory_projection'),
        'user': os.getenv('SINK_PGUSER', 'projection_owner'),
        'password': os.getenv('SINK_PGPASSWORD', 'projection_demo'),
    }


def expire_once(client):
    with client._connection(client.primary) as connection:
        with connection.cursor() as cursor:
            cursor.execute('SELECT inventory_app.expire_one()')
            return cursor.fetchone()[0]


def deliver_once(client, sink=None, source=None, after_delivery=None):
    claim = str(uuid.uuid4())
    with client._connection(client.primary) as connection:
        with connection.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute('SELECT * FROM inventory_app.claim_events(%s, 100)', (claim,))
            events = cursor.fetchall()
    if not events:
        return 0
    with client._connection(sink or sink_settings()) as connection:
        connection.autocommit = False
        with connection:
            with connection.cursor() as cursor:
                for event in events:
                    cursor.execute('SELECT inventory_projection.apply_event(%s,%s,%s,%s,%s,%s,%s,%s)', (
                        source or client.primary['dbname'], event['operation_id'],
                        event['warehouse_id'], event['product_id'], event['event_type'],
                        event['on_hand_delta'], event['reserved_delta'], event['inventory_version']
                    ))
    if after_delivery is not None:
        after_delivery()
    with client._connection(client.primary) as connection:
        with connection.cursor() as cursor:
            cursor.execute('SELECT inventory_app.acknowledge_events(%s,%s)',
                           (claim, [event['event_id'] for event in events]))
            return cursor.fetchone()[0]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('mode', choices=('expiry', 'outbox'))
    parser.add_argument('--once', action='store_true')
    args = parser.parse_args()
    stopped = threading.Event()
    for signum in (signal.SIGINT, signal.SIGTERM):
        signal.signal(signum, lambda *_: stopped.set())
    with InventoryClient(pool_size=2) as client:
        while not stopped.is_set():
            try:
                result = expire_once(client) if args.mode == 'expiry' else deliver_once(client)
                if result:
                    print(json.dumps({'worker': args.mode, 'processed': str(result)}), flush=True)
                if args.once:
                    return
                if not result:
                    stopped.wait(0.25)
            except (psycopg2.Error, TimeoutError) as error:
                print(json.dumps({'worker': args.mode, 'retry': True,
                                  'error': type(error).__name__, 'sqlstate': getattr(error, 'pgcode', None)}), flush=True)
                if args.once:
                    raise
                stopped.wait(1)


if __name__ == '__main__':
    main()

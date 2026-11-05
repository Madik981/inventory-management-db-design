from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import threading
import time
import uuid

import psycopg2

from support import database, connect, query
from inventory_client import InventoryClient, connection_settings
from workers import expire_once, deliver_once, sink_settings

checks = 0


def check(condition, label):
    global checks
    if not condition:
        raise AssertionError(label)
    checks += 1
    print('PASS ' + label, flush=True)


def main():
    with database() as (settings, admin), closing(connect(sink_settings())) as sink:
        service = dict(settings, user='inventory_service', password='inventory_service_demo')
        replica = dict(connection_settings(replica=True), dbname=settings['dbname'])
        with InventoryClient(service, replica, pool_size=4) as client:
            with client._connection(service) as c:
                pid1 = query(c, 'SELECT pg_backend_pid()')[0][0]
            with client._connection(service) as c:
                pid2 = query(c, 'SELECT pg_backend_pid()')[0][0]
                try:
                    query(c, 'UPDATE inventory_app.inventory SET on_hand=0')
                except psycopg2.errors.InsufficientPrivilege:
                    check(True, 'Application role cannot mutate stock tables directly')
                else:
                    raise AssertionError('Direct mutation was allowed')
            check(pid1 == pid2, 'Connection pool reuses a database session')
            order = str(uuid.uuid4())
            result = client.reserve_order(order, [{'warehouse_id':1,'product_id':1,'quantity':2}])
            check(result['status'] == 'active', 'Restricted application role can reserve and receive a WAL token')
            try:
                client.read_inventory(1, 1, after=result['token'], allow_stale=True)
            except ValueError:
                check(True, 'Advisory reads cannot masquerade as token-bound fresh reads')
            else:
                raise AssertionError('Conflicting read modes accepted')
            client.finish_reservation(order, 'release')

            orders = [str(uuid.uuid4()) for _ in range(12)]
            for order in orders:
                client.reserve_order(order, [{'warehouse_id':1,'product_id':1,'quantity':1}])
            query(admin, "UPDATE inventory_app.reservations SET expires_at=clock_timestamp()-interval '1 second' WHERE status='active'")
            with ThreadPoolExecutor(max_workers=4) as pool:
                expired = list(pool.map(lambda _: expire_once(client), range(16)))
            check(len(set(x for x in expired if x)) == 12,
                  'Concurrent expiry workers process every due reservation exactly once')
            check(query(admin, 'SELECT reserved FROM inventory_app.inventory')[0][0] == 0,
                  'Expiry releases all held quantities without negative counters')
            check(expire_once(client) is None, 'An idle expiry worker returns without changing stock')

            with closing(connect(settings)) as blocker:
                blocker.autocommit = False
                query(blocker, 'SELECT * FROM inventory_app.inventory FOR UPDATE')
                release = threading.Timer(1.25, blocker.commit)
                release.start()
                operation = str(uuid.uuid4())
                try:
                    result = client.adjust_stock(operation, 1, 1, 3, 'Retry test')
                finally:
                    release.join()
                check(result['status'] == 'applied', 'A lock timeout retries the whole operation successfully')
                check(query(admin, 'SELECT on_hand FROM inventory_app.inventory')[0][0] == 103,
                      'Retry after lock timeout applies the stock delta once')

            def crash():
                raise RuntimeError('Simulated crash after sink commit before source acknowledgement')
            try:
                deliver_once(client, after_delivery=crash)
            except RuntimeError:
                pass
            before = query(sink, 'SELECT count(*) FROM inventory_projection.inbox WHERE source=%s', (settings['dbname'],))[0][0]
            check(before > 0 and query(admin, 'SELECT count(*) FROM inventory_app.inventory_events WHERE published_at IS NOT NULL')[0][0] == 0,
                  'Consumer commit survives a lost publisher acknowledgement')
            query(admin, "UPDATE inventory_app.inventory_events SET delivery_until=clock_timestamp()-interval '1 second'")
            delivered = deliver_once(client)
            after = query(sink, 'SELECT count(*) FROM inventory_projection.inbox WHERE source=%s', (settings['dbname'],))[0][0]
            check(delivered == before == after, 'Expired delivery leases replay events without duplicate consumer effects')
            change = query(sink, 'SELECT on_hand_change,reserved_change FROM inventory_projection.movements WHERE source=%s', (settings['dbname'],))[0]
            check(change == (3, 0), 'Durable inbox and movement totals commit atomically')

            batch = [{'operation_id':str(uuid.uuid4()),'warehouse_id':1,'product_id':1,'delta':2,'reason':'Batch test'}]
            client.adjust_stocks(batch)
            client.adjust_stocks(batch)
            check(query(admin,'SELECT on_hand FROM inventory_app.inventory')[0][0] == 105,
                  'The batched client preserves operation-level idempotency')
            batch = [dict(batch[0],operation_id=str(uuid.uuid4())),
                     dict(batch[0],operation_id=str(uuid.uuid4()),product_id=999)]
            try:
                client.adjust_stocks(batch)
            except psycopg2.Error:
                check(query(admin,'SELECT on_hand FROM inventory_app.inventory')[0][0] == 105,
                      'A failing stock batch rolls back all preceding adjustments')
            else:
                raise AssertionError('Invalid batch was accepted')

            source = settings['dbname'] + '_reordered'
            for version, delta in ((3, 7), (1, 2)):
                query(sink, 'SELECT inventory_projection.apply_event(%s,%s,1,1,%s,%s,0,%s)',
                      (source, str(uuid.uuid4()), 'adjusted', delta, version))
            check(query(sink, 'SELECT on_hand_change,highest_version FROM inventory_projection.movements WHERE source=%s', (source,))[0] == (9,3),
                  'Out-of-order delta events retain every movement instead of dropping older versions')
            event = query(admin, 'SELECT operation_id,event_type,on_hand_delta,reserved_delta,inventory_version FROM inventory_app.inventory_events LIMIT 1')[0]
            try:
                query(sink, 'SELECT inventory_projection.apply_event(%s,%s,1,1,%s,%s,%s,%s)',
                      (settings['dbname'], event[0], event[1], event[2]+1, event[3], event[4]))
            except psycopg2.errors.RaiseException:
                check(True, 'A duplicate event identity with changed data is rejected')
            else:
                raise AssertionError('Changed event was accepted')
            query(sink, 'DELETE FROM inventory_projection.inbox WHERE source IN (%s,%s)', (settings['dbname'],source))
            query(sink, 'DELETE FROM inventory_projection.movements WHERE source IN (%s,%s)', (settings['dbname'],source))
    print(f'All {checks} operational checks passed.', flush=True)


if __name__ == '__main__':
    main()

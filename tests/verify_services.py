from contextlib import closing
import subprocess
import time
import uuid

from support import ROOT, connect, query
from inventory_client import InventoryClient
from workers import sink_settings


def wait_for(predicate, timeout=50):
    deadline=time.monotonic()+timeout
    while not predicate():
        if time.monotonic()>=deadline:
            raise AssertionError('Worker did not reach the expected state')
        time.sleep(.2)


def compose(*arguments):
    subprocess.run(['docker','compose',*arguments],cwd=ROOT,check=True,stdout=subprocess.DEVNULL,timeout=60)


def main():
    product=10**12+uuid.uuid4().int % 10**12
    order, operation=str(uuid.uuid4()),str(uuid.uuid4())
    checks=0
    def check(condition,label):
        nonlocal checks
        if not condition:
            raise AssertionError(label)
        checks+=1
        print('PASS '+label,flush=True)
    with closing(connect()) as admin, InventoryClient() as client:
        query(admin,'INSERT INTO inventory_app.products VALUES(%s,%s,%s)',(product,'WORKER-'+str(product),'Worker verification'))
        query(admin,'INSERT INTO inventory_app.inventory(warehouse_id,product_id,on_hand) VALUES(1,%s,10)',(product,))
        try:
            client.reserve_order(order,[{'warehouse_id':1,'product_id':product,'quantity':2}])
            query(admin,"UPDATE inventory_app.reservations SET expires_at=statement_timestamp()-interval '1 second' WHERE order_id=%s",(order,))
            wait_for(lambda:query(admin,'SELECT status FROM inventory_app.reservations WHERE order_id=%s',(order,))[0][0]=='expired')
            check(query(admin,'SELECT reserved FROM inventory_app.inventory WHERE product_id=%s',(product,))[0][0]==0,
                  'The deployed expiry process automatically releases an expired reservation')
            wait_for(lambda:query(admin,'SELECT count(*) FROM inventory_app.inventory_events WHERE product_id=%s AND published_at IS NULL',(product,))[0][0]==0)
            check(True,'The deployed relay automatically acknowledges committed reservation events')
            compose('stop','projection')
            try:
                check(client.adjust_stock(operation,1,product,3,'Projection outage')['status']=='applied',
                      'Inventory writes succeed independently of an unavailable projection service')
                check(query(admin,'SELECT count(*) FROM inventory_app.inventory_events WHERE operation_id=%s AND published_at IS NULL',(operation,))[0][0]==1,
                      'An undelivered movement remains durable in the outbox during consumer outage')
            finally:
                compose('start','projection')
            wait_for(lambda:query(admin,'SELECT count(*) FROM inventory_app.inventory_events WHERE product_id=%s AND published_at IS NULL',(product,))[0][0]==0)
            with closing(connect(sink_settings())) as sink:
                row=query(sink,'SELECT on_hand_change,reserved_change FROM inventory_projection.movements WHERE source=%s AND product_id=%s',
                          (client.primary['dbname'],product))[0]
                check(row==(3,0),'The deployed relay catches up after consumer recovery with correct movement totals')
        finally:
            with closing(connect(sink_settings())) as sink:
                query(sink,'DELETE FROM inventory_projection.inbox WHERE product_id=%s',(product,))
                query(sink,'DELETE FROM inventory_projection.movements WHERE product_id=%s',(product,))
            query(admin,'DELETE FROM inventory_app.inventory_events WHERE product_id=%s',(product,))
            query(admin,'DELETE FROM inventory_app.stock_adjustments WHERE product_id=%s',(product,))
            query(admin,'DELETE FROM inventory_app.reservation_items WHERE order_id=%s',(order,))
            query(admin,'DELETE FROM inventory_app.reservations WHERE order_id=%s',(order,))
            query(admin,'DELETE FROM inventory_app.inventory WHERE product_id=%s',(product,))
            query(admin,'DELETE FROM inventory_app.products WHERE product_id=%s',(product,))
    print(f'All {checks} deployed-service checks passed.',flush=True)


if __name__=='__main__':
    main()

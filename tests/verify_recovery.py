from contextlib import closing
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
import subprocess
import sys
import time
import uuid

import psycopg2
from psycopg2 import sql

from support import ROOT, database, connect, query
from inventory_client import InventoryClient, connection_settings
sys.path.insert(0,str(ROOT/'ops'))
from backup import backup, restore

checks=0


def check(condition,label):
    global checks
    if not condition:
        raise AssertionError(label)
    checks+=1
    print('PASS '+label,flush=True)


def compose(*arguments):
    subprocess.run(['docker','compose',*arguments],cwd=ROOT,check=True,stdout=subprocess.DEVNULL,timeout=90)


def wait_ready(settings, synchronous=True):
    deadline=time.monotonic()+60
    while time.monotonic()<deadline:
        try:
            with closing(connect(settings)) as c:
                if not synchronous or query(c,"SELECT count(*) FROM pg_stat_replication WHERE sync_state='sync' AND state='streaming'")[0][0]:
                    return
        except psycopg2.Error:
            pass
        time.sleep(.2)
    raise AssertionError('Database did not recover within 60 seconds')


def main():
    evidence={'timestamp_utc':datetime.now(timezone.utc).isoformat()}
    primary_settings=connection_settings()
    backup_path=ROOT/'backups'/('recovery_'+uuid.uuid4().hex+'.dump')
    with database('inventory_recovery_') as (settings, admin):
        replica=dict(connection_settings(replica=True),dbname=settings['dbname'])
        order=str(uuid.uuid4())
        with InventoryClient(settings,replica,replica_wait_seconds=5) as client:
            held=client.reserve_order(order,[{'warehouse_id':1,'product_id':1,'quantity':2}])
            client.read_inventory(1,1,after=held['token'])
        evidence['backup_sha256']=backup(settings['dbname'],backup_path)
        restored='inventory_restored_'+uuid.uuid4().hex
        start=time.monotonic()
        restore(restored,backup_path)
        evidence['restore_seconds']=round(time.monotonic()-start,3)
        try:
            with closing(connect(dict(settings,dbname=restored))) as c:
                check(query(c,'SELECT on_hand,reserved FROM inventory_app.inventory')[0]==(100,2),
                      'Verified backup restores inventory and active reservations into a new database')
                check(query(c,'SELECT count(*) FROM inventory_app.inventory_events')[0][0]==1,
                      'Backup restoration preserves the committed event ledger')
            with closing(connect(dict(settings,dbname=restored,user='inventory_service',password='inventory_service_demo'))) as c:
                try:
                    query(c,'UPDATE inventory_app.inventory SET on_hand=0')
                except psycopg2.errors.InsufficientPrivilege:
                    check(True,'Restoration reapplies the restricted application permissions')
                else:
                    raise AssertionError('Restored permissions are unsafe')
        finally:
            query(admin,sql.SQL('DROP DATABASE {}').format(sql.Identifier(restored)))

        start=time.monotonic()
        compose('kill','-s','SIGKILL','db')
        try:
            with InventoryClient(settings,replica) as client:
                advisory=client.read_inventory(1,1,allow_stale=True)
                check(advisory['stock']['available']==98 and advisory['freshness']=='not_guaranteed',
                      'Explicit advisory browsing remains available while the primary is down')
        finally:
            compose('start','db')
            wait_ready(primary_settings)
        evidence['primary_crash_recovery_seconds']=round(time.monotonic()-start,3)
        with closing(connect(settings)) as fresh:
            check(query(fresh,'SELECT on_hand,reserved FROM inventory_app.inventory')[0]==(100,2),
                  'A SIGKILL and restart retain the acknowledged stock and reservation')

        operation=str(uuid.uuid4())
        compose('stop','replica')
        pool=ThreadPoolExecutor(max_workers=1)
        blocked_client=InventoryClient(settings,replica,max_attempts=1)
        pending=pool.submit(blocked_client.adjust_stock,operation,1,1,3,'Standby outage')
        try:
            time.sleep(3)
            check(not pending.done(),'No successful write acknowledgement is returned without the synchronous standby')
        finally:
            compose('start','replica')
            wait_ready(primary_settings)
            pending.result(timeout=15)
            blocked_client.close()
            pool.shutdown(wait=True)
        with InventoryClient(settings,replica) as client:
            client.adjust_stock(operation,1,1,3,'Standby outage')
            check(client.read_inventory(1,1,authoritative=True)['stock']['on_hand']==103,
                  'Retry after a delayed acknowledgement applies the original operation exactly once')
        lost_operation=str(uuid.uuid4())
        with InventoryClient(settings,replica,max_attempts=1) as client:
            original=client._primary_token
            def lost_response(connection):
                raise psycopg2.OperationalError('Injected connection loss after commit')
            client._primary_token=lost_response
            try:
                client.adjust_stock(lost_operation,1,1,2,'Lost response')
            except psycopg2.OperationalError:
                pass
            else:
                raise AssertionError('Failure injection did not execute')
            client._primary_token=original
            replay=client.adjust_stock(lost_operation,1,1,2,'Lost response')
            check(replay['status']=='already_applied' and client.read_inventory(1,1,authoritative=True)['stock']['on_hand']==105,
                  'Connection loss after commit is recovered with the same idempotency key')
        with closing(connect(settings)) as fresh:
            check(query(fresh,'SELECT count(*) FROM inventory_app.stock_adjustments WHERE operation_id=%s',(operation,))[0][0]==1,
                  'Recovery leaves a single idempotency record for the delayed operation')
        evidence['checks_passed']=checks
        (ROOT/'tests'/'recovery_results.json').write_text(json.dumps(evidence,indent=2),encoding='utf-8')
    print(f'All {checks} recovery checks passed.',flush=True)


if __name__=='__main__':
    main()

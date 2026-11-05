from contextlib import closing
from datetime import datetime, timezone
import json
import os
import socket
import subprocess
import time
import uuid

from support import ROOT, connect, query
from inventory_client import InventoryClient


def free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0))
        return sock.getsockname()[1]


def main():
    project='inventory_failover_'+uuid.uuid4().hex[:12]
    ports=[]
    while len(set(ports))<3:
        ports=list({*ports,free_port()})
    environment=dict(os.environ,PRIMARY_PORT=str(ports[0]),REPLICA_PORT=str(ports[1]),PROJECTION_PORT=str(ports[2]))
    base=['docker','compose','-p',project]
    def compose(*arguments):
        result=subprocess.run(base+list(arguments),cwd=ROOT,env=environment,capture_output=True,text=True,timeout=120)
        if result.returncode:
            raise RuntimeError(result.stdout+result.stderr)
        return result.stdout.strip()
    primary={'host':'127.0.0.1','port':ports[0],'dbname':'inventory_case2','user':'postgres','password':'inventory_demo'}
    standby=dict(primary,port=ports[1])
    checks=[]
    def check(condition,label):
        if not condition:
            raise AssertionError(label)
        checks.append(label)
        print('PASS '+label,flush=True)
    try:
        compose('up','-d','--wait','db','replica')
        compose('run','--rm','sync-ready')
        with InventoryClient(primary,standby,replica_wait_seconds=5) as client:
            order=str(uuid.uuid4())
            held=client.reserve_order(order,[{'warehouse_id':1,'product_id':17,'quantity':2}])
            client.read_inventory(1,17,after=held['token'])
        start=time.monotonic()
        compose('kill','-s','SIGKILL','db')
        compose('rm','-f','db')
        check(compose('ps','-q','db')=='','Former primary container is removed before standby promotion')
        compose('exec','-T','replica','psql','-U','postgres','-d','inventory_case2','-v','ON_ERROR_STOP=1','-c','SELECT pg_promote(true,60)')
        with closing(connect(standby)) as promoted:
            query(promoted,"ALTER SYSTEM SET synchronous_standby_names='FIRST 1 (inventory_replacement)'")
            query(promoted,'SELECT pg_reload_conf()')
            check(not query(promoted,'SELECT pg_is_in_recovery()')[0][0], 'The fenced standby becomes the new primary')
            check(query(promoted,'SELECT status FROM inventory_app.reservations WHERE order_id=%s',(order,))[0][0]=='active',
                  'Promotion preserves the reservation acknowledged by the former primary')
            compose('up','-d','--wait','--no-deps','replacement')
            deadline=time.monotonic()+30
            while not query(promoted,"SELECT count(*) FROM pg_stat_replication WHERE application_name='inventory_replacement' AND sync_state='sync'")[0][0]:
                if time.monotonic()>deadline:
                    raise AssertionError('Replacement standby did not become synchronous')
                time.sleep(.2)
            check(True,'A fresh synchronous standby is rebuilt before checkout resumes')
        with InventoryClient(standby,standby) as client:
            check(client.finish_reservation(order,'confirm')['status']=='confirmed',
                  'Checkout resumes through the promoted primary with synchronous durability')
        seconds=round(time.monotonic()-start,3)
        (ROOT/'tests'/'failover_results.json').write_text(json.dumps({
            'timestamp_utc':datetime.now(timezone.utc).isoformat(), 'checks_passed':len(checks),
            'recovery_seconds':seconds, 'rpo_acknowledged_transactions':0,
            'scope':'Controlled single-primary failure; isolated Docker project; shared-host disaster excluded',
            'checks':checks},indent=2),encoding='utf-8')
        print(f'All {len(checks)} failover checks passed. Recovery: {seconds}s.',flush=True)
    finally:
        compose('--profile','recovery','down','-v','--remove-orphans')


if __name__=='__main__':
    main()

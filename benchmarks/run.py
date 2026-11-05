import argparse
from contextlib import closing
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import subprocess
import sys
import time
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tests'))
from support import ROOT, connect, query, apply_schema
from inventory_client import connection_settings
from psycopg2 import sql


def command(arguments):
    result = subprocess.run(arguments, capture_output=True, text=True, cwd=ROOT, timeout=600)
    if result.returncode:
        raise RuntimeError(result.stdout + result.stderr)
    return result.stdout + result.stderr


def percentile(values, fraction):
    values = sorted(values)
    return round(values[min(len(values)-1, int((len(values)-1)*fraction))]/1000, 3) if values else None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--products', type=int, default=2000000)
    parser.add_argument('--seconds', type=int, default=20)
    parser.add_argument('--clients', type=int, default=16)
    parser.add_argument('--batch', type=int, default=32)
    args = parser.parse_args()
    if args.products <= args.batch or min(args.seconds,args.clients,args.batch) < 1:
        parser.error('Positive workload dimensions and products greater than batch are required')
    database = 'inventory_bench_' + uuid.uuid4().hex
    settings = dict(connection_settings(), dbname=database)
    evidence = ROOT / 'tests' / 'benchmark_logs'
    evidence.mkdir(exist_ok=True)
    result = {'timestamp_utc':datetime.now(timezone.utc).isoformat(), 'products':args.products,
              'warehouses':100, 'inventory_rows':args.products, 'assortment':'sparse: one warehouse per product',
              'clients':args.clients, 'duration_seconds':args.seconds, 'batch_size':args.batch,
              'docker_resources':command(['docker','info','--format','CPUs={{.NCPU}} Memory={{.MemTotal}} OS={{.OperatingSystem}}']),
              'scope':'Database write path with event inserts; excludes HTTP, customer think time and concurrent outbox draining.',
              'runs':[]}
    with closing(connect(dict(settings,dbname='postgres'))) as admin:
        query(admin, sql.SQL('CREATE DATABASE {}').format(sql.Identifier(database)))
        try:
            with closing(connect(settings)) as connection:
                apply_schema(connection)
                result['postgres'] = query(connection,'SELECT version()')[0][0]
                result['sync_standbys'] = query(connection,'SHOW synchronous_standby_names')[0][0]
                query(connection, "INSERT INTO inventory_app.warehouses SELECT g,'Warehouse '||g,'City '||g FROM generate_series(1,100) g")
                print(f'Seeding {args.products:,} products and inventory rows...', flush=True)
                query(connection, "INSERT INTO inventory_app.products SELECT g,'SKU-'||g,'Product '||g FROM generate_series(1,%s) g", (args.products,))
                query(connection, "INSERT INTO inventory_app.inventory(warehouse_id,product_id,on_hand) SELECT (1+(g-1)%%100)::integer,g,1000000000 FROM generate_series(1,%s) g", (args.products,))
                query(connection, 'ANALYZE')
                result['initial_database_bytes'] = query(connection,'SELECT pg_database_size(current_database())')[0][0]
                for name, multiplier in (('adjustment',1),('batch',args.batch),('checkout',2),('hot',2)):
                    prefix = '/tmp/inventory_bench_' + uuid.uuid4().hex
                    run = int(time.time()*1000000)
                    base = ['docker','compose','exec','-T','db','pgbench','-U','postgres','-n','-c',str(args.clients),
                            '-j',str(min(args.clients,8)),'-D',f'products={args.products}','-D',f'batch={args.batch}',
                            '-D','seq=0','-D',f'run={run}','-f',f'/benchmarks/{name}.sql']
                    print(f'Running {name}...', flush=True)
                    command(base+['-T','3',database])
                    base[base.index(f'run={run}')] = f'run={run+1}'
                    start_lsn = query(connection,'SELECT pg_current_wal_lsn()::text')[0][0]
                    output = command(base+['-T',str(args.seconds),'-l','--sampling-rate=0.1',f'--log-prefix={prefix}',database])
                    (evidence/f'{name}.txt').write_text(output,encoding='utf-8')
                    log = command(['docker','compose','exec','-T','db','sh','-c',f'cat {prefix}.*'])
                    (evidence/f'{name}.log').write_text(log,encoding='utf-8')
                    samples = [float(line.split()[2]) for line in log.splitlines() if len(line.split())>=3 and line.split()[2].isdigit()]
                    tps = float(re.search(r'tps = ([\d.]+)', output)[1])
                    failed = re.search(r'number of failed transactions: (\d+)',output)
                    wal = query(connection,'SELECT pg_wal_lsn_diff(pg_current_wal_lsn(),%s::pg_lsn)',(start_lsn,))[0][0]
                    row = {'workload':name,'transactions_per_second':round(tps,2),'inventory_updates_per_second':round(tps*multiplier,2),
                           'failed_transactions':int(failed[1]) if failed else 0,'p95_transaction_ms':percentile(samples,.95),
                           'p99_transaction_ms':percentile(samples,.99),'latency_samples':len(samples),'wal_bytes':int(wal)}
                    result['runs'].append(row)
                    print(json.dumps(row),flush=True)
                result['invalid_stock_rows'] = query(connection,'SELECT count(*) FROM inventory_app.inventory WHERE reserved<0 OR on_hand<reserved')[0][0]
                result['active_reservations'] = query(connection,"SELECT count(*) FROM inventory_app.reservations WHERE status='active'")[0][0]
                result['final_database_bytes'] = query(connection,'SELECT pg_database_size(current_database())')[0][0]
                (ROOT/'tests'/'benchmark_results.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
        finally:
            query(admin,'SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname=%s',(database,))
            query(admin,sql.SQL('DROP DATABASE {}').format(sql.Identifier(database)))


if __name__ == '__main__':
    main()

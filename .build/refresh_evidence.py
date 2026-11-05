from pathlib import Path
import json
import re

ROOT=Path(__file__).resolve().parents[1]
report=ROOT/'REPORT.md'
text=report.read_text(encoding='utf-8')
bench=json.loads((ROOT/'tests/benchmark_results.json').read_text(encoding='utf-8'))
recovery=json.loads((ROOT/'tests/recovery_results.json').read_text(encoding='utf-8'))
failover=json.loads((ROOT/'tests/failover_results.json').read_text(encoding='utf-8'))
logs=[('Functional','functional_results.txt'),('Replication','replication_results.txt'),
      ('Operational','operational_results.txt'),('Deployed services','services_run.txt'),
      ('Recovery','recovery_run.txt'),('Failover','failover_run.txt')]
checks={name:[line[5:] for line in (ROOT/'tests'/filename).read_text(encoding='utf-8-sig').splitlines() if line.startswith('PASS ')]
        for name,filename in logs}
total=sum(map(len,checks.values()))
for name,filename in logs:
    content=(ROOT/'tests'/filename).read_text(encoding='utf-8-sig')
    summary=re.search(r'^All (\d+) .*checks passed\.',content,re.MULTILINE)
    if summary is None or int(summary[1])!=len(checks[name]):
        raise RuntimeError('Missing successful suite summary: '+name)
if bench['invalid_stock_rows'] or bench['active_reservations'] or any(r['failed_transactions'] for r in bench['runs']):
    raise RuntimeError('Benchmark contains failures or unresolved inventory state')
target_result='was exceeded' if next(r for r in bench['runs'] if r['workload']=='batch')['inventory_updates_per_second']>=10000 else 'was not met'
rows='\n'.join(f"| {r['workload']} | {r['inventory_updates_per_second']:,.2f} | {r['p95_transaction_ms']} / {r['p99_transaction_ms']} ms |" for r in bench['runs'])
capacity=f'''## 11 Capacity planning and measured limits

The dense upper bound is 2,000,000 x 100 = 200,000,000 inventory rows. The measured dataset contains {bench['products']:,} products, 100 warehouses and {bench['inventory_rows']:,} inventory rows, with each product initially stocked at one warehouse. This explicitly sparse dataset exercises the full product catalog and warehouse count; it does not claim to benchmark the dense Cartesian product. Functional tests separately cover the same product stocked in multiple warehouses.

The measured platform is PostgreSQL 17.11 on Docker Desktop, with 12 allocated CPUs and approximately 14.6 GiB RAM for the Docker engine. Primary and synchronous standby use persistent volumes on one host. PostgreSQL uses 512 MB shared_buffers, a 4 GB max_wal_size, a 15-minute checkpoint timeout and WAL compression. Each workload ran for {bench['duration_seconds']} seconds after a three-second warm-up, using {bench['clients']} clients. Latency percentiles are from a 10 percent random transaction sample. This is a reproducible capacity experiment, not a long-duration production soak test.

| Workload | Inventory updates / second | Transaction p95 / p99 |
| --- | --- | --- |
{rows}

Adjustment means one guarded, idempotent stock update and its event per committed transaction. Batch means {bench['batch_size']} distinct row adjustments in one atomic transaction. Checkout reserves and confirms one unit in two committed statements, so it performs two inventory updates per reported pgbench transaction. Hot checkout sends all clients to the same warehouse-product row. Batch latency is the latency of the entire batch, not an individual operation SLA.

The 10,000-update target {target_result} in the batched write scenario with synchronous WAL acknowledgement. Individual adjustments, customer checkout and a single hot row have lower measured capacity. It would be incorrect to describe this result as 10,000 independent customer orders per second. The first experiment, preserved in tests/benchmark_before.json, used 16 clients and 32-row batches and measured 9,727.86 batched updates/s. Both configuration and workload changed in the second experiment; the improvement cannot be attributed solely to one tuning parameter.

All four final runs had zero failed transactions, zero invalid stock rows and zero unfinished active reservations. The initial database occupied {bench['initial_database_bytes']/1024**2:.1f} MiB and the final database {bench['final_database_bytes']/1024**2:.1f} MiB. tests/benchmark_results.json records WAL generation and exact results; benchmarks/run.py reproduces the experiment and cleans up its database. The database write benchmark includes idempotency and event insertion. It excludes HTTP, payment, customer think time and simultaneous delivery of the benchmark's event backlog. Relay correctness and recovery are tested separately; end-to-end delivery capacity needs its own load test before deployment.

At 10,000 row updates/s, one event per update creates 864 million events/day. A finite retention and archival policy is therefore mandatory for a sustained deployment. Size storage using measured WAL and event sizes, export acknowledged immutable events in batches, partition event history by time, and retain durable operation deduplication records for the supported retry horizon. Do not erase idempotency protection when dropping an event partition. The lab keeps its small demonstration history; it is not a long-term archive.

For a sustained unbatched target, the measured adjustment rate with a 30 percent capacity reserve gives a planning estimate of six comparable primary/standby shard pairs; the measured checkout rate gives seven. These are starting estimates, not measured linear scaling. Route by warehouse ID, validate skew and cross-shard coordination, and benchmark before accepting production traffic. A hot warehouse-product row remains serialized even after warehouse sharding. Admission control and a bounded hot-item queue are required; independent copies of the same balance would violate stock ownership.

## 12 Implementation and verified recovery

Verification was performed on 5 November 2026. The package contains the original transaction and replication suites plus new operational, deployed-service, crash/backup and fenced-failover checks. The Python dependency is pinned. All test databases are isolated and removed. The failover drill creates its own random Docker project, fences its old primary by removing the container, and deletes only that temporary project's volumes afterward.

| Suite | Checks passed | Outcome |
| --- | --- | --- |
'''+ '\n'.join(f'| {name} | {len(items)} of {len(items)} | PASS |' for name,items in checks.items())+f'''
| Total | {total} of {total} | PASS |

The live worker checks confirm automatic expiry, independent inventory writes during projection outage, durable pending events and catch-up after consumer recovery. Operational tests cover restricted privileges, connection reuse, transaction retry, atomic batches, leased delivery, lost acknowledgement and out-of-order deltas. The original last-item and transfer races remain passing.

In the measured recovery run, logical restore into a fresh database took {recovery['restore_seconds']} seconds. A forced primary SIGKILL and recovery, including the advisory-read check during downtime, took {recovery['primary_crash_recovery_seconds']} seconds. The separate fenced promotion and rebuilding of synchronous protection took {failover['recovery_seconds']} seconds. These observed local results are evidence for the lab, not contractual production RTOs.

The recovery objective for an isolated primary failure is zero lost acknowledged transactions, provided the selected synchronous standby and its storage survive and the old primary is fenced. Checkout remains pending while the required synchronous protection is unavailable. For loss of the entire Docker host, the recovery point is the latest available off-host backup; local named volumes alone cannot cover that fault. The operational target is to rehearse the procedure within 60 seconds on this small lab dataset, and to measure restoration on production-sized data before assigning a production RTO.

The backup tool writes a custom-format pg_dump with a SHA-256 manifest, restores only into a new database, and reapplies restricted role permissions. Restored stock, reservations, events and permissions are checked. Commit-response loss is injected after a durable commit; replaying the same operation ID changes stock exactly once. Standby outage is tested as a pending commit, not falsely treated as an automatic rollback. Database SQL timeouts are not assumed to bound the synchronous commit wait.

Run ./ops/check.ps1 for regular verification, ./ops/check.ps1 -Recovery for the disruptive lab drills, and ./ops/check.ps1 -Benchmark for capacity measurement. Run these groups sequentially. The complete observed output is in tests/verification_results.txt. The PDF reports and submission ZIP are generated from these sources.

'''
start=text.index('## 11 Capacity planning')
end=text.index('## Appendix A',start)
text=text[:start]+capacity+text[end:]
if '## Appendix C' in text:
    text=text[:text.index('## Appendix C')]+text[text.index('## References'):]
extra='## Appendix C Operational and recovery verification\n\n| Check | Verified behavior | Result |\n| --- | --- | --- |\n'
number=0
for name in ('Operational','Deployed services','Recovery','Failover'):
    for label in checks[name]:
        number+=1
        extra+=f'| E{number:02d} | {label} | PASS |\n'
text=text.replace('## References',extra+'\n## References')
report.write_text(text,encoding='utf-8')
combined='Inventory Management verification\nVerification date 2026-11-05 (Asia/Qyzylorda)\nPersistent primary, synchronous standby, durable projection and workers\n\n'
for name,filename in logs:
    combined+=name+'\n'+(ROOT/'tests'/filename).read_text(encoding='utf-8-sig')+'\n'
combined+=f'All {total} checks passed.\n'
(ROOT/'tests/verification_results.txt').write_text(combined,encoding='utf-8')
print(f'Refreshed report with {total} observed checks')

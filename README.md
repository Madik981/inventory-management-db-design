# Inventory Management Database Design

Database Design assignment, Problem 2. Start with `DEFENSE_GUIDE_RU.md` for preparation. The submission report and technical materials are in English.

## Start

Requirements: Docker Desktop with Linux containers and Python 3.10 or newer. First preparation downloads container images and a pinned Python dependency.

```powershell
./ops/prepare.ps1
./ops/check.ps1
```

If PowerShell blocks a locally downloaded script, run its commands manually; changing machine-wide execution policy is unnecessary.

The stack contains a primary at `127.0.0.1:55434`, synchronous standby at `55435`, separate projection database at `55436`, expiry worker, and outbox relay. All databases use persistent named volumes. `sync-ready` enables and verifies synchronous WAL acknowledgement before workers start. Its successful exit is expected.

```powershell
docker compose ps -a
docker compose exec -T db psql -U postgres -d inventory_case2 -f /ops/health.sql
docker compose logs --tail 10 expiry-worker outbox-worker
```

The demo begins with stock 100, 50, 25; after reservation, confirmation and receipt, balances are 108, 49, 25. The projection stores cumulative event movements; it does not include opening stock and is not an authoritative balance.

## Submission and defense

- `REPORT.md`: complete design, alternatives, failures and measured results.
- `Inventory_Management_Report_Styled.pdf`: formatted submission.
- `Inventory_Management_Report.pdf`: compact version for viewing during the presentation.
- `DEFENSE_NOTES.md`: presentation sequence and technical answers.
- `DEFENSE_GUIDE_RU.md`: preparation guide and difficult questions.
- `DEFENSE_WALKTHROUGH.md`: reproducible interactive SQL demonstration.
- `tests/*_results.*` and `tests/*_run.txt`: actual verification evidence.
- `Case_2_Inventory_Management.zip`: submission bundle, excluding databases, environments and backups.

```powershell
docker compose exec -T db psql -X -U postgres -d postgres -f /defense/sql/05_defense_setup.sql
docker compose exec db psql -X -U postgres -d inventory_defense -P pager=off -v ON_ERROR_STOP=0 --single-step -f /defense/defense.sql
```

Setup resets only `inventory_defense`. Workers use `inventory_case2`, so they do not interfere with manual stepping. Steps 9, 11 and 13 intentionally reject invalid operations. Follow the walkthrough for the two-session race.

## Client

```powershell
./.venv/Scripts/python.exe inventory_client.py 1 17
./.venv/Scripts/python.exe inventory_client.py 1 17 --authoritative
./.venv/Scripts/python.exe inventory_client.py 1 17 --allow-stale
```

Default reads sample primary WAL and wait up to 250 ms for replay, then fall back to the primary. Tokens contain cluster, timeline and LSN. A changed timeline rejects the token; resolve the original order on the new primary after failover.

`--allow-stale` bypasses the primary and reports `freshness: not_guaranteed`. It cannot be combined with authoritative mode or a token. It supports advisory browsing during primary outage; purchases always use the primary.

`InventoryClient` is a context manager with bounded, thread-safe connection pools. Transient failures retry up to three times with unchanged operation IDs. `adjust_stocks` applies 1-100 sorted operations atomically.

Connection and statement timeouts do not bound synchronous COMMIT during standby outage. Writes remain pending instead of returning an unsafe success. If the response is lost, replay the original ID. Do not disable synchronous protection merely to make a demonstration finish.

## Credentials

The administrative lab account is `postgres` / `inventory_demo`. Workers use `inventory_service` / `inventory_service_demo`: read access and hardened function execution, but no direct table writes. Set `PGUSER` and `PGPASSWORD` to use that role in the client. Test setup needs administrator access to create isolated databases. The projection account is `projection_owner` / `projection_demo`.

These are local demonstration secrets; replace them for an external deployment. `PGHOST`, `PGPORT`, `PGDATABASE`, `PGUSER`, `PGPASSWORD`, `REPLICA_PGHOST`, and `REPLICA_PGPORT` override connections.

## Tests and capacity

```powershell
./ops/check.ps1
./ops/check.ps1 -Recovery
./ops/check.ps1 -Benchmark
```

Recovery checks intentionally interrupt the lab pair: run them separately from presentation and benchmark. The failover test creates a random isolated Docker project and deletes only that project's resources.

The benchmark uses its own temporary database with 2,000,000 products, 100 warehouses and 2,000,000 inventory rows: a sparse assortment, not the dense 200-million-row product. Four workloads report update rate, p95/p99 transaction latency, errors, WAL and allocated CPU/RAM. Checkout has two committed stock updates. Batched update throughput is not individual checkout throughput. Raw latency samples are generated in `tests/benchmark_logs/`; summaries are included in the submission.

## Backups and recovery

```powershell
./.venv/Scripts/python.exe ops/backup.py backup --file backups/inventory.dump
./.venv/Scripts/python.exe ops/backup.py restore --database inventory_restored --file backups/inventory.dump
```

Restore verifies the checksum, creates a new database and reapplies restricted access. It never replaces an existing database. Copy periodic backups off-host; disaster-recovery RPO is the latest recoverable backup's age. Point-in-time recovery additionally needs WAL archiving, which this logical backup tool does not implement.

The tested single-primary failover fences the old container, promotes the synchronous standby, rebuilds synchronous protection and then resumes checkout. See `tests/verify_failover.py` and the recorded recovery time. Both main nodes share one lab host; independent production fault domains and unattended quorum/fencing are separate deployment requirements.

## Stop

```powershell
docker compose down
```

Named volumes remain. The next startup reuses them. `docker compose down -v` deliberately deletes them and is only for an intentional complete reset.

Initialized databases do not automatically rerun changed initialization files. Use a controlled migration for an existing installation; a new extraction with fresh volumes initializes the complete version. The dedicated defense setup always recreates its own current schema.

`.build/build_report.py` regenerates the styled PDF; add `--compact` for the compact PDF. It requires ReportLab and Windows Times New Roman fonts. `.build/refresh_evidence.py` updates measured evidence; `.build/package.py` builds the archive.

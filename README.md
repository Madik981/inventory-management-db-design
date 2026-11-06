# Inventory Management — Defense

PostgreSQL inventory management with atomic reservations, row locking, synchronous
replication, warehouse transfers, and Outbox/Inbox delivery.

## Start

Requirements: Docker Desktop running Linux containers and Python 3.10+.
Run commands from this project folder in PowerShell.

First preparation installs the dependency, starts services, and prepares the console database:

```powershell
./ops/prepare.ps1
```

On subsequent starts:

```powershell
docker compose up -d --wait
./start_console.cmd
```

You can also double-click `start_console.cmd` after starting the services.
If PowerShell blocks a script, run its commands manually:

```powershell
python -m venv .venv
./.venv/Scripts/python.exe -m pip install -r requirements.txt
docker compose up -d --build --wait
./.venv/Scripts/python.exe -X utf8 -B inventory_console.py --setup --status
./start_console.cmd
```

## Present and demonstrate

Open [the presentation](presentation/Inventory%20Management%20-%20Defense.pptx).
It contains 15 slides with English scripts in the speaker notes.

The console uses Russian prompts. **Enter** accepts the value in brackets,
**Ctrl+C** returns to the menu, and **Q** exits.

| Menu | Action |
| --- | --- |
| 1 | Prepare the demo database; preserve existing data |
| 2 | View stock; read from the primary or replica |
| 3 | Reserve items from one or several warehouses |
| 4 | Select an order by row number; confirm, release, or check expiry |
| 5 | Receive stock or record a correction |
| 6 | Request, dispatch, receive, cancel, or mark a transfer lost |
| 7 | View events; process expired reservations; deliver an Outbox batch |
| 8 | Check replication and inventory invariants |
| 9 | Retry the last write with the same ID and parameters |
| D | Open the defense scenarios |
| S | Toggle SQL and parameter display |

Press **S**, then **D**. Choose a scenario and advance each step with Enter.
Each scenario uses a new test product and checks results against the actual database.
The last-item race uses two real PostgreSQL sessions.

| Scenario | Demonstrates |
| --- | --- |
| 1. Checkout | Multiple warehouses, idempotency, confirmation and cancellation |
| 2. Rollback | Insufficient stock and atomic rollback |
| 3. Expiry | Reservation deadline and rejection of a late purchase |
| 4. Last item | Lock waiting, MVCC reads and prevention of overselling |
| 5. Replication | WAL token, replay progress and read routing |
| 6. Partitioning | Warehouse partition key and EXPLAIN |
| 7. Transfers | Goods in transit and idempotent dispatch/receipt |
| 8. Outbox | Lost ACK, redelivery and Inbox deduplication |

**D → 9** runs all scenarios. For a short demonstration, use **D → 4**.
Expiry and Outbox explicitly advance a test deadline to avoid waiting 15 minutes
or 30 seconds. Replication observes the actual state without stopping nodes.

Verify the project before presenting:

```powershell
./ops/check.ps1
```

This runs all eight scenarios without pauses and checks inventory invariants.
Individual scenarios can also run directly:

```powershell
./.venv/Scripts/python.exe -X utf8 -B inventory_console.py --demo race --show-sql
./.venv/Scripts/python.exe -X utf8 -B inventory_console.py --demo outbox --show-sql
```

## Services and data

| Service | Local port | Purpose |
| --- | --- | --- |
| `db` | 55434 | Primary PostgreSQL, authoritative writes |
| `replica` | 55435 | Synchronous standby, replica reads |
| `projection` | 55436 | Inbox and cumulative inventory movements |
| `expiry-worker` | — | Release expired reservations in `inventory_case2` |
| `outbox-worker` | — | Deliver events from `inventory_case2` |
| `sync-ready` | — | Enable synchronous replication; successful exit is expected |

The console defaults to **inventory_console**. Its initial examples are product 17
with stock 100 / 50 / 25 and product 99 with one unit. Scenarios create separate products,
so they can run repeatedly without resetting these examples.

There are no continuous workers for `inventory_console`; menu 7 runs them manually.
Late confirmation still checks the deadline inside the database. Console events use
their own `source` value in the shared projection database. The projection stores
movements without the opening stock balance.

To operate on the main database:

```powershell
./start_console.cmd --database inventory_case2
```

Ordinary operations then change main data. Demo setup and fixture-based scenarios
are restricted to the console database. The database name is shown in the menu.

The last write is saved in `.state/console/` before sending. Menu 9 can replay it
after a restart. It stores no passwords and separates requests by server, database,
and user. If a response is lost, retry that request before starting another write.

## Optional two-terminal SQL demonstration

These files match the final slide. The console race provides an automatic alternative.
Setup recreates only the dedicated `inventory_defense` schema:

```powershell
docker compose exec -T db psql -X -U postgres -d postgres -f /defense/sql/05_defense_setup.sql
```

Terminal A:

```powershell
docker compose exec db psql -X -U postgres -d inventory_defense -P pager=off --single-step -f /defense/sql/defense_session_a.sql
```

Advance A until the prompt immediately before `COMMIT`, then leave it waiting.
In terminal B:

```powershell
docker compose exec db psql -X -U postgres -d inventory_defense -P pager=off --single-step -f /defense/sql/defense_session_b.sql
```

Advance B until its reservation blocks. Return to A and execute `COMMIT`.
B receives the expected insufficient-stock error. Execute B's `ROLLBACK` and
inspect its queries; afterwards continue A to confirm the winning order.
Before A commits, a third terminal can show the blocking session:

```powershell
docker compose exec -T db psql -X -U postgres -d inventory_defense -f /defense/sql/defense_observer.sql
```

## Health and configuration

```powershell
docker compose ps -a
docker compose exec -T db psql -U postgres -d inventory_case2 -f /ops/health.sql
docker compose logs --tail 10 expiry-worker outbox-worker
```

Local lab credentials: `postgres` / `inventory_demo`; worker role:
`inventory_service` / `inventory_service_demo`; projection:
`projection_owner` / `projection_demo`.

`PGHOST`, `PGPORT`, `PGUSER`, `PGPASSWORD`, `REPLICA_PGHOST` and `REPLICA_PGPORT`
configure the client. The console database is selected by `--database`, defaulting
to `inventory_console`. The original `inventory_client.py` and workers use `PGDATABASE`.
Projection settings use `SINK_PGHOST`, `SINK_PGPORT`, `SINK_PGUSER`, `SINK_PGPASSWORD`
and `SINK_PGDATABASE`. Defaults match the local Docker stack.

Writes require synchronous standby acknowledgement. A missing standby can leave
COMMIT waiting. A timeout or interruption does not prove rollback; retain the ID
and resolve the original operation. Fresh reads check replay and fall back to the
primary; advisory reads can explicitly allow stale data.

## Project files

| Path | Purpose |
| --- | --- |
| `inventory_console.py`, `start_console.cmd` | Interactive terminal client |
| `defense_scenarios.py` | Repeatable scenarios with result checks |
| `inventory_client.py` | Inventory API, pools, retries and read routing |
| `workers.py` | Expiry worker and event relay |
| `sql/` | Schema, operations, initial data and two-session demonstration |
| `docker/`, `docker-compose.yml` | Containers, replication and worker image |
| `ops/` | Preparation, defense verification and health query |
| `presentation/` | Final English presentation with speaker notes |
| `requirements.txt` | Python dependency |
| `tests/` | Retained automated checks and recorded results |
| `backups/`, `ops/backup.py` | Retained backups and the backup/restore utility |

`.venv` is the local Python environment. `.state/console` contains the retry journal.
Database contents live in Docker named volumes. Initialization SQL runs automatically
only for a new primary volume.

The separate test suite and backups are retained. Run console integration checks with:

```powershell
./.venv/Scripts/python.exe -X utf8 -B tests/verify_console.py --integration
```

Recovery and failover tests deliberately interrupt database services or create
an isolated Docker stack. Run them outside a live presentation. The optional
`recovery` profile supplies the replacement standby required by the failover test.
Backup commands:

```powershell
./.venv/Scripts/python.exe -B ops/backup.py backup --file backups/inventory.dump
./.venv/Scripts/python.exe -B ops/backup.py restore --database inventory_restored --file backups/inventory.dump
```

Restore checks the backup checksum and creates a new database.

## Stop

```powershell
docker compose down
```

Named volumes and their data remain. `docker compose down -v` deletes those volumes
and is only for an intentional full reset.

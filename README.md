# Inventory Management System

Problem 2 of the Database Design assignment. All assignment materials are in English and all code is comment-free.

## Files

- `Inventory_Management_Report_Styled.pdf`: submission report formatted using the applicable internship-report style rules.
- `REPORT.md`: editable report source.
- `DEFENSE_NOTES.md`: presentation flow and answers to likely questions.
- `DEFENSE_WALKTHROUGH.md`: Docker-only commands, expected results, and the two-session demonstration.
- `defense.sql`: 16 numbered stages for interactive execution.
- `sql/05_defense_setup.sql`: create or reset the dedicated defense database.
- `sql/defense_session_a.sql`, `sql/defense_session_b.sql`: the last-item race in two sessions.
- `sql/defense_observer.sql`: inspect the actual lock wait and deployed replication state.
- `sql/01_schema.sql`: PostgreSQL schema and constraints.
- `sql/02_operations.sql`: reservation, completion, and stock adjustment functions.
- `sql/03_demo.sql`: warehouse example and idempotent confirmation demonstration.
- `sql/04_expiry_worker.sql`: one expiry-worker iteration.
- `sql/06_transfers.sql` implements idempotent dispatch, receipt, cancellation, and loss.
- `inventory_client.py` routes reads by WAL replay progress and sends commands to the primary.
- `docker/` bootstraps streaming replication with a dedicated replication role and slot.
- `tests/verify.py`: automated concurrency and correctness checks.
- `tests/verify_replication.py` exercises real replay lag, routing, and replicated transfers.
- `tests/verification_results.txt` records all 46 observed checks from the 4 November 2026 verification run.
- `docker-compose.yml` runs a PostgreSQL 17 primary and asynchronous read replica.

## Run with Docker

For the live defense, follow `DEFENSE_WALKTHROUGH.md`. The following commands prepare the separate defense database and pause before every statement:

```powershell
docker compose up -d --wait
docker compose exec -T db psql -X -U postgres -d postgres -f /defense/sql/05_defense_setup.sql
docker compose exec db psql -X -U postgres -d inventory_defense -P pager=off -v ON_ERROR_STOP=0 --single-step -f /defense/defense.sql
```

The preparation command resets only the dedicated defense schema. Steps 9, 11, and 13 intentionally demonstrate rejected operations. All manual defense commands run psql inside Docker. The replica streams changes from the primary, including the defense database.

From this directory:

```powershell
docker compose up -d --wait
docker compose exec db psql -U postgres -d inventory_case2 -c "SELECT warehouse_id, product_id, on_hand, reserved, available FROM inventory_app.inventory ORDER BY warehouse_id;"
```

The initialization scripts run automatically. The demo reserves two iPhones in Almaty and one in Astana, confirms the purchase twice safely, and receives ten units in Almaty. Expected final stock is 108, 49, and 25, with zero reserved units.

The primary exposes localhost port 55434 and the replica exposes 55435. Both accept the demo user `postgres` with password `inventory_demo`. The separate `inventory_replicator` role uses `inventory_replication_demo`. Storage is temporary and is lost when a container is stopped. Restart the complete pair with `docker compose down` and `docker compose up -d --wait` to rebuild a consistent lab cluster. These commands discard the lab databases. The replication slot retains up to 1 GB of WAL at checkpoint time. If an offline replica falls beyond that window, it needs a fresh base backup.

Run the automated tests with Python 3 and `psql` on PATH. Set `PSQL` to the full executable path if necessary. For example, a Windows PostgreSQL 15 installation uses `$env:PSQL = 'C:\Program Files\PostgreSQL\15\bin\psql.exe'`.

```powershell
python -m pip install -r requirements.txt
python tests/verify.py
python tests/verify_replication.py
```

Each suite creates and removes a randomly named test database. The replication suite briefly pauses WAL replay on the lab replica and resumes it during cleanup, so run it against this dedicated lab pair. It needs administrative permissions. `PGHOST`, `PGPORT`, `PGUSER`, `PGPASSWORD`, and `PSQL` override primary connection or executable defaults. `REPLICA_PGHOST` and `REPLICA_PGPORT` select the read replica. The client uses `PGDATABASE`, defaulting to `inventory_case2`.

## Read routing and replication lag

```powershell
python inventory_client.py 1 17
python inventory_client.py 1 17 --authoritative
docker compose exec -T db psql -U postgres -d inventory_case2 -c "SELECT application_name, state, sync_state, sent_lsn, replay_lsn FROM pg_stat_replication;"
```

The first command returns JSON with `source`, `reason`, `lag_bytes`, and `stock`. A healthy caught-up read uses `source = replica`. Every read samples the primary's durable WAL position, then checks replay on the replica. This also handles an idle primary correctly, without interpreting an old transaction timestamp as replication lag. The default replay wait is 250 ms and can be changed with `--wait`. Connection establishment and individual SQL statements have separate two-second limits. Lag or connection failure falls back to the primary. Authoritative reads go directly to the primary.

The replay check and inventory SELECT use separate READ COMMITTED statements on the same replica connection. The stock query therefore takes a new snapshot after the required WAL has replayed. A small primary metadata query is still required per read. The primary must be available to establish the freshness boundary.

`InventoryClient` sends reservations, completions, adjustments, and transfer commands to the primary. Each successful write returns a token containing the cluster ID, timeline, and a WAL position sampled after commit. Pass `result['token']` as `after` to `read_inventory`, or pass its JSON representation to `--after`. Tokens from another timeline or cluster, or ahead of the primary's durable WAL, raise an error. The demo uses administrative credentials to read PostgreSQL control metadata. A deployed service needs restricted roles and explicit monitoring-function grants.

## Warehouse transfers

Apply `sql/06_transfers.sql` to an existing initialized primary to add the functions without recreating its tables. New Docker clusters and the defense setup load it automatically.

```sql
SELECT inventory_app.request_transfer(
    '30000000-0000-0000-0000-000000000001', 1, 2, 17, 10
);
SELECT inventory_app.advance_transfer('30000000-0000-0000-0000-000000000001', 'dispatch');
SELECT warehouse_id, on_hand, available FROM inventory_app.inventory WHERE product_id = 17;
SELECT inventory_app.advance_transfer('30000000-0000-0000-0000-000000000001', 'receive');
SELECT inventory_app.advance_transfer('30000000-0000-0000-0000-000000000001', 'receive');
SELECT warehouse_id, on_hand, available FROM inventory_app.inventory WHERE product_id = 17;
```

After the initial demo, dispatch changes Almaty from 108 to 98 while Astana stays at 49. Call `receive` only after a trusted warehouse operator or integration verifies physical receipt. Astana then becomes 59. The repeated receipt has the same result. A previously unstocked destination gets an inventory row during receipt. Both movements record an event in the same transaction as the balance and transfer status, and stream to the replica.

The `cancel` action applies to a requested transfer. The `lose` action records the loss of dispatched goods. Lost goods remain deducted from the source. Concurrent receipt and loss commands lock the same transfer row, so one terminal decision wins. The primary owns all warehouse balances in this implementation. Automatic failover, external broker delivery, and cross-shard transfer coordination require separate components.

One expiry-worker iteration can be run with:

```powershell
Get-Content -Raw sql/04_expiry_worker.sql | docker compose exec -T db psql -U postgres -d inventory_case2 -v ON_ERROR_STOP=1
```

Stop the demonstration when finished:

```powershell
docker compose down
```

## Run on an existing PostgreSQL server

Create a dedicated empty database and apply the files in order. Do not run the schema against an existing application database.

```powershell
createdb -U postgres inventory_case2
psql -U postgres -d inventory_case2 -v ON_ERROR_STOP=1 -f sql/01_schema.sql -f sql/02_operations.sql -f sql/03_demo.sql -f sql/06_transfers.sql
```

For tests on that server, set the appropriate connection variables first. The test runner still creates and removes its own separate database.

## Verification result

Verification of the extension on PostgreSQL 17.11 in Docker passed 28 functional checks and 18 replication/routing checks. These cover the original 13 reservation checks plus transfer races, real standby replay pauses, bounded waiting, primary fallback, read tokens, and replicated transfer events. During the lag test the replica displayed 10 available units while the routed read correctly returned 7 from the primary. Throughput at 10,000 updates per second and automatic failover remain separate validation work.

The defense walkthrough was additionally verified on PostgreSQL 17.11 in Docker, including the three intended rejection errors and an observed row-lock wait between sessions A and B. The losing session reported insufficient stock after A committed, and only A confirmed the last unit.

## Report formatting

Both PDF reports are generated from the current `REPORT.md`. They contain eight numbered assignment answers, the implemented architecture and transfer diagrams, the last-item challenge, and the complete 46-check results. `Case_2_Inventory_Management.zip` includes the reports, application and database code, Docker replication scripts, test suites, and the captured verification log.

The styled PDF applies A4, 2.54 cm margins, Times New Roman 12, justified body text, a 1.27 cm first-line indent, double line spacing derived from the font metrics, 12-point bold section headings, white tables with black borders and single spacing, figure labels above the drawings, and page numbers at the top right. `Inventory_Management_Report.pdf` provides a compact Helvetica layout with the same content. Both versions include navigation bookmarks.

The reference describes an internship report rather than this assignment. Its internship title page, personal details, signatures, exact section count, and special-case spacing were not imported. This assignment has no separate cover page, so page 1 is numbered. Existing SQL block and reference-list formatting is retained because the reference has no comparable examples for those elements.

The included `.build/build_report.py` regenerates the styled report. Add `--compact` to regenerate the compact version. Rendering requires ReportLab and the Windows Times New Roman font files under `C:/Windows/Fonts`.

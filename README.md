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
- `tests/verify.py`: automated concurrency and correctness checks.
- `compose.yaml`: temporary local PostgreSQL demonstration.

## Run with Docker

For the live defense, follow `DEFENSE_WALKTHROUGH.md`. The following commands prepare the separate defense database and pause before every statement:

```powershell
docker compose up -d --wait
docker compose exec -T db psql -X -U postgres -d postgres -f /defense/sql/05_defense_setup.sql
docker compose exec db psql -X -U postgres -d inventory_defense -P pager=off -v ON_ERROR_STOP=0 --single-step -f /defense/defense.sql
```

The preparation command resets only the dedicated defense schema. Steps 9, 11, and 13 intentionally demonstrate rejected operations. All manual defense commands run psql inside Docker, so they need no host PostgreSQL installation. Replication and sharding remain architecture explanations, not running services.

From this directory:

```powershell
docker compose up -d --wait
docker compose exec db psql -U postgres -d inventory_case2 -c "SELECT warehouse_id, product_id, on_hand, reserved, available FROM inventory_app.inventory ORDER BY warehouse_id;"
```

The initialization scripts run automatically. The demo reserves two iPhones in Almaty and one in Astana, confirms the purchase twice safely, and receives ten units in Almaty. Expected final stock is 108, 49, and 25, with zero reserved units.

The container exposes PostgreSQL only on localhost port 55434. Its password is `inventory_demo`. Storage is temporary and is lost when the container is stopped. This configuration is intended for a local demonstration. Change the published port if 55434 is already in use.

Run the automated tests with Python 3 and `psql` on PATH:

```powershell
python tests/verify.py
```

The runner connects to port 55434 by default, creates its own randomly named test database, loads a fresh schema, performs 13 checks, and drops only that test database. It requires database-creation privileges. Environment variables `PGHOST`, `PGPORT`, `PGUSER`, `PGPASSWORD`, and `PSQL` override the connection or executable defaults.

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
psql -U postgres -d inventory_case2 -v ON_ERROR_STOP=1 -f sql/01_schema.sql -f sql/02_operations.sql -f sql/03_demo.sql
```

For tests on that server, set the appropriate connection variables first. The test runner still creates and removes its own separate database.

## Verification result

All 13 automated checks passed on PostgreSQL 17 in Docker. Schema loading and the demo also passed on PostgreSQL 17.4. The checks cover competing buyers, idempotent commands, rollback, expiry, and stock invariants. They are not a 10,000-updates-per-second benchmark or a replication failover test.

The defense walkthrough was additionally verified on PostgreSQL 17.11 in Docker, including the three intended rejection errors and an observed row-lock wait between sessions A and B. The losing session reported insufficient stock after A committed, and only A confirmed the last unit.

## Report formatting

The styled PDF applies the relevant reference settings: A4, 2.54 cm margins, Times New Roman 12, justified body text, a 1.27 cm first-line indent, double line spacing derived from the font metrics, 12-point bold section headings, white tables with black borders and single spacing, figure labels above the drawings, and page numbers at the top right. Diagram content and proportions are preserved. The original report remains available locally as Inventory_Management_Report.pdf.

The reference describes an internship report rather than this assignment. Its internship title page, personal details, signatures, exact section count, and special-case spacing were not imported. This assignment has no separate cover page, so page 1 is numbered. Existing SQL block and reference-list formatting is retained because the reference has no comparable examples for those elements.

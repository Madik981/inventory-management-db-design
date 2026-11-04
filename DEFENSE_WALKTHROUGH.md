# Live Defense Walkthrough

## What can be demonstrated with Docker

Docker Compose provides PostgreSQL and its psql client. The complete manual demonstration below needs only Docker Desktop and a terminal. Neither a host PostgreSQL server nor a host psql installation is required. A graphical SQL editor is optional.

The executable demonstration covers the schema, warehouse partitions, transactions, conditional updates, idempotency, reservation expiry, stock adjustments, audit events, rollback, and two competing database sessions. Docker Compose also starts an asynchronous read replica. The README gives transfer commands and the Python replication suite demonstrates replay lag and primary fallback. Automated failover, physical sharding, broker delivery, and target-scale performance remain further deployment work.

## Prepare a clean demonstration

Run these commands from the assignment directory:

```powershell
docker compose up -d --wait
docker compose exec -T db psql -X -U postgres -d postgres -f /defense/sql/05_defense_setup.sql
```

The preparation script creates the dedicated inventory_defense database if necessary and rebuilds only its inventory_app schema. Running it again resets this demonstration, including its reservations and audit events. The separate inventory_case2 database is unchanged. Close both concurrency sessions before resetting.

Start the main file interactively:

```powershell
docker compose exec db psql -X -U postgres -d inventory_defense -P pager=off -v ON_ERROR_STOP=0 --single-step -f /defense/defense.sql
```

psql shows each statement and waits for Enter before executing it. The SQL file has no comments; SELECT statements print numbered steps. Do not use -T for this interactive command, because the demonstration needs a terminal. Do not execute the entire file as one transaction or enable single-transaction mode.

Steps 9, 11, and 13 deliberately produce errors. They demonstrate rejection rules. Press Enter to continue to the verification SELECT after each error. These errors occur outside explicit transactions and do not prevent the next statement from running. A full main demonstration should take less than 15 minutes between reservation and confirmation; otherwise reset and repeat, because the real reservation deadline still applies.

## Main file checkpoints

| Step | Explain | Expected result |
| --- | --- | --- |
| 1 | Connection and isolation | inventory_defense and read committed |
| 2 | Independent warehouse stock | Product 17: Almaty 100, Astana 50, Shymkent 25; product 99: one unit |
| 3 | Warehouse partitioning | 16 leaf partitions; a warehouse-specific plan touches one partition |
| 4 | Atomic allocation | Product 17 reserved: 2 in Almaty and 1 in Astana |
| 5 | Reservation idempotency | Reserved quantities remain 2 and 1 |
| 6 | Confirmation idempotency | Stock becomes 98, 49, 25; reserved is zero |
| 7 | Cancellation | Shymkent temporarily reserves 3, then returns to available 25 |
| 8 | Expiry | expired; late confirm also returns expired; Almaty remains 98 |
| 9 | Insufficient stock and atomicity | Error; no partial hold and failed_order_rows = 0 |
| 10 | Delivery idempotency | applied, then already_applied; Almaty becomes 108 |
| 11 | Reserved stock protection | Error; Astana stays on_hand 49 and reserved 49 until release |
| 12 | Explicit rollback | Temporary hold disappears; rolled_back_order_rows = 0 |
| 13 | Idempotency payload validation | Error because the old order ID is reused with different items |
| 14 | Audit and outbox | Deltas and increasing per-item versions; published_at is null because no publisher is deployed |
| 15 | Invariants | Both mismatch counts are zero; final product 17 stock is 108, 49, 25 |
| 16 | Transition to concurrency demo | Open the two sessions below |

The expiry step changes one test reservation's deadline to avoid a 15-minute wait. It is a controlled fixture change, not an operation exposed to customers.

## Two simultaneous buyers

Open two terminals in the same assignment directory. Product 99 is reserved exclusively for this scenario. Start Terminal A:

```powershell
docker compose exec db psql -X -U postgres -d inventory_defense -P pager=off -v ON_ERROR_STOP=0 --single-step -f /defense/sql/defense_session_a.sql
```

Advance through BEGIN, reserve_order, the inventory SELECT, and the A2 instruction. Stop when psql displays COMMIT and asks for Enter. The transaction is still open and holds the stock-row lock.

Start Terminal B:

```powershell
docker compose exec db psql -X -U postgres -d inventory_defense -P pager=off -v ON_ERROR_STOP=0 --single-step -f /defense/sql/defense_session_b.sql
```

Advance through BEGIN and execute reserve_order. Terminal B now waits; this is the expected database lock wait. Do not wait for it to finish before returning to Terminal A.

Optionally, use a third terminal while B is waiting:

```powershell
docker compose exec -T db psql -X -U postgres -d inventory_defense -P pager=off -f /defense/sql/defense_observer.sql
```

Terminal B should have wait_event_type = Lock and a nonempty blocking_sessions array. On the primary, the observer reports is_read_replica = false and connected_replication_standbys = 1 once streaming is established.

Return to A and execute COMMIT. B resumes, rechecks availability against A's committed update, and raises Insufficient stock. In B, execute ROLLBACK to clear the failed transaction, then run its verification queries. losing_order_rows must be zero and available must be zero.

Finally, continue A and confirm the winning order. Product 99 ends at on_hand = 0, reserved = 0, available = 0. If B checks before confirmation, it may see on_hand = 1 and reserved = 1; availability is still zero. Both observations are correct.

The two sessions represent different application instances. The database lock provides the guarantee. The transaction is kept open manually only to make the race visible during the presentation; normal application transactions do not wait for a human.

To repeat, finish or close both sessions and rerun the preparation command. The setup clears the dedicated defense database, so it resets both the main walkthrough and the race.

## Finish

```powershell
docker compose down
```

The database uses temporary container storage. Stopping the container discards its data. On the next start, run the preparation command again. The report and SQL files remain on the host.

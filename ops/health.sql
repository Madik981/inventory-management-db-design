SELECT application_name, state, sync_state,
       pg_wal_lsn_diff(pg_current_wal_lsn(), replay_lsn) AS replay_gap_bytes
FROM pg_stat_replication;
SELECT count(*) AS overdue_reservations,
       coalesce(extract(epoch FROM statement_timestamp()-min(expires_at)),0) AS oldest_overdue_seconds
FROM inventory_app.reservations
WHERE status='active' AND expires_at <= statement_timestamp();
SELECT count(*) AS pending_events,
       coalesce(extract(epoch FROM statement_timestamp()-min(occurred_at)),0) AS oldest_pending_seconds
FROM inventory_app.inventory_events WHERE published_at IS NULL;
SELECT count(*) AS invalid_inventory_rows FROM inventory_app.inventory
WHERE on_hand < 0 OR reserved < 0 OR reserved > on_hand;
SELECT application_name, wait_event_type, wait_event, pg_blocking_pids(pid)
FROM pg_stat_activity WHERE cardinality(pg_blocking_pids(pid)) > 0;

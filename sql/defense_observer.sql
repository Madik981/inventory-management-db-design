SELECT application_name, state, wait_event_type, wait_event,
       pg_blocking_pids(pid) AS blocking_sessions
FROM pg_stat_activity
WHERE datname = 'inventory_defense'
  AND application_name IN ('inventory_defense_A', 'inventory_defense_B')
ORDER BY application_name;

SELECT pg_is_in_recovery() AS is_read_replica;
SELECT count(*) AS connected_replication_standbys FROM pg_stat_replication;

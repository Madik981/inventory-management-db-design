#!/bin/sh
set -eu
psql -X -v ON_ERROR_STOP=1 -c "ALTER SYSTEM SET synchronous_standby_names = 'FIRST 1 (inventory_replica)'"
psql -X -v ON_ERROR_STOP=1 -c 'SELECT pg_reload_conf()'
for attempt in $(seq 1 60); do
    if [ "$(psql -X -Atc "SELECT count(*) FROM pg_stat_replication WHERE application_name='inventory_replica' AND state='streaming' AND sync_state='sync'")" = '1' ]; then
        exit 0
    fi
    sleep 1
done
echo 'Synchronous standby did not become ready' >&2
exit 1

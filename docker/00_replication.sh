#!/bin/sh
set -eu

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
    --set=replication_password="$REPLICATION_PASSWORD" <<'SQL'
CREATE ROLE inventory_replicator WITH LOGIN REPLICATION PASSWORD :'replication_password';
SELECT pg_create_physical_replication_slot('inventory_replica');
SQL

printf '\nhost replication inventory_replicator all scram-sha-256\n' >> "$PGDATA/pg_hba.conf"

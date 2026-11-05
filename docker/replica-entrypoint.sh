#!/bin/sh
set -eu

[ "$PGDATA" = '/var/lib/postgresql/data/standby' ]

if [ "$(id -u)" = '0' ]; then
    mkdir -p /var/lib/postgresql/data
    chown postgres:postgres /var/lib/postgresql/data
    chmod 700 /var/lib/postgresql/data
    exec gosu postgres sh /replica-entrypoint.sh
fi

if [ ! -s "$PGDATA/PG_VERSION" ]; then
    rm -rf /var/lib/postgresql/data/basebackup
    set --
    if [ "${CREATE_REPLICATION_SLOT:-0}" = '1' ]; then
        set -- --create-slot
    fi
    pg_basebackup --dbname="host=${PRIMARY_HOST:-db} port=5432 user=inventory_replicator application_name=${REPLICATION_SLOT:-inventory_replica}" \
        --pgdata=/var/lib/postgresql/data/basebackup --write-recovery-conf --wal-method=stream \
        --slot="${REPLICATION_SLOT:-inventory_replica}" --checkpoint=fast --no-password "$@"
    mv /var/lib/postgresql/data/basebackup "$PGDATA"
fi

exec postgres -D "$PGDATA" -c hot_standby=on -c listen_addresses='*'

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
    pg_basebackup --dbname="host=db port=5432 user=inventory_replicator application_name=inventory_replica" \
        --pgdata=/var/lib/postgresql/data/basebackup --write-recovery-conf --wal-method=stream \
        --slot=inventory_replica --checkpoint=fast --no-password
    mv /var/lib/postgresql/data/basebackup "$PGDATA"
fi

exec postgres -D "$PGDATA" -c hot_standby=on -c listen_addresses='*'

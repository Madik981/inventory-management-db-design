from contextlib import closing, contextmanager
from pathlib import Path
import sys
import uuid

import psycopg2
from psycopg2 import sql

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from inventory_client import connection_settings


def connect(settings=None):
    connection = psycopg2.connect(**(settings or connection_settings()), connect_timeout=3)
    connection.autocommit = True
    return connection


def query(connection, statement, parameters=None):
    with connection.cursor() as cursor:
        cursor.execute(statement, parameters)
        return cursor.fetchall() if cursor.description else []


def apply_schema(connection):
    for name in ('01_schema.sql', '02_operations.sql', '06_transfers.sql', '07_workers.sql', '08_access.sql'):
        query(connection, (ROOT / 'sql' / name).read_text(encoding='utf-8'))


@contextmanager
def database(prefix='inventory_check_'):
    name = prefix + uuid.uuid4().hex
    settings = dict(connection_settings(), dbname=name)
    with closing(connect(dict(settings, dbname='postgres'))) as admin:
        query(admin, sql.SQL('CREATE DATABASE {}').format(sql.Identifier(name)))
        try:
            with closing(connect(settings)) as connection:
                apply_schema(connection)
                query(connection, "INSERT INTO inventory_app.products VALUES (1,'TEST','Test');"
                                  "INSERT INTO inventory_app.warehouses VALUES (1,'Almaty','Almaty');"
                                  "INSERT INTO inventory_app.inventory(warehouse_id,product_id,on_hand) VALUES(1,1,100);")
                yield settings, connection
        finally:
            with closing(connect(dict(settings, dbname='postgres'))) as cleanup:
                query(cleanup, 'SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname=%s', (name,))
                query(cleanup, sql.SQL('DROP DATABASE {}').format(sql.Identifier(name)))

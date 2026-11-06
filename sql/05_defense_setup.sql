\set ON_ERROR_STOP on
\connect postgres

SELECT 'CREATE DATABASE inventory_defense'
WHERE NOT EXISTS (
    SELECT 1 FROM pg_database WHERE datname = 'inventory_defense'
)
\gexec

\connect inventory_defense
DROP SCHEMA IF EXISTS inventory_app CASCADE;
\ir 01_schema.sql
\ir 02_operations.sql
\ir 06_transfers.sql
\ir 07_workers.sql
\ir 08_access.sql

SET search_path TO inventory_app, public;

INSERT INTO products VALUES
    (17, 'IPHONE-17', 'iPhone 17'),
    (99, 'LAST-ITEM-DEMO', 'Last item concurrency demonstration');

INSERT INTO warehouses VALUES
    (1, 'Almaty warehouse', 'Almaty'),
    (2, 'Astana warehouse', 'Astana'),
    (3, 'Shymkent warehouse', 'Shymkent');

INSERT INTO inventory (warehouse_id, product_id, on_hand) VALUES
    (1, 17, 100), (2, 17, 50), (3, 17, 25), (1, 99, 1);

SELECT 'The inventory_defense database is ready. Run defense_session_a.sql and defense_session_b.sql in two terminals with --single-step.' AS result;

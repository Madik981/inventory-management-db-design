SET search_path TO inventory_app, public;

BEGIN ISOLATION LEVEL READ COMMITTED;
SET LOCAL lock_timeout = '2s';
SET LOCAL statement_timeout = '5s';

WITH candidate AS MATERIALIZED (
    SELECT order_id
    FROM reservations
    WHERE status = 'active' AND expires_at <= clock_timestamp()
    ORDER BY expires_at, order_id
    FOR UPDATE SKIP LOCKED
    LIMIT 1
)
SELECT finish_reservation(order_id, 'expire')
FROM candidate;

COMMIT;

SET search_path TO inventory_app, public;
SET application_name = 'inventory_defense_B';
SET statement_timeout = '5min';

SELECT 'B1. Run this only after Terminal A has reserved the unit and before A commits' AS instruction;
BEGIN ISOLATION LEVEL READ COMMITTED;
SELECT reserve_order(
    '20000000-0000-0000-0000-000000000002',
    '[{"warehouse_id":1,"product_id":99,"quantity":1}]'
);
ROLLBACK;

SELECT 'B2. The losing order must be absent and available stock must be zero' AS step;
SELECT count(*) AS losing_order_rows
FROM reservations WHERE order_id = '20000000-0000-0000-0000-000000000002';
SELECT warehouse_id, product_id, on_hand, reserved, available
FROM inventory WHERE warehouse_id = 1 AND product_id = 99;

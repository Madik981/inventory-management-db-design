SET search_path TO inventory_app, public;
SET application_name = 'inventory_defense_A';
SET idle_in_transaction_session_timeout = '10min';

SELECT 'A1. Begin a transaction and reserve the last unit' AS step;
BEGIN ISOLATION LEVEL READ COMMITTED;
SELECT reserve_order(
    '20000000-0000-0000-0000-000000000001',
    '[{"warehouse_id":1,"product_id":99,"quantity":1}]'
);
SELECT warehouse_id, product_id, on_hand, reserved, available
FROM inventory WHERE warehouse_id = 1 AND product_id = 99;

SELECT 'A2. Keep the next COMMIT paused. Run Terminal B until its reserve_order waits, then return here and execute COMMIT.' AS instruction;
COMMIT;

SELECT 'A3. After Terminal B reports insufficient stock, confirm the winning order' AS step;
SELECT finish_reservation('20000000-0000-0000-0000-000000000001', 'confirm');
SELECT warehouse_id, product_id, on_hand, reserved, available
FROM inventory WHERE warehouse_id = 1 AND product_id = 99;

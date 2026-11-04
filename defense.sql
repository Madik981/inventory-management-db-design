SET search_path TO inventory_app, public;
SET application_name = 'inventory_defense_main';

SELECT '01. Verify the demonstration database and isolation level' AS step;
SELECT current_database(), version();
SHOW transaction_isolation;

SELECT '02. Inspect the starting warehouse balances' AS step;
SELECT w.city, i.product_id, i.on_hand, i.reserved, i.available
FROM inventory AS i
JOIN warehouses AS w USING (warehouse_id)
ORDER BY i.product_id, i.warehouse_id;

SELECT '03. Show warehouse partitioning and partition pruning' AS step;
SELECT relid::regclass AS partition, isleaf, level
FROM pg_partition_tree('inventory_app.inventory');
EXPLAIN (COSTS OFF)
SELECT * FROM inventory WHERE warehouse_id = 1 AND product_id = 17;

SELECT '04. Reserve two units in Almaty and one in Astana atomically' AS step;
BEGIN ISOLATION LEVEL READ COMMITTED;
SELECT reserve_order(
    '10000000-0000-0000-0000-000000000001',
    '[{"warehouse_id":1,"product_id":17,"quantity":2},
      {"warehouse_id":2,"product_id":17,"quantity":1}]'
);
COMMIT;
SELECT warehouse_id, on_hand, reserved, available
FROM inventory WHERE product_id = 17 ORDER BY warehouse_id;
SELECT order_id, status, expires_at FROM reservations;
SELECT * FROM reservation_items ORDER BY warehouse_id;

SELECT '05. Replay the same request without reserving again' AS step;
SELECT reserve_order(
    '10000000-0000-0000-0000-000000000001',
    '[{"warehouse_id":1,"product_id":17,"quantity":2},
      {"warehouse_id":2,"product_id":17,"quantity":1}]'
);
SELECT warehouse_id, on_hand, reserved, available
FROM inventory WHERE product_id = 17 ORDER BY warehouse_id;

SELECT '06. Confirm the order twice and show exactly one deduction' AS step;
SELECT finish_reservation('10000000-0000-0000-0000-000000000001', 'confirm');
SELECT finish_reservation('10000000-0000-0000-0000-000000000001', 'confirm');
SELECT warehouse_id, on_hand, reserved, available
FROM inventory WHERE product_id = 17 ORDER BY warehouse_id;

SELECT '07. Reserve and cancel three units in Shymkent twice safely' AS step;
SELECT reserve_order(
    '10000000-0000-0000-0000-000000000002',
    '[{"warehouse_id":3,"product_id":17,"quantity":3}]'
);
SELECT warehouse_id, on_hand, reserved, available
FROM inventory WHERE warehouse_id = 3 AND product_id = 17;
SELECT finish_reservation('10000000-0000-0000-0000-000000000002', 'release');
SELECT finish_reservation('10000000-0000-0000-0000-000000000002', 'release');
SELECT warehouse_id, on_hand, reserved, available
FROM inventory WHERE warehouse_id = 3 AND product_id = 17;

SELECT '08. Demonstrate expiry without waiting fifteen minutes' AS step;
SELECT reserve_order(
    '10000000-0000-0000-0000-000000000003',
    '[{"warehouse_id":1,"product_id":17,"quantity":1}]'
);
SELECT 'Test fixture only: move this reservation deadline into the past' AS explanation;
UPDATE reservations
SET expires_at = clock_timestamp() - interval '1 second'
WHERE order_id = '10000000-0000-0000-0000-000000000003';
SELECT finish_reservation('10000000-0000-0000-0000-000000000003', 'expire');
SELECT finish_reservation('10000000-0000-0000-0000-000000000003', 'confirm');
SELECT warehouse_id, on_hand, reserved, available
FROM inventory WHERE warehouse_id = 1 AND product_id = 17;

SELECT '09. Expected error: insufficient Astana stock rolls back the Almaty hold too' AS step;
SELECT reserve_order(
    '10000000-0000-0000-0000-000000000004',
    '[{"warehouse_id":1,"product_id":17,"quantity":1},
      {"warehouse_id":2,"product_id":17,"quantity":1000}]'
);
SELECT warehouse_id, on_hand, reserved, available
FROM inventory WHERE product_id = 17 ORDER BY warehouse_id;
SELECT count(*) AS failed_order_rows
FROM reservations WHERE order_id = '10000000-0000-0000-0000-000000000004';

SELECT '10. Record a supplier delivery and replay it safely' AS step;
SELECT adjust_stock('10000000-0000-0000-0000-000000000005', 1, 17, 10, 'Supplier delivery');
SELECT adjust_stock('10000000-0000-0000-0000-000000000005', 1, 17, 10, 'Supplier delivery');
SELECT warehouse_id, on_hand, reserved, available
FROM inventory WHERE warehouse_id = 1 AND product_id = 17;

SELECT '11. Expected error: a correction cannot consume reserved stock' AS step;
SELECT reserve_order(
    '10000000-0000-0000-0000-000000000006',
    '[{"warehouse_id":2,"product_id":17,"quantity":49}]'
);
SELECT adjust_stock('10000000-0000-0000-0000-000000000016', 2, 17, -1, 'Count correction');
SELECT warehouse_id, on_hand, reserved, available
FROM inventory WHERE warehouse_id = 2 AND product_id = 17;
SELECT finish_reservation('10000000-0000-0000-0000-000000000006', 'release');

SELECT '12. Roll back a valid reservation explicitly' AS step;
BEGIN;
SELECT reserve_order(
    '10000000-0000-0000-0000-000000000007',
    '[{"warehouse_id":1,"product_id":17,"quantity":1}]'
);
SELECT warehouse_id, on_hand, reserved, available
FROM inventory WHERE warehouse_id = 1 AND product_id = 17;
ROLLBACK;
SELECT warehouse_id, on_hand, reserved, available
FROM inventory WHERE warehouse_id = 1 AND product_id = 17;
SELECT count(*) AS rolled_back_order_rows
FROM reservations WHERE order_id = '10000000-0000-0000-0000-000000000007';

SELECT '13. Expected error: an existing order ID cannot change its request' AS step;
SELECT reserve_order(
    '10000000-0000-0000-0000-000000000001',
    '[{"warehouse_id":1,"product_id":17,"quantity":3}]'
);

SELECT '14. Inspect the audit trail and unpublished outbox events' AS step;
SELECT event_id, operation_id, warehouse_id, product_id, event_type,
       on_hand_delta, reserved_delta, inventory_version, published_at
FROM inventory_events ORDER BY event_id;

SELECT '15. Check the stock invariant and reservation reconciliation' AS step;
SELECT count(*) AS invalid_inventory_rows
FROM inventory WHERE on_hand < 0 OR reserved < 0 OR reserved > on_hand;
WITH active_totals AS (
    SELECT ri.warehouse_id, ri.product_id, sum(ri.quantity) AS quantity
    FROM reservation_items AS ri
    JOIN reservations AS r USING (order_id)
    WHERE r.status = 'active'
    GROUP BY ri.warehouse_id, ri.product_id
)
SELECT count(*) AS reservation_counter_mismatches
FROM inventory AS i
LEFT JOIN active_totals AS a USING (warehouse_id, product_id)
WHERE i.reserved <> coalesce(a.quantity, 0);
SELECT warehouse_id, product_id, on_hand, reserved, available
FROM inventory ORDER BY product_id, warehouse_id;

SELECT '16. Run defense_session_a.sql and defense_session_b.sql in two terminals for the last-item race' AS step;

SET search_path TO inventory_app, public;

INSERT INTO products VALUES (17, 'IPHONE-17', 'iPhone 17');
INSERT INTO warehouses VALUES
    (1, 'Almaty warehouse', 'Almaty'),
    (2, 'Astana warehouse', 'Astana'),
    (3, 'Shymkent warehouse', 'Shymkent');
INSERT INTO inventory (warehouse_id, product_id, on_hand) VALUES
    (1, 17, 100), (2, 17, 50), (3, 17, 25);

BEGIN ISOLATION LEVEL READ COMMITTED;
SET LOCAL lock_timeout = '2s';
SET LOCAL statement_timeout = '5s';
SELECT reserve_order(
    '00000000-0000-0000-0000-000000000001',
    '[{"warehouse_id":1,"product_id":17,"quantity":2},
      {"warehouse_id":2,"product_id":17,"quantity":1}]'::jsonb
);
COMMIT;

SELECT warehouse_id, product_id, on_hand, reserved, available
FROM inventory ORDER BY warehouse_id, product_id;

SELECT finish_reservation('00000000-0000-0000-0000-000000000001', 'confirm');
SELECT finish_reservation('00000000-0000-0000-0000-000000000001', 'confirm');

SELECT adjust_stock(
    '00000000-0000-0000-0000-000000000002', 1, 17, 10, 'Supplier delivery'
);

SELECT warehouse_id, product_id, on_hand, reserved, available
FROM inventory ORDER BY warehouse_id, product_id;

SELECT operation_id, event_type, warehouse_id, on_hand_delta, reserved_delta
FROM inventory_events ORDER BY event_id;

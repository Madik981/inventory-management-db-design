\set start random(1, :products - :batch)
SELECT inventory_app.adjust_stock(gen_random_uuid(), warehouse_id, product_id, 1, 'benchmark batch')
FROM (SELECT (1+(g-1)%100)::integer AS warehouse_id, g::bigint AS product_id
      FROM generate_series(:start, :start+:batch-1) g ORDER BY warehouse_id, product_id) items;

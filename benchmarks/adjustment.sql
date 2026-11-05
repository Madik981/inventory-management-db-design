\set pid random(1, :products)
SELECT inventory_app.adjust_stock(gen_random_uuid(), (1+(:pid-1)%100)::integer, :pid, 1, 'benchmark');

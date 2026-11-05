\set seq :seq + 1
SELECT inventory_app.reserve_order(md5(:run::text || '/' || :client_id::text || '/' || :seq::text)::uuid,
       '[{"warehouse_id":1,"product_id":1,"quantity":1}]'::jsonb);
SELECT inventory_app.finish_reservation(md5(:run::text || '/' || :client_id::text || '/' || :seq::text)::uuid,'confirm');

\set pid random(1, :products)
\set seq :seq + 1
SELECT inventory_app.reserve_order(md5(:run::text || '/' || :client_id::text || '/' || :seq::text)::uuid,
       jsonb_build_array(jsonb_build_object('warehouse_id',(1+(:pid-1)%100)::integer,'product_id',:pid,'quantity',1)));
SELECT inventory_app.finish_reservation(md5(:run::text || '/' || :client_id::text || '/' || :seq::text)::uuid,'confirm');

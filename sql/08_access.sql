DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'inventory_service') THEN
        CREATE ROLE inventory_service LOGIN PASSWORD 'inventory_service_demo';
    END IF;
END;
$$;

ALTER FUNCTION inventory_app.reserve_order(uuid, jsonb) SECURITY DEFINER;
ALTER FUNCTION inventory_app.finish_reservation(uuid, text) SECURITY DEFINER;
ALTER FUNCTION inventory_app.adjust_stock(uuid, integer, bigint, bigint, text) SECURITY DEFINER;
ALTER FUNCTION inventory_app.request_transfer(uuid, integer, integer, bigint, bigint) SECURITY DEFINER;
ALTER FUNCTION inventory_app.advance_transfer(uuid, text) SECURITY DEFINER;
REVOKE ALL ON SCHEMA inventory_app FROM PUBLIC;
REVOKE EXECUTE ON ALL FUNCTIONS IN SCHEMA inventory_app FROM PUBLIC;
GRANT USAGE ON SCHEMA inventory_app TO inventory_service;
GRANT SELECT ON ALL TABLES IN SCHEMA inventory_app TO inventory_service;
GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA inventory_app TO inventory_service;
GRANT EXECUTE ON FUNCTION pg_catalog.pg_control_system() TO inventory_service;
GRANT EXECUTE ON FUNCTION pg_catalog.pg_control_checkpoint() TO inventory_service;

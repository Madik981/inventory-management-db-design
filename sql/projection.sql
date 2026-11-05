CREATE SCHEMA IF NOT EXISTS inventory_projection;
CREATE TABLE IF NOT EXISTS inventory_projection.inbox (
    source text NOT NULL,
    operation_id uuid NOT NULL,
    warehouse_id integer NOT NULL,
    product_id bigint NOT NULL,
    event_type text NOT NULL,
    on_hand_delta bigint NOT NULL,
    reserved_delta bigint NOT NULL,
    inventory_version bigint NOT NULL,
    received_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (source, operation_id, warehouse_id, product_id, event_type)
);
CREATE TABLE IF NOT EXISTS inventory_projection.movements (
    source text NOT NULL,
    warehouse_id integer NOT NULL,
    product_id bigint NOT NULL,
    on_hand_change bigint NOT NULL,
    reserved_change bigint NOT NULL,
    highest_version bigint NOT NULL,
    PRIMARY KEY (source, warehouse_id, product_id)
);
CREATE OR REPLACE FUNCTION inventory_projection.apply_event(
    p_source text, p_operation uuid, p_warehouse integer, p_product bigint,
    p_type text, p_on_hand bigint, p_reserved bigint, p_version bigint
)
RETURNS boolean LANGUAGE plpgsql
SET search_path TO inventory_projection, pg_temp
AS $$
DECLARE v_existing inbox%ROWTYPE;
BEGIN
    INSERT INTO inbox(source, operation_id, warehouse_id, product_id, event_type,
                      on_hand_delta, reserved_delta, inventory_version)
    VALUES(p_source, p_operation, p_warehouse, p_product, p_type, p_on_hand, p_reserved, p_version)
    ON CONFLICT DO NOTHING;
    IF NOT FOUND THEN
        SELECT * INTO v_existing FROM inbox
        WHERE source=p_source AND operation_id=p_operation AND warehouse_id=p_warehouse
          AND product_id=p_product AND event_type=p_type;
        IF ROW(v_existing.on_hand_delta, v_existing.reserved_delta, v_existing.inventory_version)
           IS DISTINCT FROM ROW(p_on_hand, p_reserved, p_version) THEN
            RAISE EXCEPTION 'Event identity was reused with different contents';
        END IF;
        RETURN false;
    END IF;
    INSERT INTO movements VALUES(p_source, p_warehouse, p_product, p_on_hand, p_reserved, p_version)
    ON CONFLICT (source, warehouse_id, product_id) DO UPDATE
    SET on_hand_change=movements.on_hand_change+EXCLUDED.on_hand_change,
        reserved_change=movements.reserved_change+EXCLUDED.reserved_change,
        highest_version=greatest(movements.highest_version, EXCLUDED.highest_version);
    RETURN true;
END;
$$;

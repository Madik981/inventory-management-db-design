SET search_path TO inventory_app, public;

CREATE OR REPLACE FUNCTION request_transfer(
    p_transfer_id uuid, p_source integer, p_destination integer,
    p_product_id bigint, p_quantity bigint
)
RETURNS text
LANGUAGE plpgsql
SET search_path TO inventory_app, pg_temp
AS $$
DECLARE
    v_transfer warehouse_transfers%ROWTYPE;
BEGIN
    INSERT INTO warehouse_transfers (
        transfer_id, source_warehouse_id, destination_warehouse_id, product_id, quantity
    ) VALUES (p_transfer_id, p_source, p_destination, p_product_id, p_quantity)
    ON CONFLICT (transfer_id) DO NOTHING;

    SELECT * INTO v_transfer FROM warehouse_transfers
    WHERE transfer_id = p_transfer_id FOR UPDATE;
    IF ROW(v_transfer.source_warehouse_id, v_transfer.destination_warehouse_id,
           v_transfer.product_id, v_transfer.quantity)
       IS DISTINCT FROM ROW(p_source, p_destination, p_product_id, p_quantity) THEN
        RAISE EXCEPTION 'Transfer ID was reused with different contents';
    END IF;
    RETURN v_transfer.status;
END;
$$;

CREATE OR REPLACE FUNCTION advance_transfer(p_transfer_id uuid, p_action text)
RETURNS text
LANGUAGE plpgsql
SET search_path TO inventory_app, pg_temp
AS $$
DECLARE
    v_transfer warehouse_transfers%ROWTYPE;
    v_status text;
    v_warehouse integer;
    v_delta bigint;
    v_version bigint;
BEGIN
    IF p_action IS NULL OR p_action NOT IN ('dispatch', 'receive', 'cancel', 'lose') THEN
        RAISE EXCEPTION 'Action must be dispatch, receive, cancel or lose';
    END IF;

    SELECT * INTO v_transfer FROM warehouse_transfers
    WHERE transfer_id = p_transfer_id FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'Unknown transfer';
    END IF;

    v_status := CASE p_action
        WHEN 'dispatch' THEN 'in_transit'
        WHEN 'receive' THEN 'received'
        WHEN 'cancel' THEN 'cancelled'
        WHEN 'lose' THEN 'lost'
    END;
    IF v_transfer.status = v_status
       OR (p_action = 'dispatch' AND v_transfer.status IN ('received', 'lost')) THEN
        RETURN v_transfer.status;
    END IF;
    IF (p_action IN ('dispatch', 'cancel') AND v_transfer.status <> 'requested')
       OR (p_action IN ('receive', 'lose') AND v_transfer.status <> 'in_transit') THEN
        RAISE EXCEPTION 'Cannot % transfer in state %', p_action, v_transfer.status;
    END IF;

    IF p_action = 'dispatch' THEN
        v_warehouse := v_transfer.source_warehouse_id;
        v_delta := -v_transfer.quantity;
        UPDATE inventory
        SET on_hand = on_hand + v_delta,
            version = version + 1,
            updated_at = clock_timestamp()
        WHERE warehouse_id = v_warehouse AND product_id = v_transfer.product_id
          AND on_hand - reserved >= v_transfer.quantity
        RETURNING version INTO v_version;
        IF NOT FOUND THEN
            RAISE EXCEPTION 'Insufficient stock for transfer';
        END IF;
    ELSIF p_action = 'receive' THEN
        v_warehouse := v_transfer.destination_warehouse_id;
        v_delta := v_transfer.quantity;
        INSERT INTO inventory (warehouse_id, product_id)
        VALUES (v_warehouse, v_transfer.product_id)
        ON CONFLICT (warehouse_id, product_id) DO NOTHING;
        UPDATE inventory
        SET on_hand = on_hand + v_delta,
            version = version + 1,
            updated_at = clock_timestamp()
        WHERE warehouse_id = v_warehouse AND product_id = v_transfer.product_id
        RETURNING version INTO v_version;
    END IF;

    IF p_action IN ('dispatch', 'receive') THEN
        INSERT INTO inventory_events (
            operation_id, warehouse_id, product_id, event_type,
            on_hand_delta, reserved_delta, inventory_version
        ) VALUES (
            p_transfer_id, v_warehouse, v_transfer.product_id,
            CASE p_action WHEN 'dispatch' THEN 'transfer_dispatched' ELSE 'transfer_received' END,
            v_delta, 0, v_version
        );
    END IF;

    UPDATE warehouse_transfers
    SET status = v_status,
        dispatched_at = CASE WHEN p_action = 'dispatch' THEN clock_timestamp() ELSE dispatched_at END,
        received_at = CASE WHEN p_action = 'receive' THEN clock_timestamp() ELSE received_at END
    WHERE transfer_id = p_transfer_id;
    RETURN v_status;
END;
$$;

SET search_path TO inventory_app, public;

CREATE FUNCTION reserve_order(p_order_id uuid, p_items jsonb)
RETURNS text
LANGUAGE plpgsql
SET search_path TO inventory_app, pg_temp
AS $$
DECLARE
    v_items jsonb;
    v_row record;
    v_existing reservations%ROWTYPE;
    v_created integer;
    v_version bigint;
BEGIN
    IF jsonb_typeof(p_items) IS DISTINCT FROM 'array'
       OR jsonb_array_length(p_items) = 0 THEN
        RAISE EXCEPTION 'Items must be a nonempty JSON array';
    END IF;

    IF jsonb_array_length(p_items) > 100 THEN
        RAISE EXCEPTION 'A reservation may contain at most 100 warehouse-product pairs';
    END IF;

    IF EXISTS (
        SELECT 1 FROM jsonb_to_recordset(p_items)
        AS x(warehouse_id integer, product_id bigint, quantity bigint)
        WHERE warehouse_id IS NULL OR product_id IS NULL
           OR quantity IS NULL OR quantity <= 0
    ) THEN
        RAISE EXCEPTION 'Every item needs valid identifiers and positive quantity';
    END IF;

    IF EXISTS (
        SELECT 1 FROM jsonb_to_recordset(p_items)
        AS x(warehouse_id integer, product_id bigint, quantity bigint)
        GROUP BY warehouse_id, product_id HAVING count(*) > 1
    ) THEN
        RAISE EXCEPTION 'Duplicate warehouse and product pair';
    END IF;

    SELECT jsonb_agg(to_jsonb(x) ORDER BY warehouse_id, product_id)
    INTO v_items
    FROM jsonb_to_recordset(p_items)
    AS x(warehouse_id integer, product_id bigint, quantity bigint);

    INSERT INTO reservations (order_id, request_items, expires_at)
    VALUES (p_order_id, v_items, clock_timestamp() + interval '15 minutes')
    ON CONFLICT (order_id) DO NOTHING;
    GET DIAGNOSTICS v_created = ROW_COUNT;

    SELECT * INTO v_existing FROM reservations
    WHERE order_id = p_order_id FOR UPDATE;

    IF v_existing.request_items <> v_items THEN
        RAISE EXCEPTION 'Order ID was reused with different items';
    END IF;
    IF v_created = 0 THEN
        RETURN v_existing.status;
    END IF;

    FOR v_row IN
        SELECT * FROM jsonb_to_recordset(v_items)
        AS x(warehouse_id integer, product_id bigint, quantity bigint)
        ORDER BY warehouse_id, product_id
    LOOP
        UPDATE inventory
        SET reserved = reserved + v_row.quantity,
            version = version + 1,
            updated_at = clock_timestamp()
        WHERE warehouse_id = v_row.warehouse_id
          AND product_id = v_row.product_id
          AND on_hand - reserved >= v_row.quantity
        RETURNING version INTO v_version;

        IF NOT FOUND THEN
            RAISE EXCEPTION 'Insufficient stock for warehouse %, product %',
                v_row.warehouse_id, v_row.product_id;
        END IF;

        INSERT INTO reservation_items
        VALUES (p_order_id, v_row.warehouse_id, v_row.product_id, v_row.quantity);

        INSERT INTO inventory_events (
            operation_id, warehouse_id, product_id, event_type,
            on_hand_delta, reserved_delta, inventory_version
        ) VALUES (
            p_order_id, v_row.warehouse_id, v_row.product_id, 'reserved',
            0, v_row.quantity, v_version
        );
    END LOOP;

    UPDATE reservations
    SET expires_at = clock_timestamp() + interval '15 minutes'
    WHERE order_id = p_order_id;
    RETURN 'active';
END;
$$;

CREATE FUNCTION finish_reservation(p_order_id uuid, p_action text)
RETURNS text
LANGUAGE plpgsql
SET search_path TO inventory_app, pg_temp
AS $$
DECLARE
    v_reservation reservations%ROWTYPE;
    v_item record;
    v_status text;
    v_delta bigint;
    v_version bigint;
BEGIN
    IF p_action IS NULL OR p_action NOT IN ('confirm', 'release', 'expire') THEN
        RAISE EXCEPTION 'Action must be confirm, release or expire';
    END IF;

    SELECT * INTO v_reservation FROM reservations
    WHERE order_id = p_order_id FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'Unknown reservation';
    END IF;
    IF v_reservation.status <> 'active' THEN
        RETURN v_reservation.status;
    END IF;

    PERFORM 1 FROM inventory AS i
    JOIN reservation_items AS r USING (warehouse_id, product_id)
    WHERE r.order_id = p_order_id
    ORDER BY i.warehouse_id, i.product_id
    FOR UPDATE OF i;

    IF clock_timestamp() >= v_reservation.expires_at THEN
        v_status := 'expired';
    ELSIF p_action = 'expire' THEN
        RETURN 'active';
    ELSIF p_action = 'confirm' THEN
        v_status := 'confirmed';
    ELSE
        v_status := 'released';
    END IF;

    FOR v_item IN
        SELECT * FROM reservation_items WHERE order_id = p_order_id
        ORDER BY warehouse_id, product_id
    LOOP
        v_delta := CASE WHEN v_status = 'confirmed' THEN -v_item.quantity ELSE 0 END;
        UPDATE inventory
        SET on_hand = on_hand + v_delta,
            reserved = reserved - v_item.quantity,
            version = version + 1,
            updated_at = clock_timestamp()
        WHERE warehouse_id = v_item.warehouse_id
          AND product_id = v_item.product_id
        RETURNING version INTO v_version;

        INSERT INTO inventory_events (
            operation_id, warehouse_id, product_id, event_type,
            on_hand_delta, reserved_delta, inventory_version
        ) VALUES (
            p_order_id, v_item.warehouse_id, v_item.product_id, v_status,
            v_delta, -v_item.quantity, v_version
        );
    END LOOP;

    UPDATE reservations
    SET status = v_status, completed_at = clock_timestamp()
    WHERE order_id = p_order_id;
    RETURN v_status;
END;
$$;

CREATE FUNCTION adjust_stock(
    p_operation_id uuid, p_warehouse_id integer,
    p_product_id bigint, p_delta bigint, p_reason text
)
RETURNS text
LANGUAGE plpgsql
SET search_path TO inventory_app, pg_temp
AS $$
DECLARE
    v_existing stock_adjustments%ROWTYPE;
    v_created integer;
    v_version bigint;
BEGIN
    INSERT INTO stock_adjustments
        (operation_id, warehouse_id, product_id, delta, reason)
    VALUES (p_operation_id, p_warehouse_id, p_product_id, p_delta, p_reason)
    ON CONFLICT (operation_id) DO NOTHING;
    GET DIAGNOSTICS v_created = ROW_COUNT;

    SELECT * INTO v_existing FROM stock_adjustments
    WHERE operation_id = p_operation_id;
    IF ROW(v_existing.warehouse_id, v_existing.product_id, v_existing.delta, v_existing.reason)
       IS DISTINCT FROM ROW(p_warehouse_id, p_product_id, p_delta, p_reason) THEN
        RAISE EXCEPTION 'Operation ID was reused with a different adjustment';
    END IF;
    IF v_created = 0 THEN
        RETURN 'already_applied';
    END IF;

    UPDATE inventory
    SET on_hand = on_hand + p_delta,
        version = version + 1,
        updated_at = clock_timestamp()
    WHERE warehouse_id = p_warehouse_id AND product_id = p_product_id
      AND on_hand + p_delta >= reserved
    RETURNING version INTO v_version;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'Unknown stock row or adjustment would consume reserved stock';
    END IF;

    INSERT INTO inventory_events (
        operation_id, warehouse_id, product_id, event_type,
        on_hand_delta, reserved_delta, inventory_version
    ) VALUES (
        p_operation_id, p_warehouse_id, p_product_id, 'adjusted',
        p_delta, 0, v_version
    );
    RETURN 'applied';
END;
$$;

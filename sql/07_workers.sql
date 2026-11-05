SET search_path TO inventory_app, public;

ALTER TABLE inventory_events ADD COLUMN IF NOT EXISTS delivery_claim uuid;
ALTER TABLE inventory_events ADD COLUMN IF NOT EXISTS delivery_until timestamptz;

CREATE OR REPLACE FUNCTION expire_one()
RETURNS uuid LANGUAGE plpgsql SECURITY DEFINER
SET search_path TO inventory_app, pg_temp
AS $$
DECLARE v_order uuid;
BEGIN
    SELECT order_id INTO v_order FROM reservations
    WHERE status = 'active' AND expires_at <= statement_timestamp()
    ORDER BY expires_at, order_id
    FOR UPDATE SKIP LOCKED LIMIT 1;
    IF v_order IS NOT NULL THEN
        PERFORM finish_reservation(v_order, 'expire');
    END IF;
    RETURN v_order;
END;
$$;

CREATE OR REPLACE FUNCTION claim_events(p_claim uuid, p_limit integer DEFAULT 100)
RETURNS SETOF inventory_events LANGUAGE plpgsql SECURITY DEFINER
SET search_path TO inventory_app, pg_temp
AS $$
BEGIN
    IF p_claim IS NULL OR p_limit NOT BETWEEN 1 AND 1000 THEN
        RAISE EXCEPTION 'Invalid event claim';
    END IF;
    RETURN QUERY
    WITH candidates AS (
        SELECT event_id FROM inventory_events
        WHERE published_at IS NULL
          AND (delivery_until IS NULL OR delivery_until <= statement_timestamp())
        ORDER BY event_id FOR UPDATE SKIP LOCKED LIMIT p_limit
    )
    UPDATE inventory_events e
    SET delivery_claim = p_claim, delivery_until = statement_timestamp() + interval '30 seconds'
    FROM candidates c WHERE e.event_id = c.event_id RETURNING e.*;
END;
$$;

CREATE OR REPLACE FUNCTION acknowledge_events(p_claim uuid, p_events bigint[])
RETURNS integer LANGUAGE plpgsql SECURITY DEFINER
SET search_path TO inventory_app, pg_temp
AS $$
DECLARE v_count integer;
BEGIN
    UPDATE inventory_events
    SET published_at = clock_timestamp(), delivery_claim = NULL, delivery_until = NULL
    WHERE event_id = ANY(p_events) AND delivery_claim = p_claim AND published_at IS NULL;
    GET DIAGNOSTICS v_count = ROW_COUNT;
    RETURN v_count;
END;
$$;

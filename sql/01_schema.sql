CREATE SCHEMA inventory_app;
SET search_path TO inventory_app, public;

CREATE TABLE products (
    product_id bigint PRIMARY KEY,
    sku text NOT NULL UNIQUE,
    name text NOT NULL
);

CREATE TABLE warehouses (
    warehouse_id integer PRIMARY KEY,
    name text NOT NULL,
    city text NOT NULL
);

CREATE TABLE inventory (
    warehouse_id integer NOT NULL REFERENCES warehouses,
    product_id bigint NOT NULL REFERENCES products,
    on_hand bigint NOT NULL DEFAULT 0,
    reserved bigint NOT NULL DEFAULT 0,
    available bigint GENERATED ALWAYS AS (on_hand - reserved) STORED,
    version bigint NOT NULL DEFAULT 0,
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (warehouse_id, product_id),
    CHECK (on_hand >= 0),
    CHECK (reserved >= 0 AND reserved <= on_hand)
) PARTITION BY HASH (warehouse_id);

DO $$
BEGIN
    FOR i IN 0..15 LOOP
        EXECUTE format(
            'CREATE TABLE inventory_p%s PARTITION OF inventory FOR VALUES WITH (MODULUS 16, REMAINDER %s)',
            i, i
        );
    END LOOP;
END;
$$;

CREATE TABLE reservations (
    order_id uuid PRIMARY KEY,
    request_items jsonb NOT NULL,
    status text NOT NULL DEFAULT 'active',
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    expires_at timestamptz NOT NULL,
    completed_at timestamptz,
    CHECK (status IN ('active', 'confirmed', 'released', 'expired')),
    CHECK ((status = 'active') = (completed_at IS NULL))
);

CREATE INDEX reservations_expiry_idx
ON reservations (expires_at, order_id)
WHERE status = 'active';

CREATE TABLE reservation_items (
    order_id uuid NOT NULL REFERENCES reservations,
    warehouse_id integer NOT NULL,
    product_id bigint NOT NULL,
    quantity bigint NOT NULL CHECK (quantity > 0),
    PRIMARY KEY (order_id, warehouse_id, product_id),
    FOREIGN KEY (warehouse_id, product_id)
        REFERENCES inventory (warehouse_id, product_id)
);

CREATE TABLE inventory_events (
    event_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    operation_id uuid NOT NULL,
    warehouse_id integer NOT NULL,
    product_id bigint NOT NULL,
    event_type text NOT NULL,
    on_hand_delta bigint NOT NULL,
    reserved_delta bigint NOT NULL,
    inventory_version bigint NOT NULL,
    occurred_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    published_at timestamptz,
    UNIQUE (operation_id, warehouse_id, product_id, event_type),
    FOREIGN KEY (warehouse_id, product_id)
        REFERENCES inventory (warehouse_id, product_id)
);

CREATE INDEX inventory_events_pending_idx
ON inventory_events (event_id) WHERE published_at IS NULL;

CREATE TABLE stock_adjustments (
    operation_id uuid PRIMARY KEY,
    warehouse_id integer NOT NULL,
    product_id bigint NOT NULL,
    delta bigint NOT NULL CHECK (delta <> 0),
    reason text NOT NULL CHECK (length(reason) > 0),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    FOREIGN KEY (warehouse_id, product_id)
        REFERENCES inventory (warehouse_id, product_id)
);

CREATE TABLE warehouse_transfers (
    transfer_id uuid PRIMARY KEY,
    source_warehouse_id integer NOT NULL REFERENCES warehouses,
    destination_warehouse_id integer NOT NULL REFERENCES warehouses,
    product_id bigint NOT NULL REFERENCES products,
    quantity bigint NOT NULL CHECK (quantity > 0),
    status text NOT NULL DEFAULT 'requested',
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    dispatched_at timestamptz,
    received_at timestamptz,
    CHECK (source_warehouse_id <> destination_warehouse_id),
    CHECK (status IN ('requested', 'in_transit', 'received', 'cancelled', 'lost'))
);

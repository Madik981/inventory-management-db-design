import concurrent.futures
import json
import os
from pathlib import Path
import subprocess
import threading
import uuid

ROOT = Path(__file__).resolve().parents[1]
PSQL = os.environ.get("PSQL", "psql")
DATABASE = "case2_test_" + uuid.uuid4().hex
ENV = os.environ.copy()
ENV.setdefault("PGHOST", "127.0.0.1")
ENV.setdefault("PGPORT", "55434")
ENV.setdefault("PGUSER", "postgres")
ENV.setdefault("PGPASSWORD", "inventory_demo")
checks = 0


def execute(sql, database=DATABASE, fail=True):
    result = subprocess.run(
        [PSQL, "-X", "-q", "-A", "-t", "-v", "ON_ERROR_STOP=1", "-d", database],
        input="SET search_path TO inventory_app, public;\n" + sql,
        text=True, capture_output=True, env=ENV, timeout=30
    )
    if fail and result.returncode:
        raise RuntimeError(result.stderr)
    return result


def check(condition, label):
    global checks
    if not condition:
        raise AssertionError(label)
    checks += 1
    print("PASS " + label, flush=True)


def new_stock(product_id, quantity=1):
    execute(f"INSERT INTO products VALUES ({product_id}, 'SKU-{product_id}', 'Test product');"
            f"INSERT INTO inventory (warehouse_id, product_id, on_hand) VALUES (1, {product_id}, {quantity});")


def reserve(order_id, product_id, quantity=1, warehouse_id=1):
    payload = json.dumps([{"warehouse_id": warehouse_id, "product_id": product_id, "quantity": quantity}])
    return f"SELECT reserve_order('{order_id}', '{payload}'::jsonb);"


def state(product_id):
    return execute(f"SELECT on_hand, reserved, available FROM inventory WHERE warehouse_id=1 AND product_id={product_id};").stdout.strip()


def race(statements):
    barrier = threading.Barrier(len(statements))
    def run(statement):
        barrier.wait(timeout=10)
        return execute(statement, fail=False)
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(statements)) as pool:
        return list(pool.map(run, statements))


def main():
    execute(f'CREATE DATABASE "{DATABASE}";', database="postgres")
    try:
        for name in ("01_schema.sql", "02_operations.sql"):
            execute((ROOT / "sql" / name).read_text(encoding="utf-8"))
        execute("INSERT INTO warehouses VALUES (1, 'Almaty', 'Almaty'), (2, 'Astana', 'Astana');")

        new_stock(1)
        orders = [str(uuid.uuid4()) for _ in range(20)]
        results = race([reserve(order, 1) for order in orders])
        check(sum(r.returncode == 0 for r in results) == 1 and state(1) == "1|1|0",
              "20 simultaneous buyers produce one reservation for one item")
        check(all(r.returncode == 0 or "Insufficient stock" in r.stderr for r in results),
              "All losing buyers receive an explicit insufficient stock error")
        winner = orders[next(i for i, r in enumerate(results) if r.returncode == 0)]
        results = race([f"SELECT finish_reservation('{winner}', 'confirm');"] * 8)
        count = execute(f"SELECT count(*) FROM inventory_events WHERE operation_id='{winner}' AND event_type='confirmed';").stdout.strip()
        check(all(r.returncode == 0 for r in results) and state(1) == "0|0|0" and count == "1",
              "Concurrent duplicate confirmations consume stock once")

        new_stock(2, 5)
        order = str(uuid.uuid4())
        results = race([reserve(order, 2, 2)] * 8)
        check(all(r.returncode == 0 for r in results) and state(2) == "5|2|3",
              "Concurrent duplicate reservation requests reserve only once")
        check(execute(reserve(order, 2, 3), fail=False).returncode != 0 and state(2) == "5|2|3",
              "Reusing an order ID with changed items is rejected")
        execute(f"SELECT finish_reservation('{order}', 'release'); SELECT finish_reservation('{order}', 'release');")
        check(state(2) == "5|0|5", "Duplicate cancellation releases stock once")

        new_stock(3)
        execute("INSERT INTO inventory (warehouse_id, product_id, on_hand) VALUES (2, 3, 0);")
        order = str(uuid.uuid4())
        payload = json.dumps([{"warehouse_id": w, "product_id": 3, "quantity": 1} for w in (1, 2)])
        result = execute(f"SELECT reserve_order('{order}', '{payload}');", fail=False)
        count = execute(f"SELECT count(*) FROM reservations WHERE order_id='{order}';").stdout.strip()
        check(result.returncode != 0 and state(3) == "1|0|1" and count == "0",
              "A failed multiwarehouse reservation rolls back all earlier items")

        new_stock(4)
        order = str(uuid.uuid4())
        execute(reserve(order, 4))
        execute(f"UPDATE reservations SET expires_at=clock_timestamp()-interval '1 second' WHERE order_id='{order}';")
        results = race([f"SELECT finish_reservation('{order}', '{action}');" for action in ("confirm", "expire")])
        status = execute(f"SELECT status FROM reservations WHERE order_id='{order}';").stdout.strip()
        check(all(r.returncode == 0 for r in results) and status == "expired" and state(4) == "1|0|1",
              "Expiry and late confirmation cannot both consume or release stock")

        new_stock(5, 2)
        order = str(uuid.uuid4())
        execute(reserve(order, 5))
        operation = str(uuid.uuid4())
        command = f"SELECT adjust_stock('{operation}', 1, 5, 3, 'Delivery');"
        results = race([command] * 8)
        check(all(r.returncode == 0 for r in results) and state(5) == "5|1|4",
              "Duplicate concurrent deliveries add physical stock once")
        result = execute(f"SELECT adjust_stock('{uuid.uuid4()}', 1, 5, -5, 'Correction');", fail=False)
        check(result.returncode != 0 and state(5) == "5|1|4",
              "Negative adjustments cannot consume reserved units")

        new_stock(6)
        order = str(uuid.uuid4())
        execute("BEGIN;" + reserve(order, 6) + "ROLLBACK;")
        check(state(6) == "1|0|1", "Transaction rollback leaves no held stock")

        new_stock(7)
        order = str(uuid.uuid4())
        execute(reserve(order, 7))
        status = execute(f"SELECT finish_reservation('{order}', 'expire');").stdout.strip()
        check(status == "active" and state(7) == "1|1|0", "Early expiry does not release a valid reservation")
        execute(f"SELECT finish_reservation('{order}', 'confirm'); SELECT finish_reservation('{order}', 'release');")
        check(state(7) == "0|0|0", "Cancellation after confirmation does not recreate sold stock")

        print(f"All {checks} checks passed.")
    finally:
        execute(f'DROP DATABASE "{DATABASE}";', database="postgres")


if __name__ == "__main__":
    main()

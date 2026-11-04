import os
from pathlib import Path
import subprocess
import time

ROOT = Path(__file__).resolve().parents[1]
env = os.environ.copy()
env.update(PGHOST='127.0.0.1', PGPORT='55434', PGUSER='postgres', PGPASSWORD='inventory_demo')
base = ['psql', '-X', '-q', '-A', '-t', '-d', 'inventory_defense']


def query(sql):
    result = subprocess.run(base + ['-v', 'ON_ERROR_STOP=1', '-c', sql], env=env, text=True, capture_output=True, timeout=10)
    if result.returncode:
        raise RuntimeError(result.stderr)
    return result.stdout.strip()


def wait_for(sql):
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if query(sql) == 't':
            return
        time.sleep(.1)
    raise AssertionError('Expected session state was not observed')


source = (ROOT / 'sql/defense_session_a.sql').read_text()
before, after = source.split('\nCOMMIT;', 1)
a = subprocess.Popen(base + ['-v', 'ON_ERROR_STOP=1'], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env, text=True)
b = None
try:
    a.stdin.write(before + '\n')
    a.stdin.flush()
    wait_for("SELECT EXISTS (SELECT 1 FROM pg_stat_activity WHERE application_name='inventory_defense_A' AND state='idle in transaction');")
    b = subprocess.Popen(base + ['-v', 'ON_ERROR_STOP=0', '-f', str(ROOT / 'sql/defense_session_b.sql')], stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env, text=True)
    wait_for("SELECT EXISTS (SELECT 1 FROM pg_stat_activity WHERE application_name='inventory_defense_B' AND wait_event_type='Lock' AND cardinality(pg_blocking_pids(pid))>0);")
    print('PASS Terminal B is blocked by Terminal A before COMMIT')
    observer = (ROOT / 'sql/defense_observer.sql').read_text()
    print(query(observer))
    a.stdin.write('COMMIT;' + after)
    a.stdin.close()
    a.stdin = None
    a_out, a_err = a.communicate(timeout=10)
    b_out, b_err = b.communicate(timeout=10)
    assert a.returncode == 0, a_err
    assert 'Insufficient stock for warehouse 1, product 99' in b_err, b_err
    assert query("SELECT on_hand || '|' || reserved || '|' || available FROM inventory_app.inventory WHERE warehouse_id=1 AND product_id=99;") == '0|0|0'
    assert query("SELECT count(*) FROM inventory_app.reservations WHERE order_id='20000000-0000-0000-0000-000000000002';") == '0'
    assert query("SELECT count(*) FROM inventory_app.inventory_events WHERE product_id=99 AND event_type='confirmed';") == '1'
    print('PASS Terminal B rejects the purchase after A commits')
    print('PASS Only A confirms; final balance is 0|0|0; losing order is absent')
finally:
    for process in (a, b):
        if process and process.poll() is None:
            process.kill()
            process.communicate()

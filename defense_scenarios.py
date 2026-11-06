"""Repeatable, isolated teaching scenarios using the project's real SQL and workers."""
from contextlib import closing
import json
import threading
import time
import uuid

import psycopg2

from workers import deliver_once, sink_settings


def check(condition, message):
    if not condition:
        raise RuntimeError('Проверка не пройдена: ' + message)
    print('  OK: ' + message)


def new_id():
    return str(uuid.uuid4())


def items(product, *pairs):
    return [{'warehouse_id': warehouse, 'product_id': product, 'quantity': quantity}
            for warehouse, quantity in pairs]


def fixture(console, label, balances=(100, 50, 25)):
    console.require_demo()
    product = int(uuid.uuid4().hex[:12], 16)
    # Atomic test fixture, never edits an existing product. Business operations below use public functions.
    with closing(console.connect()) as connection:
        connection.autocommit = False
        with connection, connection.cursor() as cursor:
            cursor.execute('INSERT INTO inventory_app.products VALUES (%s,%s,%s)',
                           (product, 'DEMO-' + str(product), 'Demo: ' + label))
            for warehouse, quantity in enumerate(balances, 1):
                cursor.execute('INSERT INTO inventory_app.inventory(warehouse_id,product_id,on_hand) VALUES (%s,%s,%s)',
                               (warehouse, product, quantity))
    print(f'Новый учебный товар: {product}. Сценарий можно запускать повторно.')
    return product


def stock(console, product):
    return console.query('SELECT warehouse_id,on_hand,reserved,available FROM inventory_app.inventory WHERE product_id=%s ORDER BY warehouse_id', (product,))


def expect_error(action, expected):
    try:
        action()
    except psycopg2.Error as error:
        if error.pgcode != 'P0001' or expected not in (error.diag.message_primary or ''):
            raise
        print('  ОЖИДАЕМОЕ ОТКЛОНЕНИЕ: ' + error.diag.message_primary)
    else:
        raise RuntimeError('Ожидалось отклонение операции: ' + expected)


def checkout(c):
    product = fixture(c, 'Checkout')
    order = new_id()
    payload = items(product, (1, 2), (2, 1))
    c.step('1. Один заказ: 2 единицы в Алматы + 1 в Астане. Одна транзакция резервирует обе позиции.')
    c.balances(product)
    check(c.write('reserve_order', order, payload)['status'] == 'active', 'Заказ active')
    after = stock(c, product)
    c.balances(product)
    check(after == [(1, 100, 2, 98), (2, 50, 1, 49), (3, 25, 0, 25)], 'Физический остаток прежний, доступный уменьшился')
    c.step('2. Повторяем тот же запрос. Это имитирует потерянный ответ и повтор клиента.')
    c.write('reserve_order', order, payload)
    check(stock(c, product) == after, 'Повтор не создал второй резерв')
    expect_error(lambda: c.write('reserve_order', order, items(product, (1, 3))), 'different items')
    c.step('3. Подтверждаем покупку дважды. Списание должно произойти один раз.')
    c.write('finish_reservation', order, 'confirm')
    c.write('finish_reservation', order, 'confirm')
    check(stock(c, product) == [(1, 98, 0, 98), (2, 49, 0, 49), (3, 25, 0, 25)], 'Продажа списана ровно один раз')
    c.balances(product)
    c.step('4. Другой покупатель резервирует товар и отменяет заказ.')
    cancelled = new_id()
    c.write('reserve_order', cancelled, items(product, (3, 3)))
    c.write('finish_reservation', cancelled, 'release')
    check(stock(c, product)[2] == (3, 25, 0, 25), 'Отмена возвращает доступность, без списания on_hand')


def rollback(c):
    product = fixture(c, 'Atomic rollback')
    before = stock(c, product)
    order = new_id()
    c.step('1. В Алматы хватает товара, в Астане просим 1000. Весь заказ должен откатиться.')
    expect_error(lambda: c.write('reserve_order', order, items(product, (1, 1), (2, 1000))), 'Insufficient stock')
    check(stock(c, product) == before, 'Ни один склад не изменился')
    rows = c.query('''SELECT
        (SELECT count(*) FROM inventory_app.reservations WHERE order_id=%s),
        (SELECT count(*) FROM inventory_app.inventory_events WHERE operation_id=%s)''', (order, order))
    check(rows == [(0, 0)], 'Нет ни заказа, ни событий откатившейся операции')
    c.step('2. Явный ROLLBACK: внутри транзакции виден резерв, после отката он исчезнет.')
    with closing(c.connect()) as connection:
        connection.autocommit = False
        try:
            with connection.cursor() as cursor:
                statement = 'SELECT inventory_app.reserve_order(%s,%s::jsonb)'
                params = (new_id(), json.dumps(items(product, (1, 1))))
                c.show('BEGIN;')
                c.show(statement, params)
                cursor.execute(statement, params)
                cursor.execute('SELECT reserved FROM inventory_app.inventory WHERE warehouse_id=1 AND product_id=%s', (product,))
                check(cursor.fetchone()[0] == 1, 'Внутри своей транзакции reserved = 1')
        finally:
            c.show('ROLLBACK;')
            connection.rollback()
    check(stock(c, product) == before, 'ROLLBACK убрал изменения')
    c.balances(product)


def expiry(c):
    product = fixture(c, 'Expiry', (5,))
    order = new_id()
    c.step('1. Создаём обычный резерв с реальным сроком 15 минут.')
    c.write('reserve_order', order, items(product, (1, 2)))
    c.query('SELECT status,expires_at FROM inventory_app.reservations WHERE order_id=%s', (order,), display=True)
    c.step('2. Только учебная фикстура: переносим срок этого резерва в прошлое, чтобы не ждать 15 минут.')
    c.query("UPDATE inventory_app.reservations SET expires_at=clock_timestamp()-interval '1 second' WHERE order_id=%s", (order,))
    c.step('3. Позднее подтверждение само проверяет срок, даже если expiry-worker ещё не пришёл.')
    check(c.write('finish_reservation', order, 'confirm')['status'] == 'expired', 'Поздняя покупка возвращает expired')
    c.write('finish_reservation', order, 'expire')
    check(stock(c, product) == [(1, 5, 0, 5)], 'Товар доступен снова; повторное освобождение безопасно')
    c.balances(product)


def race(c):
    product = fixture(c, 'Last item', (1,))
    first, second = new_id(), new_id()
    statement = 'SELECT inventory_app.reserve_order(%s,%s::jsonb)'
    payload = json.dumps(items(product, (1, 1)))
    c.step('Две отдельные сессии PostgreSQL изображают два экземпляра приложения. A резервирует последнюю единицу, B ждёт блокировку.')
    results = []
    with closing(c.connect()) as a, closing(c.connect()) as b:
        a.autocommit = b.autocommit = False
        # The controlled interleaving lasts less than a second normally; no sleep while explaining.
        with b.cursor() as cursor:
            cursor.execute("SET LOCAL lock_timeout='5s'; SET LOCAL statement_timeout='8s'")
        def purchase_b():
            try:
                with b.cursor() as cursor:
                    cursor.execute(statement, (second, payload))
                    status = cursor.fetchone()[0]
                b.commit()
                results.append(('success', status))
            except psycopg2.Error as error:
                b.rollback()
                results.append(('error', error.pgcode, error.diag.message_primary))
        thread = None
        try:
            with a.cursor() as cursor:
                c.show('BEGIN; -- Session A')
                c.show(statement, (first, payload))
                cursor.execute(statement, (first, payload))
            print('A: reserved = 1 внутри транзакции; COMMIT пока не выполнен.')
            c.show(statement + ' -- Session B', (second, payload))
            thread = threading.Thread(target=purchase_b, name='defense-customer-b')
            thread.start()
            blocked = False
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline:
                rows = c.query('SELECT %s = ANY(pg_blocking_pids(%s))', (a.get_backend_pid(), b.get_backend_pid()))
                if rows[0][0]:
                    blocked = True
                    break
                if not thread.is_alive():
                    break
                time.sleep(0.02)
            check(blocked, 'PostgreSQL подтвердил: B ждёт блокировку A')
            check(stock(c, product) == [(1, 1, 0, 1)], 'MVCC: обычный SELECT видит предыдущую committed-версию, без ожидания A')
            c.show('COMMIT; -- Session A')
            a.commit()
            print('A: COMMIT. B просыпается и перепроверяет условие available >= 1.')
            thread.join(timeout=9)
            check(not thread.is_alive(), 'Сессия B завершилась')
            check(len(results) == 1 and results[0][0:2] == ('error', 'P0001')
                  and 'Insufficient stock' in results[0][2], 'B получил Insufficient stock, второй резерв не создан')
        finally:
            a.rollback()
            if thread is not None and thread.is_alive():
                b.cancel()
                thread.join(timeout=10)
            b.rollback()
    check(stock(c, product) == [(1, 1, 1, 0)], 'Осталась ровно одна активная бронь')
    c.query('SELECT order_id,status FROM inventory_app.reservations WHERE order_id IN (%s,%s)', (first, second), display=True)
    c.write('finish_reservation', first, 'confirm')
    check(stock(c, product) == [(1, 0, 0, 0)], 'Товар купил только A')
    c.balances(product)


def replication(c):
    product = fixture(c, 'Replication', (10,))
    c.step('1. Принимаем 5 единиц. Получаем подтверждённый COMMIT и WAL-токен.')
    result = c.write('adjust_stock', new_id(), 1, product, 5, 'Defense receipt')
    print('Read token: ' + json.dumps(result['token']))
    c.step('2. Читаем через существующий InventoryClient: ждём replay до 250 мс, затем fallback на primary.')
    read = c.client.read_inventory(1, product, after=result['token'])
    print(json.dumps(read, indent=2))
    check(read['stock']['on_hand'] == 15, 'Read-your-writes: видим свою приёмку')
    print('source показывает фактический источник. caught_up — replay достиг проверенной WAL-позиции.')
    print('ACK синхронной реплики означает durable flush WAL. Replay проверяется отдельно.')
    print('Этот сценарий наблюдает реальную репликацию; задержка и отказ здесь искусственно не создаются.')
    c.health()


def partitioning(c):
    c.step('1. Покажем реальные 16 HASH-партиций по warehouse_id в одной PostgreSQL-базе.')
    c.query("SELECT relid::regclass AS partition, isleaf FROM pg_partition_tree('inventory_app.inventory')", display=True)
    c.step('2. EXPLAIN: условие warehouse_id позволяет PostgreSQL читать нужную партицию.')
    c.query('EXPLAIN (COSTS OFF) SELECT * FROM inventory_app.inventory WHERE warehouse_id=1 AND product_id=17', display=True)
    print('Партиции локальные. Физического шардирования по разным серверам в этом стенде нет.')


def transfers(c):
    product = fixture(c, 'Transfer', (10, 0))
    transfer = new_id()
    c.step('1. Заявка на перемещение 3 единиц Алматы → Астана. Пока ничего не списывается.')
    c.write('request_transfer', transfer, 1, 2, product, 3)
    check(stock(c, product) == [(1, 10, 0, 10), (2, 0, 0, 0)], 'Заявка не меняет остатки')
    c.step('2. Отправка: источник −3. Эти 3 единицы в пути, в Астане их ещё нет.')
    c.write('advance_transfer', transfer, 'dispatch')
    c.write('advance_transfer', transfer, 'dispatch')
    check(stock(c, product) == [(1, 7, 0, 7), (2, 0, 0, 0)], 'Повтор dispatch не списал товар дважды')
    c.balances(product)
    c.step('3. Приёмка в Астане: получатель +3. Повтор receive безопасен.')
    c.write('advance_transfer', transfer, 'receive')
    c.write('advance_transfer', transfer, 'receive')
    check(stock(c, product) == [(1, 7, 0, 7), (2, 3, 0, 3)], 'Итого 10 единиц, перемещение завершено')
    c.balances(product)


class LostAcknowledgement(Exception):
    """Fault injected after sink commit, before source acknowledgement."""


def outbox(c):
    sink = sink_settings()
    # Check the sink before making new writes; drain earlier demos using the real relay.
    c.query('SELECT count(*) FROM inventory_projection.inbox', settings=sink)
    for _ in range(100):
        if deliver_once(c.client, sink) == 0:
            break
    else:
        raise RuntimeError('В демо-базе больше 10000 ожидающих событий. Сначала обработайте очередь.')
    product = fixture(c, 'Outbox', (10,))
    operation = new_id()
    c.step('1. Приёмка +5 создаёт изменение остатка и событие в одной транзакции.')
    c.write('adjust_stock', operation, 1, product, 5, 'Outbox demo')
    c.query('SELECT event_id,event_type,on_hand_delta,published_at FROM inventory_app.inventory_events WHERE operation_id=%s', (operation,), display=True)
    c.step('2. Реальный relay отправит событие. Имитируем сбой ПОСЛЕ COMMIT потребителя, но ДО ACK источнику.')
    def crash():
        raise LostAcknowledgement()
    c.show('SELECT * FROM inventory_app.claim_events(%s,100);', ('<claim UUID>',))
    c.show('SELECT inventory_projection.apply_event(...); -- sink transaction, then COMMIT')
    try:
        deliver_once(c.client, sink, after_delivery=crash)
    except LostAcknowledgement:
        print('  Ожидаемый сбой: потребитель сохранил событие, published_at ещё NULL.')
    else:
        raise RuntimeError('Relay не получил ожидаемое событие.')
    check(c.query('SELECT published_at IS NULL FROM inventory_app.inventory_events WHERE operation_id=%s', (operation,)) == [(True,)], 'Источник ещё не получил ACK')
    movement_sql = '''SELECT on_hand_change,reserved_change FROM inventory_projection.movements
                      WHERE source=%s AND warehouse_id=1 AND product_id=%s'''
    params = (c.primary['dbname'], product)
    check(c.query(movement_sql, params, settings=sink) == [(5, 0)], 'Потребитель уже применил +5')
    c.step('3. Учебная фикстура: истекает lease события, не ждём 30 секунд. Relay доставит его повторно.')
    c.query("UPDATE inventory_app.inventory_events SET delivery_until=clock_timestamp()-interval '1 second' WHERE operation_id=%s", (operation,))
    count = deliver_once(c.client, sink)
    c.show('SELECT inventory_app.acknowledge_events(%s,%s);', ('<claim UUID>', '<event IDs>'))
    check(count == 1, 'ACK отмечает одно событие доставленным')
    check(c.query(movement_sql, params, settings=sink) == [(5, 0)], 'Inbox убрал дубликат: движение +5, а не +10')
    check(c.query('SELECT count(*) FROM inventory_projection.inbox WHERE source=%s AND operation_id=%s',
                  (c.primary['dbname'], operation), settings=sink) == [(1,)], 'В inbox одна запись для операции')
    c.query(movement_sql, params, settings=sink, display=True)
    print('Projection хранит движения (+5), а inventory — остаток (15). Начальный запас 10 не входит в projection.')


SCENARIOS = [
    ('checkout', 'Покупка, несколько складов, идемпотентность и отмена', checkout),
    ('rollback', 'Недостаток товара и полный откат транзакции', rollback),
    ('expiry', 'Истечение резерва и поздняя покупка', expiry),
    ('race', 'Последний товар: две сессии, блокировка и MVCC', race),
    ('replication', 'Primary / standby, WAL, ACK и актуальное чтение', replication),
    ('partitioning', 'Партиции по складу и EXPLAIN', partitioning),
    ('transfers', 'Перемещение между складами и повторные запросы', transfers),
    ('outbox', 'Outbox / Inbox: сбой до ACK и повторная доставка', outbox),
]


def run_scenario(console, name):
    console.require_demo()
    selected = SCENARIOS if name == 'all' else [entry for entry in SCENARIOS if entry[0] == name]
    if not selected:
        raise ValueError('Unknown scenario: ' + name)
    for key, title, action in selected:
        print('\n' + '=' * 68 + '\n' + title + '\n' + '=' * 68)
        action(console)
        console.invariants()
        print(f'\nPASS [{key}]: результат проверен запросами к БД.')

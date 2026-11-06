"""Interactive terminal UI for the existing inventory client (Python + psycopg2 only)."""
import argparse
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sys
import uuid

try:
    import psycopg2
    from psycopg2 import sql
    from inventory_client import InventoryClient, connection_settings
except ImportError:
    print('Install dependencies: python -m pip install -r requirements.txt')
    raise SystemExit(1)

ROOT = Path(__file__).resolve().parent
DEMO_DATABASE = 'inventory_console'
SCHEMA_FILES = ('01_schema.sql', '02_operations.sql', '06_transfers.sql',
                '07_workers.sql', '08_access.sql')
WRITE_METHODS = {'reserve_order', 'finish_reservation', 'adjust_stock',
                 'request_transfer', 'advance_transfer'}


def number(prompt, default=None, minimum=1, maximum=9223372036854775807):
    while True:
        value = input(f'{prompt}' + (f' [{default}]' if default is not None else '') + ': ').strip()
        try:
            result = int(value) if value else default
            if result is not None and minimum <= result <= maximum:
                return result
        except ValueError:
            pass
        print(f'Введите целое число от {minimum} до {maximum}. Ctrl+C — назад.')


def text_input(prompt, default=None):
    while True:
        result = input(prompt + (f' [{default}]' if default else '') + ': ').strip() or default
        if result:
            return result
        print('Поле не должно быть пустым. Ctrl+C — назад.')


def identifier(prompt='ID операции (Enter — новый)'):
    while True:
        value = input(prompt + ': ').strip()
        try:
            return str(uuid.UUID(value)) if value else str(uuid.uuid4())
        except ValueError:
            print('Нужен UUID вида 12345678-1234-1234-1234-123456789abc.')


def table(headers, rows):
    if not rows:
        print('  Нет записей.')
        return
    strings = [[str(value) if value is not None else '—' for value in row] for row in rows]
    widths = [max(len(str(h)), *(len(row[i]) for row in strings)) for i, h in enumerate(headers)]
    print('  ' + ' | '.join(str(h).ljust(w) for h, w in zip(headers, widths)))
    print('  ' + '-+-'.join('-' * w for w in widths))
    for row in strings:
        print('  ' + ' | '.join(value.ljust(w) for value, w in zip(row, widths)))


def error_message(error):
    if isinstance(error, psycopg2.Error):
        message = error.diag.message_primary or str(error).strip()
        print(f'\nОшибка БД [{error.pgcode or "connection"}]: {message}')
        if isinstance(error, psycopg2.OperationalError):
            print('Проверьте Docker Desktop и выполните: docker compose up -d --wait')
        if error.pgcode == '3D000' or error.pgcode == '42P01':
            print('Для отдельной демо-базы выберите пункт 1: Подготовить базу.')
        if isinstance(error, psycopg2.OperationalError) or error.pgcode in ('57014', '55P03'):
            print('Если это была запись, её исход может быть неизвестен. Повторите тот же ID через пункт 9.')
    else:
        print(f'\n{type(error).__name__}: {error}')


class VisibleClient(InventoryClient):
    def __init__(self, show, **kwargs):
        super().__init__(**kwargs)
        self.show = show

    def _write_once(self, statement, parameters):
        self.show(statement, parameters)
        return super()._write_once(statement, parameters)


class Console:
    def __init__(self, database=DEMO_DATABASE, show_sql=False, pause=True, journal_dir=None):
        self.primary = dict(connection_settings(), dbname=database)
        self.replica = dict(connection_settings(replica=True), dbname=database)
        self.show_sql = show_sql
        self.pause = pause
        self.last_token = None
        self.client = VisibleClient(self.show, primary=self.primary, replica=self.replica)
        # No passwords in the journal or its key; separate history for each endpoint and database.
        endpoint = '|'.join(str(self.primary[k]) for k in ('host', 'port', 'dbname', 'user'))
        key = hashlib.sha256(endpoint.encode()).hexdigest()[:16]
        self.journal = Path(journal_dir or ROOT / '.state' / 'console') / f'{key}.json'

    def close(self):
        self.client.close()

    def show(self, statement, parameters=None):
        if self.show_sql:
            print('\n  SQL> ' + str(statement).strip())
            if parameters:
                print('  Params: ' + json.dumps(parameters, ensure_ascii=False, default=str))

    def connect(self, settings=None):
        connection = psycopg2.connect(
            **(settings or self.primary), connect_timeout=3,
            application_name='inventory_console',
            options='-c statement_timeout=5000 -c lock_timeout=2000')
        connection.autocommit = True
        return connection

    def query(self, statement, parameters=None, settings=None, display=False):
        self.show(statement, parameters)
        with closing(self.connect(settings)) as connection, connection.cursor() as cursor:
            cursor.execute(statement, parameters)
            rows = cursor.fetchall() if cursor.description else []
            if display and cursor.description:
                table([column.name for column in cursor.description], rows)
            return rows

    def step(self, message):
        print('\n' + message)
        if self.pause:
            input('Enter — выполнить шаг; Ctrl+C — вернуться в меню ')

    def require_demo(self):
        name = self.primary['dbname']
        if name != DEMO_DATABASE and not name.startswith('inventory_console_test_'):
            raise ValueError('Демонстрации и подготовка разрешены только в inventory_console. '
                             'Запустите клиент без --database для отдельной демо-базы.')

    def setup(self):
        self.require_demo()
        with closing(self.connect(dict(self.primary, dbname='postgres'))) as admin:
            with admin.cursor() as cursor:
                cursor.execute('SELECT 1 FROM pg_database WHERE datname = %s', (self.primary['dbname'],))
                if not cursor.fetchone():
                    cursor.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(self.primary['dbname'])))
        with closing(self.connect()) as connection:
            connection.autocommit = False
            with connection, connection.cursor() as cursor:
                # Serialize simultaneous first starts; never drop or silently overwrite an existing schema.
                cursor.execute("SELECT pg_advisory_xact_lock(74184012)")
                cursor.execute("SELECT to_regnamespace('inventory_app')")
                if cursor.fetchone()[0]:
                    print('Демо-база уже подготовлена. Существующие данные сохранены.')
                    return
                for name in SCHEMA_FILES:
                    cursor.execute((ROOT / 'sql' / name).read_text(encoding='utf-8'))
                cursor.execute("""
                    INSERT INTO inventory_app.products VALUES
                      (17, 'IPHONE-17', 'iPhone 17'), (99, 'LAST-ITEM', 'Last item');
                    INSERT INTO inventory_app.warehouses VALUES
                      (1, 'Almaty warehouse', 'Almaty'), (2, 'Astana warehouse', 'Astana'),
                      (3, 'Shymkent warehouse', 'Shymkent');
                    INSERT INTO inventory_app.inventory(warehouse_id, product_id, on_hand) VALUES
                      (1,17,100), (2,17,50), (3,17,25), (1,99,1);
                """)
        print('Готово: inventory_console, товар 17 — остатки 100 / 50 / 25, товар 99 — 1 шт.')
        print('Это отдельная база. Основная inventory_case2 и прежняя inventory_defense не изменены.')

    def write(self, method, *parameters):
        if method not in WRITE_METHODS:
            raise ValueError('Unknown write method')
        request = {'method': method, 'parameters': parameters}
        # Persist the ID BEFORE sending. A lost response or process restart must not create a new ID.
        self.journal.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.journal.with_name(self.journal.name + '.' + uuid.uuid4().hex + '.tmp')
        try:
            temporary.write_text(json.dumps(request, ensure_ascii=False, indent=2), encoding='utf-8')
            temporary.replace(self.journal)
        finally:
            temporary.unlink(missing_ok=True)
        print(f'\nОперация: {method} | ID: {parameters[0]}')
        result = getattr(self.client, method)(*parameters)
        self.last_token = result['token']
        print(f'Результат: {result["status"]}')
        if self.show_sql:
            print('COMMIT подтверждён. Read token: ' + json.dumps(self.last_token))
        return result

    def replay(self):
        if not self.journal.exists():
            print('Нет сохранённого запроса. Сначала выполните операцию записи.')
            return
        request = json.loads(self.journal.read_text(encoding='utf-8'))
        print('Повторяем последнюю запись с теми же ID и параметрами:')
        print(json.dumps(request, ensure_ascii=False, indent=2))
        return self.write(request['method'], *request['parameters'])

    def balances(self, product=None):
        return self.query('''SELECT i.warehouse_id AS warehouse, w.city, i.product_id AS product,
                    i.on_hand, i.reserved, i.available, i.version
                FROM inventory_app.inventory i JOIN inventory_app.warehouses w USING (warehouse_id)
                WHERE (%s::bigint IS NULL OR i.product_id = %s)
                ORDER BY i.product_id, i.warehouse_id LIMIT 30''', (product, product), display=True)

    def catalog(self):
        self.query('SELECT warehouse_id, city FROM inventory_app.warehouses ORDER BY warehouse_id LIMIT 30', display=True)
        self.query('SELECT product_id, name FROM inventory_app.products ORDER BY product_id DESC LIMIT 20', display=True)
        print('Показаны первые 30 складов и последние 20 товаров. Можно ввести любой известный ID.')

    def stock_menu(self):
        print('1 — все остатки (до 30 строк); 2 — по товару; 3 — одно чтение primary/replica')
        choice = number('Действие', 1, maximum=3)
        if choice == 1:
            return self.balances()
        if choice == 2:
            return self.balances(number('Product ID', 17))
        warehouse, product = number('Warehouse ID', 1, maximum=2147483647), number('Product ID', 17)
        print('1 — актуальное с fallback; 2 — primary; 3 — replica (может отставать)')
        mode = number('Режим', 1, maximum=3)
        result = self.client.read_inventory(warehouse, product, authoritative=mode == 2,
                                           allow_stale=mode == 3, after=self.last_token if mode == 1 else None)
        print(json.dumps(result, ensure_ascii=False, indent=2))

    def reserve(self):
        self.catalog()
        count = number('Количество позиций (пар склад–товар)', 1, maximum=100)
        items = []
        for index in range(count):
            print(f'Позиция {index + 1}/{count}')
            item = {'warehouse_id': number('Warehouse ID', 1, maximum=2147483647),
                    'product_id': number('Product ID', 17), 'quantity': number('Количество', 1)}
            if any((i['warehouse_id'], i['product_id']) == (item['warehouse_id'], item['product_id']) for i in items):
                raise ValueError('Пара склад–товар повторяется. Объедините её количество в одну позицию.')
            items.append(item)
        return self.write('reserve_order', identifier('Order ID (Enter — создать автоматически)'), items)

    def choose_record(self, kind):
        if kind == 'order':
            rows = self.query('SELECT order_id, status, expires_at FROM inventory_app.reservations ORDER BY created_at DESC LIMIT 20')
            headers = ['#', 'order_id', 'status', 'expires_at']
        else:
            rows = self.query('''SELECT transfer_id, status, source_warehouse_id, destination_warehouse_id, quantity
                FROM inventory_app.warehouse_transfers ORDER BY created_at DESC LIMIT 20''')
            headers = ['#', 'transfer_id', 'status', 'from', 'to', 'qty']
        table(headers, [(i, *row) for i, row in enumerate(rows, 1)])
        while True:
            value = input('Номер строки или полный UUID (Enter — назад): ').strip()
            if not value:
                return None
            if value.isdigit() and 1 <= int(value) <= len(rows):
                return str(rows[int(value) - 1][0])
            try:
                return str(uuid.UUID(value))
            except ValueError:
                print('Введите номер из таблицы или UUID.')

    def order_menu(self):
        order = self.choose_record('order')
        if order is None:
            return
        self.query('SELECT warehouse_id, product_id, quantity FROM inventory_app.reservation_items WHERE order_id=%s', (order,), display=True)
        print('1 — подтвердить покупку; 2 — отменить резерв; 3 — проверить истечение срока; 0 — назад')
        choice = number('Действие', 0, minimum=0, maximum=3)
        if choice:
            self.write('finish_reservation', order, ('confirm', 'release', 'expire')[choice - 1])

    def adjust(self):
        self.catalog()
        warehouse, product = number('Warehouse ID', 1, maximum=2147483647), number('Product ID', 17)
        delta = number('Изменение (+ приёмка, − списание)', 10, minimum=-9223372036854775807)
        if delta == 0:
            raise ValueError('Изменение должно отличаться от нуля.')
        reason = text_input('Причина', 'Supplier delivery')
        self.write('adjust_stock', identifier(), warehouse, product, delta, reason)
        self.balances(product)

    def transfer_menu(self):
        print('1 — создать перемещение; 2 — выбрать и продолжить; 0 — назад')
        choice = number('Действие', 1, minimum=0, maximum=2)
        if choice == 1:
            source = number('Со склада', 1, maximum=2147483647)
            destination = number('На склад', 2, maximum=2147483647)
            if source == destination:
                raise ValueError('Выберите разные склады.')
            product, quantity = number('Product ID', 17), number('Количество', 1)
            return self.write('request_transfer', identifier(), source, destination, product, quantity)
        if choice == 2:
            transfer = self.choose_record('transfer')
            if transfer:
                print('1 — отправить; 2 — принять; 3 — отменить до отправки; 4 — отметить потерю; 0 — назад')
                action = number('Действие', 0, minimum=0, maximum=4)
                if action:
                    self.write('advance_transfer', transfer, ('dispatch', 'receive', 'cancel', 'lose')[action - 1])

    def events(self):
        self.query('''SELECT event_id, warehouse_id, product_id, event_type, on_hand_delta AS stock_delta,
                reserved_delta, published_at IS NOT NULL AS delivered
            FROM inventory_app.inventory_events ORDER BY event_id DESC LIMIT 20''', display=True)
        print('Последние 20 событий. delivered=true означает, что источник получил ACK потребителя.')

    def events_menu(self):
        from workers import deliver_once, expire_once
        print('1 — события; 2 — обработать истёкшие резервы; 3 — доставить пакет Outbox; 0 — назад')
        choice = number('Действие', 1, minimum=0, maximum=3)
        if choice == 1:
            self.events()
        elif choice == 2:
            count = 0
            for _ in range(100):
                self.show('SELECT inventory_app.expire_one();')
                if expire_once(self.client) is None:
                    break
                count += 1
            print(f'Освобождено резервов: {count} (лимит одного запуска — 100).')
        elif choice == 3:
            print(f'Доставка из {self.primary["dbname"]} в настроенную projection-базу.')
            print(f'Подтверждено событий: {deliver_once(self.client)} (до 100 за один запуск).')
            self.events()

    def health(self):
        self.query('''SELECT current_database() AS database, pg_is_in_recovery() AS standby,
            current_setting('transaction_isolation') AS isolation,
            current_setting('synchronous_commit') AS sync_commit,
            current_setting('synchronous_standby_names') AS sync_standbys''', display=True)
        self.query('''SELECT application_name, state, sync_state, sent_lsn, flush_lsn, replay_lsn
            FROM pg_stat_replication''', display=True)
        try:
            self.query('SELECT pg_is_in_recovery() AS standby, pg_last_wal_replay_lsn() AS replay_lsn', settings=self.replica, display=True)
        except psycopg2.Error as error:
            error_message(error)
        self.invariants()

    def invariants(self):
        rows = self.query('''WITH active AS (
            SELECT warehouse_id, product_id, sum(quantity) AS quantity
            FROM inventory_app.reservation_items JOIN inventory_app.reservations USING(order_id)
            WHERE status='active' GROUP BY warehouse_id, product_id)
            SELECT count(*) FILTER (WHERE on_hand < 0 OR reserved < 0 OR reserved > on_hand) AS invalid_stock,
                   count(*) FILTER (WHERE reserved <> coalesce(quantity,0)) AS counter_mismatches
            FROM inventory_app.inventory LEFT JOIN active USING(warehouse_id, product_id)''', display=True)
        if rows != [(0, 0)]:
            raise RuntimeError('Нарушен инвариант остатков или счётчиков резервов.')

    def demos(self):
        from defense_scenarios import SCENARIOS, run_scenario
        for i, (_, title, _) in enumerate(SCENARIOS, 1):
            print(f' {i}. {title}')
        print(' 9. Вся защита по шагам\n 0. Назад')
        choice = number('Сценарий', 1, minimum=0, maximum=9)
        if choice:
            run_scenario(self, 'all' if choice == 9 else SCENARIOS[choice - 1][0])

    def menu(self):
        actions = {'1': self.setup, '2': self.stock_menu, '3': self.reserve, '4': self.order_menu,
                   '5': self.adjust, '6': self.transfer_menu, '7': self.events_menu,
                   '8': self.health, '9': self.replay, 'd': self.demos}
        while True:
            print('\n' + '=' * 68)
            print(f' INVENTORY | {self.primary["dbname"]} | {self.primary["host"]}:{self.primary["port"]}')
            print(f' SQL: {"включён" if self.show_sql else "выключен"} | Enter — значение в [скобках] | Ctrl+C — назад')
            print(' 1  Подготовить демо-базу       2  Остатки / чтение с реплики')
            print(' 3  Создать резерв             4  Заказы / подтвердить / отменить')
            print(' 5  Приёмка / списание         6  Перемещения между складами')
            print(' 7  События / Outbox           8  Состояние БД / инварианты')
            print(' 9  Повторить последнюю запись с тем же ID (сохраняется после выхода)')
            print(' D  Сценарии защиты            S  Показ SQL вкл/выкл\n Q  Выход')
            try:
                choice = input('\nВыберите действие: ').strip().lower()
                if choice in ('q', '0', 'exit'):
                    return
                if choice == 's':
                    self.show_sql = not self.show_sql
                elif choice in actions:
                    actions[choice]()
                else:
                    print('Выберите пункт меню.')
            except EOFError:
                return
            except KeyboardInterrupt:
                print('\nВозврат в меню. При прерывании записи проверьте её ID; пункт 9 повторяет запрос.')
            except (psycopg2.Error, ValueError, RuntimeError, TimeoutError, OSError) as error:
                error_message(error)


def main(argv=None):
    from defense_scenarios import SCENARIOS, run_scenario
    parser = argparse.ArgumentParser(description='Inventory: операции и готовые сценарии защиты. Без аргументов — меню.')
    parser.add_argument('--database', default=DEMO_DATABASE, help='База (по умолчанию отдельная inventory_console)')
    parser.add_argument('--show-sql', action='store_true', help='Показывать SQL и параметры')
    parser.add_argument('--no-pause', action='store_true', help='Не ждать Enter между шагами сценария')
    parser.add_argument('--setup', action='store_true', help='Подготовить демо-базу, не удаляя существующие данные')
    parser.add_argument('--demo', choices=['all'] + [item[0] for item in SCENARIOS], help='Запустить сценарий без меню')
    parser.add_argument('--status', action='store_true', help='Проверить состояние и инварианты без меню')
    args = parser.parse_args(argv)
    console = Console(args.database, args.show_sql, not args.no_pause)
    try:
        if args.setup:
            console.setup()
        if args.demo:
            run_scenario(console, args.demo)
        if args.status:
            console.health()
        if not (args.setup or args.demo or args.status):
            console.menu()
        return 0
    except (EOFError, KeyboardInterrupt):
        print('\nОстановлено. Исход прерванной записи проверьте по её ID.')
        return 130
    except (psycopg2.Error, ValueError, RuntimeError, TimeoutError, OSError) as error:
        error_message(error)
        return 1
    finally:
        console.close()


if __name__ == '__main__':
    raise SystemExit(main())

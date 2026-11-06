"""Run: python -X utf8 tests/verify_console.py [--integration].

Integration tests create/drop their own database and remove only their own sink rows.
"""
from contextlib import closing, redirect_stdout
import io
import json
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import psycopg2
from psycopg2 import sql
from inventory_console import Console, identifier, main, number
from defense_scenarios import run_scenario
from workers import sink_settings

INTEGRATION = '--integration' in sys.argv
if INTEGRATION:
    sys.argv.remove('--integration')


class temporary_directory:
    """Use inherited workspace ACLs (tempfile's private ACL can fail in Windows sandboxes)."""
    def __init__(self):
        self.path = Path(__file__).resolve().parents[1] / '.state' / 'console-tests' / uuid.uuid4().hex
        self.path.mkdir(parents=True)
        self.name = str(self.path)

    def __enter__(self):
        return self.name

    def __exit__(self, *_):
        self.cleanup()

    def cleanup(self):
        for file in self.path.iterdir():
            file.unlink()
        self.path.rmdir()


class InputTests(unittest.TestCase):
    def test_number_reprompts_for_invalid_negative_and_overflow(self):
        with patch('builtins.input', side_effect=['abc', '-1', '101', '7']), redirect_stdout(io.StringIO()):
            self.assertEqual(number('Quantity', maximum=100), 7)

    def test_number_enter_uses_default(self):
        with patch('builtins.input', return_value=''):
            self.assertEqual(number('Quantity', 2), 2)

    def test_identifier_new_and_existing(self):
        with patch('builtins.input', return_value=''):
            created = identifier()
        self.assertEqual(str(uuid.UUID(created)), created)
        with patch('builtins.input', side_effect=['wrong', created]), redirect_stdout(io.StringIO()):
            self.assertEqual(identifier(), created)

    def test_fixture_guard_blocks_business_database(self):
        console = Console('inventory_case2')
        try:
            with self.assertRaises(ValueError):
                console.setup()
            with self.assertRaises(ValueError):
                run_scenario(console, 'race')
        finally:
            console.close()

    def test_journal_survives_failure_and_restart_with_same_id(self):
        with temporary_directory() as directory, redirect_stdout(io.StringIO()):
            first = Console(journal_dir=directory)
            order = str(uuid.uuid4())
            payload = [{'warehouse_id': 1, 'product_id': 17, 'quantity': 1}]
            try:
                with patch.object(first.client, 'reserve_order', side_effect=psycopg2.OperationalError('lost response')):
                    with self.assertRaises(psycopg2.OperationalError):
                        first.write('reserve_order', order, payload)
                saved = json.loads(first.journal.read_text(encoding='utf-8'))
                self.assertEqual(saved['parameters'], [order, payload])
                second = Console(journal_dir=directory)
                try:
                    with patch.object(second.client, 'reserve_order', return_value={'status': 'active', 'token': {}}) as call:
                        second.replay()
                        call.assert_called_once_with(order, payload)
                finally:
                    second.close()
            finally:
                first.close()

    def test_journal_rejects_non_write_method(self):
        with temporary_directory() as directory:
            console = Console(journal_dir=directory)
            try:
                with self.assertRaises(ValueError):
                    console.write('close')
            finally:
                console.close()

    def test_menu_bad_input_interrupt_and_eof(self):
        console = Console()
        try:
            with patch('builtins.input', side_effect=['not-a-choice', KeyboardInterrupt, EOFError]), redirect_stdout(io.StringIO()):
                console.menu()
        finally:
            console.close()

    def test_cli_returns_failure_on_connection_error(self):
        with patch.object(Console, 'health', side_effect=psycopg2.OperationalError('offline')), redirect_stdout(io.StringIO()):
            self.assertEqual(main(['--status']), 1)


@unittest.skipUnless(INTEGRATION, 'Use --integration with running Docker stack')
class IntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = temporary_directory()
        cls.name = 'inventory_console_test_' + uuid.uuid4().hex[:12]
        cls.console = Console(cls.name, pause=False, journal_dir=cls.directory.name)
        cls.log = io.StringIO()
        try:
            with redirect_stdout(cls.log):
                cls.console.setup()
        except Exception:
            cls.console.close()
            cls.directory.cleanup()
            raise

    @classmethod
    def tearDownClass(cls):
        cls.console.close()
        try:
            with closing(psycopg2.connect(**sink_settings(), connect_timeout=3)) as sink:
                with sink, sink.cursor() as cursor:
                    cursor.execute('DELETE FROM inventory_projection.inbox WHERE source=%s', (cls.name,))
                    cursor.execute('DELETE FROM inventory_projection.movements WHERE source=%s', (cls.name,))
            with closing(cls.console.connect(dict(cls.console.primary, dbname='postgres'))) as admin, admin.cursor() as cursor:
                cursor.execute(sql.SQL('DROP DATABASE {}').format(sql.Identifier(cls.name)))
        finally:
            cls.directory.cleanup()

    def test_all_eight_demos_repeat_without_reset(self):
        with redirect_stdout(self.log):
            for _ in range(2):
                run_scenario(self.console, 'all')
            self.console.balances(17)
        # Scenarios never touch the user's manual examples.
        self.assertEqual(self.console.query('SELECT warehouse_id,on_hand,reserved FROM inventory_app.inventory WHERE product_id=17 ORDER BY warehouse_id'),
                         [(1, 100, 0), (2, 50, 0), (3, 25, 0)])
        self.assertEqual(self.log.getvalue().count('PASS ['), 16)

    def test_manual_reserve_select_confirm_adjust_transfer(self):
        c = self.console
        with redirect_stdout(self.log):
            # Menu defaults reserve one iPhone in Almaty; row 1 is the newest order.
            with patch('builtins.input', side_effect=['', '', '', '', '']):
                c.reserve()
            with patch('builtins.input', side_effect=['1', '1']):
                c.order_menu()
            with patch('builtins.input', side_effect=['', '', '2', 'Test receipt', '']):
                c.adjust()
            with patch('builtins.input', side_effect=['1', '', '', '', '', '']):
                c.transfer_menu()
            with patch('builtins.input', side_effect=['2', '1', '1']):
                c.transfer_menu()
            with patch('builtins.input', side_effect=['2', '1', '2']):
                c.transfer_menu()
            c.invariants()
        self.assertEqual(c.query('SELECT warehouse_id,on_hand,reserved FROM inventory_app.inventory WHERE product_id=17 ORDER BY warehouse_id'),
                         [(1, 100, 0), (2, 51, 0), (3, 25, 0)])

    def test_setup_preserves_existing_rows(self):
        c = self.console
        before = c.query('SELECT count(*),sum(on_hand),sum(reserved) FROM inventory_app.inventory')
        with redirect_stdout(self.log):
            c.setup()
        self.assertEqual(c.query('SELECT count(*),sum(on_hand),sum(reserved) FROM inventory_app.inventory'), before)

    def test_worker_menu_and_replica_read(self):
        c = self.console
        with redirect_stdout(self.log):
            for choice in ('1', '2', '3'):
                with patch('builtins.input', return_value=choice):
                    c.events_menu()
            with patch('builtins.input', side_effect=['3', '', '', '1']):
                c.stock_menu()
            c.invariants()

    def test_cli_sql_output_and_exit_status(self):
        result = subprocess.run([sys.executable, '-X', 'utf8', 'inventory_console.py',
                                 '--database', self.name, '--demo', 'partitioning', '--no-pause', '--show-sql'],
                                cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True,
                                encoding='utf-8', timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('SQL>', result.stdout)
        self.assertIn('PASS [partitioning]', result.stdout)


if __name__ == '__main__':
    unittest.main(verbosity=2)

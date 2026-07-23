import sqlite3
import tempfile
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connections
from django.test import TestCase


class BackupDbCommandTests(TestCase):
    """`manage.py backup_db` — cópia de segurança do SQLite.

    O banco de teste do Django é em memória, então os testes montam um arquivo
    SQLite próprio e apontam a conexão para ele — é o mesmo caminho de código
    que roda em produção.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.output_dir = Path(self.tmp.name) / 'backups'
        self.source = self._make_source_db()

    def _make_source_db(self):
        source = Path(self.tmp.name) / 'db.sqlite3'
        connection = sqlite3.connect(source)
        with connection:
            connection.execute('CREATE TABLE menu_restaurant (name text)')
            connection.execute("INSERT INTO menu_restaurant VALUES ('Backup Kitchen')")
        connection.close()
        return source

    def _run(self, **options):
        out = StringIO()
        with patch.dict(connections['default'].settings_dict, {'NAME': str(self.source)}):
            result = call_command(
                'backup_db', output_dir=str(self.output_dir), stdout=out, **options,
            )
        return Path(result), out.getvalue()

    def _backups(self):
        return sorted(self.output_dir.glob('db-*.sqlite3'))

    def test_creates_a_readable_copy(self):
        destination, output = self._run()

        self.assertTrue(destination.exists())
        self.assertIn('Backup gravado', output)

        # A cópia é um SQLite válido e traz os dados do banco.
        copy = sqlite3.connect(destination)
        try:
            names = copy.execute('SELECT name FROM menu_restaurant').fetchall()
        finally:
            copy.close()
        self.assertEqual(names, [('Backup Kitchen',)])

    def test_keeps_only_the_newest_backups(self):
        self.output_dir.mkdir(parents=True)
        for stamp in ('20260101-000000', '20260102-000000', '20260103-000000'):
            (self.output_dir / f'db-{stamp}.sqlite3').touch()

        self._run(keep=2)

        remaining = [path.name for path in self._backups()]
        self.assertEqual(len(remaining), 2)
        # Sobram a cópia recém-criada (data de hoje) e a mais recente das antigas.
        self.assertIn('db-20260103-000000.sqlite3', remaining)
        self.assertNotIn('db-20260101-000000.sqlite3', remaining)

    def test_keep_zero_removes_nothing(self):
        self.output_dir.mkdir(parents=True)
        (self.output_dir / 'db-20260101-000000.sqlite3').touch()

        self._run(keep=0)

        self.assertEqual(len(self._backups()), 2)

    def test_fails_clearly_on_other_databases(self):
        with patch.object(connections['default'], 'vendor', 'postgresql'):
            with self.assertRaises(CommandError) as ctx:
                call_command('backup_db', output_dir=str(self.output_dir))
        self.assertIn('pg_dump', str(ctx.exception))

    def test_fails_clearly_without_the_database_file(self):
        missing = Path(self.tmp.name) / 'sumiu.sqlite3'
        with patch.dict(connections['default'].settings_dict, {'NAME': str(missing)}):
            with self.assertRaises(CommandError):
                call_command('backup_db', output_dir=str(self.output_dir))

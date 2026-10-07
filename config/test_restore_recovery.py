"""Restauração completa, arquivos incompletos e retomada das tarefas automáticas."""
import json
import sqlite3
import tempfile
import warnings
import zipfile
from contextlib import closing
from datetime import timedelta
from io import StringIO
from pathlib import Path

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase
from django.utils import timezone


class RestoreRecoveryTests(TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.target = self.root/'restored'
        self.source = self.root/'source.sqlite3'
        with closing(sqlite3.connect(self.source)) as database, database:
            database.execute('CREATE TABLE example (value TEXT)')
            database.execute("INSERT INTO example VALUES ('Preservado')")

    def archive(self, *, manifest=None, members=None):
        archive = self.root/'site.zip'
        if manifest is None:
            manifest = {'database_engine':'sqlite', 'database_file':'database.sqlite3'}
        if members is None:
            members = [('database.sqlite3', self.source.read_bytes()), ('media/upload.txt', b'preservado')]
        with warnings.catch_warnings(), zipfile.ZipFile(archive, 'w') as output:
            warnings.simplefilter('ignore', UserWarning)
            output.writestr('manifest.json', json.dumps(manifest))
            for name, data in members:
                output.writestr(name, data)
        return archive

    def restore(self, archive):
        output = StringIO()
        call_command('restore_site', str(archive), target_dir=str(self.target), stdout=output)
        return output.getvalue()

    def test_missing_database_is_rejected_before_creating_destination(self):
        archive = self.archive(members=[('media/upload.txt', b'upload')])
        with self.assertRaises(CommandError):
            self.restore(archive)
        self.assertFalse(self.target.exists())

    def test_invalid_manifest_is_reported_as_command_error(self):
        archive = self.archive(manifest=['unexpected'])
        with self.assertRaises(CommandError):
            self.restore(archive)
        self.assertFalse(self.target.exists())

    def test_corrupt_database_cannot_leave_partial_installation(self):
        archive = self.archive(members=[('database.sqlite3', b'not a sqlite database'), ('media/upload.txt', b'upload')])
        with self.assertRaises(CommandError):
            self.restore(archive)
        self.assertFalse(self.target.exists())

    def test_duplicate_archive_members_are_rejected(self):
        archive = self.archive(members=[('database.sqlite3', self.source.read_bytes()),
            ('database.sqlite3', b'different')])
        with self.assertRaises(CommandError):
            self.restore(archive)
        self.assertFalse(self.target.exists())

    def test_windows_alternate_stream_path_is_rejected(self):
        archive = self.archive(members=[('database.sqlite3', self.source.read_bytes()), ('media/photo:stream', b'data')])
        with self.assertRaises(CommandError):
            self.restore(archive)
        self.assertFalse(self.target.exists())

    def test_normalized_duplicate_paths_are_rejected(self):
        archive = self.archive(members=[('database.sqlite3', self.source.read_bytes()),
            ('media/folder//photo.txt', b'first'), ('media/folder/photo.txt', b'second')])
        with self.assertRaises(CommandError):
            self.restore(archive)
        self.assertFalse(self.target.exists())

    def test_restored_operations_restart_without_old_leases_or_stale_health(self):
        with closing(sqlite3.connect(self.source)) as database, database:
            database.execute('CREATE TABLE config_operationstatus (next_run_at TEXT, lease_until TEXT, last_success_at TEXT, last_error TEXT)')
            future = (timezone.now()+timedelta(days=1)).isoformat()
            database.execute('INSERT INTO config_operationstatus VALUES (?, ?, ?, ?)',
                [future, future, timezone.now().isoformat(), 'old error'])
        self.restore(self.archive())
        with closing(sqlite3.connect(self.target/'db.sqlite3')) as database:
            values = database.execute('SELECT next_run_at, lease_until, last_success_at, last_error FROM config_operationstatus').fetchone()
            self.assertEqual(database.execute('SELECT value FROM example').fetchone()[0], 'Preservado')
        self.assertEqual(values[1:], (None, None, ''))
        self.assertLessEqual(timezone.datetime.fromisoformat(values[0]), timezone.now())

    def test_postgres_extraction_retains_manifest_and_explains_next_step(self):
        archive = self.archive(manifest={'database_engine':'postgresql', 'database_file':'database.dump', 'postgres_major':16},
            members=[('database.dump', b'PGDMP-example')])
        output = self.restore(archive)
        self.assertIn('pg_restore', output)
        self.assertIn('16', output)
        self.assertEqual(json.loads((self.target/'manifest.json').read_text(encoding='utf-8'))['postgres_major'], 16)

    def test_failed_validation_preserves_existing_empty_destination(self):
        self.target.mkdir()
        archive = self.archive(members=[('database.sqlite3', b'corrupt')])
        with self.assertRaises(CommandError):
            self.restore(archive)
        self.assertTrue(self.target.is_dir())
        self.assertEqual(list(self.target.iterdir()), [])

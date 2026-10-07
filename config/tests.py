import sqlite3
import tempfile
import json
import zipfile
from io import StringIO
from datetime import timedelta, time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch, MagicMock

from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connections
from django.test import TestCase, override_settings
from django.utils import timezone

from config.models import OperationStatus
from config.readiness import launch_requirements
from config.management.commands.run_operations import TASKS
from menu.models import BusinessHours, Restaurant


def sqlite_connection(source):
    return SimpleNamespace(vendor='sqlite', settings_dict={'NAME':str(source)}, is_in_memory_db=lambda:False)


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
        with patch('config.management.commands.backup_db.connections', {'default':sqlite_connection(self.source)}):
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
        with patch('config.management.commands.backup_db.connections', {'default':sqlite_connection(missing)}):
            with self.assertRaises(CommandError):
                call_command('backup_db', output_dir=str(self.output_dir))


class SiteBackupTests(TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.source = self.root/'source.sqlite3'
        source = sqlite3.connect(self.source)
        source.execute('CREATE TABLE example (value TEXT)')
        source.execute("INSERT INTO example VALUES ('preservado')")
        source.commit()
        source.close()
        self.media = self.root/'media'
        self.media.mkdir()
        (self.media/'foto.txt').write_text('upload preservado', encoding='utf-8')
        self.secret = self.root/'secret'
        self.secret.write_text('test-private-key-'*4, encoding='utf-8')

    def test_backup_restores_database_media_and_installation_key(self):
        with override_settings(BACKUP_ROOT=self.root/'backups', MEDIA_ROOT=self.media,
                PERSISTED_SECRET_PATH=self.secret), patch('config.management.commands.backup_site.connections',
                {'default':sqlite_connection(self.source)}):
            call_command('backup_site', stdout=StringIO())
        archive = next((self.root/'backups').glob('site-*.zip'))
        target = self.root/'restored'
        call_command('restore_site', str(archive), target_dir=str(target), stdout=StringIO())
        restored = sqlite3.connect(target/'db.sqlite3')
        self.assertEqual(restored.execute('SELECT value FROM example').fetchone()[0], 'preservado')
        restored.close()
        self.assertEqual((target/'media/foto.txt').read_text(encoding='utf-8'), 'upload preservado')
        self.assertEqual((target/'django-secret').read_text(encoding='utf-8'), self.secret.read_text(encoding='utf-8'))

    def test_restore_refuses_nonempty_destination(self):
        with self.assertRaises(CommandError):
            call_command('restore_site', str(self.root/'not-used.zip'), target_dir=str(self.media), stdout=StringIO())
        self.assertTrue((self.media/'foto.txt').exists())

    def test_external_backup_uses_configured_bucket_without_exposing_keys(self):
        with override_settings(BACKUP_ROOT=self.root/'backups', MEDIA_ROOT=self.media,
                PERSISTED_SECRET_PATH=self.secret, BACKUP_BUCKET='test-bucket', BACKUP_ACCESS_KEY='test',
                BACKUP_SECRET_KEY='test', BACKUP_ENDPOINT='https://storage.example.invalid'), patch(
                'config.management.commands.backup_site.connections', {'default':sqlite_connection(self.source)}), patch('boto3.client') as client:
            call_command('backup_site', stdout=StringIO())
        args = client.return_value.upload_file.call_args.args
        self.assertEqual(args[1], 'test-bucket')
        self.assertTrue(args[2].startswith('onmenu/site-'))

    def test_external_upload_failure_preserves_local_copy(self):
        with override_settings(BACKUP_ROOT=self.root/'backups', MEDIA_ROOT=self.media,
                PERSISTED_SECRET_PATH=self.secret, BACKUP_BUCKET='test-bucket', BACKUP_ACCESS_KEY='test',
                BACKUP_SECRET_KEY='test', BACKUP_ENDPOINT='https://storage.example.invalid'), patch(
                'config.management.commands.backup_site.connections', {'default':sqlite_connection(self.source)}), patch('boto3.client') as client:
            client.return_value.upload_file.side_effect = RuntimeError('test failure')
            with self.assertRaises(CommandError):
                call_command('backup_site', stdout=StringIO())
        self.assertEqual(len(list((self.root/'backups').glob('site-*.zip'))), 1)

    def test_restore_rejects_path_traversal_before_creating_destination(self):
        archive = self.root/'invalid.zip'
        with zipfile.ZipFile(archive, 'w') as output:
            output.writestr('manifest.json', json.dumps({'database_file':'database.sqlite3'}))
            output.writestr('../outside.txt', 'invalid')
        target = self.root/'restored'
        with self.assertRaises(CommandError):
            call_command('restore_site', str(archive), target_dir=str(target), stdout=StringIO())
        self.assertFalse(target.exists())


class OperationsCommandTests(TestCase):
    def test_tasks_are_recorded_and_not_run_twice_before_interval(self):
        with patch('config.management.commands.run_operations.call_command') as command:
            call_command('run_operations', once=True, skip_backup=True, stdout=StringIO())
            first_count = command.call_count
            call_command('run_operations', once=True, skip_backup=True, stdout=StringIO())
            self.assertEqual(first_count, 6)
            self.assertEqual(command.call_count, first_count)
        self.assertEqual(OperationStatus.objects.count(), 6)
        self.assertFalse(OperationStatus.objects.filter(last_success_at__isnull=True).exists())

    def test_worker_keeps_task_diagnostic_in_logs(self):
        def execute(name, **options):
            if name == 'sync_pending_pix':
                options['stderr'].write('Não foi possível consultar o Pix de OM-1.')
        errors = StringIO()
        with patch('config.management.commands.run_operations.call_command', side_effect=execute):
            call_command('run_operations', once=True, skip_backup=True, stdout=StringIO(), stderr=errors)
        self.assertIn('Não foi possível consultar o Pix de OM-1.', errors.getvalue())
        self.assertTrue(OperationStatus.objects.get(name='sync_pending_pix').last_error)

    def test_worker_reports_configuration_errors_and_continues_other_tasks(self):
        def execute(name, **options):
            if name == 'sync_pending_pix':
                raise CommandError('Configure o cliente PostgreSQL 16.')
        errors = StringIO()
        with patch('config.management.commands.run_operations.call_command', side_effect=execute):
            call_command('run_operations', once=True, skip_backup=True, stdout=StringIO(), stderr=errors)
        self.assertIn('Configure o cliente PostgreSQL 16.', errors.getvalue())
        self.assertIsNotNone(OperationStatus.objects.get(name='sync_pending_card').last_success_at)

    def test_restart_after_restore_clears_old_health_and_leases(self):
        future = timezone.now()+timedelta(hours=1)
        job = OperationStatus.objects.create(name='sync_pending_pix', next_run_at=future, lease_until=future,
            last_success_at=timezone.now(), last_error='Erro anterior')
        call_command('reset_operations', stdout=StringIO())
        job.refresh_from_db()
        self.assertIsNone(job.lease_until)
        self.assertIsNone(job.last_success_at)
        self.assertEqual(job.last_error, '')
        self.assertLessEqual(job.next_run_at, timezone.now())


class PostgreSQLBackupVersionTests(TestCase):
    def test_mismatched_client_is_rejected_before_creating_archive(self):
        database = MagicMock(vendor='postgresql', settings_dict={})
        database.cursor.return_value.__enter__.return_value.fetchone.return_value = ['160011']
        version = SimpleNamespace(stdout='pg_dump (PostgreSQL) 17.11')
        with tempfile.TemporaryDirectory() as directory, override_settings(BACKUP_ROOT=Path(directory)), patch(
                'config.management.commands.backup_site.connections', {'default':database}), patch(
                'config.management.commands.backup_site.subprocess.run', return_value=version) as command:
            with self.assertRaisesMessage(CommandError, 'POSTGRES_CLIENT_VERSION=16'):
                call_command('backup_site', stdout=StringIO())
            self.assertEqual(command.call_count, 1)
            self.assertFalse(list(Path(directory).glob('site-*.zip')))


class LaunchReadinessTests(TestCase):
    def check(self, label):
        return next(item['ok'] for item in launch_requirements() if item['label'] == label)

    def test_seven_empty_hour_records_do_not_count_as_configured(self):
        restaurant = Restaurant.objects.create(name='Restaurante')
        BusinessHours.objects.bulk_create([BusinessHours(restaurant=restaurant, day_of_week=day)
            for day in range(7)])
        self.assertFalse(self.check('Horários preenchidos nos sete dias'))
        restaurant.business_hours.update(is_closed=True)
        restaurant.business_hours.filter(day_of_week=0).update(is_closed=False,
            open_time=time(18), close_time=time(2))
        self.assertTrue(self.check('Horários preenchidos nos sete dias'))

    @override_settings(EMAIL_BACKEND='django.core.mail.backends.smtp.EmailBackend',
        EMAIL_HOST='', DEFAULT_FROM_EMAIL='atendimento@example.com')
    def test_smtp_backend_alone_is_not_ready(self):
        self.assertFalse(self.check('Envio real de e-mail configurado'))
        with override_settings(EMAIL_HOST='smtp.example.com', EMAIL_HOST_USER='test', EMAIL_HOST_PASSWORD=''):
            self.assertFalse(self.check('Envio real de e-mail configurado'))
        with override_settings(EMAIL_HOST='smtp.example.com', EMAIL_HOST_USER='test', EMAIL_HOST_PASSWORD='test'):
            self.assertTrue(self.check('Envio real de e-mail configurado'))

    def test_stopped_or_failing_operations_do_not_count_as_ready(self):
        label = 'Operação automática executando sem falhas recentes'
        self.assertFalse(self.check(label))
        OperationStatus.objects.bulk_create([OperationStatus(name=name, last_success_at=timezone.now())
            for name in TASKS])
        self.assertTrue(self.check(label))
        OperationStatus.objects.filter(name='sync_pending_pix').update(last_success_at=timezone.now()-timedelta(minutes=10))
        self.assertFalse(self.check(label))
        OperationStatus.objects.filter(name='sync_pending_pix').update(last_success_at=timezone.now(), last_error='Falha')
        self.assertFalse(self.check(label))

    @override_settings(EMAIL_BACKEND='django.core.mail.backends.smtp.EmailBackend',
        EMAIL_HOST='smtp.example.com', EMAIL_HOST_USER='test', EMAIL_HOST_PASSWORD='test',
        DEFAULT_FROM_EMAIL='OnMenu <atendimento@example.com>')
    def test_sender_display_name_is_supported(self):
        self.assertTrue(self.check('Envio real de e-mail configurado'))


class ProductionSecretTests(TestCase):
    @override_settings(DEBUG=False, SECRET_KEY='django-insecure-known-development-key')
    def test_development_key_blocks_production_initialization(self):
        from orders.checks import check_production_secret
        self.assertEqual([error.id for error in check_production_secret(None)], ['config.E001'])

    @override_settings(DEBUG=False, SECRET_KEY='test-fixture-ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789',
        SECRET_KEY_FALLBACKS=[])
    def test_valid_installation_key_allows_production_check(self):
        from orders.checks import check_production_secret
        self.assertEqual(check_production_secret(None), [])

    @override_settings(DEBUG=False, SECRET_KEY='test-fixture-ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789',
        SECRET_KEY_FALLBACKS=['django-insecure-old-development-key'])
    def test_weak_fallback_key_also_blocks_production(self):
        from orders.checks import check_production_secret
        self.assertEqual([error.id for error in check_production_secret(None)], ['config.E001'])

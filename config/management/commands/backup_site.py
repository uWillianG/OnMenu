import json
import os
import re
import sqlite3
import subprocess
import tempfile
import zipfile
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import connections
from django.utils import timezone


class Command(BaseCommand):
    help = 'Cópia consistente do banco e dos uploads, com manifesto e verificação do arquivo ZIP.'

    def handle(self, *args, **options):
        root = settings.BACKUP_ROOT
        root.mkdir(parents=True, exist_ok=True)
        connection = connections['default']
        stamp = timezone.localtime().strftime('%Y%m%d-%H%M%S-%f')
        destination = root / f'site-{stamp}.zip'
        with tempfile.TemporaryDirectory(dir=root) as directory:
            snapshot = Path(directory) / ('database.sqlite3' if connection.vendor == 'sqlite' else 'database.dump')
            if connection.vendor == 'sqlite':
                if connection.is_in_memory_db():
                    raise CommandError('backup_site requer um banco em arquivo.')
                source = Path(connection.settings_dict['NAME'])
                if not source.exists():
                    raise CommandError('O arquivo do banco não foi encontrado.')
                origin = sqlite3.connect(source)
                target = sqlite3.connect(snapshot)
                try:
                    origin.backup(target)
                finally:
                    target.close()
                    origin.close()
            elif connection.vendor == 'postgresql':
                database = connection.settings_dict
                with connection.cursor() as cursor:
                    cursor.execute("SELECT current_setting('server_version_num')")
                    server_major = int(cursor.fetchone()[0]) // 10000
                try:
                    version = subprocess.run(['pg_dump', '--version'], check=True, capture_output=True, text=True)
                except (OSError, subprocess.CalledProcessError) as exc:
                    raise CommandError('Instale o cliente PostgreSQL da mesma versão principal do servidor.') from exc
                client_major = re.search(r'PostgreSQL\) (\d+)\.', version.stdout)
                if not client_major or int(client_major.group(1)) != server_major:
                    raise CommandError(f'O backup exige cliente PostgreSQL {server_major}. '
                        f'Configure POSTGRES_CLIENT_VERSION={server_major} e reconstrua a imagem.')
                environment = os.environ.copy()
                environment['PGPASSWORD'] = database['PASSWORD']
                try:
                    subprocess.run(['pg_dump', '--format=custom', '--file', str(snapshot),
                        '--host', database.get('HOST') or 'localhost', '--port', str(database.get('PORT') or '5432'),
                        '--username', database['USER'], database['NAME']],
                        env=environment, check=True, capture_output=True)
                except (OSError, subprocess.CalledProcessError) as exc:
                    raise CommandError('Não foi possível copiar o Postgres. Confira pg_dump e a conexão.') from exc
            else:
                raise CommandError('Banco não suportado por backup_site.')
            with zipfile.ZipFile(destination, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
                archive.write(snapshot, snapshot.name)
                if settings.PERSISTED_SECRET_PATH.is_file():
                    archive.write(settings.PERSISTED_SECRET_PATH, 'django-secret')
                for file in settings.MEDIA_ROOT.rglob('*'):
                    if file.is_file() and not file.is_symlink():
                        archive.write(file, 'media/' + file.relative_to(settings.MEDIA_ROOT).as_posix())
                archive.writestr('manifest.json', json.dumps({'created_at':timezone.now().isoformat(),
                    'database_engine':connection.vendor, 'database_file':snapshot.name, 'includes_media':True,
                    'postgres_major':server_major if connection.vendor == 'postgresql' else None}))
            with zipfile.ZipFile(destination) as archive:
                if archive.testzip():
                    raise CommandError('A verificação do backup falhou.')
        keep = settings.BACKUP_KEEP
        if settings.BACKUP_BUCKET:
            if not settings.BACKUP_ACCESS_KEY or not settings.BACKUP_SECRET_KEY:
                raise CommandError('Preencha as credenciais do backup externo. A cópia local foi preservada.')
            if settings.BACKUP_ENDPOINT and not settings.BACKUP_ENDPOINT.startswith('https://'):
                raise CommandError('Use HTTPS para o destino de backup externo.')
            import boto3
            from botocore.config import Config
            try:
                client = boto3.client('s3', endpoint_url=settings.BACKUP_ENDPOINT or None,
                    aws_access_key_id=settings.BACKUP_ACCESS_KEY, aws_secret_access_key=settings.BACKUP_SECRET_KEY,
                    region_name=settings.BACKUP_REGION,
                    config=Config(connect_timeout=10, read_timeout=30, retries={'max_attempts':2}))
                key = '/'.join(part for part in (settings.BACKUP_PREFIX, destination.name) if part)
                client.upload_file(str(destination), settings.BACKUP_BUCKET, key)
            except Exception as exc:
                raise CommandError('Falha ao enviar o backup externo. A cópia local foi preservada.') from exc
        if keep > 0:
            for old in sorted(root.glob('site-*.zip'), reverse=True)[keep:]:
                old.unlink()
        self.stdout.write(self.style.SUCCESS(f'Backup do banco e dos uploads: {destination}'))

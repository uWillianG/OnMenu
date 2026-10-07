import json
import os
import shutil
import sqlite3
import tempfile
import zipfile
from contextlib import closing
from pathlib import Path, PurePosixPath

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone


class Command(BaseCommand):
    help = 'Extrai um backup verificado para uma pasta vazia; nunca sobrescreve a instalação existente.'

    def add_arguments(self, parser):
        parser.add_argument('archive')
        parser.add_argument('--target-dir', required=True)

    def handle(self, *args, **options):
        target = Path(options['target_dir']).resolve()
        if target.exists() and (not target.is_dir() or any(target.iterdir())):
            raise CommandError('Escolha uma pasta vazia para a restauração.')
        try:
            with zipfile.ZipFile(options['archive']) as archive:
                if archive.testzip():
                    raise CommandError('O backup está corrompido.')
                manifest = json.loads(archive.read('manifest.json'))
                if not isinstance(manifest, dict):
                    raise CommandError('O manifesto do backup não é suportado.')
                database_file = manifest['database_file']
                if database_file not in ('database.sqlite3', 'database.dump'):
                    raise CommandError('O manifesto do backup não é suportado.')
                names = archive.namelist()
                if database_file not in names or len({name.casefold() for name in names}) != len(names):
                    raise CommandError('O backup está incompleto ou contém arquivos repetidos.')
                engine = 'sqlite' if database_file == 'database.sqlite3' else 'postgresql'
                if manifest.get('database_engine') not in (None, engine):
                    raise CommandError('O banco informado no manifesto não corresponde ao arquivo.')
                for name in names:
                    path = PurePosixPath(name)
                    canonical = path.as_posix() + ('/' if name.endswith('/') else '')
                    if (path.is_absolute() or '..' in path.parts or '\\' in name or canonical != name
                            or any(':' in part or part.endswith(('.', ' ')) for part in path.parts)):
                        raise CommandError('O backup contém um caminho inválido.')
                    if name not in ('manifest.json', database_file, 'django-secret') and not name.startswith('media/'):
                        raise CommandError('O backup contém um arquivo inesperado.')
                target.parent.mkdir(parents=True, exist_ok=True)
                with tempfile.TemporaryDirectory(prefix='onmenu-restore-', dir=target.parent) as directory:
                    stage = Path(directory).resolve()
                    for name in names:
                        destination = stage / ('db.sqlite3' if name == 'database.sqlite3' else name)
                        if not destination.resolve().is_relative_to(stage):
                            raise CommandError('O backup contém um caminho inválido.')
                        if name.endswith('/'):
                            destination.mkdir(parents=True, exist_ok=True)
                            continue
                        destination.parent.mkdir(parents=True, exist_ok=True)
                        with archive.open(name) as source, destination.open('wb') as output:
                            shutil.copyfileobj(source, output)
                        if name == 'django-secret':
                            os.chmod(destination, 0o600)
                    database = stage / ('db.sqlite3' if engine == 'sqlite' else database_file)
                    with database.open('rb') as source:
                        header = source.read(16)
                    if engine == 'sqlite':
                        if header != b'SQLite format 3\x00':
                            raise CommandError('O banco SQLite do backup está inválido.')
                        with closing(sqlite3.connect(database.as_uri()+'?mode=ro', uri=True)) as restored:
                            if restored.execute('PRAGMA quick_check').fetchall() != [('ok',)]:
                                raise CommandError('A integridade do banco restaurado falhou.')
                        with closing(sqlite3.connect(database)) as restored, restored:
                            if restored.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='config_operationstatus'").fetchone():
                                restored.execute('UPDATE config_operationstatus SET lease_until=NULL, next_run_at=?, last_success_at=NULL, last_error=?',
                                    [timezone.now().isoformat(), ''])
                    elif not header.startswith(b'PGDMP'):
                        raise CommandError('O arquivo PostgreSQL não é um backup no formato esperado.')
                    if target.exists():
                        if not target.is_dir() or any(target.iterdir()):
                            raise CommandError('A pasta de destino deixou de estar vazia. Nada foi sobrescrito.')
                        target.rmdir()
                    stage.rename(target)
        except (zipfile.BadZipFile, KeyError, ValueError, OSError, sqlite3.Error) as exc:
            raise CommandError('Não foi possível validar e restaurar o backup.') from exc
        if database_file == 'database.dump':
            major = manifest.get('postgres_major')
            version = f' {major}' if isinstance(major, int) else ''
            self.stdout.write(self.style.SUCCESS(f'Arquivos extraídos em {target}. Execute pg_restore em um banco PostgreSQL{version} vazio. '
                'Após restaurar, execute reset_operations no banco de destino e configure a pasta de mídia.'))
        else:
            self.stdout.write(self.style.SUCCESS(f'Backup restaurado em {target}. Aponte o banco e a mídia para esta pasta e reinicie a operação automática.'))

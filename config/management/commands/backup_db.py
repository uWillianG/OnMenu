"""Cópia de segurança do banco SQLite.

Uso típico (agendar no Task Scheduler/cron, ex.: de hora em hora):

    python manage.py backup_db

Usa a API de backup online do SQLite, então pode rodar com o site no ar: o
arquivo gerado é consistente mesmo se alguém estiver fechando um pedido no
mesmo instante. Com Postgres (DATABASE_URL) o comando não se aplica — use
``pg_dump``.
"""

import sqlite3
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import connections
from django.utils import timezone

BACKUP_PREFIX = 'db-'
BACKUP_SUFFIX = '.sqlite3'


class Command(BaseCommand):
    help = 'Gera uma cópia de segurança do banco SQLite e remove as mais antigas.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--output-dir',
            default=None,
            help=f'Pasta de destino (padrão: {settings.BACKUP_ROOT}).',
        )
        parser.add_argument(
            '--keep',
            type=int,
            default=None,
            help=(
                'Quantas cópias manter, da mais recente para a mais antiga '
                f'(padrão: {settings.BACKUP_KEEP}; 0 mantém todas).'
            ),
        )

    def handle(self, *args, **options):
        connection = connections['default']
        if connection.vendor != 'sqlite':
            raise CommandError(
                f'backup_db só cobre SQLite; este projeto está em {connection.vendor}. '
                'Use a ferramenta do banco (ex.: pg_dump) no agendador.'
            )

        if connection.is_in_memory_db():
            raise CommandError('O banco está em memória; não há arquivo para copiar.')

        source = Path(connection.settings_dict['NAME'])
        if not source.exists():
            raise CommandError(f'Banco não encontrado em {source}.')

        output_dir = Path(options['output_dir'] or settings.BACKUP_ROOT)
        output_dir.mkdir(parents=True, exist_ok=True)

        stamp = timezone.localtime().strftime('%Y%m%d-%H%M%S')
        destination = output_dir / f'{BACKUP_PREFIX}{stamp}{BACKUP_SUFFIX}'

        self._copy(source, destination)

        size_mb = destination.stat().st_size / (1024 * 1024)
        self.stdout.write(self.style.SUCCESS(f'Backup gravado em {destination} ({size_mb:.1f} MB)'))

        keep = settings.BACKUP_KEEP if options['keep'] is None else options['keep']
        for removed in self._prune(output_dir, keep):
            self.stdout.write(f'Backup antigo removido: {removed.name}')

        return str(destination)

    def _copy(self, source, destination):
        """Backup online do SQLite: conexão própria, sem parar o site.

        A conexão é aberta para leitura e escrita de propósito — em modo WAL o
        SQLite precisa do arquivo ``-shm``, e abrir como somente-leitura falha.
        """
        origin = sqlite3.connect(source)
        try:
            target = sqlite3.connect(destination)
            try:
                origin.backup(target)
            finally:
                target.close()
        finally:
            origin.close()

    def _prune(self, output_dir, keep):
        """Mantém apenas as ``keep`` cópias mais recentes."""
        if keep <= 0:
            return []
        backups = sorted(
            output_dir.glob(f'{BACKUP_PREFIX}*{BACKUP_SUFFIX}'),
            key=lambda path: path.name,
            reverse=True,
        )
        removed = []
        for old in backups[keep:]:
            old.unlink()
            removed.append(old)
        return removed

"""Remove registros antigos de tentativas de acesso (``AccessAttempt``).

A gravação já apaga, por chave, o que saiu da janela — mas restam linhas de
chaves que nunca mais voltaram (o IP do atacante de ontem). Este comando faz a
faxina geral. Agende uma vez por dia:

    .\\.venv\\Scripts\\python manage.py prune_access_attempts

Mantém uma folga (``--hours``, padrão 24 h) acima da maior janela, para nunca
apagar tentativa que ainda conta para algum limite.
"""

from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from accounts.models import AccessAttempt


class Command(BaseCommand):
    help = 'Apaga tentativas de acesso mais antigas que a janela de limite (faxina).'

    def add_arguments(self, parser):
        parser.add_argument(
            '--hours',
            type=int,
            default=24,
            help='Idade mínima (em horas) para apagar. Padrão: 24.',
        )

    def handle(self, *args, **options):
        cutoff = timezone.now() - timedelta(hours=options['hours'])
        removed, _ = AccessAttempt.objects.filter(created_at__lt=cutoff).delete()
        self.stdout.write(self.style.SUCCESS(
            f'Tentativas de acesso removidas: {removed}.'
        ))

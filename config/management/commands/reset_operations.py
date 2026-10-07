from django.core.management.base import BaseCommand
from django.utils import timezone

from config.models import OperationStatus


class Command(BaseCommand):
    help = 'Após restaurar o banco e parar os workers, libera as tarefas e exige uma nova verificação de saúde.'

    def add_arguments(self, parser):
        parser.add_argument('--database', default='default', help='Conexão do banco já restaurado (padrão: default).')

    def handle(self, *args, **options):
        count = OperationStatus.objects.using(options['database']).update(lease_until=None, next_run_at=timezone.now(),
            last_success_at=None, last_error='')
        self.stdout.write(self.style.SUCCESS(f'{count} tarefa(s) pronta(s) para retomar. Inicie o serviço de operação.'))

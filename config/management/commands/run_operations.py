import time
from datetime import timedelta
from io import StringIO

from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.db.models import Q
from django.utils import timezone

from config.models import OperationStatus

TASKS = {
    'sync_pending_pix':60, 'sync_pending_card':120, 'send_whatsapp_notifications':10,
    'expire_abandoned_orders':300, 'prune_access_attempts':86400,
    'prune_customer_data':86400, 'backup_site':3600,
}


class Command(BaseCommand):
    help = 'Executa as tarefas de operação; pronto para serviço/contêiner separado do servidor web.'

    def add_arguments(self, parser):
        parser.add_argument('--once', action='store_true')
        parser.add_argument('--skip-backup', action='store_true')

    def handle(self, *args, **options):
        while True:
            for name, interval in TASKS.items():
                if name == 'backup_site' and options['skip_backup']:
                    continue
                job, _ = OperationStatus.objects.get_or_create(name=name)
                now = timezone.now()
                claimed = OperationStatus.objects.filter(pk=job.pk, next_run_at__lte=now).filter(
                    Q(lease_until__isnull=True) | Q(lease_until__lt=now)
                ).update(lease_until=now+timedelta(minutes=20), next_run_at=now+timedelta(seconds=interval))
                if not claimed:
                    continue
                output, errors = StringIO(), StringIO()
                try:
                    call_command(name, stdout=output, stderr=errors)
                    if errors.getvalue().strip():
                        OperationStatus.objects.filter(pk=job.pk).update(
                            last_error='A tarefa registrou falhas. Consulte os logs da operação.', lease_until=None)
                        self.stderr.write(f'{name}: falha; consultar logs da operação.')
                        self.stderr.write(errors.getvalue().strip())
                    else:
                        OperationStatus.objects.filter(pk=job.pk).update(last_success_at=timezone.now(),
                            last_error='', lease_until=None)
                except Exception as exc:
                    OperationStatus.objects.filter(pk=job.pk).update(last_error='Falha na execução da tarefa.', lease_until=None)
                    diagnostic = str(exc) if isinstance(exc, CommandError) else type(exc).__name__
                    self.stderr.write(f'{name}: falha na execução. {diagnostic}')
            if options['once']:
                return
            time.sleep(5)

"""Sincroniza cobranças Pix pendentes com o Mercado Pago.

Rede de segurança caso o webhook não chegue (item 10 da spec). Agende para
rodar a cada ~10 minutos (Agendador de Tarefas do Windows / cron):

    .\\.venv\\Scripts\\python manage.py sync_pending_pix
"""

from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from orders.models import PixPayment
from orders.services import mercadopago as mp_service
from orders.services import pedidos as pedidos_service

# Janela: ignora cobranças muito antigas (já tratadas como expiradas).
LOOKBACK_MINUTES = 60


class Command(BaseCommand):
    help = 'Consulta o status das cobranças Pix pendentes no Mercado Pago e atualiza os pedidos.'

    def handle(self, *args, **options):
        cutoff = timezone.now() - timedelta(minutes=LOOKBACK_MINUTES)
        pending = PixPayment.objects.select_related('order').filter(
            status=PixPayment.Status.PENDING,
            created_at__gte=cutoff,
        )

        checked = updated = expired = 0
        for pix in pending:
            checked += 1

            if pix.is_expired:
                pix.status = PixPayment.Status.EXPIRED
                pix.save(update_fields=['status', 'updated_at'])
                pedidos_service.marcar_cancelado(pix.order)
                expired += 1
                continue

            if not pix.mp_payment_id:
                continue

            try:
                info = mp_service.buscar_status(pix.mp_payment_id)
            except mp_service.PixError as exc:
                self.stderr.write(f'Pix {pix.mp_payment_id}: {exc}')
                continue

            before = pix.status
            pedidos_service.aplicar_status_mp(pix, info.get('status'))
            if pix.status != before:
                updated += 1

        self.stdout.write(self.style.SUCCESS(
            f'Pix sincronizados: {checked} verificados, {updated} atualizados, {expired} expirados.'
        ))

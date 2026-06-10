"""Sincroniza pagamentos com cartão pendentes/em análise com o Mercado Pago.

Rede de segurança para status ``in_process`` sem webhook (item 11 da spec). Agende
para rodar periodicamente (Agendador de Tarefas do Windows / cron):

    .\\.venv\\Scripts\\python manage.py sync_pending_card
"""

from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from orders.models import CardPayment
from orders.services import mercadopago as mp_service
from orders.services import pedidos as pedidos_service

# Janela: análise antifraude pode levar até ~24h.
LOOKBACK_HOURS = 48


class Command(BaseCommand):
    help = 'Consulta no Mercado Pago os pagamentos com cartão pendentes/em análise e atualiza os pedidos.'

    def handle(self, *args, **options):
        cutoff = timezone.now() - timedelta(hours=LOOKBACK_HOURS)
        pending = CardPayment.objects.select_related('order').filter(
            status__in=[CardPayment.Status.PENDING, CardPayment.Status.IN_PROCESS],
            created_at__gte=cutoff,
        )

        checked = updated = 0
        for card in pending:
            checked += 1
            if not card.mp_payment_id:
                continue
            try:
                info = mp_service.buscar_status(card.mp_payment_id)
            except mp_service.PixError as exc:
                self.stderr.write(f'Cartão {card.mp_payment_id}: {exc}')
                continue

            before = card.status
            pedidos_service.aplicar_status_mp(card, info.get('status'))
            if card.status != before:
                updated += 1

        self.stdout.write(self.style.SUCCESS(
            f'Cartões sincronizados: {checked} verificados, {updated} atualizados.'
        ))

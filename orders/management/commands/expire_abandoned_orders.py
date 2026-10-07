from datetime import timedelta

from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone

from orders.models import Order, PaymentAttempt
from orders.services import payments, pedidos


class Command(BaseCommand):
    help = 'Finaliza pedidos online abandonados sem cobrança em andamento ou pagamento aprovado.'

    def handle(self, *args, **options):
        cutoff = timezone.now() - timedelta(minutes=settings.PAYMENT_ABANDONED_MINUTES)
        candidates = Order.objects.filter(created_at__lt=cutoff, status='received',
            payment_method__in=['pix', 'credit_card']).exclude(payment_status__in=['paid', 'partially_refunded', 'refunded', 'charged_back'])
        count = 0
        for candidate in candidates.iterator():
            with payments.locked_order(candidate.pk) as order:
                payments.ensure_legacy_attempts(order)
                if order.payment_attempts.filter(status__in=PaymentAttempt.BUSY_STATUSES).exists():
                    continue
                if order.is_paid or order.status != 'received':
                    continue
                previous = order.status
                order.status = 'cancelled'
                order.payment_status = 'cancelled'
                order.save(update_fields=['status', 'payment_status', 'updated_at'])
                pedidos.registrar_status(order, previous)
                count += 1
        self.stdout.write(self.style.SUCCESS(f'{count} pedido(s) abandonado(s) finalizado(s).'))

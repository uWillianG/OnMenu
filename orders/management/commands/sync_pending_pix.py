from django.core.management.base import BaseCommand
from django.db.models import Q

from orders.models import Order, PaymentAttempt
from orders.services import mercadopago, payments


class Command(BaseCommand):
    help = 'Reconcilia todas as cobranças Pix pendentes, inclusive respostas incertas e cobranças antigas.'

    def handle(self, *args, **options):
        for order in Order.objects.filter(pix_payment__status='pending').iterator():
            with payments.locked_order(order.pk) as current:
                payments.ensure_legacy_attempts(current)
        checked = updated = 0
        for attempt in PaymentAttempt.objects.filter(method='pix').filter(
                Q(status__in=PaymentAttempt.BUSY_STATUSES) |
                Q(status='approved', refund_requested_at__isnull=False)).select_related('order').iterator():
            checked += 1
            try:
                before = attempt.status
                after = payments.reconcile(attempt)
                updated += before != after.status
            except (mercadopago.PixError, payments.PaymentError) as exc:
                self.stderr.write(f'Não foi possível reconciliar {attempt.order.order_number}: {exc}')
        self.stdout.write(self.style.SUCCESS(f'Pix: {checked} verificados, {updated} atualizados.'))

from datetime import timedelta

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from accounts.data_views import anonymize_orders
from orders.models import Order


class Command(BaseCommand):
    help = 'Anonimiza dados pessoais de pedidos finalizados fora do período de conservação configurado.'

    @transaction.atomic
    def handle(self, *args, **options):
        cutoff = timezone.now() - timedelta(days=settings.CUSTOMER_DATA_RETENTION_DAYS)
        orders = Order.objects.filter(status__in=['delivered', 'cancelled'], created_at__lt=cutoff).exclude(customer_name='Cliente anonimizado')
        count = orders.count()
        anonymize_orders(orders)
        self.stdout.write(self.style.SUCCESS(f'{count} pedido(s) anonimizado(s).'))

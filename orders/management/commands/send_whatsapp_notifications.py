from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from orders.models import WhatsAppMessage
from orders.services.whatsapp_outbox import send_message


class Command(BaseCommand):
    help = 'Envia templates WhatsApp consentidos da fila e registra falhas sem bloquear pedidos.'

    def handle(self, *args, **options):
        WhatsAppMessage.objects.filter(status='sending', updated_at__lt=timezone.now()-timedelta(minutes=5)).update(
            status='uncertain', last_error='Envio interrompido; verificar provedor antes de reenviar.')
        sent = 0
        for message in WhatsAppMessage.objects.filter(status='pending', next_attempt_at__lte=timezone.now()).select_related('order')[:100]:
            sent += send_message(message)
        self.stdout.write(self.style.SUCCESS(f'{sent} mensagem(ns) enviada(s).'))

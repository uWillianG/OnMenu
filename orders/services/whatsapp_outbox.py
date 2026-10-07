from datetime import timedelta

import requests
from django.conf import settings
from django.db.models import F
from django.utils import timezone

from ..models import WhatsAppMessage
from .whatsapp import formatar_numero


def send_message(message):
    if not WhatsAppMessage.objects.filter(pk=message.pk, status='pending').update(
        status='sending', attempts=F('attempts') + 1, updated_at=timezone.now()):
        return False
    message.refresh_from_db()
    message.order.refresh_from_db()
    superseded = (message.order.status != message.order_status or
                  message.order.status_changes.filter(created_at__gt=message.created_at).exists())
    if not message.order.whatsapp_opt_in or not formatar_numero(message.order.phone) or superseded:
        message.status = 'skipped'
        message.save(update_fields=['status', 'updated_at'])
        return False
    if settings.WHATSAPP_MOCK:
        message.status = 'sent' if settings.DEBUG else 'skipped'
        message.provider_id = f'MOCK-{message.pk}' if settings.DEBUG else ''
        message.save(update_fields=['status', 'provider_id', 'updated_at'])
        return False
    payload = {'messaging_product': 'whatsapp', 'to': formatar_numero(message.order.phone), 'type':'template',
        'template': {'name': settings.WHATSAPP_STATUS_TEMPLATE,
            'language': {'code': settings.WHATSAPP_TEMPLATE_LANGUAGE},
            'components': [{'type':'body', 'parameters': [
                {'type':'text', 'text':message.order.order_number},
                {'type':'text', 'text':message.order.label_for_status(message.order_status)},
            ]}]}}
    try:
        response = requests.post(
            f'https://graph.facebook.com/{settings.WHATSAPP_API_VERSION}/{settings.WHATSAPP_PHONE_ID}/messages',
            json=payload, headers={'Authorization':f'Bearer {settings.WHATSAPP_TOKEN}'}, timeout=15)
        if response.status_code == 429:
            message.status = 'pending' if message.attempts < 6 else 'failed'
            message.next_attempt_at = timezone.now() + timedelta(minutes=2 ** message.attempts)
            message.last_error = 'Limite temporário de envio do provedor.'
        elif response.status_code >= 500:
            message.status = 'uncertain'
            message.last_error = 'O provedor não confirmou o envio. Verifique antes de reenviar.'
        elif response.status_code >= 400:
            message.status = 'failed'
            message.last_error = f'O provedor recusou o template (HTTP {response.status_code}).'
        else:
            message.provider_id = response.json()['messages'][0]['id']
            message.status = 'sent'
            message.last_error = ''
    except (requests.RequestException, ValueError, KeyError, IndexError):
        message.status = 'uncertain'
        message.last_error = 'Resposta de envio incerta. Verifique o provedor antes de reenviar.'
    message.save(update_fields=['status', 'provider_id', 'last_error', 'next_attempt_at', 'updated_at'])
    return message.status == 'sent'

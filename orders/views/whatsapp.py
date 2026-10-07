import hashlib
import hmac
import json

from django.conf import settings
from django.http import HttpResponse
from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from ..models import WhatsAppMessage


@staff_member_required
def outbox(request):
    return render(request, 'orders/whatsapp_outbox.html',
                  {'deliveries':WhatsAppMessage.objects.select_related('order').order_by('-created_at')[:100]})


@staff_member_required
@require_http_methods(['POST'])
def retry_message(request, message_id):
    message = get_object_or_404(WhatsAppMessage.objects.select_related('order'), pk=message_id)
    if request.POST.get('confirm_retry') != 'on':
        messages.warning(request, 'Confirme que verificou o envio no provedor antes de reenviar.')
    elif message.status not in ('failed','uncertain') or not message.order.whatsapp_opt_in:
        messages.warning(request, 'Esta mensagem não pode ser reenviada.')
    else:
        WhatsAppMessage.objects.filter(pk=message.pk, status__in=['failed','uncertain']).update(
            status='pending', next_attempt_at=timezone.now(), last_error='', attempts=0)
        messages.success(request, 'Mensagem recolocada na fila de envio.')
    return redirect('orders:whatsapp_outbox')


@csrf_exempt
@require_http_methods(['GET', 'POST'])
def webhook_whatsapp(request):
    if request.method == 'GET':
        expected = settings.WHATSAPP_WEBHOOK_VERIFY_TOKEN
        token = request.GET.get('hub.verify_token', '')
        if expected and request.GET.get('hub.mode') == 'subscribe' and hmac.compare_digest(token.encode(), expected.encode()):
            return HttpResponse(request.GET.get('hub.challenge', ''), content_type='text/plain')
        return HttpResponse(status=403)
    if not settings.WHATSAPP_APP_SECRET:
        return HttpResponse(status=401)
    expected = 'sha256=' + hmac.new(settings.WHATSAPP_APP_SECRET.encode(), request.body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected.encode(), request.headers.get('x-hub-signature-256', '').encode()):
        return HttpResponse(status=401)
    try:
        body = json.loads(request.body)
        for entry in body.get('entry', []):
            for change in entry.get('changes', []):
                for update in change.get('value', {}).get('statuses', []):
                    state = update.get('status')
                    if state not in ('sent', 'delivered', 'read', 'failed'):
                        continue
                    message = WhatsAppMessage.objects.filter(provider_id=update.get('id', '')).first()
                    if message and not (message.status == 'read' or message.status == 'delivered' and state == 'sent'):
                        message.status = state
                        message.last_error = 'O provedor informou falha na entrega.' if state == 'failed' else ''
                        message.save(update_fields=['status', 'last_error', 'updated_at'])
    except (ValueError, TypeError, AttributeError):
        return HttpResponse(status=400)
    return HttpResponse(status=200)

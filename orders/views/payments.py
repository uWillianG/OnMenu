"""Pagamentos online (Mercado Pago): cobranças Pix, cartão de crédito e os
webhooks compartilhados por ambos."""

import hashlib
import hmac
import json
import logging

from django.conf import settings
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from .. import selectors
from ..models import CardPayment, Order, PixPayment, PaymentAttempt
from ..services import mercadopago as mp_service
from ..services import payments as payment_service
from .common import get_own_order
from ..forms import CheckoutForm
from accounts.validators import validate_cpf

logger = logging.getLogger(__name__)


@require_http_methods(['GET', 'POST'])
def payment_resume(request, order_number):
    order = get_own_order(request, order_number,
        Order.objects.select_related('restaurant').prefetch_related('items'))
    if not order.can_pay:
        return redirect('orders:confirmation', order_number=order.order_number)
    if request.method == 'POST':
        if order.payment_method == 'pix':
            if not order.customer_email:
                email = request.POST.get('customer_email', '').strip()
                try:
                    validate_email(email)
                except ValidationError:
                    return JsonResponse({'ok':False,'error':'Informe um e-mail válido para o comprovante.'}, status=400)
                order.customer_email = email
                order.save(update_fields=['customer_email', 'updated_at'])
            return pix_recreate(request, order_number)
        return JsonResponse({'ok': True, 'mode': 'card', 'order_number': order.order_number,
            'amount': str(order.total), 'card_pay_url': reverse('orders:card_pay', args=[order.order_number]),
            'confirmation_url': reverse('orders:confirmation', args=[order.order_number])})
    form = CheckoutForm(instance=order, restaurant=order.restaurant)
    for name, field in form.fields.items():
        if name != 'payment_method' and not (name == 'customer_email' and order.payment_method == 'pix' and not order.customer_email):
            field.disabled = True
    form.fields['payment_method'].choices = [(order.payment_method, order.get_payment_method_display())]
    context = {'ok': True, 'mode': 'card', 'order_number': order.order_number,
        'amount': str(order.total), 'card_pay_url': reverse('orders:card_pay', args=[order.order_number]),
        'confirmation_url': reverse('orders:confirmation', args=[order.order_number])}
    latest = order.payment_attempts.first()
    return render(request, 'orders/checkout.html', {'form': form, 'resume_order': order,
        'cart_items': [{'item': {'name': item.item_name}, 'quantity': item.quantity,
                        'line_total': item.line_total} for item in order.items.all()],
        'subtotal': order.subtotal, 'delivery_fee': order.delivery_fee, 'estimated_total': order.total,
        'restaurant': order.restaurant, 'delivery_areas': {},
        'mercadopago_public_key': settings.MERCADOPAGO_PUBLIC_KEY,
        'payment_mock_allowed': settings.MERCADOPAGO_MOCK and settings.MERCADOPAGO_MOCK_ALLOWED,
        'resume_card_context': context if order.payment_method == 'credit_card' else None,
        'resume_attempt': _card_payload(latest, order) if latest and latest.method == 'credit_card' else None,
        'available_payment_methods': [(value, label) for value, label in Order.PaymentMethod.choices
            if value in payment_service.enabled_methods() and value != order.payment_method],
    })


@require_http_methods(['GET'])
def payment_state(request, order_number):
    order = get_own_order(request, order_number)
    with payment_service.locked_order(order.pk) as current:
        payment_service.ensure_legacy_attempts(current)
    latest = order.payment_attempts.first()
    if latest and latest.status in PaymentAttempt.BUSY_STATUSES:
        try:
            latest = payment_service.reconcile(latest)
        except (mp_service.PixError, payment_service.PaymentError):
            logger.warning('Pagamento em verificação: %s', order.order_number)
    order.refresh_from_db()
    data = _card_payload(latest, order) if latest and latest.method == 'credit_card' else {}
    data.update(ok=True, paid=order.is_paid, cancelled=order.status == 'cancelled',
        payment_status=order.payment_status, attempt_status=latest.status if latest else '',
        confirmation_url=reverse('orders:confirmation', args=[order.order_number]))
    return JsonResponse(data)


@require_http_methods(['POST'])
def payment_change(request, order_number):
    order = get_own_order(request, order_number)
    method = request.POST.get('payment_method', '')
    if method not in payment_service.enabled_methods():
        messages.warning(request, 'Selecione um meio de pagamento disponível.')
        return redirect('orders:payment_resume', order_number=order_number)
    cpf = order.customer_cpf
    if method == 'pix':
        try:
            cpf = validate_cpf(request.POST.get('customer_cpf') or cpf)
        except ValidationError:
            messages.warning(request, 'Informe um CPF válido para utilizar Pix.')
            return redirect('orders:payment_resume', order_number=order_number)
    with payment_service.locked_order(order.pk) as current:
        if not current.can_pay:
            return redirect('orders:confirmation', order_number=order_number)
        payment_service.ensure_legacy_attempts(current)
    for attempt in order.payment_attempts.filter(status__in=PaymentAttempt.BUSY_STATUSES):
        if attempt.method == 'pix' and attempt.mp_payment_id:
            try:
                info = mp_service.cancelar(attempt.mp_payment_id)
                payment_service.apply_provider_info(attempt, info)
            except (mp_service.PixError, payment_service.PaymentError):
                messages.warning(request, 'Não foi possível confirmar o cancelamento do Pix anterior.')
                return redirect('orders:payment_resume', order_number=order_number)
    with payment_service.locked_order(order.pk) as current:
        if not current.can_pay or current.payment_attempts.filter(status__in=PaymentAttempt.BUSY_STATUSES).exists():
            messages.warning(request, 'Aguarde a confirmação do pagamento em andamento antes de trocar o meio.')
            return redirect('orders:payment_resume', order_number=order_number)
        current.payment_method = method
        current.payment_status = 'pending'
        current.customer_cpf = cpf
        current.save(update_fields=['payment_method', 'payment_status', 'customer_cpf', 'updated_at'])
        if not current.requires_online_payment:
            from ..services.notificacoes import notificar_admins_novo_pedido
            notificar_admins_novo_pedido(current)
    return redirect('orders:payment_resume' if current.requires_online_payment else 'orders:confirmation', order_number=order_number)


# --------------------------------------------------------------------------- #
# Pix / Mercado Pago
# --------------------------------------------------------------------------- #

def create_pix_for_order(order):
    """Cria (ou reusa) a cobrança Pix de um pedido e persiste o PixPayment."""
    payment_service.create_pix(order)
    return PixPayment.objects.get(order=order)


def pix_payload(pix, order):
    return {
        'ok': True,
        'pixId': pix.mp_payment_id,
        'qrCodeBase64': pix.qr_code_base64,
        'qrCodeText': pix.qr_code_text,
        'expiracao': pix.expires_at.isoformat() if pix.expires_at else None,
        'order_number': order.order_number,
        'confirmation_url': reverse('orders:confirmation', args=[order.order_number]),
        'payment_url': reverse('orders:payment_resume', args=[order.order_number]),
        'amount': str(order.total),
        'delivery_fee': str(order.delivery_fee),
    }


@require_http_methods(['GET'])
def pix_status(request, pix_id):
    """Polling do frontend: devolve o status atual da cobrança Pix."""
    pix = _get_payment_attempt(request, pix_id, 'pix')

    if pix.status == PixPayment.Status.PENDING:
        try:
            pix = payment_service.reconcile(pix)
        except (mp_service.PixError, payment_service.PaymentError):
            logger.warning('Falha ao consultar status do Pix %s', pix.mp_payment_id)

    return JsonResponse({
        'status': pix.status,
        'paid': pix.status == PixPayment.Status.APPROVED,
        'cancelled': pix.status in (PixPayment.Status.CANCELLED, PixPayment.Status.EXPIRED),
        'confirmation_url': reverse('orders:confirmation', args=[pix.order.order_number]),
    })


@require_http_methods(['POST'])
def pix_recreate(request, order_number):
    """Gera uma nova cobrança Pix para um pedido cujo Pix expirou/foi cancelado."""
    order = get_own_order(request, order_number)

    if order.payment_method != Order.PaymentMethod.PIX:
        return JsonResponse({'ok': False, 'error': 'Este pedido não usa Pix.'}, status=400)
    if order.status == Order.Status.CANCELLED:
        return JsonResponse({'ok': False, 'error': 'Este pedido foi cancelado.'}, status=409)
    if order.is_paid:
        return JsonResponse({
            'ok': True,
            'paid': True,
            'confirmation_url': reverse('orders:confirmation', args=[order.order_number]),
        })

    try:
        pix = create_pix_for_order(order)
    except (mp_service.PixError, payment_service.PaymentError) as exc:
        logger.exception('Falha ao recriar cobrança Pix')
        return _payment_error(exc, order)
    return JsonResponse(pix_payload(pix, order))


@csrf_exempt
@require_http_methods(['POST'])
def webhook_pix(request):
    """Webhook do Mercado Pago para cobranças Pix."""
    return _handle_payment_webhook(request)


@csrf_exempt
@require_http_methods(['POST'])
def webhook_card(request):
    """Webhook do Mercado Pago para pagamentos com cartão."""
    return _handle_payment_webhook(request)


def _handle_payment_webhook(request):
    """Confirma/cancela o pedido conforme o pagamento. Responde 200 ao MP."""
    if not _webhook_signature_ok(request):
        return HttpResponse(status=401)

    try:
        body = json.loads(request.body or b'{}')
    except (ValueError, TypeError):
        body = {}
    if not isinstance(body, dict):
        return HttpResponse(status=400)

    data = body.get('data') or {}
    if not isinstance(data, dict):
        return HttpResponse(status=400)
    data_id = data.get('id') or request.GET.get('data.id')
    topic = body.get('type') or body.get('topic') or request.GET.get('type')

    # Responde 200 sempre que possível; processa de forma defensiva.
    if data_id and topic in (None, 'payment'):
        try:
            _process_payment_webhook(str(data_id))
        except mp_service.PixError:
            logger.warning('Webhook: falha ao consultar pagamento %s', data_id)
            return HttpResponse(status=503)
        except Exception:  # noqa: BLE001 - erro temporário permite nova entrega do webhook
            logger.exception('Webhook: erro inesperado ao processar %s', data_id)
            return HttpResponse(status=500)

    return HttpResponse(status=200)


def _find_payment(mp_payment_id, external_reference=None):
    """Acha o PixPayment ou CardPayment correspondente, por id do MP ou referência."""
    for model in (PixPayment, CardPayment):
        payment = model.objects.select_related('order').filter(mp_payment_id=mp_payment_id).first()
        if payment is None and external_reference:
            payment = (
                model.objects.select_related('order')
                .filter(external_reference=external_reference)
                .first()
            )
        if payment is not None:
            return payment
    return None


def _get_payment_attempt(request, payment_id, method):
    """Busca também cobranças antigas sem depender do snapshot atual do pedido."""
    attempt = PaymentAttempt.objects.select_related('order').filter(
        mp_payment_id=payment_id, method=method).first()
    if attempt is None:
        model = PixPayment if method == 'pix' else CardPayment
        payment = get_object_or_404(model.objects.select_related('order'), mp_payment_id=payment_id)
        if not selectors.can_view_order(request, payment.order):
            raise Http404('Pagamento não encontrado.')
        with payment_service.locked_order(payment.order_id) as current:
            payment_service.ensure_legacy_attempts(current)
        attempt = get_object_or_404(PaymentAttempt.objects.select_related('order'),
            mp_payment_id=payment_id, method=method)
    if not selectors.can_view_order(request, attempt.order):
        raise Http404('Pagamento não encontrado.')
    return attempt


def _process_payment_webhook(data_id):
    info = mp_service.buscar_status(data_id)
    attempt = PaymentAttempt.objects.select_related('order').filter(mp_payment_id=data_id).first()
    if attempt is None and info.get('external_reference'):
        attempt = PaymentAttempt.objects.select_related('order').filter(
            external_reference=info['external_reference']).first()
    if attempt:
        payment_service.apply_provider_info(attempt, {**info, 'id':data_id})
        return
    payment = _find_payment(data_id, info.get('external_reference'))
    if payment is not None:
        with payment_service.locked_order(payment.order_id) as current:
            payment_service.ensure_legacy_attempts(current)
        attempt = PaymentAttempt.objects.select_related('order').get(mp_payment_id=payment.mp_payment_id)
        payment_service.apply_provider_info(attempt, {**info, 'id':data_id})


# --------------------------------------------------------------------------- #
# Cartão de crédito
# --------------------------------------------------------------------------- #

# Mensagens amigáveis de recusa (sem expor o código interno do MP).
_CARD_ERROR_MESSAGES = {
    'cc_rejected_insufficient_amount': 'Saldo insuficiente.',
    'cc_rejected_bad_filled_security_code': 'Código de segurança inválido.',
    'cc_rejected_bad_filled_date': 'Data de validade inválida.',
    'cc_rejected_bad_filled_other': 'Dados do cartão inválidos. Confira e tente novamente.',
    'cc_rejected_call_for_authorize': 'Autorize o pagamento com seu banco e tente novamente.',
    'cc_rejected_card_disabled': 'Cartão desabilitado. Use outro cartão.',
    'cc_rejected_high_risk': 'Pagamento recusado. Tente outro meio de pagamento.',
    'cc_rejected_max_attempts': 'Muitas tentativas. Use outro cartão.',
}


def _card_error_message(status_detail):
    return _CARD_ERROR_MESSAGES.get(status_detail, 'Cartão recusado. Tente outro cartão.')


def _card_payload(card, order, message=''):
    data = card.response_data if isinstance(card, PaymentAttempt) else {}
    return {
        'ok': True,
        'status': card.status,
        'paymentId': card.mp_payment_id,
        'message': message,
        'requires_action': card.status == 'pending' and bool(data.get('creq')),
        'redirect_url': data.get('redirect_url', ''),
        'creq': data.get('creq', ''),
        'confirmation_url': reverse('orders:confirmation', args=[order.order_number]),
        'payment_url': reverse('orders:payment_resume', args=[order.order_number]),
    }


def _payment_error(exc, order):
    message = exc.messages[0] if isinstance(exc, payment_service.PaymentError) else 'Não foi possível consultar o pagamento.'
    return JsonResponse({'ok': False, 'error': message, 'order_number': order.order_number,
        'payment_url': reverse('orders:payment_resume', args=[order.order_number])},
        status=getattr(exc, 'http_status', 409))


@require_http_methods(['POST'])
def card_pay(request, order_number):
    """Processa o pagamento com cartão a partir do token gerado pelo Brick."""
    order = get_own_order(request, order_number)
    if order.payment_method != Order.PaymentMethod.CREDIT_CARD or order.status == Order.Status.CANCELLED:
        return JsonResponse({'ok': False, 'error': 'Este pedido não aceita pagamento com cartão.'}, status=409)

    # Idempotência: não cobra de novo se já houver pagamento aprovado/em análise.
    existing = getattr(order, 'card_payment', None)
    if existing and existing.status in (CardPayment.Status.APPROVED, CardPayment.Status.IN_PROCESS):
        return JsonResponse(_card_payload(existing, order))

    token = request.POST.get('token', '')
    if not token:
        return JsonResponse({'ok': False, 'error': 'Token do cartão ausente.'}, status=400)

    try:
        card = payment_service.create_card(order,
            token=token,
            installments=request.POST.get('installments') or 1,
            payment_method_id=request.POST.get('payment_method_id', ''),
            issuer_id=request.POST.get('issuer_id', ''),
            payer_email=request.POST.get('payer_email', '') or order.customer_email,
            payer_cpf=request.POST.get('payer_cpf', '') or order.customer_cpf,
        )
    except payment_service.PaymentError as exc:
        logger.info('Pagamento não iniciado: %s', exc.code)
        return _payment_error(exc, order)

    payload = _card_payload(card, order)
    if card.status == 'rejected':
        payload['message'] = _card_error_message(card.status_detail)
    return JsonResponse(payload)


@require_http_methods(['GET'])
def card_status(request, payment_id):
    """Polling/refresh do status de um pagamento com cartão."""
    card = _get_payment_attempt(request, payment_id, 'credit_card')

    if card.status in (CardPayment.Status.PENDING, CardPayment.Status.IN_PROCESS) \
            and not settings.MERCADOPAGO_MOCK:
        try:
            card = payment_service.reconcile(card)
        except (mp_service.PixError, payment_service.PaymentError):
            logger.warning('Falha ao consultar status do cartão %s', card.mp_payment_id)

    return JsonResponse({
        'status': card.status,
        'paid': card.status == CardPayment.Status.APPROVED,
        'rejected': card.status == CardPayment.Status.REJECTED,
        'confirmation_url': reverse('orders:confirmation', args=[card.order.order_number]),
    })


@require_http_methods(['GET'])
def card_3ds_callback(request):
    """Retorno da autenticação 3DS: consulta o status final e segue para a confirmação."""
    payment_id = request.GET.get('payment_id') or request.GET.get('data.id') or ''
    card = None
    if payment_id:
        card = _get_payment_attempt(request, payment_id, 'credit_card')
        if not settings.MERCADOPAGO_MOCK:
            try:
                card = payment_service.reconcile(card, force=True)
            except (mp_service.PixError, payment_service.PaymentError):
                logger.warning('3DS callback: falha ao consultar %s', payment_id)

    if card is not None:
        return redirect('orders:confirmation', order_number=card.order.order_number)
    messages.warning(request, 'Não foi possível confirmar o pagamento. Verifique seu pedido.')
    return redirect('menu:menu_list')


def _webhook_signature_ok(request):
    """Valida o header x-signature do Mercado Pago.

    Sem secret configurado só aceita em desenvolvimento (modo mock ou DEBUG) —
    em produção um webhook sem assinatura verificável é recusado, senão qualquer
    um poderia marcar pedidos como pagos.
    """
    secret = settings.MERCADOPAGO_WEBHOOK_SECRET
    if not secret:
        if settings.MERCADOPAGO_MOCK or settings.DEBUG:
            return True
        logger.error(
            'Webhook recusado: MERCADOPAGO_WEBHOOK_SECRET não configurado em produção.'
        )
        return False

    signature = request.headers.get('x-signature', '')
    request_id = request.headers.get('x-request-id', '')
    data_id = request.GET.get('data.id') or ''
    if not data_id:
        try:
            data_id = (json.loads(request.body or b'{}').get('data') or {}).get('id') or ''
        except (ValueError, TypeError, AttributeError):
            data_id = ''

    ts = v1 = ''
    for part in signature.split(','):
        key, _, value = part.strip().partition('=')
        if key == 'ts':
            ts = value
        elif key == 'v1':
            v1 = value
    if not (ts and v1):
        return False

    manifest = f'id:{data_id};request-id:{request_id};ts:{ts};'
    expected = hmac.new(secret.encode(), manifest.encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected.encode(), v1.encode())

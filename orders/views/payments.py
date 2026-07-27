"""Pagamentos online (Mercado Pago): cobranças Pix, cartão de crédito e os
webhooks compartilhados por ambos."""

import hashlib
import hmac
import json
import logging

from django.conf import settings
from django.contrib import messages
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from .. import selectors
from ..models import CardPayment, Order, PixPayment
from ..services import mercadopago as mp_service
from ..services import pedidos as pedidos_service
from .common import get_own_order

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Pix / Mercado Pago
# --------------------------------------------------------------------------- #

def create_pix_for_order(order):
    """Cria (ou reusa) a cobrança Pix de um pedido e persiste o PixPayment."""
    existing = getattr(order, 'pix_payment', None)
    if (
        existing
        and existing.status == PixPayment.Status.PENDING
        and not existing.is_expired
        and existing.mp_payment_id
    ):
        return existing

    data = mp_service.criar_pix(
        amount=order.total,
        description=f'Pedido {order.order_number}',
        payer_email=order.customer_email or settings.PIX_DEFAULT_PAYER_EMAIL,
        payer_name=order.customer_name,
        payer_cpf=order.customer_cpf,
        external_reference=order.order_number,
    )
    pix, _ = PixPayment.objects.update_or_create(
        order=order,
        defaults={
            'mp_payment_id': data['id'],
            'external_reference': order.order_number,
            'status': PixPayment.Status.PENDING,
            'amount': order.total,
            'qr_code_text': data['qr_code_text'],
            'qr_code_base64': data['qr_code_base64'],
            'txid': data.get('txid', ''),
            'expires_at': data.get('expires_at'),
        },
    )
    return pix


def pix_payload(pix, order):
    return {
        'ok': True,
        'pixId': pix.mp_payment_id,
        'qrCodeBase64': pix.qr_code_base64,
        'qrCodeText': pix.qr_code_text,
        'expiracao': pix.expires_at.isoformat() if pix.expires_at else None,
        'order_number': order.order_number,
        'confirmation_url': reverse('orders:confirmation', args=[order.order_number]),
    }


@require_http_methods(['GET'])
def pix_status(request, pix_id):
    """Polling do frontend: devolve o status atual da cobrança Pix."""
    pix = get_object_or_404(PixPayment.objects.select_related('order'), mp_payment_id=pix_id)
    if not selectors.can_view_order(request, pix.order):
        raise Http404('Cobrança não encontrada.')

    if pix.status == PixPayment.Status.PENDING:
        if pix.is_expired:
            pix.status = PixPayment.Status.EXPIRED
            pix.save(update_fields=['status', 'updated_at'])
            pedidos_service.marcar_cancelado(pix.order)
        elif not settings.MERCADOPAGO_MOCK:
            # Sem webhook (ex.: localhost), consultamos o MP diretamente.
            try:
                info = mp_service.buscar_status(pix.mp_payment_id)
                pedidos_service.aplicar_status_mp(pix, info.get('status'))
            except mp_service.PixError:
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

    if order.payment_status == Order.PaymentStatus.PAID:
        return JsonResponse({
            'ok': True,
            'paid': True,
            'confirmation_url': reverse('orders:confirmation', args=[order.order_number]),
        })

    try:
        pix = create_pix_for_order(order)
    except mp_service.PixError as exc:
        logger.exception('Falha ao recriar cobrança Pix')
        return JsonResponse({'ok': False, 'error': str(exc)}, status=502)
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

    data_id = (body.get('data') or {}).get('id') or request.GET.get('data.id')
    topic = body.get('type') or body.get('topic') or request.GET.get('type')

    # Responde 200 sempre que possível; processa de forma defensiva.
    if data_id and topic in (None, 'payment'):
        try:
            _process_payment_webhook(str(data_id))
        except mp_service.PixError:
            logger.warning('Webhook: falha ao consultar pagamento %s', data_id)
        except Exception:  # noqa: BLE001 - nunca devolver 500 para o MP
            logger.exception('Webhook: erro inesperado ao processar %s', data_id)

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


def _process_payment_webhook(data_id):
    info = mp_service.buscar_status(data_id)
    payment = _find_payment(data_id, info.get('external_reference'))
    if payment is not None:
        pedidos_service.aplicar_status_mp(payment, info.get('status'))


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
    return {
        'ok': True,
        'status': card.status,
        'paymentId': card.mp_payment_id,
        'message': message,
        'requires_action': False,
        'redirect_url': '',
        'confirmation_url': reverse('orders:confirmation', args=[order.order_number]),
    }


@require_http_methods(['POST'])
def card_pay(request, order_number):
    """Processa o pagamento com cartão a partir do token gerado pelo Brick."""
    order = get_own_order(request, order_number)

    # Idempotência: não cobra de novo se já houver pagamento aprovado/em análise.
    existing = getattr(order, 'card_payment', None)
    if existing and existing.status in (CardPayment.Status.APPROVED, CardPayment.Status.IN_PROCESS):
        return JsonResponse(_card_payload(existing, order))

    token = request.POST.get('token', '')
    if not token:
        return JsonResponse({'ok': False, 'error': 'Token do cartão ausente.'}, status=400)

    try:
        data = mp_service.criar_pagamento_cartao(
            amount=order.total,
            description=f'Pedido {order.order_number}',
            token=token,
            installments=request.POST.get('installments') or 1,
            payment_method_id=request.POST.get('payment_method_id', ''),
            issuer_id=request.POST.get('issuer_id', ''),
            payer_email=request.POST.get('payer_email', '') or order.customer_email,
            payer_cpf=request.POST.get('payer_cpf', '') or order.customer_cpf,
            external_reference=order.order_number,
        )
    except mp_service.PixError as exc:
        logger.exception('Falha ao processar cartão')
        return JsonResponse({'ok': False, 'error': str(exc)}, status=503)

    card, _ = CardPayment.objects.update_or_create(
        order=order,
        defaults={
            'mp_payment_id': data['id'],
            'external_reference': order.order_number,
            'status': data['status'],
            'status_detail': data.get('status_detail', ''),
            'amount': order.total,
            'installments': data.get('installments', 1),
            'payment_method_id': data.get('payment_method_id', ''),
            'last_four': data.get('last_four', ''),
        },
    )
    pedidos_service.aplicar_status_mp(card, data['status'])

    payload = _card_payload(card, order)
    if data['status'] == 'rejected':
        payload['message'] = _card_error_message(data.get('status_detail'))
    elif data.get('requires_action'):
        payload['requires_action'] = True
        payload['redirect_url'] = data.get('redirect_url', '')
    return JsonResponse(payload)


@require_http_methods(['GET'])
def card_status(request, payment_id):
    """Polling/refresh do status de um pagamento com cartão."""
    card = get_object_or_404(CardPayment.objects.select_related('order'), mp_payment_id=payment_id)
    if not selectors.can_view_order(request, card.order):
        raise Http404('Pagamento não encontrado.')

    if card.status in (CardPayment.Status.PENDING, CardPayment.Status.IN_PROCESS) \
            and not settings.MERCADOPAGO_MOCK:
        try:
            info = mp_service.buscar_status(card.mp_payment_id)
            pedidos_service.aplicar_status_mp(card, info.get('status'))
        except mp_service.PixError:
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
        card = CardPayment.objects.select_related('order').filter(mp_payment_id=payment_id).first()
        if card is not None and not settings.MERCADOPAGO_MOCK:
            try:
                info = mp_service.buscar_status(card.mp_payment_id)
                pedidos_service.aplicar_status_mp(card, info.get('status'))
            except mp_service.PixError:
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
        except (ValueError, TypeError):
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
    return hmac.compare_digest(expected, v1)

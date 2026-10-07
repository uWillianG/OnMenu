"""Tentativas persistentes: reserva antes de rede, reconciliação e histórico.

A transação não permanece aberta durante chamadas ao provedor. O UPDATE do
pedido serializa as reservas tanto no PostgreSQL quanto no SQLite (onde
select_for_update não bloqueia). Tokens de cartão não são persistidos.
"""
import hashlib
from contextlib import contextmanager
from decimal import Decimal, InvalidOperation
from datetime import timedelta

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from accounts.validators import validate_cpf
from django.db import transaction
from django.db.models import F, Q
from django.utils import timezone

from ..models import CardPayment, Order, PaymentAttempt, PixPayment
from . import mercadopago as gateway
from . import pedidos


class PaymentError(ValidationError):
    def __init__(self, message, *, code='payment_invalid', http_status=400):
        super().__init__(message, code=code)
        self.http_status = http_status


def enabled_methods():
    mock = settings.MERCADOPAGO_MOCK and settings.MERCADOPAGO_MOCK_ALLOWED
    configured = bool(settings.MERCADOPAGO_ACCESS_TOKEN and settings.MERCADOPAGO_WEBHOOK_SECRET)
    methods = [Order.PaymentMethod.CASH, Order.PaymentMethod.CARD_ON_DELIVERY]
    if settings.PAYMENT_PIX_ENABLED and (mock or configured):
        methods.append(Order.PaymentMethod.PIX)
    if settings.PAYMENT_CARD_ENABLED and (mock or (configured and settings.MERCADOPAGO_PUBLIC_KEY)):
        methods.append(Order.PaymentMethod.CREDIT_CARD)
    return methods


@contextmanager
def locked_order(order_id):
    with transaction.atomic():
        if not Order.objects.filter(pk=order_id).update(payment_status=F('payment_status')):
            raise PaymentError('Pedido não encontrado.')
        yield Order.objects.select_related('restaurant').get(pk=order_id)


def validate_payment(order, method):
    if order.payment_method != method:
        raise PaymentError('Este meio de pagamento não corresponde ao pedido.')
    if not order.can_pay:
        raise PaymentError('Este pedido não aceita um novo pagamento.', code='payment_closed', http_status=409)
    if method not in enabled_methods():
        raise PaymentError('Este meio de pagamento está indisponível no momento.')
    if not Decimal('0.01') <= order.total <= settings.CART_MAX_SUBTOTAL + Decimal('9999.99'):
        raise PaymentError('O valor do pedido não permite pagamento online.')


def ensure_legacy_attempts(order):
    """Mantém cobranças anteriores à mudança reconciliáveis sem sobrescrevê-las."""
    for model, method, relation in (
        (PixPayment, 'pix', 'pix_payment'), (CardPayment, 'credit_card', 'card_payment')
    ):
        payment = getattr(order, relation, None)
        if not payment or not payment.mp_payment_id:
            continue
        if PaymentAttempt.objects.filter(mp_payment_id=payment.mp_payment_id).exists():
            continue
        data = {'id': payment.mp_payment_id, 'status': payment.status}
        if method == 'pix':
            data.update(qr_code_text=payment.qr_code_text, qr_code_base64=payment.qr_code_base64)
        PaymentAttempt.objects.create(order=order, method=method, amount=payment.amount,
            mp_payment_id=payment.mp_payment_id, status=payment.status,
            external_reference=f'legacy-{method}-{payment.pk}', response_data=data,
            expires_at=getattr(payment, 'expires_at', None))


def reserve(order, method, *, token=''):
    fingerprint = hashlib.sha256(token.encode()).hexdigest() if token else ''
    with locked_order(order.pk) as current:
        validate_payment(current, method)
        ensure_legacy_attempts(current)
        if fingerprint:
            previous = current.payment_attempts.filter(method=method, fingerprint=fingerprint).first()
            if previous:
                if previous.status in (PaymentAttempt.Status.CREATING, PaymentAttempt.Status.UNCERTAIN):
                    raise PaymentError('Estamos verificando o pagamento. Aguarde antes de tentar novamente.',
                                       code='payment_busy', http_status=409)
                return previous, False
        busy = current.payment_attempts.filter(status__in=PaymentAttempt.BUSY_STATUSES).first()
        if busy:
            if method == 'pix' and busy.method == method and busy.status == 'pending':
                return busy, False
            raise PaymentError('Já existe um pagamento em andamento. Aguarde a confirmação.',
                               code='payment_busy', http_status=409)
        if method == 'credit_card' and current.payment_attempts.filter(method=method).count() >= settings.PAYMENT_MAX_CARD_ATTEMPTS:
            raise PaymentError('Limite de tentativas atingido. Entre em contato com o restaurante.',
                               code='payment_limit', http_status=429)
        attempt = PaymentAttempt.objects.create(order=current, method=method, amount=current.total,
                                                fingerprint=fingerprint)
        return attempt, True


def _save_result(attempt, data):
    if not data.get('id') or len(str(data['id'])) > 64:
        _failed(attempt, gateway.PixError('O provedor não retornou a identificação da cobrança.'))
    # Persistir somente o resultado normalizado; nenhuma informação de cartão.
    stored = {key: value for key, value in data.items() if key != 'expires_at'}
    with locked_order(attempt.order_id):
        current = PaymentAttempt.objects.select_related('order').get(pk=attempt.pk)
        # Um webhook pode ter aprovado enquanto a resposta HTTP ainda chegava.
        _validate_provider_info(current, data)
        current.response_data = stored
        current.mp_payment_id = str(data['id'])
        current.status_detail = data.get('status_detail', '')
        current.expires_at = data.get('expires_at')
        current.save(update_fields=['response_data', 'mp_payment_id', 'status_detail', 'expires_at', 'updated_at'])
        return apply_provider_info(current, {**data, 'status':data.get('status') or 'pending'})


def _sync_snapshot(attempt):
    """Compatibilidade com as telas/admin existentes; histórico fica nas tentativas."""
    # Nunca deixar uma notificação de cobrança antiga sobrescrever a mais nova.
    latest = attempt.order.payment_attempts.filter(method=attempt.method).first()
    if latest and latest.pk != attempt.pk:
        return
    data = attempt.response_data
    common = {'mp_payment_id': attempt.mp_payment_id, 'external_reference': attempt.external_reference,
              'status': attempt.status, 'amount': attempt.amount}
    if attempt.method == 'pix':
        if attempt.status not in PixPayment.Status.values:
            return
        common.update(qr_code_text=data.get('qr_code_text', ''), qr_code_base64=data.get('qr_code_base64', ''),
                      txid=data.get('txid', ''), expires_at=attempt.expires_at)
        PixPayment.objects.update_or_create(order=attempt.order, defaults=common)
    elif attempt.status in CardPayment.Status.values:
        common.update(status_detail=attempt.status_detail, installments=data.get('installments', 1),
                      payment_method_id=data.get('payment_method_id', ''), last_four=data.get('last_four', ''))
        CardPayment.objects.update_or_create(order=attempt.order, defaults=common)


def _failed(attempt, exc):
    with locked_order(attempt.order_id):
        current = PaymentAttempt.objects.get(pk=attempt.pk)
        if current.status == PaymentAttempt.Status.CREATING:
            current.status = PaymentAttempt.Status.UNCERTAIN if exc.uncertain else PaymentAttempt.Status.FAILED
            current.save(update_fields=['status', 'updated_at'])
    raise PaymentError('Estamos verificando o pagamento. Você pode acompanhar o pedido.'
                       if exc.uncertain else str(exc), code='payment_uncertain' if exc.uncertain else 'payment_failed',
                       http_status=409 if exc.uncertain else 422) from exc


def create_pix(order):
    # Uma cobrança vencida precisa ser consultada antes de liberar outra.
    with locked_order(order.pk) as current:
        ensure_legacy_attempts(current)
    for pending in order.payment_attempts.filter(method='pix', status__in=PaymentAttempt.BUSY_STATUSES):
        if pending.expires_at and pending.expires_at <= timezone.now():
            reconcile(pending, force=True)
    order.refresh_from_db()
    attempt, created = reserve(order, 'pix')
    if not created:
        return attempt
    try:
        data = gateway.criar_pix(amount=attempt.amount, description=f'Pedido {order.order_number}',
            payer_email=order.customer_email or settings.PIX_DEFAULT_PAYER_EMAIL,
            payer_name=order.customer_name, payer_cpf=order.customer_cpf,
            external_reference=attempt.external_reference, idempotency_key=str(attempt.pk))
    except gateway.PixError as exc:
        _failed(attempt, exc)
    return _save_result(attempt, data)


def create_card(order, *, token, installments=1, **payer):
    if not token or len(token) > 256:
        raise PaymentError('Token do cartão inválido.')
    try:
        installments = int(installments)
    except (ValueError, TypeError):
        raise PaymentError('Número de parcelas inválido.')
    if installments != 1:
        raise PaymentError('O pagamento deve ser feito em uma parcela.')
    try:
        if payer.get('payer_email'):
            validate_email(payer['payer_email'])
        if payer.get('payer_cpf'):
            payer['payer_cpf'] = validate_cpf(payer['payer_cpf'])
    except ValidationError as exc:
        raise PaymentError('Confira o e-mail e o CPF do titular do cartão.') from exc
    attempt, created = reserve(order, 'credit_card', token=token)
    if not created:
        return attempt
    try:
        data = gateway.criar_pagamento_cartao(amount=attempt.amount,
            description=f'Pedido {order.order_number}', token=token, installments=installments,
            external_reference=attempt.external_reference, idempotency_key=str(attempt.pk), **payer)
    except gateway.PixError as exc:
        _failed(attempt, exc)
    return _save_result(attempt, data)


def _validate_provider_info(attempt, info):
    try:
        if info.get('amount') not in (None, '') and Decimal(str(info['amount'])) != attempt.amount:
            raise PaymentError('O valor confirmado não corresponde ao pedido.')
    except InvalidOperation:
        raise PaymentError('O provedor retornou um valor inválido.')
    if info.get('currency_id') not in (None, 'BRL'):
        raise PaymentError('A moeda do pagamento não corresponde ao pedido.')
    method = info.get('payment_method_id')
    if method and (method == 'pix') != (attempt.method == 'pix'):
        raise PaymentError('O meio de pagamento não corresponde à tentativa.')
    if (info.get('payment_type_id') and attempt.method == 'credit_card'
            and info['payment_type_id'] != 'credit_card'):
        raise PaymentError('O tipo do cartão não corresponde à tentativa.')
    payment_id = str(info.get('id') or '')
    if payment_id and (len(payment_id) > 64 or attempt.mp_payment_id and payment_id != attempt.mp_payment_id):
        raise PaymentError('A identificação do pagamento não corresponde à tentativa.')
    if info.get('external_reference') and info['external_reference'] != attempt.external_reference:
        # Referências históricas eram o número do pedido.
        if not (attempt.external_reference.startswith('legacy-') and info['external_reference'] == attempt.order.order_number):
            raise PaymentError('A referência do pagamento não corresponde ao pedido.')


def apply_provider_info(attempt, info):
    _validate_provider_info(attempt, info)
    with locked_order(attempt.order_id):
        current = PaymentAttempt.objects.select_related('order').get(pk=attempt.pk)
        _validate_provider_info(current, info)
        if not current.mp_payment_id and info.get('id'):
            current.mp_payment_id = str(info['id'])
            current.save(update_fields=['mp_payment_id', 'updated_at'])
        raw_refund = info.get('refund_amount')
        if raw_refund not in (None, ''):
            try:
                refunded = Decimal(str(raw_refund))
            except InvalidOperation:
                raise PaymentError('O provedor retornou um estorno inválido.')
            if not refunded.is_finite() or not Decimal('0') <= refunded <= current.amount:
                raise PaymentError('O valor estornado não corresponde ao pagamento.')
            current.refunded_amount = max(current.refunded_amount, refunded)
        if info.get('status') == 'refunded':
            current.refunded_amount = current.amount
        current.save(update_fields=['refunded_amount', 'updated_at'])
        pedidos.aplicar_status_mp(current, info.get('status'))
        current.order.refresh_from_db()
        if (0 < current.refunded_amount < current.amount and current.order.is_paid
                and current.order.requires_online_payment and not current.order.payment_attempts.filter(
                    status='approved').exclude(pk=current.pk).exists()):
            pedidos._set_order_status(current.order, Order.PaymentStatus.PARTIALLY_REFUNDED)
        current.refresh_from_db()
        _sync_snapshot(current)
    return current


def reconcile(attempt, *, force=False):
    if not force:
        now = timezone.now()
        claimed = PaymentAttempt.objects.filter(pk=attempt.pk).filter(
            Q(last_checked_at__isnull=True) | Q(last_checked_at__lte=now-timedelta(seconds=5))
        ).update(last_checked_at=now)
        if not claimed:
            return PaymentAttempt.objects.select_related('order').get(pk=attempt.pk)
    if attempt.refund_requested_at and attempt.status == 'approved':
        return refund(order=attempt.order, attempt_id=attempt.pk)
    if settings.MERCADOPAGO_MOCK:
        if attempt.method == 'pix' and attempt.status == 'pending' and attempt.expires_at and attempt.expires_at <= timezone.now():
            return apply_provider_info(attempt, {'status': 'expired'})
        return attempt
    if attempt.mp_payment_id:
        info = gateway.buscar_status(attempt.mp_payment_id)
        if (attempt.method == 'pix' and info.get('status') == 'pending'
                and attempt.expires_at and attempt.expires_at <= timezone.now()):
            info = gateway.cancelar(attempt.mp_payment_id)
        return apply_provider_info(attempt, info)
    results = gateway.buscar_por_referencia(attempt.external_reference)
    if results:
        if len(results) != 1:
            raise PaymentError('O provedor retornou mais de uma cobrança. O pagamento permanece em verificação.')
        data = results[0]
        if (not data.get('id') or not data.get('status') or data.get('currency_id') != 'BRL'
                or not data.get('external_reference') or not data.get('payment_method_id')
                or data.get('transaction_amount') in (None, '')):
            raise PaymentError('O provedor retornou uma cobrança incompleta.')
        info = {'id':data['id'], 'status':data['status'], 'amount':data['transaction_amount'],
                'currency_id':data['currency_id'], 'external_reference':data['external_reference'],
                'payment_method_id':data['payment_method_id'],
                'payment_type_id':data.get('payment_type_id'),
                'refund_amount':data.get('transaction_amount_refunded')}
        _validate_provider_info(attempt, info)
        if data.get('payment_method_id') == 'pix':
            tx = (data.get('point_of_interaction') or {}).get('transaction_data') or {}
            normalized = {'id': str(data['id']), 'status': data['status'],
                'qr_code_text': tx.get('qr_code', ''), 'qr_code_base64': tx.get('qr_code_base64', ''),
                'expires_at': gateway._parse_dt(data.get('date_of_expiration'))}
        else:
            normalized = gateway._normalize_card_response(data)
        return _save_result(attempt, {**normalized, **info})
    return attempt


def request_refund(order, *, attempt_id=None):
    """Persiste a intenção antes de qualquer chamada de rede ou aviso ao cliente."""
    with locked_order(order.pk) as current:
        ensure_legacy_attempts(current)
        scope = current.payment_attempts.all()
        if attempt_id is not None:
            scope = scope.filter(pk=attempt_id)
        attempts = list(scope.filter(status='approved'))
        if not attempts and not scope.filter(status='refunded').exists():
            raise PaymentError('Não há pagamento aprovado para estornar.')
        for attempt in attempts:
            if not attempt.refund_requested_at:
                attempt.refund_requested_at = timezone.now()
                attempt.requested_refund_amount = attempt.amount - attempt.refunded_amount
                attempt.save(update_fields=['refund_requested_at', 'requested_refund_amount', 'updated_at'])
        return attempts


def refund(order, *, attempt_id=None):
    attempts = request_refund(order, attempt_id=attempt_id)
    if not attempts:
        scope = order.payment_attempts.filter(status='refunded')
        if attempt_id is not None:
            scope = scope.filter(pk=attempt_id)
        return scope.first()
    result = None
    for attempt in attempts:
        try:
            gateway.reembolsar(attempt.mp_payment_id, amount=attempt.requested_refund_amount)
            info = {'status': 'refunded'} if settings.MERCADOPAGO_MOCK else gateway.buscar_status(attempt.mp_payment_id)
        except gateway.PixError as exc:
            raise PaymentError('O estorno está em verificação. A sincronização tentará confirmar novamente.',
                               code='refund_pending', http_status=409) from exc
        result = apply_provider_info(attempt, info)
    return result

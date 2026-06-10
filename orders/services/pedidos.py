"""Regras de negócio para refletir o status do pagamento Pix no pedido."""

from __future__ import annotations

import logging

from ..models import Order

logger = logging.getLogger(__name__)

# Status do Mercado Pago → status do pedido.
_APPROVED = {'approved'}
_IN_PROCESS = {'in_process'}
_REJECTED = {'rejected'}
_CANCELLED = {'cancelled', 'expired', 'refunded', 'charged_back'}


def _set_order_status(order: Order, new_status) -> None:
    # Nunca reverter um pedido já pago.
    if order.payment_status == Order.PaymentStatus.PAID and new_status != Order.PaymentStatus.PAID:
        return
    if order.payment_status != new_status:
        order.payment_status = new_status
        order.save(update_fields=['payment_status', 'updated_at'])


def marcar_pago(order: Order) -> None:
    _set_order_status(order, Order.PaymentStatus.PAID)


def marcar_em_analise(order: Order) -> None:
    _set_order_status(order, Order.PaymentStatus.IN_PROCESS)


def marcar_recusado(order: Order) -> None:
    _set_order_status(order, Order.PaymentStatus.REJECTED)


def marcar_cancelado(order: Order) -> None:
    _set_order_status(order, Order.PaymentStatus.CANCELLED)


def _payment_status_for(payment, mp_status):
    """Status do registro de pagamento (PixPayment ou CardPayment) para o status do MP."""
    choices = payment.Status
    mapping = {
        'approved': choices.APPROVED,
        'rejected': getattr(choices, 'REJECTED', None),
        'refunded': getattr(choices, 'REFUNDED', None),
        'in_process': getattr(choices, 'IN_PROCESS', None),
        'expired': getattr(choices, 'EXPIRED', None),
        'cancelled': choices.CANCELLED,
    }
    return mapping.get(mp_status)


def aplicar_status_mp(payment, mp_status: str | None):
    """Aplica o status do MP ao registro de pagamento (Pix ou Cartão) e ao Order.

    Idempotente. ``mp_status`` None (modo mock) não altera nada.
    """
    if not mp_status:
        return payment

    if mp_status in _APPROVED:
        marcar_pago(payment.order)
    elif mp_status in _IN_PROCESS:
        marcar_em_analise(payment.order)
    elif mp_status in _REJECTED:
        marcar_recusado(payment.order)
    elif mp_status in _CANCELLED:
        marcar_cancelado(payment.order)
    # Demais status (pending, authorized) mantêm o pedido como está.

    new_payment_status = _payment_status_for(payment, mp_status)
    if new_payment_status is not None and payment.status != new_payment_status:
        payment.status = new_payment_status
        payment.save(update_fields=['status', 'updated_at'])
    return payment

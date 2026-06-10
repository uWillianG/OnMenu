"""Regras de negócio para refletir o status do pagamento Pix no pedido."""

from __future__ import annotations

import logging

from ..models import Order, PixPayment

logger = logging.getLogger(__name__)

# Status do Mercado Pago → ação no pedido.
_APPROVED = {'approved'}
_CANCELLED = {'cancelled', 'expired', 'rejected', 'refunded', 'charged_back'}


def marcar_pago(order: Order) -> None:
    if order.payment_status != Order.PaymentStatus.PAID:
        order.payment_status = Order.PaymentStatus.PAID
        order.save(update_fields=['payment_status', 'updated_at'])


def marcar_cancelado(order: Order) -> None:
    # Não reverter um pedido já pago.
    if order.payment_status == Order.PaymentStatus.PAID:
        return
    if order.payment_status != Order.PaymentStatus.CANCELLED:
        order.payment_status = Order.PaymentStatus.CANCELLED
        order.save(update_fields=['payment_status', 'updated_at'])


def aplicar_status_mp(pix: PixPayment, mp_status: str | None) -> PixPayment:
    """Aplica o status do MP ao PixPayment e ao Order. Idempotente.

    ``mp_status`` None (modo mock) não altera nada.
    """
    if not mp_status:
        return pix

    if mp_status in _APPROVED:
        if pix.status != PixPayment.Status.APPROVED:
            pix.status = PixPayment.Status.APPROVED
            pix.save(update_fields=['status', 'updated_at'])
        marcar_pago(pix.order)
    elif mp_status in _CANCELLED:
        new_status = (
            PixPayment.Status.EXPIRED
            if mp_status == 'expired'
            else PixPayment.Status.CANCELLED
        )
        if pix.status != new_status:
            pix.status = new_status
            pix.save(update_fields=['status', 'updated_at'])
        marcar_cancelado(pix.order)
    # Demais status (pending, in_process, authorized) mantêm pendente.
    return pix

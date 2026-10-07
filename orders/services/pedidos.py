"""Regras de negócio para refletir o status do pagamento Pix no pedido."""

from __future__ import annotations

import logging

from ..models import Order, OrderStatusChange, PaymentAttempt

logger = logging.getLogger(__name__)

# Status do Mercado Pago → status do pedido.
_APPROVED = {'approved'}
_IN_PROCESS = {'in_process'}
_REJECTED = {'rejected'}
_CANCELLED = {'cancelled', 'expired', 'refunded', 'charged_back'}


def _set_order_status(order: Order, new_status) -> bool:
    """Aplica o novo status de pagamento. Retorna ``True`` se houve mudança."""
    # Nunca reverter um pedido já pago.
    if order.payment_status == Order.PaymentStatus.PARTIALLY_REFUNDED and new_status == Order.PaymentStatus.PAID:
        return False
    if order.payment_status in (Order.PaymentStatus.PAID, Order.PaymentStatus.PARTIALLY_REFUNDED) and new_status not in (
        Order.PaymentStatus.PAID, Order.PaymentStatus.PARTIALLY_REFUNDED, Order.PaymentStatus.REFUNDED, Order.PaymentStatus.CHARGED_BACK
    ):
        return False
    if order.payment_status != new_status:
        order.payment_status = new_status
        order.save(update_fields=['payment_status', 'updated_at'])
        return True
    return False


def marcar_pago(order: Order) -> None:
    changed = _set_order_status(order, Order.PaymentStatus.PAID)
    # Só avisa os admins na transição real para "pago" (idempotente): pedidos
    # online (Pix/cartão) entram na fila da cozinha quando o pagamento confirma.
    if changed and order.status in Order.ACTIVE_STATUSES:
        from . import notificacoes  # import tardio evita ciclo de importação
        notificacoes.notificar_admins_novo_pedido(order)


def marcar_em_analise(order: Order) -> None:
    _set_order_status(order, Order.PaymentStatus.IN_PROCESS)


def marcar_recusado(order: Order) -> None:
    _set_order_status(order, Order.PaymentStatus.REJECTED)


def marcar_cancelado(order: Order) -> None:
    _set_order_status(order, Order.PaymentStatus.CANCELLED)


def registrar_status(order: Order, from_status: str = '', user=None):
    """Guarda uma entrada na linha do tempo do pedido (quem mudou o quê).

    Best-effort: a auditoria nunca pode derrubar o checkout nem a ação de quem
    está no painel. ``from_status`` vazio marca a criação do pedido.
    """
    if from_status == order.status:
        return None
    try:
        return OrderStatusChange.objects.create(
            order=order,
            from_status=from_status,
            to_status=order.status,
            changed_by=user if (user is not None and user.is_authenticated) else None,
        )
    except Exception:  # noqa: BLE001 - auditoria não interrompe a operação
        logger.exception('Falha ao registrar mudança de situação de %s', order.order_number)
        return None


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
        'pending': getattr(choices, 'PENDING', None),
        'charged_back': getattr(choices, 'CHARGED_BACK', None),
    }
    return mapping.get(mp_status)


def aplicar_status_mp(payment, mp_status: str | None):
    """Aplica o status do MP ao registro de pagamento (Pix ou Cartão) e ao Order.

    Idempotente. ``mp_status`` None (modo mock) não altera nada.
    """
    if not mp_status:
        return payment
    payment.refresh_from_db()

    # Eventos atrasados não podem desfazer uma aprovação ou um estorno final.
    if payment.status in ('refunded', 'charged_back') and mp_status not in ('refunded', 'charged_back'):
        return payment
    if payment.status == 'approved' and mp_status not in ('approved', 'refunded', 'charged_back'):
        return payment
    order = payment.order
    order.refresh_from_db()
    latest = (order.payment_attempts.first() if isinstance(payment, PaymentAttempt) else None)
    applies_to_order = latest is None or latest.pk == payment.pk or mp_status == 'approved'
    if isinstance(payment, PaymentAttempt) and mp_status in ('refunded', 'charged_back'):
        applies_to_order = order.requires_online_payment and not order.payment_attempts.filter(
            status='approved').exclude(pk=payment.pk).exists()

    if isinstance(payment, PaymentAttempt) and mp_status == 'approved':
        duplicate = order.payment_attempts.filter(status='approved', refund_requested_at__isnull=True).exclude(pk=payment.pk).exists()
        paid_offline = order.is_paid and not order.requires_online_payment
        if (order.status == 'cancelled' or duplicate or paid_offline) and not payment.refund_requested_at:
            from django.utils import timezone
            payment.refund_requested_at = timezone.now()
            payment.requested_refund_amount = payment.amount - payment.refunded_amount
            payment.save(update_fields=['refund_requested_at', 'requested_refund_amount', 'updated_at'])

    if applies_to_order:
        if mp_status in _APPROVED:
            marcar_pago(order)
        elif mp_status == 'refunded':
            _set_order_status(order, Order.PaymentStatus.REFUNDED)
        elif mp_status == 'charged_back':
            _set_order_status(order, Order.PaymentStatus.CHARGED_BACK)
        elif mp_status in _IN_PROCESS:
            marcar_em_analise(order)
        elif mp_status in _REJECTED:
            marcar_recusado(order)
        elif mp_status in _CANCELLED:
            marcar_cancelado(order)
        elif mp_status == 'pending':
            _set_order_status(order, Order.PaymentStatus.PENDING)
    # Demais status (pending, authorized) mantêm o pedido como está.

    new_payment_status = _payment_status_for(payment, mp_status)
    if new_payment_status is not None and payment.status != new_payment_status:
        payment.status = new_payment_status
        payment.save(update_fields=['status', 'updated_at'])
    return payment

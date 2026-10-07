from django.core.exceptions import ValidationError

from ..models import Order, PaymentAttempt
from . import notificacoes, payments, pedidos
from . import mercadopago


def _cancel_pending_pix(order):
    with payments.locked_order(order.pk) as current:
        if current.status == 'cancelled':
            return
        payments.ensure_legacy_attempts(current)
        pending = list(current.payment_attempts.filter(status__in=PaymentAttempt.BUSY_STATUSES))
        if any(attempt.method != 'pix' or attempt.status != 'pending' or not attempt.mp_payment_id for attempt in pending):
            raise ValidationError('O pagamento ainda está em verificação. Aguarde a confirmação antes de cancelar.')
    # O pedido continua bloqueado para nova cobrança enquanto a consulta ocorre.
    for attempt in pending:
        try:
            info = mercadopago.cancelar(attempt.mp_payment_id)
            payments.apply_provider_info(attempt, info)
        except (mercadopago.PixError, payments.PaymentError) as exc:
            raise ValidationError('Não foi possível confirmar o cancelamento do Pix. O pedido permanece em verificação.') from exc


def change_status(order, new_status, actor, *, confirm_refund=False, resolve_pending_pix=False):
    if new_status not in Order.Status.values:
        raise ValidationError('Selecione uma situação válida.')
    if new_status == 'cancelled' and resolve_pending_pix:
        _cancel_pending_pix(order)
    with payments.locked_order(order.pk) as current:
        previous = current.status
        if new_status == previous:
            return current, False
        if previous == 'cancelled' or previous == 'delivered' and new_status != 'cancelled':
            raise ValidationError('Pedidos finalizados não podem voltar para a fila de preparo.')
        if new_status in ('preparing', 'out_for_delivery', 'delivered') and not current.can_prepare:
            raise ValidationError('Aguarde a confirmação do pagamento antes de preparar o pedido.')
        if new_status == 'cancelled':
            payments.ensure_legacy_attempts(current)
            if current.payment_attempts.filter(status__in=PaymentAttempt.BUSY_STATUSES).exists():
                raise ValidationError('O pagamento ainda está em verificação. Aguarde a confirmação antes de cancelar.')
        refund_required = new_status == 'cancelled' and current.is_paid and current.requires_online_payment
        if refund_required:
            if not actor.has_perm('orders.refund_payment'):
                raise ValidationError('O responsável com permissão de estorno deve cancelar este pedido.')
            if not confirm_refund:
                raise ValidationError('Confirme o cancelamento e o estorno integral.')
            payments.request_refund(current)
        current.status = new_status
        fields = ['status', 'updated_at']
        if new_status == 'delivered' and not current.requires_online_payment:
            current.payment_status = 'paid'
            fields.append('payment_status')
        current.save(update_fields=fields)
        pedidos.registrar_status(current, previous, actor)
    notificacoes.notificar_status_pedido(current)
    if refund_required:
        payments.refund(current)
    return current, True

"""Orquestra os avisos ao cliente quando a situação do pedido muda.

Centraliza as duas formas de notificação disparadas pelo administrador:
1. **No sistema**: cria uma ``Notification`` (sininho/lista) para o cliente logado.
2. **WhatsApp**: envia uma mensagem ao telefone do pedido (mock-safe).

Tudo é best-effort: uma falha em um canal apenas é registrada no log e não
interrompe a ação do administrador.
"""

from __future__ import annotations

import logging

from ..models import Notification
from . import whatsapp as whatsapp_service

logger = logging.getLogger(__name__)


def mensagem_status(order) -> str:
    return f'Seu pedido {order.order_number} agora está: {order.status_display}.'


def notificar_status_pedido(order) -> None:
    """Cria a notificação no sistema e envia o WhatsApp para a situação atual."""
    msg = mensagem_status(order)

    # 1) Notificação no sistema (apenas para pedidos com conta).
    if order.user_id:
        try:
            Notification.objects.create(user_id=order.user_id, order=order, message=msg)
        except Exception:  # noqa: BLE001 - não pode quebrar a ação do admin
            logger.exception('Falha ao criar notificação para o pedido %s', order.order_number)

    # 2) WhatsApp (mock-safe).
    try:
        whatsapp_service.enviar_texto(order.phone, msg)
    except whatsapp_service.WhatsAppError:
        logger.warning('Falha ao enviar WhatsApp para o pedido %s', order.order_number)
    except Exception:  # noqa: BLE001
        logger.exception('Erro inesperado ao enviar WhatsApp para %s', order.order_number)

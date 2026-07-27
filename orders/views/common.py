"""Helpers compartilhados entre os módulos de views de ``orders``."""

from django.http import Http404
from django.shortcuts import get_object_or_404

from .. import selectors
from ..models import Order


def get_own_order(request, order_number, queryset=None):
    """Pedido do próprio visitante (ou da equipe); 404 para qualquer outro.

    Evita que os números sequenciais sirvam para enumerar os pedidos do
    restaurante — ver ``selectors.can_view_order``.
    """
    order = get_object_or_404(
        queryset if queryset is not None else Order.objects.all(),
        order_number=order_number,
    )
    if not selectors.can_view_order(request, order):
        raise Http404('Pedido não encontrado.')
    return order

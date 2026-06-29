"""Consultas auxiliares de pedidos para a interface do cliente."""

from django.db.models import Q

from .models import Neighborhood, Order


def get_delivery_fee_range():
    """Faixa de taxa de entrega (mín, máx) somando cidade + bairro ativos.

    Retorna ``None`` quando não há áreas de entrega cadastradas.
    """
    pairs = (
        Neighborhood.objects
        .filter(is_active=True, city__is_active=True)
        .select_related('city')
    )
    fees = [n.city.delivery_fee + n.delivery_fee for n in pairs]
    if not fees:
        return None
    return min(fees), max(fees)

# Números de pedido guardados na sessão para acompanhamento na home
# (cobre pedidos feitos sem login).
SESSION_ORDERS_KEY = 'tracked_orders'


def remember_order(request, order):
    """Guarda o pedido recém-criado na sessão para exibir o acompanhamento."""
    tracked = request.session.get(SESSION_ORDERS_KEY, [])
    if order.order_number in tracked:
        return
    tracked.insert(0, order.order_number)
    request.session[SESSION_ORDERS_KEY] = tracked[:10]
    request.session.modified = True


def get_tracked_active_orders(request):
    """Pedidos ativos a exibir na tela principal.

    Combina os pedidos do usuário logado com os números guardados na sessão
    (para quem pediu sem login). Mostra apenas os que ainda estão em andamento.
    """
    numbers = request.session.get(SESSION_ORDERS_KEY, [])
    conditions = Q()
    has_condition = False
    if request.user.is_authenticated:
        conditions |= Q(user=request.user)
        has_condition = True
    if numbers:
        conditions |= Q(order_number__in=numbers)
        has_condition = True
    if not has_condition:
        return []
    return list(
        Order.objects.filter(conditions, status__in=Order.ACTIVE_STATUSES)
        .order_by('-created_at')[:5]
    )

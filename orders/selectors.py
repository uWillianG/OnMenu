"""Consultas auxiliares de pedidos para a interface do cliente."""

from datetime import timedelta
from decimal import Decimal

from django.db.models import Avg, Count, Q, Sum
from django.db.models.functions import TruncDate
from django.utils import timezone

from .models import Neighborhood, Order, OrderItem


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


# ── Relatórios do painel ───────────────────────────────────────────────────

# Períodos oferecidos na tela de relatórios: rótulo + quantos dias para trás
# (0 = só hoje).
REPORT_PERIODS = {
    'hoje': ('Hoje', 0),
    '7d': ('Últimos 7 dias', 6),
    '30d': ('Últimos 30 dias', 29),
    '12m': ('Últimos 12 meses', 364),
}
DEFAULT_PERIOD = 'hoje'


def resolve_report_period(period):
    """Traduz a chave do período em (chave, rótulo, data inicial, data final)."""
    key = period if period in REPORT_PERIODS else DEFAULT_PERIOD
    label, days_back = REPORT_PERIODS[key]
    end = timezone.localdate()
    return key, label, end - timedelta(days=days_back), end


def get_sales_report(start, end):
    """Números de venda do período (datas locais, inclusivas nas duas pontas).

    Pedidos cancelados ficam de fora: não viraram receita. Pagamentos ainda
    pendentes (dinheiro, cartão na entrega) contam, porque o pedido foi feito.
    """
    orders = Order.objects.filter(
        created_at__date__gte=start,
        created_at__date__lte=end,
    ).exclude(status=Order.Status.CANCELLED)

    totals = orders.aggregate(
        revenue=Sum('total'),
        items_revenue=Sum('subtotal'),
        delivery_revenue=Sum('delivery_fee'),
        order_count=Count('id'),
        average_ticket=Avg('total'),
    )
    revenue = totals['revenue'] or Decimal('0.00')
    order_count = totals['order_count'] or 0

    cancelled_count = Order.objects.filter(
        created_at__date__gte=start,
        created_at__date__lte=end,
        status=Order.Status.CANCELLED,
    ).count()

    return {
        'start': start,
        'end': end,
        'revenue': revenue,
        'items_revenue': totals['items_revenue'] or Decimal('0.00'),
        'delivery_revenue': totals['delivery_revenue'] or Decimal('0.00'),
        'order_count': order_count,
        'average_ticket': totals['average_ticket'] or Decimal('0.00'),
        'cancelled_count': cancelled_count,
        'daily': _daily_series(orders, start, end),
        'top_items': _top_items(orders),
        'by_payment': _breakdown(orders, 'payment_method', Order.PaymentMethod),
        'by_fulfillment': _breakdown(orders, 'fulfillment_method', Order.FulfillmentMethod),
    }


def _daily_series(orders, start, end):
    """Faturamento por dia, com os dias sem venda preenchidos com zero."""
    rows = (
        orders
        .annotate(day=TruncDate('created_at'))
        .values('day')
        .annotate(revenue=Sum('total'), order_count=Count('id'))
    )
    by_day = {row['day']: row for row in rows}

    days = (end - start).days + 1
    # Em janelas longas o gráfico diário vira ruído; mostramos as últimas semanas.
    if days > 62:
        days = 62
        start = end - timedelta(days=days - 1)

    series = []
    peak = Decimal('0.00')
    for offset in range(days):
        day = start + timedelta(days=offset)
        row = by_day.get(day)
        revenue = (row or {}).get('revenue') or Decimal('0.00')
        peak = max(peak, revenue)
        series.append({
            'day': day,
            'revenue': revenue,
            'order_count': (row or {}).get('order_count') or 0,
        })

    # Altura relativa de cada barra (0–100) para o gráfico do template. Dias com
    # venda ganham um mínimo visível; dias zerados ficam realmente em zero.
    for point in series:
        if not peak or not point['revenue']:
            point['percent'] = 0
        else:
            point['percent'] = max(3, int(point['revenue'] / peak * 100))
    return series


def _top_items(orders):
    """Itens mais vendidos no período (por quantidade)."""
    return list(
        OrderItem.objects
        .filter(order__in=orders)
        .values('item_name')
        .annotate(quantity=Sum('quantity'), revenue=Sum('line_total'))
        .order_by('-quantity', 'item_name')[:10]
    )


def _breakdown(orders, field, choices):
    """Distribuição dos pedidos por um campo com choices (com % do total)."""
    rows = orders.values(field).annotate(
        order_count=Count('id'), revenue=Sum('total'),
    )
    labels = dict(choices.choices)
    total = sum(row['order_count'] for row in rows) or 1
    result = [
        {
            'label': labels.get(row[field], row[field]),
            'order_count': row['order_count'],
            'revenue': row['revenue'] or Decimal('0.00'),
            'percent': round(row['order_count'] / total * 100),
        }
        for row in rows
    ]
    return sorted(result, key=lambda row: row['order_count'], reverse=True)


def can_view_order(request, order):
    """O pedido pertence a quem está pedindo a página?

    Os números são sequenciais (OM-1, OM-2, …), então as páginas públicas de
    pedido precisam ser restritas — senão dá para varrer os pedidos do
    restaurante trocando o número na URL. Vale para a equipe, para o dono da
    conta e para quem fez o pedido nesta sessão (compra sem login).
    """
    user = request.user
    if user.is_authenticated and (user.is_staff or order.user_id == user.id):
        return True
    return order.order_number in request.session.get(SESSION_ORDERS_KEY, [])


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

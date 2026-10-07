"""Painel da equipe: lista de pedidos (com filtros e polling), relatórios,
impressão de comandas, atualização em massa e detalhe/edição de um pedido."""

import hashlib

from django.conf import settings
from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.core.paginator import Paginator
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_http_methods

from .. import selectors
from ..forms import OrderStatusForm
from ..models import Order
from ..services import notificacoes as notificacoes_service
from ..services import pedidos as pedidos_service
from ..services import payments as payment_service
from ..services.status import change_status
from ..services import whatsapp as whatsapp_service

# Pedidos finalizados por página. Os ativos não paginam: são a fila de trabalho
# da cozinha e precisam estar todos à vista — e são poucos por natureza.
STAFF_FINISHED_PAGE_SIZE = 20


def _staff_orders_signature(active_orders, inactive_orders, finished_total, waiting_orders=()):
    """Assinatura leve do estado atual do painel.

    Muda quando um pedido novo chega, quando um status muda ou quando um pedido
    passa de ativo para finalizado — é o que o polling usa para decidir se a
    lista precisa ser atualizada em tela. Cobre só o que está em tela; o total
    de finalizados entra para que uma mudança fora da página atual (que altera a
    contagem) também dispare a atualização.
    """
    parts = [f'{o.order_number}:{o.status}:{o.payment_status}:{o.updated_at.isoformat()}'
             for o in list(active_orders) + list(inactive_orders) + list(waiting_orders)]
    parts.sort()
    parts.append(f'total:{finished_total}')
    return hashlib.md5('|'.join(parts).encode()).hexdigest()


def _staff_orders_context(request):
    """Monta o contexto do painel de pedidos aplicando os filtros da query."""
    orders = Order.objects.select_related('restaurant').prefetch_related('items')

    status_filter = request.GET.get('status', '')
    payment_filter = request.GET.get('payment_method', '')
    fulfillment_filter = request.GET.get('fulfillment_method', '')
    date_filter = request.GET.get('date', '')

    if status_filter in Order.Status.values:
        orders = orders.filter(status=status_filter)
    else:
        status_filter = ''

    if payment_filter in Order.PaymentMethod.values:
        orders = orders.filter(payment_method=payment_filter)
    else:
        payment_filter = ''

    if fulfillment_filter in Order.FulfillmentMethod.values:
        orders = orders.filter(fulfillment_method=fulfillment_filter)
    else:
        fulfillment_filter = ''

    parsed_date = parse_date(date_filter) if date_filter else None
    if parsed_date:
        first, last = selectors.local_day_bounds(parsed_date)
        orders = orders.filter(created_at__gte=first, created_at__lte=last)
    else:
        date_filter = ''

    # Duas consultas indexadas em vez de carregar a tabela inteira e separar em
    # Python: a lista de finalizados cresce para sempre, e este mesmo caminho é
    # percorrido pelo polling do painel a cada poucos segundos.
    ready = Q(payment_status__in=Order.PAID_STATUSES) | Q(payment_method__in=[
        Order.PaymentMethod.CASH, Order.PaymentMethod.CARD_ON_DELIVERY])
    active_orders = list(orders.filter(status__in=Order.ACTIVE_STATUSES).filter(ready))
    waiting_orders = list(orders.filter(status__in=Order.ACTIVE_STATUSES).exclude(ready))
    finished_page = Paginator(
        # created_at não é único; o id desempata para a paginação não repetir
        # nem pular pedidos criados no mesmo instante.
        orders.exclude(status__in=Order.ACTIVE_STATUSES).order_by('-created_at', '-id'),
        STAFF_FINISHED_PAGE_SIZE,
    ).get_page(request.GET.get('page'))
    inactive_orders = list(finished_page)
    finished_total = finished_page.paginator.count

    return {
        'active_orders': active_orders,
        'waiting_orders': waiting_orders,
        'waiting_count': len(waiting_orders),
        'inactive_orders': inactive_orders,
        'active_count': len(active_orders),
        'inactive_count': finished_total,
        'finished_page': finished_page,
        'status_filter': status_filter,
        'payment_filter': payment_filter,
        'fulfillment_filter': fulfillment_filter,
        'date_filter': date_filter,
        'has_filters': any([
            status_filter, payment_filter, fulfillment_filter, date_filter,
        ]),
        'status_choices': Order.Status.choices,
        'bulk_status_choices': Order.bulk_status_choices(),
        'payment_choices': Order.PaymentMethod.choices,
        'fulfillment_choices': Order.FulfillmentMethod.choices,
        'orders_signature': _staff_orders_signature(
            active_orders, inactive_orders, finished_total, waiting_orders,
        ),
    }


@staff_member_required
def staff_order_list(request):
    return render(request, 'orders/staff_order_list.html', _staff_orders_context(request))


@staff_member_required
def staff_reports(request):
    """Relatórios de venda do estabelecimento (faturamento, ticket, ranking)."""
    period, period_label, start, end = selectors.resolve_report_period(
        request.GET.get('periodo', ''),
    )
    report = selectors.get_sales_report(start, end)
    return render(
        request,
        'orders/staff_reports.html',
        {
            'report': report,
            'period': period,
            'period_label': period_label,
            'period_choices': [
                (key, label) for key, (label, _days) in selectors.REPORT_PERIODS.items()
            ],
        },
    )


@staff_member_required
def staff_orders_feed(request):
    """Atualização em tempo real do painel (polling).

    Devolve os cartões de pedidos já renderizados e uma assinatura do estado.
    O frontend só troca o HTML em tela quando a assinatura muda, mantendo a
    seleção e a navegação atuais.
    """
    ctx = _staff_orders_context(request)
    active_html = render_to_string(
        'orders/_staff_order_cards.html',
        {
            'orders': ctx['active_orders'],
            'empty_title': 'Nenhum pedido ativo',
            'empty_text': 'Os novos pedidos aparecem aqui assim que chegam.',
        },
        request=request,
    )
    inactive_html = render_to_string(
        'orders/_staff_order_cards.html',
        {
            'orders': ctx['inactive_orders'],
            'empty_title': 'Nenhum pedido finalizado',
            'empty_text': 'Pedidos entregues ou cancelados aparecem aqui.',
        },
        request=request,
    )
    # O paginador acompanha os cartões: sem isto o polling deixaria em tela
    # controles apontando para uma quantidade de páginas que já mudou.
    pager_html = render_to_string(
        'includes/pagination.html',
        {
            'page': ctx['finished_page'],
            'pager_label': 'Páginas de pedidos finalizados',
        },
        request=request,
    )
    return JsonResponse({
        'ok': True,
        'signature': ctx['orders_signature'],
        'active_count': ctx['active_count'],
        'waiting_count': ctx['waiting_count'],
        'waiting_html': render_to_string('orders/_staff_order_cards.html',
            {'orders': ctx['waiting_orders'], 'empty_title': 'Nenhum pagamento pendente',
             'empty_text': 'Pedidos online aguardando pagamento aparecem aqui.'}, request=request),
        'inactive_count': ctx['inactive_count'],
        'active_html': active_html,
        'inactive_html': inactive_html,
        'pager_html': pager_html,
    })


def _orders_for_print(queryset):
    return queryset.select_related('restaurant').prefetch_related('items__options')


# Tipos de impressão: completa (caixa), cozinha e entregador.
PRINT_VARIANTS = {
    'completa': 'Caixa',
    'cozinha': 'Cozinha',
    'entregador': 'Entregador',
}


def _print_variant(request):
    """Lê o tipo de impressão da query (?tipo=), com fallback para 'completa'."""
    tipo = request.GET.get('tipo', 'completa')
    return tipo if tipo in PRINT_VARIANTS else 'completa'


@staff_member_required
def staff_order_print(request, order_number):
    """Página de impressão (comanda) de um único pedido."""
    order = get_object_or_404(
        _orders_for_print(Order.objects.all()),
        order_number=order_number,
    )
    variant = _print_variant(request)
    return render(
        request,
        'orders/print_orders.html',
        {
            'orders': [order],
            'auto_print': True,
            'scope_label': 'Pedido',
            'variant': variant,
            'variant_label': PRINT_VARIANTS[variant],
        },
    )


@staff_member_required
def staff_orders_print_active(request):
    """Página de impressão de todos os pedidos ativos (em andamento)."""
    orders = _orders_for_print(
        Order.objects.filter(status__in=Order.ACTIVE_STATUSES).filter(
            Q(payment_status__in=Order.PAID_STATUSES) | Q(payment_method__in=['cash', 'card_on_delivery']))
    )
    variant = _print_variant(request)
    return render(
        request,
        'orders/print_orders.html',
        {
            'orders': list(orders),
            'auto_print': True,
            'scope_label': 'Pedidos ativos',
            'variant': variant,
            'variant_label': PRINT_VARIANTS[variant],
        },
    )


@staff_member_required
def staff_order_summary(request, order_number):
    """Fragmento HTML com o resumo do pedido, exibido em modal no painel."""
    order = get_object_or_404(
        Order.objects.select_related('restaurant').prefetch_related('items__options'),
        order_number=order_number,
    )
    return render(request, 'orders/_order_summary.html', {'order': order})


@staff_member_required
@require_http_methods(['POST'])
def staff_orders_bulk_update(request):
    """Atualiza a situação de vários pedidos selecionados de uma só vez."""
    order_numbers = request.POST.getlist('order_numbers')
    new_status = request.POST.get('status', '')

    if new_status not in Order.Status.values:
        messages.warning(request, 'Selecione uma situação válida.')
    elif not order_numbers:
        messages.warning(request, 'Selecione ao menos um pedido.')
    else:
        selected = Order.objects.filter(order_number__in=order_numbers)
        if new_status == Order.Status.CANCELLED and selected.filter(payment_status__in=Order.PAID_STATUSES, payment_method__in=['pix','credit_card']).exists():
            messages.warning(request, 'Cancele pedidos pagos pela tela de detalhe para confirmar o estorno individual.')
            return redirect('orders:staff_order_list')
        if new_status in (Order.Status.PREPARING, Order.Status.OUT_FOR_DELIVERY, Order.Status.DELIVERED):
            blocked = selected.filter(payment_method__in=['pix', 'credit_card']).exclude(payment_status__in=Order.PAID_STATUSES)
            if blocked.exists():
                messages.warning(request, 'Aguarde o pagamento online antes de avançar esses pedidos.')
                return redirect('orders:staff_order_list')
        changed = list(selected.exclude(status=new_status))
        try:
            with transaction.atomic():
                for order in changed:
                    change_status(order, new_status, request.user)
        except ValidationError as exc:
            messages.warning(request, exc.messages[0])
            return redirect('orders:staff_order_list')
        updated = len(changed)
        label = Order.Status(new_status).label
        messages.success(
            request,
            f'{updated} pedido(s) atualizado(s) para "{label}".',
        )

    next_url = request.POST.get('next', '')
    if next_url and url_has_allowed_host_and_scheme(
        next_url, allowed_hosts={request.get_host()}
    ):
        return redirect(next_url)
    return redirect('orders:staff_order_list')


@staff_member_required
@require_http_methods(['GET', 'POST'])
def staff_order_detail(request, order_number):
    order = get_object_or_404(
        Order.objects.select_related('restaurant').prefetch_related(
            'items__options',
            'status_changes__changed_by',
        ),
        order_number=order_number,
    )

    # Capturado antes de validar o form: form.is_valid() já aplica o novo
    # status na instância (via _post_clean), então leríamos o valor novo.
    previous_status = order.status

    if request.method == 'POST':
        form = OrderStatusForm(request.POST, instance=order)
        if form.is_valid():
            try:
                order, changed = change_status(order, form.cleaned_data['status'], request.user,
                    confirm_refund=request.POST.get('confirm_refund') == 'on', resolve_pending_pix=True)
            except ValidationError as exc:
                order.refresh_from_db()
                form.add_error('status', exc.messages[0])
            else:
                messages.success(request, f'Status do pedido {order.order_number} atualizado.')
                url = reverse('orders:staff_order_detail', args=[order.order_number])
                return redirect(f'{url}?updated=1')
    else:
        form = OrderStatusForm(instance=order)

    return render(
        request,
        'orders/staff_order_detail.html',
        {
            'order': order,
            'form': form,
            'payment_attempts': order.payment_attempts.all(),
            'can_refund': order.is_paid and order.requires_online_payment and request.user.has_perm('orders.refund_payment'),
            'wa_customer_url': whatsapp_service.montar_link_wame(
                order.phone, notificacoes_service.mensagem_status(order)
            ),
            'WHATSAPP_MOCK': settings.WHATSAPP_MOCK,
        },
    )


@staff_member_required
@require_http_methods(['POST'])
def staff_order_refund(request, order_number):
    order = get_object_or_404(Order, order_number=order_number)
    if not request.user.has_perm('orders.refund_payment'):
        messages.error(request, 'Você não tem permissão para estornar pagamentos.')
    elif request.POST.get('confirm_refund') != 'on':
        messages.warning(request, 'Confirme o estorno integral antes de continuar.')
    else:
        try:
            with payment_service.locked_order(order.pk) as current:
                payment_service.ensure_legacy_attempts(current)
                if not current.requires_online_payment:
                    raise payment_service.PaymentError('O estorno online se aplica somente a pagamentos Pix ou cartão online.')
                if not current.is_paid and current.payment_status != 'refunded':
                    raise payment_service.PaymentError('Não há pagamento confirmado para estornar.')
            current, changed = change_status(current, 'cancelled', request.user, confirm_refund=True)
            if not changed:
                payment_service.refund(current)
            messages.success(request, 'Estorno solicitado e registrado.')
        except ValidationError as exc:
            messages.warning(request, exc.messages[0])
    return redirect('orders:staff_order_detail', order_number=order.order_number)

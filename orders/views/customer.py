"""Views voltadas ao cliente após o pedido: confirmação, acompanhamento e o
sininho de notificações."""

from urllib.parse import quote

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import render

from ..models import Notification, Order
from .common import get_own_order


def confirmation(request, order_number):
    order = get_own_order(
        request,
        order_number,
        Order.objects.select_related('restaurant').prefetch_related('items__options'),
    )
    wa_url = None
    if order.restaurant.whatsapp_number:
        items_text = '\n'.join(
            f'{i.quantity}x {i.item_name} - R$ {i.line_total}'
            for i in order.items.all()
        )
        msg = (
            f'Olá! Acabei de fazer um pedido.\n'
            f'Número: {order.order_number}\n'
            f'Itens:\n{items_text}\n'
            f'Total: R$ {order.total}\n'
            f'Pagamento: {order.get_payment_method_display()}'
        )
        if order.fulfillment_method == Order.FulfillmentMethod.DELIVERY and order.address:
            msg += f'\nEndereço: {order.address}'
        wa_url = f'https://wa.me/{order.restaurant.whatsapp_number}?text={quote(msg)}'

    return render(request, 'orders/confirmation.html', {'order': order, 'wa_url': wa_url})


def track_order(request, order_number):
    order = get_own_order(request, order_number)
    if request.GET.get('json'):
        return JsonResponse({
            'status': order.status,
            'status_display': order.status_display,
        })
    return render(request, 'orders/track_order.html', {'order': order})


@login_required
def notifications_list(request):
    """Lista as notificações do cliente e marca as não lidas como lidas."""
    notifications = list(
        Notification.objects.filter(user=request.user).select_related('order')[:50]
    )
    Notification.objects.filter(user=request.user, is_read=False).update(is_read=True)
    return render(request, 'orders/notifications.html', {'notifications': notifications})


@login_required
def notifications_feed(request):
    """Estado das notificações do usuário para o sininho ao vivo (polling).

    Devolve o total de não lidas e o maior id de notificação, usado pelo
    frontend para tocar o alerta sonoro quando uma nova notificação chega.
    """
    qs = Notification.objects.filter(user=request.user)
    latest_id = qs.order_by('-id').values_list('id', flat=True).first() or 0
    unread = qs.filter(is_read=False).count()
    return JsonResponse({'ok': True, 'count': unread, 'latest_id': latest_id})

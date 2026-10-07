"""Views voltadas ao cliente após o pedido: confirmação, acompanhamento e o
sininho de notificações."""

from urllib.parse import quote

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.contrib import messages
from django.shortcuts import redirect
from django.views.decorators.http import require_POST
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
        from ..services.whatsapp import montar_link_wame
        wa_url = montar_link_wame(order.restaurant.whatsapp_number, msg)

    refund_pending = order.payment_attempts.filter(status='approved', refund_requested_at__isnull=False).exists()
    return render(request, 'orders/confirmation.html', {'order': order, 'wa_url': wa_url,
        'refund_pending':refund_pending})


def track_order(request, order_number):
    order = get_own_order(request, order_number)
    if request.GET.get('json'):
        return JsonResponse({
            'status': order.status,
            'status_display': order.status_display,
        })
    return render(request, 'orders/track_order.html', {'order': order})


@require_POST
def stop_whatsapp(request, order_number):
    order = get_own_order(request, order_number)
    order.whatsapp_opt_in = False
    order.save(update_fields=['whatsapp_opt_in', 'updated_at'])
    order.whatsapp_messages.filter(status='pending').update(status='skipped')
    messages.success(request, 'Avisos de WhatsApp desativados para este pedido.')
    return redirect('orders:confirmation', order_number=order.order_number)


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

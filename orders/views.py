import hashlib
import hmac
import json
import logging
from decimal import Decimal
from urllib.parse import quote

from django.conf import settings
from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.db import transaction
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from cart.cart import Cart
from menu.selectors import get_current_restaurant, is_restaurant_open

from .forms import CheckoutForm, OrderStatusForm
from .models import City, Order, OrderItem, OrderItemOption, PixPayment
from .services import mercadopago as mp_service
from .services import pedidos as pedidos_service

logger = logging.getLogger(__name__)


@require_http_methods(['GET', 'POST'])
def checkout(request):
    cart = Cart(request)
    cart_items = cart.items

    if not cart_items:
        messages.warning(request, 'Seu carrinho está vazio.')
        return redirect('cart:cart_detail')

    unavailable_items = [entry['item'].name for entry in cart_items if not entry['item'].is_available]
    if unavailable_items:
        messages.warning(
            request,
            'Remova os itens indisponíveis antes de finalizar: ' + ', '.join(unavailable_items),
        )
        return redirect('cart:cart_detail')

    restaurant = get_current_restaurant() or cart_items[0]['item'].category.restaurant

    if is_restaurant_open(restaurant) is False:
        messages.warning(request, 'O restaurante está fechado no momento. Tente mais tarde.')
        return redirect('menu:menu_list')

    if request.method == 'POST':
        form = CheckoutForm(request.POST)
        is_ajax = request.headers.get('x-requested-with') == 'XMLHttpRequest'
        if form.is_valid():
            order = _create_order_from_cart(
                form=form,
                cart=cart,
                cart_items=cart_items,
                restaurant=restaurant,
            )
            cart.clear()
            # Pagamento Pix via modal: cria a cobrança e devolve o QR Code (JSON).
            if order.payment_method == Order.PaymentMethod.PIX and is_ajax:
                try:
                    pix = _create_pix_for_order(order)
                except mp_service.PixError as exc:
                    logger.exception('Falha ao criar cobrança Pix')
                    return JsonResponse({'ok': False, 'error': str(exc)}, status=502)
                return JsonResponse(_pix_payload(pix, order))
            messages.success(request, f'Pedido {order.order_number} recebido.')
            return redirect('orders:confirmation', order_number=order.order_number)
        if is_ajax:
            return JsonResponse({'ok': False, 'errors': form.errors}, status=400)
    else:
        form = CheckoutForm(initial={'fulfillment_method': Order.FulfillmentMethod.DELIVERY})

    # Delivery fee is computed from the selected city + neighborhood, so it
    # starts at zero and is updated client-side as the customer chooses.
    return render(
        request,
        'orders/checkout.html',
        {
            'form': form,
            'cart_items': cart_items,
            'subtotal': cart.subtotal,
            'delivery_fee': Decimal('0.00'),
            'estimated_total': cart.subtotal,
            'delivery_areas': _delivery_areas_data(),
            'restaurant': restaurant,
        },
    )


def _delivery_areas_data():
    """City/neighborhood fees keyed by city id, for the checkout dropdowns."""
    cities = City.objects.filter(is_active=True).prefetch_related('neighborhoods')
    return {
        str(city.id): {
            'name': city.name,
            'fee': city.delivery_fee,
            'neighborhoods': [
                {'id': n.id, 'name': n.name, 'fee': n.delivery_fee}
                for n in city.neighborhoods.all()
                if n.is_active
            ],
        }
        for city in cities
    }


def confirmation(request, order_number):
    order = get_object_or_404(
        Order.objects.select_related('restaurant').prefetch_related('items__options'),
        order_number=order_number,
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


# --------------------------------------------------------------------------- #
# Pix / Mercado Pago
# --------------------------------------------------------------------------- #

def _create_pix_for_order(order):
    """Cria (ou reusa) a cobrança Pix de um pedido e persiste o PixPayment."""
    existing = getattr(order, 'pix_payment', None)
    if (
        existing
        and existing.status == PixPayment.Status.PENDING
        and not existing.is_expired
        and existing.mp_payment_id
    ):
        return existing

    data = mp_service.criar_pix(
        amount=order.total,
        description=f'Pedido {order.order_number}',
        payer_email=order.customer_email or settings.PIX_DEFAULT_PAYER_EMAIL,
        payer_name=order.customer_name,
        payer_cpf=order.customer_cpf,
        external_reference=order.order_number,
    )
    pix, _ = PixPayment.objects.update_or_create(
        order=order,
        defaults={
            'mp_payment_id': data['id'],
            'external_reference': order.order_number,
            'status': PixPayment.Status.PENDING,
            'amount': order.total,
            'qr_code_text': data['qr_code_text'],
            'qr_code_base64': data['qr_code_base64'],
            'txid': data.get('txid', ''),
            'expires_at': data.get('expires_at'),
        },
    )
    return pix


def _pix_payload(pix, order):
    return {
        'ok': True,
        'pixId': pix.mp_payment_id,
        'qrCodeBase64': pix.qr_code_base64,
        'qrCodeText': pix.qr_code_text,
        'expiracao': pix.expires_at.isoformat() if pix.expires_at else None,
        'order_number': order.order_number,
        'confirmation_url': reverse('orders:confirmation', args=[order.order_number]),
    }


@require_http_methods(['GET'])
def pix_status(request, pix_id):
    """Polling do frontend: devolve o status atual da cobrança Pix."""
    pix = get_object_or_404(PixPayment.objects.select_related('order'), mp_payment_id=pix_id)

    if pix.status == PixPayment.Status.PENDING:
        if pix.is_expired:
            pix.status = PixPayment.Status.EXPIRED
            pix.save(update_fields=['status', 'updated_at'])
            pedidos_service.marcar_cancelado(pix.order)
        elif not settings.MERCADOPAGO_MOCK:
            # Sem webhook (ex.: localhost), consultamos o MP diretamente.
            try:
                info = mp_service.buscar_status(pix.mp_payment_id)
                pedidos_service.aplicar_status_mp(pix, info.get('status'))
            except mp_service.PixError:
                logger.warning('Falha ao consultar status do Pix %s', pix.mp_payment_id)

    return JsonResponse({
        'status': pix.status,
        'paid': pix.status == PixPayment.Status.APPROVED,
        'cancelled': pix.status in (PixPayment.Status.CANCELLED, PixPayment.Status.EXPIRED),
        'confirmation_url': reverse('orders:confirmation', args=[pix.order.order_number]),
    })


@require_http_methods(['POST'])
def pix_recreate(request, order_number):
    """Gera uma nova cobrança Pix para um pedido cujo Pix expirou/foi cancelado."""
    order = get_object_or_404(Order, order_number=order_number)

    if order.payment_status == Order.PaymentStatus.PAID:
        return JsonResponse({
            'ok': True,
            'paid': True,
            'confirmation_url': reverse('orders:confirmation', args=[order.order_number]),
        })

    try:
        pix = _create_pix_for_order(order)
    except mp_service.PixError as exc:
        logger.exception('Falha ao recriar cobrança Pix')
        return JsonResponse({'ok': False, 'error': str(exc)}, status=502)
    return JsonResponse(_pix_payload(pix, order))


@csrf_exempt
@require_http_methods(['POST'])
def webhook_pix(request):
    """Webhook do Mercado Pago: confirma/cancela o pedido conforme o pagamento."""
    if not _webhook_signature_ok(request):
        return HttpResponse(status=401)

    try:
        body = json.loads(request.body or b'{}')
    except (ValueError, TypeError):
        body = {}

    data_id = (body.get('data') or {}).get('id') or request.GET.get('data.id')
    topic = body.get('type') or body.get('topic') or request.GET.get('type')

    # Responde 200 sempre que possível; processa de forma defensiva.
    if data_id and topic in (None, 'payment'):
        try:
            info = mp_service.buscar_status(str(data_id))
            ref = info.get('external_reference')
            pix = (
                PixPayment.objects.select_related('order')
                .filter(mp_payment_id=str(data_id))
                .first()
            )
            if pix is None and ref:
                pix = (
                    PixPayment.objects.select_related('order')
                    .filter(external_reference=ref)
                    .first()
                )
            if pix is not None:
                pedidos_service.aplicar_status_mp(pix, info.get('status'))
        except mp_service.PixError:
            logger.warning('Webhook: falha ao consultar pagamento %s', data_id)
        except Exception:  # noqa: BLE001 - nunca devolver 500 para o MP
            logger.exception('Webhook: erro inesperado ao processar %s', data_id)

    return HttpResponse(status=200)


def _webhook_signature_ok(request):
    """Valida o header x-signature do MP. Sem secret configurado, aceita (dev)."""
    secret = settings.MERCADOPAGO_WEBHOOK_SECRET
    if not secret:
        return True

    signature = request.headers.get('x-signature', '')
    request_id = request.headers.get('x-request-id', '')
    data_id = request.GET.get('data.id') or ''
    if not data_id:
        try:
            data_id = (json.loads(request.body or b'{}').get('data') or {}).get('id') or ''
        except (ValueError, TypeError):
            data_id = ''

    ts = v1 = ''
    for part in signature.split(','):
        key, _, value = part.strip().partition('=')
        if key == 'ts':
            ts = value
        elif key == 'v1':
            v1 = value
    if not (ts and v1):
        return False

    manifest = f'id:{data_id};request-id:{request_id};ts:{ts};'
    expected = hmac.new(secret.encode(), manifest.encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, v1)


def track_order(request, order_number):
    order = get_object_or_404(Order, order_number=order_number)
    if request.GET.get('json'):
        return JsonResponse({
            'status': order.status,
            'status_display': order.get_status_display(),
        })
    return render(request, 'orders/track_order.html', {'order': order})


@staff_member_required
def staff_order_list(request):
    status_filter = request.GET.get('status', '')
    orders = Order.objects.select_related('restaurant').prefetch_related('items')

    if status_filter in Order.Status.values:
        orders = orders.filter(status=status_filter)
    else:
        status_filter = ''

    return render(
        request,
        'orders/staff_order_list.html',
        {
            'orders': orders,
            'status_filter': status_filter,
            'status_choices': Order.Status.choices,
        },
    )


@staff_member_required
@require_http_methods(['GET', 'POST'])
def staff_order_detail(request, order_number):
    order = get_object_or_404(
        Order.objects.select_related('restaurant').prefetch_related('items__options'),
        order_number=order_number,
    )

    if request.method == 'POST':
        form = OrderStatusForm(request.POST, instance=order)
        if form.is_valid():
            form.save()
            messages.success(request, f'Order {order.order_number} status updated.')
            return redirect('orders:staff_order_detail', order_number=order.order_number)
    else:
        form = OrderStatusForm(instance=order)

    return render(
        request,
        'orders/staff_order_detail.html',
        {
            'order': order,
            'form': form,
        },
    )


@transaction.atomic
def _create_order_from_cart(form, cart, cart_items, restaurant):
    order = form.save(commit=False)
    order.restaurant = restaurant
    order.subtotal = cart.subtotal

    city = form.cleaned_data.get('city')
    neighborhood = form.cleaned_data.get('neighborhood')

    if order.fulfillment_method == Order.FulfillmentMethod.DELIVERY:
        order.address_city = city.name if city else ''
        order.address_neighborhood = neighborhood.name if neighborhood else ''
        order.delivery_fee = (
            (city.delivery_fee if city else Decimal('0.00'))
            + (neighborhood.delivery_fee if neighborhood else Decimal('0.00'))
        )
    else:
        order.address_city = ''
        order.address_neighborhood = ''
        order.delivery_fee = Decimal('0.00')

    order.save()

    for entry in cart_items:
        item = entry['item']
        order_item = OrderItem.objects.create(
            order=order,
            menu_item=item,
            item_name=item.name,
            unit_price=entry['unit_price'],
            quantity=entry['quantity'],
            line_total=entry['line_total'],
            notes=entry.get('notes', ''),
        )
        for choice in entry.get('options', []):
            OrderItemOption.objects.create(
                order_item=order_item,
                group_name=choice.group.name,
                choice_name=choice.name,
                extra_price=choice.extra_price,
            )

    return order

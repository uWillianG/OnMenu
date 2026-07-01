import hashlib
import hmac
import json
import logging
from decimal import Decimal
from urllib.parse import quote

from django.conf import settings
from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from cart.cart import Cart
from menu.selectors import get_current_restaurant, is_restaurant_open

from . import selectors
from .forms import CheckoutForm, OrderStatusForm
from .models import (
    CardPayment,
    City,
    Notification,
    Order,
    OrderItem,
    OrderItemOption,
    PixPayment,
)
from .services import mercadopago as mp_service
from .services import notificacoes as notificacoes_service
from .services import pedidos as pedidos_service
from .services import whatsapp as whatsapp_service

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
                user=request.user if request.user.is_authenticated else None,
            )
            # Guarda o pedido na sessão para o acompanhamento na tela principal.
            selectors.remember_order(request, order)
            cart.clear()
            # Pagamento Pix via modal: cria a cobrança e devolve o QR Code (JSON).
            if order.payment_method == Order.PaymentMethod.PIX and is_ajax:
                try:
                    pix = _create_pix_for_order(order)
                except mp_service.PixError as exc:
                    logger.exception('Falha ao criar cobrança Pix')
                    return JsonResponse({'ok': False, 'error': str(exc)}, status=502)
                return JsonResponse(_pix_payload(pix, order))
            # Cartão de crédito: o pedido é criado aqui; o pagamento ocorre depois
            # que o Brick tokeniza o cartão e o frontend chama orders:card_pay.
            if order.payment_method == Order.PaymentMethod.CREDIT_CARD and is_ajax:
                return JsonResponse({
                    'ok': True,
                    'mode': 'card',
                    'order_number': order.order_number,
                    'amount': str(order.total),
                    'public_key': settings.MERCADOPAGO_PUBLIC_KEY,
                    'card_pay_url': reverse('orders:card_pay', args=[order.order_number]),
                    'confirmation_url': reverse('orders:confirmation', args=[order.order_number]),
                })
            messages.success(request, f'Pedido {order.order_number} recebido.')
            return redirect('orders:confirmation', order_number=order.order_number)
        if is_ajax:
            return JsonResponse({'ok': False, 'errors': form.errors}, status=400)
    else:
        form = CheckoutForm(initial=_checkout_initial(request))

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
            'mercadopago_public_key': settings.MERCADOPAGO_PUBLIC_KEY,
        },
    )


def _checkout_initial(request):
    """Pré-preenche o checkout com os dados salvos do cliente logado.

    Evita digitar de novo nome, telefone e endereço já cadastrados no perfil.
    """
    initial = {'fulfillment_method': Order.FulfillmentMethod.DELIVERY}
    user = request.user
    if not user.is_authenticated:
        return initial

    initial['customer_name'] = user.get_full_name() or user.get_username()
    profile = getattr(user, 'profile', None)
    if profile is not None:
        initial.update({
            'phone': profile.phone,
            'customer_cpf': profile.cpf_display,
            'address_street': profile.address_street,
            'address_number': profile.address_number,
            'address_complement': profile.address_complement,
            'city': profile.city_id,
            'neighborhood': profile.neighborhood_id,
        })
    return initial


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
    """Webhook do Mercado Pago para cobranças Pix."""
    return _handle_payment_webhook(request)


@csrf_exempt
@require_http_methods(['POST'])
def webhook_card(request):
    """Webhook do Mercado Pago para pagamentos com cartão."""
    return _handle_payment_webhook(request)


def _handle_payment_webhook(request):
    """Confirma/cancela o pedido conforme o pagamento. Responde 200 ao MP."""
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
            _process_payment_webhook(str(data_id))
        except mp_service.PixError:
            logger.warning('Webhook: falha ao consultar pagamento %s', data_id)
        except Exception:  # noqa: BLE001 - nunca devolver 500 para o MP
            logger.exception('Webhook: erro inesperado ao processar %s', data_id)

    return HttpResponse(status=200)


def _find_payment(mp_payment_id, external_reference=None):
    """Acha o PixPayment ou CardPayment correspondente, por id do MP ou referência."""
    for model in (PixPayment, CardPayment):
        payment = model.objects.select_related('order').filter(mp_payment_id=mp_payment_id).first()
        if payment is None and external_reference:
            payment = (
                model.objects.select_related('order')
                .filter(external_reference=external_reference)
                .first()
            )
        if payment is not None:
            return payment
    return None


def _process_payment_webhook(data_id):
    info = mp_service.buscar_status(data_id)
    payment = _find_payment(data_id, info.get('external_reference'))
    if payment is not None:
        pedidos_service.aplicar_status_mp(payment, info.get('status'))


# Mensagens amigáveis de recusa (sem expor o código interno do MP).
_CARD_ERROR_MESSAGES = {
    'cc_rejected_insufficient_amount': 'Saldo insuficiente.',
    'cc_rejected_bad_filled_security_code': 'Código de segurança inválido.',
    'cc_rejected_bad_filled_date': 'Data de validade inválida.',
    'cc_rejected_bad_filled_other': 'Dados do cartão inválidos. Confira e tente novamente.',
    'cc_rejected_call_for_authorize': 'Autorize o pagamento com seu banco e tente novamente.',
    'cc_rejected_card_disabled': 'Cartão desabilitado. Use outro cartão.',
    'cc_rejected_high_risk': 'Pagamento recusado. Tente outro meio de pagamento.',
    'cc_rejected_max_attempts': 'Muitas tentativas. Use outro cartão.',
}


def _card_error_message(status_detail):
    return _CARD_ERROR_MESSAGES.get(status_detail, 'Cartão recusado. Tente outro cartão.')


def _card_payload(card, order, message=''):
    return {
        'ok': True,
        'status': card.status,
        'paymentId': card.mp_payment_id,
        'message': message,
        'requires_action': False,
        'redirect_url': '',
        'confirmation_url': reverse('orders:confirmation', args=[order.order_number]),
    }


@require_http_methods(['POST'])
def card_pay(request, order_number):
    """Processa o pagamento com cartão a partir do token gerado pelo Brick."""
    order = get_object_or_404(Order, order_number=order_number)

    # Idempotência: não cobra de novo se já houver pagamento aprovado/em análise.
    existing = getattr(order, 'card_payment', None)
    if existing and existing.status in (CardPayment.Status.APPROVED, CardPayment.Status.IN_PROCESS):
        return JsonResponse(_card_payload(existing, order))

    token = request.POST.get('token', '')
    if not token:
        return JsonResponse({'ok': False, 'error': 'Token do cartão ausente.'}, status=400)

    try:
        data = mp_service.criar_pagamento_cartao(
            amount=order.total,
            description=f'Pedido {order.order_number}',
            token=token,
            installments=request.POST.get('installments') or 1,
            payment_method_id=request.POST.get('payment_method_id', ''),
            issuer_id=request.POST.get('issuer_id', ''),
            payer_email=request.POST.get('payer_email', '') or order.customer_email,
            payer_cpf=request.POST.get('payer_cpf', '') or order.customer_cpf,
            external_reference=order.order_number,
        )
    except mp_service.PixError as exc:
        logger.exception('Falha ao processar cartão')
        return JsonResponse({'ok': False, 'error': str(exc)}, status=503)

    card, _ = CardPayment.objects.update_or_create(
        order=order,
        defaults={
            'mp_payment_id': data['id'],
            'external_reference': order.order_number,
            'status': data['status'],
            'status_detail': data.get('status_detail', ''),
            'amount': order.total,
            'installments': data.get('installments', 1),
            'payment_method_id': data.get('payment_method_id', ''),
            'last_four': data.get('last_four', ''),
        },
    )
    pedidos_service.aplicar_status_mp(card, data['status'])

    payload = _card_payload(card, order)
    if data['status'] == 'rejected':
        payload['message'] = _card_error_message(data.get('status_detail'))
    elif data.get('requires_action'):
        payload['requires_action'] = True
        payload['redirect_url'] = data.get('redirect_url', '')
    return JsonResponse(payload)


@require_http_methods(['GET'])
def card_status(request, payment_id):
    """Polling/refresh do status de um pagamento com cartão."""
    card = get_object_or_404(CardPayment.objects.select_related('order'), mp_payment_id=payment_id)

    if card.status in (CardPayment.Status.PENDING, CardPayment.Status.IN_PROCESS) \
            and not settings.MERCADOPAGO_MOCK:
        try:
            info = mp_service.buscar_status(card.mp_payment_id)
            pedidos_service.aplicar_status_mp(card, info.get('status'))
        except mp_service.PixError:
            logger.warning('Falha ao consultar status do cartão %s', card.mp_payment_id)

    return JsonResponse({
        'status': card.status,
        'paid': card.status == CardPayment.Status.APPROVED,
        'rejected': card.status == CardPayment.Status.REJECTED,
        'confirmation_url': reverse('orders:confirmation', args=[card.order.order_number]),
    })


@require_http_methods(['GET'])
def card_3ds_callback(request):
    """Retorno da autenticação 3DS: consulta o status final e segue para a confirmação."""
    payment_id = request.GET.get('payment_id') or request.GET.get('data.id') or ''
    card = None
    if payment_id:
        card = CardPayment.objects.select_related('order').filter(mp_payment_id=payment_id).first()
        if card is not None and not settings.MERCADOPAGO_MOCK:
            try:
                info = mp_service.buscar_status(card.mp_payment_id)
                pedidos_service.aplicar_status_mp(card, info.get('status'))
            except mp_service.PixError:
                logger.warning('3DS callback: falha ao consultar %s', payment_id)

    if card is not None:
        return redirect('orders:confirmation', order_number=card.order.order_number)
    messages.warning(request, 'Não foi possível confirmar o pagamento. Verifique seu pedido.')
    return redirect('menu:menu_list')


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


@staff_member_required
def staff_order_list(request):
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
        orders = orders.filter(created_at__date=parsed_date)
    else:
        date_filter = ''

    # Separa pedidos em andamento (ativos) dos finalizados (entregues/cancelados),
    # avaliando em Python para não fazer duas queries adicionais.
    order_list = list(orders)
    active_orders = [o for o in order_list if o.is_active]
    inactive_orders = [o for o in order_list if not o.is_active]

    return render(
        request,
        'orders/staff_order_list.html',
        {
            'active_orders': active_orders,
            'inactive_orders': inactive_orders,
            'active_count': len(active_orders),
            'inactive_count': len(inactive_orders),
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
        },
    )


def _orders_for_print(queryset):
    return queryset.select_related('restaurant').prefetch_related('items__options')


@staff_member_required
def staff_order_print(request, order_number):
    """Página de impressão (comanda) de um único pedido."""
    order = get_object_or_404(
        _orders_for_print(Order.objects.all()),
        order_number=order_number,
    )
    return render(
        request,
        'orders/print_orders.html',
        {'orders': [order], 'auto_print': True, 'scope_label': 'Pedido'},
    )


@staff_member_required
def staff_orders_print_active(request):
    """Página de impressão de todos os pedidos ativos (em andamento)."""
    orders = _orders_for_print(
        Order.objects.filter(status__in=Order.ACTIVE_STATUSES)
    )
    return render(
        request,
        'orders/print_orders.html',
        {
            'orders': list(orders),
            'auto_print': True,
            'scope_label': 'Pedidos ativos',
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
        # Notifica apenas os pedidos cuja situação realmente muda.
        changed = list(selected.exclude(status=new_status))
        updated = selected.update(status=new_status, updated_at=timezone.now())
        for order in changed:
            order.status = new_status
            notificacoes_service.notificar_status_pedido(order)
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
        Order.objects.select_related('restaurant').prefetch_related('items__options'),
        order_number=order_number,
    )

    # Capturado antes de validar o form: form.is_valid() já aplica o novo
    # status na instância (via _post_clean), então leríamos o valor novo.
    previous_status = order.status

    if request.method == 'POST':
        form = OrderStatusForm(request.POST, instance=order)
        if form.is_valid():
            form.save()
            if order.status != previous_status:
                notificacoes_service.notificar_status_pedido(order)
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
            'wa_customer_url': whatsapp_service.montar_link_wame(
                order.phone, notificacoes_service.mensagem_status(order)
            ),
            'WHATSAPP_MOCK': settings.WHATSAPP_MOCK,
        },
    )


@login_required
@require_http_methods(['POST'])
def repeat_order(request, order_number):
    """Recria o carrinho com os itens de um pedido anterior e leva ao checkout.

    Só permite repetir pedidos da própria conta. Itens que saíram do cardápio
    (ou ficaram indisponíveis) são omitidos com um aviso. As opções escolhidas
    são remapeadas dos nomes salvos (snapshot) para as escolhas atuais do item.
    """
    order = get_object_or_404(
        Order.objects.prefetch_related('items__options', 'items__menu_item'),
        order_number=order_number,
        user=request.user,
    )

    cart = Cart(request)
    cart.clear()

    added = 0
    skipped = []
    for order_item in order.items.all():
        item = order_item.menu_item
        if item is None or not item.is_available:
            skipped.append(order_item.item_name)
            continue
        options = _rebuild_item_options(item, order_item)
        cart.add(
            item,
            quantity=order_item.quantity,
            options=options,
            notes=order_item.notes,
        )
        added += 1

    if added == 0:
        messages.warning(
            request,
            'Não foi possível repetir o pedido: os itens não estão mais disponíveis.',
        )
        return redirect('accounts:profile')

    if skipped:
        messages.info(
            request,
            'Alguns itens não estão mais disponíveis e foram removidos: '
            + ', '.join(skipped),
        )

    return redirect('orders:checkout')


def _rebuild_item_options(item, order_item):
    """Mapeia as opções salvas (nomes) de volta para os IDs das escolhas atuais.

    Retorna um dict {group_id: choice_id | [choice_ids]} no formato esperado
    pelo carrinho. Grupos/escolhas que não existem mais são ignorados.
    """
    groups = {g.name: g for g in item.complement_groups.prefetch_related('choices').all()}
    by_group = {}
    for opt in order_item.options.all():
        group = groups.get(opt.group_name)
        if group is None:
            continue
        choice = next((c for c in group.choices.all() if c.name == opt.choice_name), None)
        if choice is None:
            continue
        by_group.setdefault(str(group.id), []).append(str(choice.id))

    return {
        gid: (ids[0] if len(ids) == 1 else ids)
        for gid, ids in by_group.items()
    }


@transaction.atomic
def _create_order_from_cart(form, cart, cart_items, restaurant, user=None):
    order = form.save(commit=False)
    order.restaurant = restaurant
    order.user = user
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

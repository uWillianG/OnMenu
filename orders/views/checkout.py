"""Checkout: valida o formulário, cria o pedido a partir do carrinho e despacha
o pagamento (dinheiro/maquininha, Pix ou cartão). Inclui também a repetição de
um pedido anterior, que remonta o carrinho e leva de volta ao checkout."""

import logging
from decimal import Decimal

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_http_methods

from accounts import throttle

from cart.cart import Cart
from menu.selectors import get_current_restaurant, is_restaurant_open

from .. import selectors
from ..forms import CheckoutForm
from ..models import City, Order, OrderItem, OrderItemOption
from ..services import mercadopago as mp_service
from ..services import notificacoes as notificacoes_service
from ..services import pedidos as pedidos_service
from .payments import create_pix_for_order, pix_payload

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
        is_ajax = request.headers.get('x-requested-with') == 'XMLHttpRequest'
        # Limita a criação de pedidos por IP: contém spam de pedidos e o "card
        # testing" (o card_pay já limita 3 tentativas por pedido, mas o atacante
        # trocaria de pedido; aqui limitamos quantos pedidos ele consegue abrir).
        ip = throttle.client_ip(request)
        if throttle.is_blocked(throttle.CHECKOUT, ip):
            msg = throttle.retry_message(throttle.CHECKOUT)
            if is_ajax:
                return JsonResponse({'ok': False, 'error': msg}, status=429)
            messages.error(request, msg)
            return redirect('cart:cart_detail')
        throttle.record(throttle.CHECKOUT, ip)
        form = CheckoutForm(request.POST)
        if form.is_valid():
            # Só depois de validar o form dá para saber se é entrega ou retirada.
            minimo = _minimum_order_error(restaurant, form.cleaned_data, cart.subtotal)
            if minimo:
                form.add_error('fulfillment_method', minimo)
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
            # Avisa os admins assim que o pedido é realizado. Pagamentos online
            # (Pix/cartão) só avisam quando confirmados — ver pedidos.marcar_pago.
            if order.payment_method not in (
                Order.PaymentMethod.PIX,
                Order.PaymentMethod.CREDIT_CARD,
            ):
                notificacoes_service.notificar_admins_novo_pedido(order)
            # Pagamento Pix via modal: cria a cobrança e devolve o QR Code (JSON).
            if order.payment_method == Order.PaymentMethod.PIX and is_ajax:
                try:
                    pix = create_pix_for_order(order)
                except mp_service.PixError as exc:
                    logger.exception('Falha ao criar cobrança Pix')
                    return JsonResponse({'ok': False, 'error': str(exc)}, status=502)
                return JsonResponse(pix_payload(pix, order))
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
            'free_delivery_missing': restaurant.missing_for_free_delivery(cart.subtotal),
            'minimum_missing': restaurant.missing_for_minimum(cart.subtotal),
            'mercadopago_public_key': settings.MERCADOPAGO_PUBLIC_KEY,
        },
    )


def _money(value):
    """Formata um Decimal no padrão brasileiro, para mensagens ao cliente."""
    return f'{settings.CURRENCY_SYMBOL} {value:.2f}'.replace('.', ',')


def _minimum_order_error(restaurant, cleaned_data, subtotal):
    """Mensagem de pedido mínimo, quando o carrinho não alcança o valor.

    A regra vale só para entrega — retirada no balcão não tem mínimo.
    """
    if cleaned_data.get('fulfillment_method') != Order.FulfillmentMethod.DELIVERY:
        return ''
    missing = restaurant.missing_for_minimum(subtotal)
    if missing is None:
        return ''
    return (
        f'O pedido mínimo para entrega é {_money(restaurant.minimum_order)}. '
        f'Faltam {_money(missing)} — ou escolha retirada no local.'
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
        base_fee = (
            (city.delivery_fee if city else Decimal('0.00'))
            + (neighborhood.delivery_fee if neighborhood else Decimal('0.00'))
        )
        # Zera a taxa quando o carrinho passa do valor de frete grátis.
        order.delivery_fee = restaurant.delivery_fee_for(order.subtotal, base_fee)
    else:
        order.address_city = ''
        order.address_neighborhood = ''
        order.delivery_fee = Decimal('0.00')

    order.save()
    pedidos_service.registrar_status(order, user=user)

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

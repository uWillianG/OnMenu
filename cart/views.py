from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from menu.models import MenuItem

from .cart import Cart


def cart_detail(request):
    cart = Cart(request)
    return render(
        request,
        'cart/cart_detail.html',
        {
            'cart': cart,
            'cart_items': cart.items,
            'subtotal': cart.subtotal,
        },
    )


@require_POST
def cart_add(request, item_id):
    cart = Cart(request)
    item = get_object_or_404(MenuItem, pk=item_id)

    if not item.is_available:
        messages.warning(request, f'{item.name} está indisponível no momento.')
        return redirect(_next_url(request))

    # Parse option groups — supports both single-select (radio) and multi-select (checkbox)
    options = {}
    group_keys = {key for key in request.POST if key.startswith('option_group_')}
    for key in group_keys:
        group_id = key[len('option_group_'):]
        values = [v for v in request.POST.getlist(key) if v]
        if len(values) == 1:
            options[group_id] = values[0]
        elif len(values) > 1:
            options[group_id] = values

    notes = request.POST.get('item_notes', '').strip()

    cart.add(item, quantity=_positive_int(request.POST.get('quantity'), 1), options=options, notes=notes)
    messages.success(request, f'{item.name} foi adicionado ao carrinho.')
    return redirect(_next_url(request))


@require_POST
def cart_update(request, item_id):
    cart = Cart(request)
    item = get_object_or_404(MenuItem, pk=item_id)
    quantity = _positive_int(request.POST.get('quantity'), 0)

    if quantity <= 0:
        cart.remove(item)
        messages.info(request, f'{item.name} foi removido do carrinho.')
    else:
        cart.add(item, quantity=quantity, override_quantity=True)
        messages.success(request, f'Quantidade de {item.name} atualizada.')

    return redirect('cart:cart_detail')


@require_POST
def cart_remove(request, item_id):
    cart = Cart(request)
    item = get_object_or_404(MenuItem, pk=item_id)
    cart.remove(item)
    messages.info(request, f'{item.name} foi removido do carrinho.')
    return redirect('cart:cart_detail')


def _positive_int(value, default):
    try:
        quantity = int(value)
    except (TypeError, ValueError):
        return default
    return max(quantity, 0)


def _next_url(request):
    fallback = reverse('menu:menu_list')
    next_url = request.POST.get('next') or fallback
    if url_has_allowed_host_and_scheme(
        next_url,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return next_url
    return fallback

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
        messages.warning(request, f'{item.name} is currently unavailable.')
        return redirect(_next_url(request))

    options = {}
    for key, value in request.POST.items():
        if key.startswith('option_group_') and value:
            group_id = key[len('option_group_'):]
            options[group_id] = value

    cart.add(item, quantity=_positive_int(request.POST.get('quantity'), 1), options=options)
    messages.success(request, f'{item.name} was added to your cart.')
    return redirect(_next_url(request))


@require_POST
def cart_update(request, item_id):
    cart = Cart(request)
    item = get_object_or_404(MenuItem, pk=item_id)
    quantity = _positive_int(request.POST.get('quantity'), 0)

    if quantity <= 0:
        cart.remove(item)
        messages.info(request, f'{item.name} was removed from your cart.')
    else:
        cart.add(item, quantity=quantity, override_quantity=True)
        messages.success(request, f'{item.name} quantity updated.')

    return redirect('cart:cart_detail')


@require_POST
def cart_remove(request, item_id):
    cart = Cart(request)
    item = get_object_or_404(MenuItem, pk=item_id)
    cart.remove(item)
    messages.info(request, f'{item.name} was removed from your cart.')
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

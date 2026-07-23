from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from menu.models import MenuItem
from menu.selectors import get_current_restaurant

from .cart import Cart
from .validators import clean_options


def cart_detail(request):
    cart = Cart(request)
    cart_items = cart.items
    restaurant = get_current_restaurant()

    # Itens disponíveis (únicos) para alimentar o modal "adicionar ao carrinho",
    # o mesmo aberto ao clicar num produto na tela principal. Prefetch dos
    # complementos evita N+1 ao renderizar os dados ocultos.
    available_ids = {
        entry['item'].id for entry in cart_items if entry['item'].is_available
    }
    modal_items = (
        MenuItem.objects.filter(id__in=available_ids)
        .prefetch_related('complement_groups__choices')
        if available_ids
        else MenuItem.objects.none()
    )

    return render(
        request,
        'cart/cart_detail.html',
        {
            'cart': cart,
            'cart_items': cart_items,
            'subtotal': cart.subtotal,
            'modal_items': modal_items,
            'restaurant': restaurant,
            # Empurrões de valor: quanto falta para o frete grátis / pedido mínimo.
            'free_delivery_missing': (
                restaurant.missing_for_free_delivery(cart.subtotal) if restaurant else None
            ),
            'minimum_missing': (
                restaurant.missing_for_minimum(cart.subtotal) if restaurant else None
            ),
        },
    )


def _parse_options(request):
    """Lê os grupos de opção do POST (radio → str, checkbox → list)."""
    options = {}
    group_keys = {key for key in request.POST if key.startswith('option_group_')}
    for key in group_keys:
        group_id = key[len('option_group_'):]
        values = [v for v in request.POST.getlist(key) if v]
        if len(values) == 1:
            options[group_id] = values[0]
        elif len(values) > 1:
            options[group_id] = values
    return options


@require_POST
def cart_add(request, item_id):
    cart = Cart(request)
    item = get_object_or_404(MenuItem, pk=item_id)

    if not item.is_available:
        messages.warning(request, f'{item.name} está indisponível no momento.')
        return redirect(_next_url(request))

    options, error = clean_options(item, _parse_options(request))
    if error:
        messages.warning(request, error)
        return redirect(_next_url(request))

    notes = request.POST.get('item_notes', '').strip()

    cart.add(
        item,
        quantity=_positive_int(request.POST.get('quantity'), 1),
        options=options,
        notes=notes,
    )
    messages.success(request, f'{item.name} foi adicionado ao carrinho.')
    return redirect(_next_url(request))


@require_POST
def cart_edit(request, line_id):
    """Substitui a configuração de uma linha existente (edição no carrinho)."""
    cart = Cart(request)
    entry = cart.get_line(line_id)
    if entry is None:
        messages.warning(request, 'Item não encontrado no carrinho.')
        return redirect('cart:cart_detail')

    item = get_object_or_404(MenuItem, pk=entry['item_id'])
    if not item.is_available:
        messages.warning(request, f'{item.name} está indisponível no momento.')
        return redirect('cart:cart_detail')

    options, error = clean_options(item, _parse_options(request))
    if error:
        messages.warning(request, error)
        return redirect('cart:cart_detail')

    notes = request.POST.get('item_notes', '').strip()

    cart.replace(
        line_id,
        item,
        quantity=_positive_int(request.POST.get('quantity'), 1),
        options=options,
        notes=notes,
    )
    messages.success(request, f'{item.name} atualizado.')
    return redirect('cart:cart_detail')


@require_POST
def cart_update(request, line_id):
    cart = Cart(request)
    quantity = _positive_int(request.POST.get('quantity'), 0)

    if quantity <= 0:
        cart.remove(line_id)
        messages.info(request, 'Item removido do carrinho.')
    else:
        cart.set_quantity(line_id, quantity)
        messages.success(request, 'Quantidade atualizada.')

    return redirect('cart:cart_detail')


@require_POST
def cart_remove(request, line_id):
    cart = Cart(request)
    cart.remove(line_id)
    messages.info(request, 'Item removido do carrinho.')
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

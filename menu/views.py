from datetime import datetime

from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.db.models import Prefetch
from django.shortcuts import get_object_or_404, redirect, render

from cart.cart import Cart
from orders.selectors import get_tracked_active_orders

from .models import BusinessHours, MenuItem
from .selectors import get_current_restaurant, get_open_status


def menu_list(request):
    restaurant = get_current_restaurant()
    categories = []
    featured_items = []
    open_status = {'is_open': None, 'today_hours': None, 'detail': ''}
    cart = Cart(request)
    cart_items = cart.items

    if restaurant:
        categories = restaurant.categories.filter(is_active=True).prefetch_related(
            Prefetch(
                'items',
                queryset=MenuItem.objects.order_by('display_order', 'name').prefetch_related(
                    'option_groups__choices'
                ),
            ),
        )
        featured_items = (
            MenuItem.objects
            .filter(category__restaurant=restaurant, is_featured=True, is_available=True)
            .select_related('category')
            .prefetch_related('option_groups__choices')
            .order_by('display_order', 'name')
        )
        open_status = get_open_status(restaurant)

    return render(
        request,
        'menu/menu_list.html',
        {
            'restaurant': restaurant,
            'categories': categories,
            'featured_items': featured_items,
            'is_open': open_status['is_open'],
            'open_status': open_status,
            'cart': cart,
            'cart_items': cart_items,
            'tracked_orders': get_tracked_active_orders(request),
        },
    )


def _build_week_hours(restaurant):
    """Return all 7 weekdays (existing rows or blanks) ordered Mon→Sun."""
    today = datetime.now().weekday()
    existing = {h.day_of_week: h for h in restaurant.business_hours.all()}
    week = []
    for value, label in BusinessHours.DAY_CHOICES:
        week.append({
            'day': value,
            'label': label,
            'hours': existing.get(value),
            'is_today': value == today,
        })
    return week


def _parse_time(raw):
    raw = (raw or '').strip()
    if not raw:
        return None
    try:
        return datetime.strptime(raw, '%H:%M').time()
    except ValueError:
        return None


@staff_member_required
def edit_business_hours(request):
    """Dedicated staff-only screen for editing weekly business hours."""
    restaurant = get_current_restaurant()
    if not restaurant:
        messages.error(request, 'Restaurante não configurado.')
        return redirect('menu:menu_list')

    if request.method == 'POST':
        for value, _label in BusinessHours.DAY_CHOICES:
            is_closed = request.POST.get(f'active_{value}') != 'on'
            open_time = _parse_time(request.POST.get(f'open_{value}'))
            close_time = _parse_time(request.POST.get(f'close_{value}'))

            # Sem horários válidos = dia fechado.
            if not is_closed and (open_time is None or close_time is None):
                is_closed = True

            BusinessHours.objects.update_or_create(
                restaurant=restaurant,
                day_of_week=value,
                defaults={
                    'is_closed': is_closed,
                    'open_time': None if is_closed else open_time,
                    'close_time': None if is_closed else close_time,
                },
            )

        messages.success(request, 'Horário de funcionamento atualizado.')
        return redirect('menu:edit_business_hours')

    return render(
        request,
        'menu/business_hours_form.html',
        {
            'restaurant': restaurant,
            'week_hours': _build_week_hours(restaurant),
        },
    )


def item_detail(request, pk, slug):
    restaurant = get_current_restaurant()
    item_queryset = MenuItem.objects.select_related(
        'category',
        'category__restaurant',
    ).prefetch_related('option_groups__choices')
    if restaurant:
        item_queryset = item_queryset.filter(category__restaurant=restaurant)
    item = get_object_or_404(item_queryset, pk=pk, slug=slug)

    return render(request, 'menu/item_detail.html', {'item': item})

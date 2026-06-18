from django.db.models import Prefetch
from django.shortcuts import get_object_or_404, render

from cart.cart import Cart
from orders.selectors import get_tracked_active_orders

from .models import MenuItem
from .selectors import get_current_restaurant, is_restaurant_open


def menu_list(request):
    restaurant = get_current_restaurant()
    categories = []
    featured_items = []
    is_open = None
    business_hours = []
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
        is_open = is_restaurant_open(restaurant)
        business_hours = list(restaurant.business_hours.all())

    return render(
        request,
        'menu/menu_list.html',
        {
            'restaurant': restaurant,
            'categories': categories,
            'featured_items': featured_items,
            'is_open': is_open,
            'business_hours': business_hours,
            'cart': cart,
            'cart_items': cart_items,
            'tracked_orders': get_tracked_active_orders(request),
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

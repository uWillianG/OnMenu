from django.db.models import Prefetch
from django.shortcuts import get_object_or_404, render

from .models import MenuItem
from .selectors import get_current_restaurant


def menu_list(request):
    restaurant = get_current_restaurant()
    categories = []

    if restaurant:
        categories = restaurant.categories.filter(is_active=True).prefetch_related(
            Prefetch(
                'items',
                queryset=MenuItem.objects.order_by('display_order', 'name'),
            ),
        )

    return render(
        request,
        'menu/menu_list.html',
        {
            'restaurant': restaurant,
            'categories': categories,
        },
    )


def item_detail(request, pk, slug):
    restaurant = get_current_restaurant()
    item_queryset = MenuItem.objects.select_related(
        'category',
        'category__restaurant',
    )
    if restaurant:
        item_queryset = item_queryset.filter(category__restaurant=restaurant)
    item = get_object_or_404(item_queryset, pk=pk, slug=slug)

    return render(request, 'menu/item_detail.html', {'item': item})

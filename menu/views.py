from datetime import datetime

from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.db.models import Prefetch
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render

from cart.cart import Cart
from orders.selectors import get_delivery_fee_range, get_tracked_active_orders

from .forms import CategoryForm, MenuItemForm, RestaurantInfoForm, RestaurantLogoForm
from .models import BusinessHours, Category, MenuItem
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


def restaurant_info(request):
    """Página pública com todas as informações do estabelecimento."""
    restaurant = get_current_restaurant()
    if not restaurant:
        messages.error(request, 'Restaurante não configurado.')
        return redirect('menu:menu_list')

    open_status = get_open_status(restaurant)
    whatsapp_digits = ''.join(filter(str.isdigit, restaurant.whatsapp_number or ''))
    fee_range = get_delivery_fee_range()

    return render(
        request,
        'menu/restaurant_info.html',
        {
            'restaurant': restaurant,
            'open_status': open_status,
            'is_open': open_status['is_open'],
            'week_hours': _build_week_hours(restaurant),
            'whatsapp_digits': whatsapp_digits,
            'delivery_fee_min': fee_range[0] if fee_range else None,
            'delivery_fee_max': fee_range[1] if fee_range else None,
            'has_fee_range': fee_range is not None,
        },
    )


@staff_member_required
def edit_restaurant_info(request):
    """Edição (staff) dos dados de contato e entrega do estabelecimento."""
    restaurant = get_current_restaurant()
    if not restaurant:
        messages.error(request, 'Restaurante não configurado.')
        return redirect('menu:menu_list')

    if request.method == 'POST':
        form = RestaurantInfoForm(request.POST, instance=restaurant)
        if form.is_valid():
            form.save()
            messages.success(request, 'Informações do estabelecimento atualizadas.')
            return redirect('menu:restaurant_info')
    else:
        form = RestaurantInfoForm(instance=restaurant)

    return render(
        request,
        'menu/restaurant_info_form.html',
        {'form': form, 'restaurant': restaurant},
    )


@staff_member_required
def update_logo(request):
    """Permite que o staff envie/altere o logo do estabelecimento."""
    restaurant = get_current_restaurant()
    if not restaurant:
        messages.error(request, 'Restaurante não configurado.')
        return redirect('menu:menu_list')

    if request.method == 'POST':
        form = RestaurantLogoForm(request.POST, request.FILES, instance=restaurant)
        if form.is_valid():
            form.save()
            messages.success(request, 'Logo atualizado.')
        else:
            first_error = next(iter(form.errors.values()))[0]
            messages.error(request, f'Não foi possível atualizar o logo: {first_error}')

    return redirect('menu:restaurant_info')


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


@staff_member_required
def manage_menu(request):
    """Tela do admin para gerenciar o cardápio (categorias e itens)."""
    restaurant = get_current_restaurant()
    if not restaurant:
        messages.error(request, 'Restaurante não configurado.')
        return redirect('menu:menu_list')

    categories = (
        restaurant.categories
        .prefetch_related(
            Prefetch(
                'items',
                queryset=MenuItem.objects.order_by('display_order', 'name'),
            ),
        )
        .order_by('display_order', 'name')
    )
    item_count = MenuItem.objects.filter(category__restaurant=restaurant).count()

    return render(
        request,
        'menu/manage_menu.html',
        {
            'restaurant': restaurant,
            'categories': categories,
            'item_count': item_count,
            'category_form': CategoryForm(),
        },
    )


@staff_member_required
def category_create(request):
    """Cria uma nova categoria a partir do formulário inline da tela de gestão."""
    restaurant = get_current_restaurant()
    if not restaurant:
        messages.error(request, 'Restaurante não configurado.')
        return redirect('menu:menu_list')

    if request.method == 'POST':
        form = CategoryForm(request.POST)
        if form.is_valid():
            category = form.save(commit=False)
            category.restaurant = restaurant
            category.save()
            messages.success(request, f'Categoria “{category.name}” criada.')
        else:
            first_error = next(iter(form.errors.values()))[0]
            messages.error(request, f'Não foi possível criar a categoria: {first_error}')

    return redirect('menu:manage_menu')


@staff_member_required
def category_delete(request, pk):
    """Exclui uma categoria (e seus itens) do restaurante atual."""
    restaurant = get_current_restaurant()
    category = get_object_or_404(Category, pk=pk, restaurant=restaurant)
    if request.method == 'POST':
        name = category.name
        category.delete()
        messages.success(request, f'Categoria “{name}” excluída.')
    return redirect('menu:manage_menu')


@staff_member_required
def item_create(request):
    """Cadastra um novo item do cardápio (lanche, bebida, etc.)."""
    restaurant = get_current_restaurant()
    if not restaurant:
        messages.error(request, 'Restaurante não configurado.')
        return redirect('menu:menu_list')

    if not restaurant.categories.exists():
        messages.error(request, 'Crie uma categoria antes de adicionar itens.')
        return redirect('menu:manage_menu')

    if request.method == 'POST':
        form = MenuItemForm(request.POST, request.FILES, restaurant=restaurant)
        if form.is_valid():
            item = form.save()
            messages.success(request, f'Item “{item.name}” adicionado ao cardápio.')
            return redirect('menu:manage_menu')
    else:
        initial = {}
        category_id = request.GET.get('category')
        if category_id:
            initial['category'] = category_id
        form = MenuItemForm(restaurant=restaurant, initial=initial)

    return render(
        request,
        'menu/item_form.html',
        {'form': form, 'restaurant': restaurant, 'is_edit': False},
    )


@staff_member_required
def item_edit(request, pk):
    """Edita um item existente do cardápio."""
    restaurant = get_current_restaurant()
    item = get_object_or_404(MenuItem, pk=pk, category__restaurant=restaurant)

    if request.method == 'POST':
        form = MenuItemForm(request.POST, request.FILES, instance=item, restaurant=restaurant)
        if form.is_valid():
            form.save()
            messages.success(request, f'Item “{item.name}” atualizado.')
            return redirect('menu:manage_menu')
    else:
        form = MenuItemForm(instance=item, restaurant=restaurant)

    return render(
        request,
        'menu/item_form.html',
        {'form': form, 'restaurant': restaurant, 'is_edit': True, 'item': item},
    )


@staff_member_required
def item_toggle_available(request, pk):
    """Bloqueia/desbloqueia a venda de um item (alterna is_available)."""
    restaurant = get_current_restaurant()
    item = get_object_or_404(MenuItem, pk=pk, category__restaurant=restaurant)
    if request.method == 'POST':
        item.is_available = not item.is_available
        item.save(update_fields=['is_available', 'updated_at'])
        msg = (
            f'Item “{item.name}” liberado para venda.'
            if item.is_available
            else f'Item “{item.name}” bloqueado.'
        )
        if request.headers.get('x-requested-with') == 'XMLHttpRequest':
            return JsonResponse({'available': item.is_available, 'message': msg})
        messages.success(request, msg)
    return redirect('menu:manage_menu')


@staff_member_required
def item_delete(request, pk):
    """Exclui um item do cardápio."""
    restaurant = get_current_restaurant()
    item = get_object_or_404(MenuItem, pk=pk, category__restaurant=restaurant)
    if request.method == 'POST':
        name = item.name
        item.delete()
        messages.success(request, f'Item “{name}” excluído.')
    return redirect('menu:manage_menu')


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

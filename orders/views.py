from decimal import Decimal

from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods

from cart.cart import Cart
from menu.selectors import get_current_restaurant

from .forms import CheckoutForm, OrderStatusForm
from .models import Order, OrderItem


@require_http_methods(['GET', 'POST'])
def checkout(request):
    cart = Cart(request)
    cart_items = cart.items

    if not cart_items:
        messages.warning(request, 'Your cart is empty.')
        return redirect('cart:cart_detail')

    unavailable_items = [entry['item'].name for entry in cart_items if not entry['item'].is_available]
    if unavailable_items:
        messages.warning(
            request,
            'Remove unavailable items before checkout: ' + ', '.join(unavailable_items),
        )
        return redirect('cart:cart_detail')

    restaurant = get_current_restaurant() or cart_items[0]['item'].category.restaurant
    delivery_fee = restaurant.delivery_fee if restaurant.accepts_delivery else Decimal('0.00')

    if request.method == 'POST':
        form = CheckoutForm(request.POST)
        if form.is_valid():
            order = _create_order_from_cart(
                form=form,
                cart=cart,
                cart_items=cart_items,
                restaurant=restaurant,
            )
            cart.clear()
            messages.success(request, f'Order {order.order_number} received.')
            return redirect('orders:confirmation', order_number=order.order_number)
    else:
        form = CheckoutForm(initial={'fulfillment_method': Order.FulfillmentMethod.DELIVERY})

    return render(
        request,
        'orders/checkout.html',
        {
            'form': form,
            'cart_items': cart_items,
            'subtotal': cart.subtotal,
            'delivery_fee': delivery_fee,
            'estimated_total': cart.subtotal + delivery_fee,
            'restaurant': restaurant,
        },
    )


def confirmation(request, order_number):
    order = get_object_or_404(
        Order.objects.select_related('restaurant').prefetch_related('items'),
        order_number=order_number,
    )
    return render(request, 'orders/confirmation.html', {'order': order})


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
        Order.objects.select_related('restaurant').prefetch_related('items'),
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
    order.delivery_fee = (
        restaurant.delivery_fee
        if order.fulfillment_method == Order.FulfillmentMethod.DELIVERY
        else Decimal('0.00')
    )
    order.save()

    for entry in cart_items:
        item = entry['item']
        OrderItem.objects.create(
            order=order,
            menu_item=item,
            item_name=item.name,
            unit_price=item.price,
            quantity=entry['quantity'],
            line_total=entry['line_total'],
        )

    return order

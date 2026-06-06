from decimal import Decimal, InvalidOperation

from django.conf import settings


def cart_summary(request):
    cart = request.session.get(settings.CART_SESSION_ID, {})
    total = 0
    for raw in cart.values():
        if isinstance(raw, int):
            total += raw
        elif isinstance(raw, dict):
            total += int(raw.get('qty', 0))

    subtotal_str = request.session.get(settings.CART_SESSION_ID + '_subtotal', '0')
    try:
        subtotal = Decimal(subtotal_str)
    except (InvalidOperation, TypeError):
        subtotal = Decimal('0.00')

    return {
        'cart_item_count': total,
        'cart_subtotal': subtotal,
        'currency_symbol': getattr(settings, 'CURRENCY_SYMBOL', '$'),
    }

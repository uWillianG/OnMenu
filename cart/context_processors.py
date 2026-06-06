from django.conf import settings


def cart_summary(request):
    cart = request.session.get(settings.CART_SESSION_ID, {})
    return {
        'cart_item_count': sum(int(quantity) for quantity in cart.values()),
        'currency_symbol': getattr(settings, 'CURRENCY_SYMBOL', '$'),
    }

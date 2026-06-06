from decimal import Decimal

from django.conf import settings

from menu.models import MenuItem


class Cart:
    def __init__(self, request):
        self.session = request.session
        self.cart = self.session.get(settings.CART_SESSION_ID, {})

    def __iter__(self):
        item_ids = self.cart.keys()
        menu_items = MenuItem.objects.select_related(
            'category',
            'category__restaurant',
        ).filter(id__in=item_ids)
        item_map = {str(item.id): item for item in menu_items}

        for item_id, quantity in self.cart.items():
            item = item_map.get(str(item_id))
            if item is None:
                continue

            quantity = int(quantity)
            yield {
                'item': item,
                'quantity': quantity,
                'unit_price': item.price,
                'line_total': item.price * Decimal(quantity),
            }

    def __len__(self):
        return sum(int(quantity) for quantity in self.cart.values())

    @property
    def items(self):
        return list(self)

    @property
    def subtotal(self):
        return sum((entry['line_total'] for entry in self), Decimal('0.00'))

    def add(self, item, quantity=1, override_quantity=False):
        item_id = str(item.id)
        quantity = max(int(quantity), 1)

        if override_quantity:
            self.cart[item_id] = quantity
        else:
            self.cart[item_id] = int(self.cart.get(item_id, 0)) + quantity

        self.save()

    def remove(self, item):
        item_id = str(item.id)
        if item_id in self.cart:
            del self.cart[item_id]
            self.save()

    def clear(self):
        if settings.CART_SESSION_ID in self.session:
            del self.session[settings.CART_SESSION_ID]
            self.session.modified = True
        self.cart = {}

    def save(self):
        self.session[settings.CART_SESSION_ID] = self.cart
        self.session.modified = True

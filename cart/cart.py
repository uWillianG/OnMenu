from decimal import Decimal

from django.conf import settings

from menu.models import ItemOptionChoice, MenuItem


class Cart:
    def __init__(self, request):
        self.session = request.session
        self.cart = self.session.get(settings.CART_SESSION_ID, {})

    def _entry(self, item_id):
        raw = self.cart.get(str(item_id), {})
        if isinstance(raw, int):
            return {'qty': raw, 'options': {}}
        return raw

    def __iter__(self):
        item_ids = self.cart.keys()
        menu_items = MenuItem.objects.select_related(
            'category',
            'category__restaurant',
        ).filter(id__in=item_ids)
        item_map = {str(item.id): item for item in menu_items}

        all_choice_ids = []
        for item_id in self.cart:
            all_choice_ids.extend(self._entry(item_id).get('options', {}).values())

        choice_map = {}
        if all_choice_ids:
            choices = ItemOptionChoice.objects.select_related('group').filter(id__in=all_choice_ids)
            choice_map = {str(c.id): c for c in choices}

        for item_id in self.cart:
            item = item_map.get(str(item_id))
            if item is None:
                continue
            entry = self._entry(item_id)
            quantity = max(int(entry.get('qty', 1)), 1)
            selected_choices = [
                choice_map[cid]
                for cid in entry.get('options', {}).values()
                if cid in choice_map
            ]
            extra = sum(c.extra_price for c in selected_choices)
            unit_price = item.price + extra

            yield {
                'item': item,
                'quantity': quantity,
                'unit_price': unit_price,
                'line_total': unit_price * Decimal(quantity),
                'options': selected_choices,
            }

    def __len__(self):
        total = 0
        for raw in self.cart.values():
            if isinstance(raw, int):
                total += raw
            else:
                total += int(raw.get('qty', 0))
        return total

    @property
    def items(self):
        return list(self)

    @property
    def subtotal(self):
        return sum((entry['line_total'] for entry in self), Decimal('0.00'))

    def add(self, item, quantity=1, override_quantity=False, options=None):
        item_id = str(item.id)
        quantity = max(int(quantity), 1)
        options = options or {}
        existing = self._entry(item_id)

        if override_quantity:
            self.cart[item_id] = {'qty': quantity, 'options': options}
        else:
            new_qty = int(existing.get('qty', 0)) + quantity
            self.cart[item_id] = {
                'qty': new_qty,
                'options': options if options else existing.get('options', {}),
            }

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
        self.session[settings.CART_SESSION_ID + '_subtotal'] = str(self.subtotal)
        self.session.modified = True

from decimal import Decimal

from django.conf import settings

from menu.models import ComplementChoice, MenuItem


class Cart:
    def __init__(self, request):
        self.session = request.session
        self.cart = self.session.get(settings.CART_SESSION_ID, {})

    def _entry(self, item_id):
        """Normalise raw session value → dict with qty/options/notes."""
        raw = self.cart.get(str(item_id), {})
        if isinstance(raw, int):
            return {'qty': raw, 'options': {}, 'notes': ''}
        return {
            'qty': int(raw.get('qty', 0)),
            'options': raw.get('options', {}),
            'notes': raw.get('notes', ''),
        }

    def _all_choice_ids(self):
        """Flatten all choice IDs from every cart entry (handles str and list values)."""
        ids = []
        for item_id in self.cart:
            for val in self._entry(item_id).get('options', {}).values():
                if isinstance(val, list):
                    ids.extend(val)
                else:
                    ids.append(val)
        return ids

    def __iter__(self):
        item_ids = self.cart.keys()
        menu_items = MenuItem.objects.select_related(
            'category',
            'category__restaurant',
        ).filter(id__in=item_ids)
        item_map = {str(item.id): item for item in menu_items}

        choice_map = {}
        all_ids = self._all_choice_ids()
        if all_ids:
            choices = ComplementChoice.objects.select_related('group').filter(id__in=all_ids)
            choice_map = {str(c.id): c for c in choices}

        for item_id in self.cart:
            item = item_map.get(str(item_id))
            if item is None:
                continue
            entry = self._entry(item_id)
            quantity = max(int(entry.get('qty', 1)), 1)

            # Build flat list of selected choices (handles both str and list values)
            selected_choices = []
            for val in entry.get('options', {}).values():
                if isinstance(val, list):
                    for cid in val:
                        c = choice_map.get(str(cid))
                        if c:
                            selected_choices.append(c)
                else:
                    c = choice_map.get(str(val))
                    if c:
                        selected_choices.append(c)

            extra = sum(c.extra_price for c in selected_choices)
            unit_price = item.price + extra

            yield {
                'item': item,
                'quantity': quantity,
                'unit_price': unit_price,
                'line_total': unit_price * Decimal(quantity),
                'options': selected_choices,
                'notes': entry.get('notes', ''),
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

    def add(self, item, quantity=1, override_quantity=False, options=None, notes=''):
        item_id = str(item.id)
        quantity = max(int(quantity), 1)
        options = options or {}
        existing = self._entry(item_id)

        if override_quantity:
            self.cart[item_id] = {'qty': quantity, 'options': options, 'notes': notes}
        else:
            new_qty = int(existing.get('qty', 0)) + quantity
            self.cart[item_id] = {
                'qty': new_qty,
                'options': options if options else existing.get('options', {}),
                'notes': notes if notes else existing.get('notes', ''),
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

import hashlib
from decimal import Decimal

from django.conf import settings

from menu.models import ComplementChoice, MenuItem


class Cart:
    def __init__(self, request):
        self.session = request.session
        self.cart = self.session.get(settings.CART_SESSION_ID, {})

    # ── Identidade da linha ────────────────────────────────────────────────
    # Uma "linha" do carrinho é única por item + complementos/extras +
    # observações. Assim o mesmo lanche com pão/ponto/complemento diferente
    # aparece como linhas separadas; só somamos a quantidade quando a escolha
    # é idêntica.
    @staticmethod
    def _canonical_options(options):
        """Representação determinística das opções escolhidas (para o hash)."""
        norm = {}
        for gid, val in (options or {}).items():
            if isinstance(val, list):
                norm[str(gid)] = sorted(str(v) for v in val)
            else:
                norm[str(gid)] = str(val)
        return sorted(norm.items())

    @classmethod
    def _line_key(cls, item_id, options, notes):
        """Chave estável: mesmo item + opções + observações → mesma chave."""
        payload = repr((str(item_id), cls._canonical_options(options), (notes or '').strip()))
        return hashlib.sha1(payload.encode('utf-8')).hexdigest()[:16]

    def _entry(self, raw, key):
        """Normaliza um valor salvo → dict item_id/qty/options/notes.

        Tolera o formato legado, em que a chave era o próprio ``item_id`` e o
        valor era um int (qty) ou um dict sem ``item_id``.
        """
        if isinstance(raw, int):
            return {'item_id': str(key), 'qty': raw, 'options': {}, 'notes': ''}
        return {
            'item_id': str(raw.get('item_id', key)),
            'qty': int(raw.get('qty', 0)),
            'options': raw.get('options', {}),
            'notes': raw.get('notes', ''),
        }

    def _all_choice_ids(self):
        """Achata todos os choice IDs de todas as linhas (str e list)."""
        ids = []
        for key, raw in self.cart.items():
            for val in self._entry(raw, key).get('options', {}).values():
                if isinstance(val, list):
                    ids.extend(val)
                else:
                    ids.append(val)
        return ids

    def __iter__(self):
        entries = {key: self._entry(raw, key) for key, raw in self.cart.items()}
        item_ids = {e['item_id'] for e in entries.values()}
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

        for key, entry in entries.items():
            item = item_map.get(entry['item_id'])
            if item is None:
                continue
            quantity = max(int(entry.get('qty', 1)), 1)

            # Build flat list of selected choices (handles both str and list values)
            selected_choices = []
            for val in entry.get('options', {}).values():
                values = val if isinstance(val, list) else [val]
                for cid in values:
                    c = choice_map.get(str(cid))
                    if c:
                        selected_choices.append(c)

            extra = sum(c.extra_price for c in selected_choices)
            unit_price = item.price + extra

            yield {
                'line_id': key,
                'item': item,
                'quantity': quantity,
                'unit_price': unit_price,
                'line_total': unit_price * Decimal(quantity),
                'options': selected_choices,
                'notes': entry.get('notes', ''),
            }

    def __len__(self):
        total = 0
        for key, raw in self.cart.items():
            total += int(self._entry(raw, key).get('qty', 0))
        return total

    @property
    def items(self):
        return list(self)

    @property
    def subtotal(self):
        return sum((entry['line_total'] for entry in self), Decimal('0.00'))

    def add(self, item, quantity=1, options=None, notes=''):
        """Adiciona uma configuração ao carrinho.

        Soma a quantidade quando o item + opções + observações já existem;
        caso contrário cria uma nova linha.
        """
        item_id = str(item.id)
        quantity = max(int(quantity), 1)
        options = options or {}
        notes = notes or ''
        key = self._line_key(item_id, options, notes)

        existing = self.cart.get(key)
        existing_qty = self._entry(existing, key)['qty'] if existing else 0

        self.cart[key] = {
            'item_id': item_id,
            'qty': existing_qty + quantity,
            'options': options,
            'notes': notes,
        }
        self.save()

    def get_line(self, line_id):
        """Entrada normalizada de uma linha (ou ``None`` se não existir)."""
        if line_id not in self.cart:
            return None
        return self._entry(self.cart[line_id], line_id)

    def replace(self, line_id, item, quantity=1, options=None, notes=''):
        """Substitui uma linha por uma nova configuração.

        Remove a linha ``line_id`` e adiciona a nova escolha via :meth:`add`, que
        mescla a quantidade caso a nova configuração coincida com outra linha já
        existente no carrinho.
        """
        self.remove(line_id)
        self.add(item, quantity=quantity, options=options, notes=notes)

    def set_quantity(self, line_id, quantity):
        """Define a quantidade de uma linha específica (remove se <= 0)."""
        if line_id not in self.cart:
            return
        if quantity <= 0:
            del self.cart[line_id]
        else:
            entry = self._entry(self.cart[line_id], line_id)
            self.cart[line_id] = {
                'item_id': entry['item_id'],
                'qty': int(quantity),
                'options': entry['options'],
                'notes': entry['notes'],
            }
        self.save()

    def remove(self, line_id):
        """Remove uma linha específica do carrinho."""
        if line_id in self.cart:
            del self.cart[line_id]
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

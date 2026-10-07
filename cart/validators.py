"""Validação das escolhas de complemento enviadas ao carrinho.

O modal do cardápio já impede escolhas inválidas no navegador, mas o POST é
público — sem esta camada dá para mandar um lanche sem o complemento
obrigatório (ex.: o ponto da carne) ou anexar opções de outro item.
"""

from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError


def is_sellable(item, restaurant):
    return bool(restaurant and restaurant.is_active and item.is_available
                and item.category.is_active and item.category.restaurant_id == restaurant.pk)


def validate_quantity(quantity):
    if not 1 <= quantity <= settings.CART_MAX_QUANTITY:
        raise ValidationError(f'Escolha entre 1 e {settings.CART_MAX_QUANTITY} unidades por item.')


def validate_checkout_cart(cart, restaurant):
    entries = cart.items
    if not restaurant or not restaurant.is_active:
        raise ValidationError('O restaurante não está recebendo pedidos.')
    if not entries or len(entries) != len(cart.cart):
        raise ValidationError('Seu carrinho mudou. Remova os produtos que saíram do cardápio.')
    subtotal = Decimal('0.00')
    for entry in entries:
        item = entry['item']
        if not is_sellable(item, restaurant):
            raise ValidationError(f'{item.name} não está mais disponível. Revise seu carrinho.')
        validate_quantity(entry['quantity'])
        raw = cart.get_line(entry['line_id'])
        options, error = clean_options(item, raw['options'])
        if error or options != raw['options']:
            raise ValidationError(error or f'Os complementos de {item.name} mudaram. Edite o item.')
        if len(entry['notes']) > 300:
            raise ValidationError('Use até 300 caracteres nas observações de cada item.')
        if entry['unit_price'] < 0:
            raise ValidationError('Um produto está com preço inválido. Entre em contato com o restaurante.')
        subtotal += entry['line_total']
    if subtotal > settings.CART_MAX_SUBTOTAL:
        raise ValidationError('O total ultrapassa o limite permitido para um pedido.')
    return entries


def clean_options(item, raw_options):
    """Normaliza as opções escolhidas para um item do cardápio.

    Descarta grupos que não pertencem ao item e escolhas que não pertencem ao
    grupo, mantém uma única escolha nos grupos de escolha única e exige resposta
    nos grupos obrigatórios.

    Recebe o dicionário cru ``{group_id: choice_id | [choice_id, ...]}`` e
    devolve ``(options, erro)`` — ``erro`` é uma mensagem em pt-BR ou ``None``.
    """
    cleaned = {}

    for group in item.complement_groups.prefetch_related('choices'):
        valid_ids = {str(choice.id) for choice in group.choices.all()}
        raw = raw_options.get(str(group.id), [])
        values = raw if isinstance(raw, list) else [raw]
        # dict.fromkeys remove repetições preservando a ordem de escolha.
        chosen = list(dict.fromkeys(str(v) for v in values if str(v) in valid_ids))

        if group.is_single:
            chosen = chosen[:1]

        if not chosen:
            if group.required:
                return {}, f'Escolha uma opção em “{group.name}” para adicionar {item.name}.'
            continue

        cleaned[str(group.id)] = chosen[0] if len(chosen) == 1 else chosen

    return cleaned, None

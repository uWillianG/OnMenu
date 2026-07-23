"""Validação das escolhas de complemento enviadas ao carrinho.

O modal do cardápio já impede escolhas inválidas no navegador, mas o POST é
público — sem esta camada dá para mandar um lanche sem o complemento
obrigatório (ex.: o ponto da carne) ou anexar opções de outro item.
"""


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

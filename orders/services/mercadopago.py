"""Wrapper fino sobre o SDK do Mercado Pago para cobranças Pix.

Expõe duas funções de alto nível usadas pelas views/jobs:

- ``criar_pix(...)``   → cria a cobrança e devolve os dados do QR Code.
- ``buscar_status(id)`` → consulta o status atual de um pagamento.

Quando ``settings.MERCADOPAGO_MOCK`` está ligado (sem access token configurado),
as funções retornam dados falsos para permitir testar a UI sem cobrança real.
"""

from __future__ import annotations

import base64
import logging
import uuid
import hashlib
from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.utils import timezone

logger = logging.getLogger(__name__)


class PixError(Exception):
    """Falha ao criar/consultar uma cobrança Pix no Mercado Pago."""

    def __init__(self, message, *, uncertain=True):
        super().__init__(message)
        self.uncertain = uncertain


def _mock_allowed():
    if settings.MERCADOPAGO_MOCK and not settings.MERCADOPAGO_MOCK_ALLOWED:
        raise PixError('Pagamentos online ainda não foram configurados.', uncertain=False)
    return settings.MERCADOPAGO_MOCK


def _request_options(key):
    from mercadopago.config import RequestOptions
    options = RequestOptions(connection_timeout=15.0, max_retries=0)
    options.custom_headers = {'x-idempotency-key': str(key)}
    return options


# PNG 1x1 transparente — placeholder de QR Code usado no modo mock.
_MOCK_QR_PNG_BASE64 = (
    'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk'
    'YPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=='
)


def _sdk():
    """Instancia o SDK do Mercado Pago. Levanta PixError se faltar token."""
    if not settings.MERCADOPAGO_ACCESS_TOKEN:
        raise PixError('MERCADOPAGO_ACCESS_TOKEN não configurado.')
    import mercadopago  # import tardio: dependência só necessária fora do mock

    from mercadopago.config import RequestOptions
    return mercadopago.SDK(settings.MERCADOPAGO_ACCESS_TOKEN,
        request_options=RequestOptions(connection_timeout=15.0, max_retries=0))


def _mock_criar_pix(amount, external_reference, expiration_minutes):
    expires_at = timezone.now() + timedelta(minutes=expiration_minutes)
    fake_id = f'MOCK-{uuid.uuid4().hex[:12]}'
    copia_cola = (
        '00020126360014BR.GOV.BCB.PIX0114+5500000000000'
        f'5204000053039865802BR5909OnMenu6009SAO PAULO62070503***6304MOCK'
    )
    return {
        'id': fake_id,
        'status': 'pending',
        'qr_code_text': copia_cola,
        'qr_code_base64': _MOCK_QR_PNG_BASE64,
        'txid': external_reference,
        'expires_at': expires_at,
    }


def criar_pix(
    *,
    amount: Decimal,
    description: str,
    payer_email: str,
    payer_name: str = '',
    payer_cpf: str = '',
    external_reference: str,
    expiration_minutes: int | None = None,
    idempotency_key: str | None = None,
) -> dict:
    """Cria uma cobrança Pix e devolve os dados normalizados do QR Code.

    Retorna ``{id, status, qr_code_text, qr_code_base64, txid, expires_at}``.
    """
    if expiration_minutes is None:
        expiration_minutes = settings.PIX_EXPIRATION_MINUTES

    if _mock_allowed():
        return _mock_criar_pix(amount, external_reference, expiration_minutes)

    expires_at = timezone.now() + timedelta(minutes=expiration_minutes)
    first_name, _, last_name = (payer_name or '').partition(' ')
    cpf = ''.join(ch for ch in (payer_cpf or '') if ch.isdigit())

    payer: dict = {'email': payer_email}
    if first_name:
        payer['first_name'] = first_name
    if last_name:
        payer['last_name'] = last_name
    if cpf:
        payer['identification'] = {'type': 'CPF', 'number': cpf}

    payload = {
        'transaction_amount': float(amount),
        'description': description,
        'payment_method_id': 'pix',
        'external_reference': external_reference,
        'date_of_expiration': expires_at.isoformat(),
        'payer': payer,
    }

    # Chave de idempotência: evita cobrança duplicada em retries de rede.
    request_options = _request_options(idempotency_key or external_reference)

    try:
        if request_options is not None:
            result = _sdk().payment().create(payload, request_options)
        else:
            result = _sdk().payment().create(payload)
    except Exception as exc:  # noqa: BLE001 - normaliza qualquer erro do SDK
        raise PixError('Não foi possível confirmar a criação do Pix.') from exc

    if result.get('status') not in (200, 201):
        logger.warning('Mercado Pago recusou Pix: HTTP %s', result.get('status'))
        raise PixError('Não foi possível iniciar o Pix. Confira os dados ou tente outro meio.',
                       uncertain=int(result.get('status') or 500) >= 500)

    data = result['response']
    tx = (data.get('point_of_interaction') or {}).get('transaction_data') or {}
    exp_raw = data.get('date_of_expiration')
    return {
        'id': str(data.get('id', '')),
        'status': data.get('status', 'pending'),
        'qr_code_text': tx.get('qr_code', ''),
        'qr_code_base64': tx.get('qr_code_base64', ''),
        'txid': tx.get('ticket_url', '') or external_reference,
        'expires_at': _parse_dt(exp_raw) or expires_at,
    }


def criar_pagamento_cartao(
    *,
    amount: Decimal,
    description: str,
    token: str,
    installments: int = 1,
    payment_method_id: str = '',
    issuer_id: str = '',
    payer_email: str = '',
    payer_cpf: str = '',
    external_reference: str,
    idempotency_key: str | None = None,
) -> dict:
    """Cria um pagamento com cartão a partir do token gerado no browser.

    Retorna ``{id, status, status_detail, installments, payment_method_id,
    last_four, requires_action, redirect_url}``.
    """
    if _mock_allowed():
        return _mock_pagamento_cartao(token, installments, payment_method_id)

    cpf = ''.join(ch for ch in (payer_cpf or '') if ch.isdigit())
    payer: dict = {'email': payer_email or settings.PIX_DEFAULT_PAYER_EMAIL}
    if cpf:
        payer['identification'] = {'type': 'CPF', 'number': cpf}

    payload = {
        'transaction_amount': float(amount),
        'description': description,
        'token': token,
        'installments': int(installments or 1),
        'payment_method_id': payment_method_id,
        'external_reference': external_reference,
        'three_d_secure_mode': 'optional',
        'payer': payer,
    }
    if issuer_id:
        payload['issuer_id'] = issuer_id

    # Idempotência por tentativa: permite re-tentar após uma recusa (novo token).
    # Compatibilidade para chamadores diretos: o mesmo token/referência tem a
    # mesma chave. A aplicação fornece a UUID persistida da tentativa.
    stable_key = hashlib.sha256(f'{external_reference}:{token}'.encode()).hexdigest()
    request_options = _request_options(idempotency_key or stable_key)

    try:
        if request_options is not None:
            result = _sdk().payment().create(payload, request_options)
        else:
            result = _sdk().payment().create(payload)
    except Exception as exc:  # noqa: BLE001
        raise PixError('Não foi possível confirmar o pagamento com cartão.') from exc

    if result.get('status') not in (200, 201):
        logger.warning('Mercado Pago recusou cartão: HTTP %s', result.get('status'))
        raise PixError('Não foi possível iniciar o pagamento. Confira os dados ou tente outro meio.',
                       uncertain=int(result.get('status') or 500) >= 500)

    return _normalize_card_response(result['response'])


def _normalize_card_response(data: dict) -> dict:
    card = data.get('card') or {}
    three_ds = data.get('three_ds_info') or {}
    redirect_url = three_ds.get('external_resource_url', '')
    status = data.get('status')
    return {
        'id': str(data.get('id', '')),
        'status': status,
        'status_detail': data.get('status_detail', ''),
        'installments': data.get('installments', 1) or 1,
        'payment_method_id': data.get('payment_method_id', ''),
        'last_four': card.get('last_four_digits', '') or '',
        'requires_action': bool(redirect_url) and status == 'pending',
        'redirect_url': redirect_url,
        'creq': three_ds.get('creq', ''),
    }


def _mock_pagamento_cartao(token, installments, payment_method_id):
    rejected = (token or '').upper().startswith('MOCK-REJECT')
    challenge = (token or '').upper() == 'MOCK-3DS'
    return {
        'id': f'MOCK-{uuid.uuid4().hex[:12]}',
        'status': 'pending' if challenge else ('rejected' if rejected else 'approved'),
        'status_detail': 'cc_rejected_other_reason' if rejected else 'accredited',
        'installments': int(installments or 1),
        'payment_method_id': payment_method_id or 'visa',
        'last_four': '1234',
        'requires_action': challenge,
        'redirect_url': 'https://example.invalid/challenge' if challenge else '',
        'creq': 'TEST-CREQ' if challenge else '',
    }


def reembolsar(mp_payment_id: str, amount: Decimal | None = None) -> dict:
    """Estorna um pagamento usando a identidade persistida da solicitação."""
    if _mock_allowed():
        return {'id': mp_payment_id, 'status': 'refunded'}

    body = {} if amount is None else {'amount': float(amount)}
    try:
        result = _sdk().refund().create(mp_payment_id, body,
            _request_options(f'refund-{mp_payment_id}-{amount or "full"}'))
    except Exception as exc:  # noqa: BLE001
        raise PixError('Não foi possível confirmar o estorno.') from exc

    if result.get('status') not in (200, 201):
        raise PixError('O provedor não confirmou o estorno. Verifique o pagamento na conta do estabelecimento.')
    return result['response']


def buscar_status(mp_payment_id: str) -> dict:
    """Consulta o status de um pagamento.

    Retorna ``{id, status, status_detail, external_reference}``. No modo mock devolve
    ``status=None`` para o chamador manter o status do banco.
    """
    if settings.MERCADOPAGO_MOCK:
        return {'id': mp_payment_id, 'status': None, 'status_detail': '', 'external_reference': None}

    try:
        result = _sdk().payment().get(mp_payment_id)
    except Exception as exc:  # noqa: BLE001
        raise PixError('Não foi possível consultar o pagamento.') from exc

    if result.get('status') != 200:
        raise PixError(f'Pagamento {mp_payment_id} não encontrado no Mercado Pago.')

    data = result['response']
    return {
        'id': str(data.get('id', '')),
        'status': data.get('status'),
        'status_detail': data.get('status_detail', ''),
        'external_reference': data.get('external_reference'),
        'amount': str(data.get('transaction_amount', '')),
        'currency_id': data.get('currency_id'),
        'payment_method_id': data.get('payment_method_id'),
        'payment_type_id': data.get('payment_type_id'),
        'refund_amount': str(data.get('transaction_amount_refunded', '0')),
    }


def buscar_por_referencia(external_reference):
    """Recupera resposta perdida sem solicitar uma nova cobrança."""
    if settings.MERCADOPAGO_MOCK:
        return []
    try:
        result = _sdk().payment().search({'external_reference': external_reference,
                                        'sort': 'date_created', 'criteria': 'desc'})
    except Exception as exc:
        raise PixError('Não foi possível consultar o pagamento.') from exc
    if result.get('status') != 200:
        raise PixError('Não foi possível consultar o pagamento.')
    return (result.get('response') or {}).get('results') or []


def cancelar(mp_payment_id):
    """Cancela uma cobrança pendente antes de liberar outra tentativa."""
    if _mock_allowed():
        return {'id': mp_payment_id, 'status': 'cancelled'}
    try:
        result = _sdk().payment().update(mp_payment_id, {'status': 'cancelled'},
                                        _request_options(f'cancel-{mp_payment_id}'))
    except Exception as exc:
        raise PixError('Não foi possível confirmar o cancelamento da cobrança.') from exc
    if result.get('status') != 200:
        return buscar_status(mp_payment_id)
    return {'id': str(mp_payment_id), 'status': result['response'].get('status')}


def _parse_dt(value):
    if not value:
        return None
    from django.utils.dateparse import parse_datetime

    try:
        return parse_datetime(value)
    except (ValueError, TypeError):
        return None

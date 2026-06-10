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
from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.utils import timezone

logger = logging.getLogger(__name__)


class PixError(Exception):
    """Falha ao criar/consultar uma cobrança Pix no Mercado Pago."""


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

    return mercadopago.SDK(settings.MERCADOPAGO_ACCESS_TOKEN)


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
) -> dict:
    """Cria uma cobrança Pix e devolve os dados normalizados do QR Code.

    Retorna ``{id, status, qr_code_text, qr_code_base64, txid, expires_at}``.
    """
    if expiration_minutes is None:
        expiration_minutes = settings.PIX_EXPIRATION_MINUTES

    if settings.MERCADOPAGO_MOCK:
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
    request_options = None
    try:
        from mercadopago.config import RequestOptions

        request_options = RequestOptions()
        request_options.custom_headers = {'x-idempotency-key': external_reference}
    except Exception:  # pragma: no cover - versões antigas do SDK
        request_options = None

    try:
        if request_options is not None:
            result = _sdk().payment().create(payload, request_options)
        else:
            result = _sdk().payment().create(payload)
    except Exception as exc:  # noqa: BLE001 - normaliza qualquer erro do SDK
        raise PixError(f'Erro ao criar cobrança Pix: {exc}') from exc

    if result.get('status') not in (200, 201):
        raise PixError(f'Mercado Pago recusou a cobrança: {result.get("response")}')

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
) -> dict:
    """Cria um pagamento com cartão a partir do token gerado no browser.

    Retorna ``{id, status, status_detail, installments, payment_method_id,
    last_four, requires_action, redirect_url}``.
    """
    if settings.MERCADOPAGO_MOCK:
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
    request_options = None
    try:
        from mercadopago.config import RequestOptions

        request_options = RequestOptions()
        request_options.custom_headers = {'x-idempotency-key': uuid.uuid4().hex}
    except Exception:  # pragma: no cover - versões antigas do SDK
        request_options = None

    try:
        if request_options is not None:
            result = _sdk().payment().create(payload, request_options)
        else:
            result = _sdk().payment().create(payload)
    except Exception as exc:  # noqa: BLE001
        raise PixError(f'Erro ao processar o cartão: {exc}') from exc

    if result.get('status') not in (200, 201):
        raise PixError(f'Mercado Pago recusou o pagamento: {result.get("response")}')

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
    }


def _mock_pagamento_cartao(token, installments, payment_method_id):
    rejected = (token or '').upper() == 'MOCK-REJECT'
    return {
        'id': f'MOCK-{uuid.uuid4().hex[:12]}',
        'status': 'rejected' if rejected else 'approved',
        'status_detail': 'cc_rejected_other_reason' if rejected else 'accredited',
        'installments': int(installments or 1),
        'payment_method_id': payment_method_id or 'visa',
        'last_four': '1234',
        'requires_action': False,
        'redirect_url': '',
    }


def reembolsar(mp_payment_id: str, amount: Decimal | None = None) -> dict:
    """Estorna (total ou parcial) um pagamento. Gancho — não exposto na UI ainda."""
    if settings.MERCADOPAGO_MOCK:
        return {'id': mp_payment_id, 'status': 'refunded'}

    body = {} if amount is None else {'amount': float(amount)}
    try:
        result = _sdk().refund().create(mp_payment_id, body)
    except Exception as exc:  # noqa: BLE001
        raise PixError(f'Erro ao estornar pagamento {mp_payment_id}: {exc}') from exc

    if result.get('status') not in (200, 201):
        raise PixError(f'Mercado Pago recusou o estorno: {result.get("response")}')
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
        raise PixError(f'Erro ao consultar pagamento {mp_payment_id}: {exc}') from exc

    if result.get('status') != 200:
        raise PixError(f'Pagamento {mp_payment_id} não encontrado no Mercado Pago.')

    data = result['response']
    return {
        'id': str(data.get('id', '')),
        'status': data.get('status'),
        'status_detail': data.get('status_detail', ''),
        'external_reference': data.get('external_reference'),
    }


def _parse_dt(value):
    if not value:
        return None
    from django.utils.dateparse import parse_datetime

    try:
        return parse_datetime(value)
    except (ValueError, TypeError):
        return None

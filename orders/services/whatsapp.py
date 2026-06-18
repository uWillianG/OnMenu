"""Envio de mensagens via WhatsApp Cloud API (Meta).

Sem ``WHATSAPP_TOKEN``/``WHATSAPP_PHONE_ID`` configurados, roda em modo mock: a
mensagem é apenas registrada no log, sem envio real — análogo ao modo mock do
Mercado Pago. Assim a feature funciona em desenvolvimento sem credenciais.

Observação de produção: a API da Meta só permite **texto livre** dentro da
janela de atendimento de 24h. Mensagens iniciadas pela empresa fora dessa janela
exigem *templates* aprovados — adapte ``enviar_texto`` para ``type: template``
nesse caso.
"""

from __future__ import annotations

import logging
from urllib.parse import quote

from django.conf import settings

logger = logging.getLogger(__name__)


class WhatsAppError(Exception):
    """Falha ao enviar mensagem pela WhatsApp Cloud API."""


def formatar_numero(phone: str) -> str:
    """Normaliza o telefone para o formato internacional (somente dígitos).

    Adiciona o DDI padrão (``WHATSAPP_DEFAULT_COUNTRY_CODE``) quando o número
    parece local (até 11 dígitos, padrão brasileiro DDD + número).
    """
    digits = ''.join(ch for ch in (phone or '') if ch.isdigit())
    if not digits:
        return ''
    cc = settings.WHATSAPP_DEFAULT_COUNTRY_CODE
    if cc and not digits.startswith(cc) and len(digits) <= 11:
        digits = f'{cc}{digits}'
    return digits


def montar_link_wame(phone: str, mensagem: str) -> str:
    """Link clique-para-conversar (wa.me) com a mensagem pré-preenchida."""
    numero = formatar_numero(phone)
    if not numero:
        return ''
    return f'https://wa.me/{numero}?text={quote(mensagem)}'


def enviar_texto(phone: str, mensagem: str) -> dict:
    """Envia uma mensagem de texto ao cliente.

    Em modo mock (sem credenciais) apenas registra no log e retorna. Em produção
    chama a Graph API. Levanta ``WhatsAppError`` em falhas de rede/da API.
    """
    numero = formatar_numero(phone)
    if not numero:
        logger.warning('WhatsApp: telefone vazio/inválido — mensagem não enviada.')
        return {'ok': False, 'reason': 'no_phone'}

    if settings.WHATSAPP_MOCK:
        logger.info('WhatsApp (mock) → %s: %s', numero, mensagem)
        return {'ok': True, 'mock': True}

    try:
        import requests
    except ImportError as exc:  # pragma: no cover - requests é dependência instalada
        raise WhatsAppError('Biblioteca requests indisponível.') from exc

    url = (
        f'https://graph.facebook.com/{settings.WHATSAPP_API_VERSION}'
        f'/{settings.WHATSAPP_PHONE_ID}/messages'
    )
    payload = {
        'messaging_product': 'whatsapp',
        'to': numero,
        'type': 'text',
        'text': {'body': mensagem},
    }
    headers = {
        'Authorization': f'Bearer {settings.WHATSAPP_TOKEN}',
        'Content-Type': 'application/json',
    }
    try:
        resp = requests.post(url, json=payload, headers=headers, timeout=10)
    except requests.RequestException as exc:
        raise WhatsAppError(str(exc)) from exc

    if resp.status_code >= 400:
        logger.warning('WhatsApp API %s: %s', resp.status_code, resp.text[:300])
        raise WhatsAppError(f'WhatsApp API status {resp.status_code}')
    return {'ok': True, 'mock': False, 'response': resp.json()}

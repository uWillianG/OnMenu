"""Login social com Google via OAuth2 (authorization-code flow).

Fluxo em três passos: (1) redireciona o cliente para o Google com um ``state``
anti-CSRF; (2) o Google volta com um ``code``, que trocamos por um access token
no endpoint de token (usando o client secret, servidor-a-servidor); (3) com o
token buscamos o perfil (nome + e-mail verificado) no endpoint de userinfo.

Sem ``GOOGLE_OAUTH_CLIENT_ID``/``GOOGLE_OAUTH_CLIENT_SECRET`` configurados, o
recurso fica desligado (``settings.GOOGLE_OAUTH_ENABLED = False``) e o botão não
é exibido — análogo ao modo mock dos outros serviços externos.
"""

from __future__ import annotations

import logging
from urllib.parse import urlencode

import requests
from django.conf import settings
from django.urls import reverse

logger = logging.getLogger(__name__)

AUTH_ENDPOINT = 'https://accounts.google.com/o/oauth2/v2/auth'
TOKEN_ENDPOINT = 'https://oauth2.googleapis.com/token'
USERINFO_ENDPOINT = 'https://openidconnect.googleapis.com/v1/userinfo'
SCOPE = 'openid email profile'


class GoogleOAuthError(Exception):
    """Falha em alguma etapa do fluxo OAuth2 com o Google."""


def get_redirect_uri(request) -> str:
    """URL de callback absoluta — deve casar com o console do Google."""
    return request.build_absolute_uri(reverse('accounts:google_callback'))


def build_authorization_url(request, state: str) -> str:
    """Monta a URL para onde o cliente é redirecionado no início do fluxo."""
    params = {
        'client_id': settings.GOOGLE_OAUTH_CLIENT_ID,
        'redirect_uri': get_redirect_uri(request),
        'response_type': 'code',
        'scope': SCOPE,
        'state': state,
        'access_type': 'online',
        'prompt': 'select_account',
    }
    return f'{AUTH_ENDPOINT}?{urlencode(params)}'


def exchange_code(request, code: str) -> dict:
    """Troca o ``code`` recebido no callback por tokens de acesso."""
    data = {
        'code': code,
        'client_id': settings.GOOGLE_OAUTH_CLIENT_ID,
        'client_secret': settings.GOOGLE_OAUTH_CLIENT_SECRET,
        'redirect_uri': get_redirect_uri(request),
        'grant_type': 'authorization_code',
    }
    try:
        resp = requests.post(TOKEN_ENDPOINT, data=data, timeout=10)
    except requests.RequestException as exc:
        raise GoogleOAuthError(str(exc)) from exc

    if resp.status_code >= 400:
        logger.warning('Google token %s: %s', resp.status_code, resp.text[:300])
        raise GoogleOAuthError(f'Token endpoint status {resp.status_code}')
    return resp.json()


def fetch_userinfo(access_token: str) -> dict:
    """Busca o perfil do usuário (nome, e-mail, email_verified) no Google."""
    headers = {'Authorization': f'Bearer {access_token}'}
    try:
        resp = requests.get(USERINFO_ENDPOINT, headers=headers, timeout=10)
    except requests.RequestException as exc:
        raise GoogleOAuthError(str(exc)) from exc

    if resp.status_code >= 400:
        logger.warning('Google userinfo %s: %s', resp.status_code, resp.text[:300])
        raise GoogleOAuthError(f'Userinfo endpoint status {resp.status_code}')
    return resp.json()

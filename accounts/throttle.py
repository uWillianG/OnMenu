"""Limite de tentativas (janela deslizante) para conter força bruta e spam nas
telas sensíveis: login, cadastro, recuperação de senha e checkout.

Sem dependência externa nem cache compartilhado: uma linha em ``AccessAttempt``
por tentativa. Isso funciona igual entre vários workers do gunicorn — o cache em
memória padrão do Django seria por processo e multiplicaria o teto pelo número
de workers, justamente o que não se quer num limite de segurança.

Uso típico numa view::

    ip = throttle.client_ip(request)
    if throttle.is_blocked(throttle.LOGIN, ip):
        ...  # recusa com throttle.retry_message(throttle.LOGIN)
    # depois de uma tentativa que falhou:
    throttle.record(throttle.LOGIN, ip)
    # depois de um sucesso:
    throttle.clear(throttle.LOGIN, ip)

Desligue tudo com ``DJANGO_RATELIMIT_ENABLED=False`` (settings.RATELIMIT_ENABLED).
"""

from datetime import timedelta

from django.conf import settings
from django.utils import timezone

from .models import AccessAttempt

# Escopos (o valor vai para AccessAttempt.scope; mantenha curto, cabe em 32).
LOGIN = 'login'            # por IP
LOGIN_USER = 'login_user'  # por identificador digitado (e-mail/usuário)
SIGNUP = 'signup'
PASSWORD_RESET = 'password_reset'
CHECKOUT = 'checkout'

# (máximo de tentativas, janela em segundos). Tetos folgados para o uso legítimo
# e apertados o bastante para inviabilizar varredura automatizada.
RULES = {
    LOGIN: (8, 5 * 60),           # 8 falhas por IP em 5 min
    LOGIN_USER: (8, 15 * 60),     # 8 falhas contra a mesma conta em 15 min
    SIGNUP: (5, 60 * 60),         # 5 cadastros por IP em 1 h
    PASSWORD_RESET: (5, 60 * 60),  # 5 pedidos de recuperação por IP em 1 h
    CHECKOUT: (30, 10 * 60),      # 30 finalizações por IP em 10 min
}


def _enabled():
    return getattr(settings, 'RATELIMIT_ENABLED', True)


def client_ip(request):
    """IP do cliente para servir de chave.

    Atrás de proxy/CDN o ``REMOTE_ADDR`` é o do proxy; nesse caso usamos o
    primeiro salto do ``X-Forwarded-For``. Só confiamos nesse cabeçalho quando
    ``RATELIMIT_TRUST_FORWARDED`` está ligado (junto com ``DJANGO_BEHIND_PROXY``),
    porque fora de um proxy confiável ele é forjável.
    """
    if getattr(settings, 'RATELIMIT_TRUST_FORWARDED', False):
        forwarded = request.META.get('HTTP_X_FORWARDED_FOR', '')
        if forwarded:
            first = forwarded.split(',')[0].strip()
            if first:
                return first[:128]
    return (request.META.get('REMOTE_ADDR') or 'desconhecido')[:128]


def is_blocked(scope, key):
    """True se ``(scope, key)`` já atingiu o teto dentro da janela."""
    if not _enabled() or not key:
        return False
    limit, window = RULES[scope]
    since = timezone.now() - timedelta(seconds=window)
    count = AccessAttempt.objects.filter(
        scope=scope, key=key, created_at__gte=since,
    ).count()
    return count >= limit


def record(scope, key):
    """Registra uma tentativa e limpa as que já saíram da janela dessa chave."""
    if not _enabled() or not key:
        return
    AccessAttempt.objects.create(scope=scope, key=str(key)[:128])
    _, window = RULES[scope]
    cutoff = timezone.now() - timedelta(seconds=window)
    AccessAttempt.objects.filter(
        scope=scope, key=str(key)[:128], created_at__lt=cutoff,
    ).delete()


def clear(scope, key):
    """Zera o contador de ``(scope, key)`` (ex.: após um login bem-sucedido)."""
    if not key:
        return
    AccessAttempt.objects.filter(scope=scope, key=str(key)[:128]).delete()


def retry_message(scope):
    """Mensagem pt-BR pedindo para aguardar, com a espera aproximada."""
    _, window = RULES[scope]
    minutes = max(1, round(window / 60))
    return (
        f'Muitas tentativas em pouco tempo. Aguarde cerca de {minutes} '
        f'{"minuto" if minutes == 1 else "minutos"} e tente novamente.'
    )

"""Checagens de deploy do app de pedidos (``manage.py check --deploy``)."""

from django.conf import settings
from django.core.checks import Tags, Warning, register


@register(Tags.security, deploy=True)
def check_mercadopago_webhook_secret(app_configs, **kwargs):
    """Com pagamento real ligado, o webhook precisa de assinatura verificável."""
    if not settings.MERCADOPAGO_ACCESS_TOKEN or settings.MERCADOPAGO_WEBHOOK_SECRET:
        return []
    return [
        Warning(
            'MERCADOPAGO_ACCESS_TOKEN está configurado, mas '
            'MERCADOPAGO_WEBHOOK_SECRET não.',
            hint=(
                'Sem o secret os webhooks do Mercado Pago são recusados e os '
                'pedidos só saem de "pendente" pelos comandos sync_pending_pix / '
                'sync_pending_card. Copie o secret no painel do Mercado Pago '
                '(Suas integrações → Webhooks) para o .env.'
            ),
            id='orders.W001',
        ),
    ]


@register(Tags.security, deploy=True)
def check_base_url(app_configs, **kwargs):
    """A BASE_URL é o que o Mercado Pago e o Google OAuth usam para voltar."""
    if settings.BASE_URL.startswith('https://'):
        return []
    return [
        Warning(
            f'BASE_URL={settings.BASE_URL!r} não é HTTPS.',
            hint=(
                'É o endereço usado no retorno do 3DS e no callback do Google '
                'OAuth. Aponte para a URL pública do site (https://…).'
            ),
            id='orders.W002',
        ),
    ]

"""Checagens de deploy do app de pedidos (``manage.py check --deploy``)."""

from django.conf import settings
from django.core.checks import Error, Tags, Warning, register
from django.core.checks.security.base import check_secret_key, check_secret_key_fallbacks


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


@register(Tags.security, deploy=True)
def check_payment_readiness(app_configs, **kwargs):
    if settings.DEBUG:
        return []
    errors = []
    if settings.MERCADOPAGO_MOCK_ALLOWED:
        errors.append(Error('Simulação de pagamentos não pode ser habilitada em produção.', id='orders.E001'))
    if settings.PAYMENT_PIX_ENABLED or settings.PAYMENT_CARD_ENABLED:
        if not settings.MERCADOPAGO_ACCESS_TOKEN or not settings.MERCADOPAGO_WEBHOOK_SECRET:
            errors.append(Error('Preencha as credenciais e o secret do Mercado Pago ou desative os meios online.', id='orders.E002'))
        if settings.PAYMENT_CARD_ENABLED and not settings.MERCADOPAGO_PUBLIC_KEY:
            errors.append(Error('Cartão online exige MERCADOPAGO_PUBLIC_KEY.', id='orders.E003'))
    if settings.EMAIL_BACKEND.rsplit('.', 2)[-2] in ('console', 'locmem', 'dummy', 'filebased'):
        errors.append(Error('Configure o envio real de e-mails para a recuperação de senha.', id='accounts.E001'))
    if not settings.WHATSAPP_MOCK and (not settings.WHATSAPP_APP_SECRET or not settings.WHATSAPP_WEBHOOK_VERIFY_TOKEN):
        errors.append(Error('Preencha o secret do aplicativo e o token de verificação do webhook WhatsApp.', id='orders.E004'))
    return errors


@register(Tags.security, deploy=True)
def check_production_secret(app_configs, **kwargs):
    if settings.DEBUG:
        return []
    if check_secret_key(app_configs) or check_secret_key_fallbacks(app_configs):
        return [Error('A chave privada de produção precisa ser substituída.',
            hint='No Docker, deixe DJANGO_SECRET_KEY vazio para gerar a chave persistente da instalação. '
                 'Em outra hospedagem, configure uma chave forte e exclusiva; revise também SECRET_KEY_FALLBACKS.',
            id='config.E001')]
    return []

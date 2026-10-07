from datetime import timedelta
from email.utils import parseaddr

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.utils import timezone
from menu.models import MenuItem
from menu.selectors import get_current_restaurant
from orders.models import Neighborhood
from orders.services.payments import enabled_methods
from config.models import OperationStatus
from config.management.commands.run_operations import TASKS


def _email_configured():
    try:
        validate_email(parseaddr(settings.DEFAULT_FROM_EMAIL)[1])
    except (ValidationError, ValueError):
        return False
    return bool(settings.EMAIL_BACKEND.endswith('smtp.EmailBackend') and settings.EMAIL_HOST
        and bool(settings.EMAIL_HOST_USER) == bool(settings.EMAIL_HOST_PASSWORD))


def _operations_running():
    now = timezone.now()
    operations = {job.name:job for job in OperationStatus.objects.all()}
    return all(name in operations and not operations[name].last_error
        and operations[name].last_success_at
        and operations[name].last_success_at >= now-timedelta(seconds=max(interval*2, 300))
        for name, interval in TASKS.items())


def launch_requirements():
    restaurant = get_current_restaurant()
    hours = list(restaurant.business_hours.all()) if restaurant else []
    complete_hours = ({day.day_of_week for day in hours} == set(range(7)) and all(
        day.is_closed or day.open_time is not None and day.close_time is not None for day in hours))
    checks = [
        ('Estabelecimento cadastrado', bool(restaurant)),
        ('Nome do responsável / razão social', bool(restaurant and restaurant.legal_name)),
        ('E-mail de atendimento e privacidade', bool(restaurant and restaurant.contact_email)),
        ('Endereço do estabelecimento', bool(restaurant and restaurant.address)),
        ('Telefone de atendimento', bool(restaurant and restaurant.phone)),
        ('Produtos disponíveis', bool(restaurant and MenuItem.objects.filter(category__restaurant=restaurant,
            category__is_active=True, is_available=True).exists())),
        ('Horários preenchidos nos sete dias', complete_hours),
        ('Entrega ou retirada habilitada', bool(restaurant and (restaurant.accepts_delivery or restaurant.accepts_pickup))),
        ('Regiões para entrega', bool(restaurant and (not restaurant.accepts_delivery or
            Neighborhood.objects.filter(is_active=True, city__is_active=True).exists()))),
        ('Envio real de e-mail configurado', _email_configured()),
        ('Pix configurado ou desativado', not settings.PAYMENT_PIX_ENABLED or 'pix' in enabled_methods() and not settings.MERCADOPAGO_MOCK),
        ('Cartão online configurado ou desativado', not settings.PAYMENT_CARD_ENABLED or 'credit_card' in enabled_methods() and not settings.MERCADOPAGO_MOCK),
        ('HTTPS e modo de produção', not settings.DEBUG and settings.BASE_URL.startswith('https://')),
        ('Destino de backup externo', bool(settings.BACKUP_BUCKET and settings.BACKUP_ACCESS_KEY and settings.BACKUP_SECRET_KEY)),
        ('Operação automática executando sem falhas recentes', _operations_running()),
    ]
    return [{'label':label, 'ok':ok} for label,ok in checks]

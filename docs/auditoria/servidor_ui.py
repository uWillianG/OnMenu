"""Servidor local descartavel da auditoria. Banco em memoria e dados ficticios."""
import logging
import os
import sys
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))
os.environ.update({
    'DJANGO_SETTINGS_MODULE': 'config.settings', 'DATABASE_URL': 'sqlite:///:memory:',
    'DJANGO_DEBUG': 'True', 'DJANGO_ALLOWED_HOSTS': 'localhost,127.0.0.1',
    'DJANGO_RATELIMIT_ENABLED': 'False', 'MERCADOPAGO_ACCESS_TOKEN': '',
    'MERCADOPAGO_PUBLIC_KEY': 'TEST-AUDIT-PUBLIC-KEY', 'MERCADOPAGO_WEBHOOK_SECRET': '',
    'WHATSAPP_TOKEN': '', 'WHATSAPP_PHONE_ID': '',
    'GOOGLE_OAUTH_CLIENT_ID': '', 'GOOGLE_OAUTH_CLIENT_SECRET': '',
    'EMAIL_BACKEND': 'django.core.mail.backends.locmem.EmailBackend',
})
import django
django.setup()
from django.contrib.auth import get_user_model
from django.contrib.sessions.models import Session
from django.core.management import call_command
from django.db import connection
from django.test import Client
from menu.models import Category, ComplementChoice, ComplementGroup, MenuItem, Restaurant
from orders.models import City, Neighborhood

logging.disable(logging.INFO)
assert connection.vendor == 'sqlite' and connection.is_in_memory_db()
call_command('migrate', verbosity=0)
restaurant = Restaurant.objects.create(name='Cozinha da Auditoria', address='Rua Ficticia, 10',
    phone='11999998888', whatsapp_number='5511999998888')
category = Category.objects.create(restaurant=restaurant, name='Lanches')
item = MenuItem.objects.create(category=category, name='Lanche de teste',
    description='Produto ficticio para validar o fluxo sem realizar cobrancas.',
    price=Decimal('20'), is_featured=True)
group = ComplementGroup.objects.create(restaurant=restaurant, name='Ponto da carne', required=True)
choice = ComplementChoice.objects.create(group=group, name='Ao ponto')
item.complement_groups.add(group)
city = City.objects.create(name='Cidade Ficticia', delivery_fee=Decimal('2'))
Neighborhood.objects.create(city=city, name='Centro', delivery_fee=Decimal('3'))
user = get_user_model().objects.create_user(username='auditoria', email='teste@example.invalid',
    password='Auditoria@123', first_name='Cliente', last_name='Ficticio', is_staff=True, is_superuser=True)
user.profile.phone = '11999998888'
user.profile.save()
client = Client()
client.force_login(user)
runtime_dir = ROOT / '.audit-tmp'
runtime_dir.mkdir(exist_ok=True)
(runtime_dir / 'browser-session.txt').write_text(client.session.session_key, encoding='utf-8')
print('AUDIT_SERVER_READY', flush=True)
call_command('runserver', '127.0.0.1:8765', use_reloader=False, use_threading=False)

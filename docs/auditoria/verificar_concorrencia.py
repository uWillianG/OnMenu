"""Duas conexões/threads reais em banco descartável; provedor financeiro simulado."""
import json
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))
runtime = ROOT / '.audit-tmp'
runtime.mkdir(exist_ok=True)
database = runtime / f'concurrency-{os.getpid()}.sqlite3'
audit_database_url = os.environ.get('ONMENU_AUDIT_DATABASE_URL', '')
if audit_database_url:
    assert audit_database_url == 'postgresql://audit:audit@onmenu-audit-postgres:5432/onmenu_audit_concurrency'
os.environ.update({'DJANGO_SETTINGS_MODULE':'config.settings', 'DATABASE_URL':audit_database_url,
    'DJANGO_SQLITE_PATH':str(database), 'DJANGO_DEBUG':'True', 'DJANGO_ALLOWED_HOSTS':'testserver',
    'MERCADOPAGO_ACCESS_TOKEN':'', 'MERCADOPAGO_PUBLIC_KEY':'', 'MERCADOPAGO_WEBHOOK_SECRET':'',
    'WHATSAPP_TOKEN':'', 'WHATSAPP_PHONE_ID':'', 'GOOGLE_OAUTH_CLIENT_ID':'',
    'GOOGLE_OAUTH_CLIENT_SECRET':'', 'EMAIL_BACKEND':'django.core.mail.backends.locmem.EmailBackend'})
import django
django.setup()
from django.core.management import call_command
from django.db import close_old_connections
from django.test import Client
from menu.models import Category, MenuItem, Restaurant
from orders.models import Order, PaymentAttempt
from orders.services import payments

call_command('migrate', verbosity=0, interactive=False)
restaurant = Restaurant.objects.create(name='Concorrência')
category = Category.objects.create(restaurant=restaurant, name='Lanches')
item = MenuItem.objects.create(category=category, name='Lanche', price=20)
initial = Client()
initial.post(f'/cart/add/{item.pk}/', {'quantity':1})
session_key = initial.session.session_key
token = initial.session['checkout_token']
data = {'fulfillment_method':'pickup', 'payment_method':'credit_card', 'customer_name':'Cliente Teste',
        'phone':'11999998888', 'checkout_token':token, 'expected_total':'20.00'}
barrier = threading.Barrier(2)

def checkout():
    close_old_connections()
    client = Client()
    client.cookies['sessionid'] = session_key
    barrier.wait()
    response = client.post('/orders/checkout/', data, HTTP_X_REQUESTED_WITH='XMLHttpRequest')
    close_old_connections()
    return response.status_code, response.json()

with ThreadPoolExecutor(max_workers=2) as executor:
    futures = [executor.submit(checkout) for _ in range(2)]
    checkouts = [future.result(timeout=20) for future in futures]
assert all(status == 200 for status, _ in checkouts), checkouts
assert len({body['order_number'] for _,body in checkouts}) == 1
assert Order.objects.count() == 1
order_id = Order.objects.get().pk
barrier = threading.Barrier(2)

def provider(**kwargs):
    time.sleep(0.2)
    return {'id':'MP-ONLY-ONE', 'status':'approved', 'installments':1}

def pay(token):
    close_old_connections()
    order = Order.objects.get(pk=order_id)
    barrier.wait()
    try:
        payments.create_card(order, token=token)
        result = 200
    except payments.PaymentError as exc:
        result = exc.http_status
    close_old_connections()
    return result

with patch('orders.services.mercadopago.criar_pagamento_cartao', side_effect=provider) as create:
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(pay, token) for token in ('test-token-a','test-token-b')]
        payments_results = [future.result(timeout=20) for future in futures]
    charges = create.call_count
assert charges == 1
assert PaymentAttempt.objects.count() == 1
valid = PaymentAttempt.objects.get()
extra = PaymentAttempt.objects.create(order=valid.order, method='pix', amount=valid.amount,
    status='cancelled', mp_payment_id='MP-EXTRA')
barrier = threading.Barrier(2)

def approve(attempt_id):
    close_old_connections()
    attempt = PaymentAttempt.objects.select_related('order').get(pk=attempt_id)
    barrier.wait()
    payments.apply_provider_info(attempt, {'status':'approved'})
    close_old_connections()

with ThreadPoolExecutor(max_workers=2) as executor:
    futures = [executor.submit(approve, attempt_id) for attempt_id in (valid.pk, extra.pk)]
    for future in futures:
        future.result(timeout=20)
valid.refresh_from_db()
extra.refresh_from_db()
assert valid.refund_requested_at is None
assert extra.refund_requested_at is not None
assert Order.objects.get(pk=order_id).is_paid
result = {'banco':'PostgreSQL 16' if audit_database_url else 'SQLite WAL em arquivo',
          'threads':2, 'checkout_status':[status for status,_ in checkouts],
          'pedidos':Order.objects.count(), 'pagamentos_status':payments_results,
          'cobrancas_solicitadas':charges, 'tentativas_preservadas':PaymentAttempt.objects.count(),
          'aprovacoes_simultaneas_preservam_pagamento_valido':True,
          'estorno_individual_da_cobranca_excedente':True}
filename = 'concorrencia-postgres.json' if audit_database_url else 'concorrencia.json'
(ROOT/'docs/auditoria'/filename).write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
print(json.dumps(result, ensure_ascii=False, indent=2))

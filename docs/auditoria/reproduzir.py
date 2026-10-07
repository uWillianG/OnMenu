"""Reproducoes da auditoria de lancamento, sem tocar no banco local.

Execute com .venv/Scripts/python docs/auditoria/reproduzir.py.
Usa SQLite em memoria, fixtures ficticias e integracoes externas simuladas.
As verificacoes caracterizam os problemas encontrados; nao aprovam um release.
"""

import argparse
import json
import logging
import os
import sys
from datetime import timedelta
from decimal import Decimal
from io import StringIO
from pathlib import Path
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
os.environ.update({
    'DJANGO_SETTINGS_MODULE': 'config.settings',
    'DATABASE_URL': 'sqlite:///:memory:',
    'DJANGO_DEBUG': 'True',
    'DJANGO_ALLOWED_HOSTS': 'testserver,localhost',
    'DJANGO_RATELIMIT_ENABLED': 'False',
    'MERCADOPAGO_ACCESS_TOKEN': '',
    'MERCADOPAGO_PUBLIC_KEY': '',
    'MERCADOPAGO_WEBHOOK_SECRET': '',
    'WHATSAPP_TOKEN': '',
    'WHATSAPP_PHONE_ID': '',
    'GOOGLE_OAUTH_CLIENT_ID': '',
    'GOOGLE_OAUTH_CLIENT_SECRET': '',
    'EMAIL_BACKEND': 'django.core.mail.backends.locmem.EmailBackend',
})

import django
django.setup()

from django.conf import settings
from django.contrib.auth import authenticate, get_user_model
from django.core.management import call_command
from django.db import connection
from django.test import Client, override_settings
from django.utils import timezone

from accounts.forms import SignupForm
from cart.cart import Cart
from menu.models import Category, ComplementChoice, ComplementGroup, MenuItem, Restaurant
from orders.models import CardPayment, City, Neighborhood, Order, OrderItem, PixPayment
from orders.selectors import get_sales_report
from orders.services import mercadopago, pedidos, whatsapp

logging.disable(logging.CRITICAL)
assert connection.vendor == 'sqlite' and connection.is_in_memory_db()
call_command('migrate', verbosity=0)
results = []


def fixture():
    call_command('flush', verbosity=0, interactive=False)
    restaurant = Restaurant.objects.create(name='Restaurante ficticio')
    category = Category.objects.create(restaurant=restaurant, name='Lanches')
    item = MenuItem.objects.create(category=category, name='Lanche', price=Decimal('20'))
    city = City.objects.create(name='Cidade ficticia', delivery_fee=Decimal('2'))
    neighborhood = Neighborhood.objects.create(city=city, name='Centro', delivery_fee=Decimal('3'))
    client = Client(raise_request_exception=False)
    return client, restaurant, category, item, city, neighborhood


def checkout_data(city, neighborhood, **extra):
    data = {
        'customer_name': 'Cliente Ficticio', 'phone': '(11) 99999-8888',
        'fulfillment_method': 'pickup', 'payment_method': 'cash',
        'customer_cpf': '111.111.111-11', 'city': city.pk,
        'neighborhood': neighborhood.pk, 'address_street': 'Rua ficticia',
        'address_number': '10',
    }
    data.update(extra)
    return data


def add(client, item, **data):
    return client.post(f'/cart/add/{item.pk}/', {'quantity': 1, **data})


def place(client, city, neighborhood, **extra):
    return client.post('/orders/checkout/', checkout_data(city, neighborhood, **extra),
                       HTTP_X_REQUESTED_WITH='XMLHttpRequest')


def order_for(client, restaurant, payment_method='credit_card', **extra):
    order = Order.objects.create(restaurant=restaurant, customer_name='Cliente Ficticio',
                                 phone='11999998888', subtotal=Decimal('20'),
                                 payment_method=payment_method, **extra)
    session = client.session
    session['tracked_orders'] = [order.order_number]
    session.save()
    return order


def card_data(payment_id='CARD-TEST', status='approved'):
    return {'id': payment_id, 'status': status, 'status_detail': 'test',
            'installments': 1, 'payment_method_id': 'visa', 'last_four': '1234'}


def probe(identifier, run):
    try:
        details = run(*fixture())
        results.append({'id': identifier, 'reproduzido': True, 'observado': details})
    except Exception as exc:
        results.append({'id': identifier, 'reproduzido': False,
                        'erro': f'{type(exc).__name__}: {exc}'})


def disabled_fulfillment(client, restaurant, category, item, city, neighborhood):
    restaurant.accepts_delivery = restaurant.accepts_pickup = False
    restaurant.save()
    observations = {}
    for method in ('delivery', 'pickup'):
        add(client, item)
        response = place(client, city, neighborhood, fulfillment_method=method)
        observations[method] = response.status_code
    assert Order.objects.count() == 2
    return observations


def inactive_restaurant(client, restaurant, category, item, city, neighborhood):
    add(client, item)
    restaurant.is_active = False
    restaurant.save()
    response = place(client, city, neighborhood)
    assert Order.objects.count() == 1
    return {'http': response.status_code, 'pedidos_com_restaurante_inativo': 1}


def hidden_items(client, restaurant, category, item, city, neighborhood):
    category.is_active = False
    category.save()
    add(client, item)
    place(client, city, neighborhood)
    other = Restaurant.objects.create(name='Outro restaurante', is_active=False)
    other_category = Category.objects.create(restaurant=other, name='Outra categoria')
    other_item = MenuItem.objects.create(category=other_category, name='Outro item', price=Decimal('5'))
    add(client, other_item)
    place(client, city, neighborhood)
    assert Order.objects.count() == 2
    last = Order.objects.latest('id')
    assert last.restaurant_id != last.items.first().menu_item.category.restaurant_id
    return {'categoria_inativa_vendida': True, 'item_de_outro_restaurante_vendido': True}


def obsolete_options(client, restaurant, category, item, city, neighborhood):
    group = ComplementGroup.objects.create(restaurant=restaurant, name='Ponto', required=True)
    choice = ComplementChoice.objects.create(group=group, name='Ao ponto')
    item.complement_groups.add(group)
    add(client, item, **{f'option_group_{group.pk}': choice.pk})
    choice.delete()
    place(client, city, neighborhood)
    order = Order.objects.get()
    assert order.items.get().options.count() == 0
    return {'pedido_sem_complemento_obrigatorio': order.order_number}


def pix_failure(client, restaurant, category, item, city, neighborhood):
    add(client, item)
    with patch('orders.services.mercadopago.criar_pix', side_effect=mercadopago.PixError('Falha simulada')):
        response = place(client, city, neighborhood, payment_method='pix')
        assert response.status_code == 502 and Order.objects.count() == 1
        # SessionMiddleware nao persiste a sessao em respostas >= 500. O pedido
        # ja foi commitado, mas o carrinho do browser permanece e gera outro.
        assert client.session.get(settings.CART_SESSION_ID)
        assert PixPayment.objects.count() == 0 and 'order_number' not in response.json()
        retry = place(client, city, neighborhood, payment_method='pix')
    assert retry.status_code == 502 and Order.objects.count() == 2
    return {'http': 502, 'carrinho_persistido': True, 'pedidos_sem_cobranca_apos_retentativa': 2,
            'resposta_sem_identificador_do_pedido': True}


def pix_resume(client, restaurant, category, item, city, neighborhood):
    add(client, item)
    result = place(client, city, neighborhood, payment_method='pix').json()
    reload_response = client.get('/orders/checkout/')
    confirmation = client.get(result['confirmation_url'])
    html = confirmation.content.decode()
    assert reload_response.status_code == 302 and reload_response.url == '/cart/'
    assert confirmation.status_code == 200 and 'pix-qr' not in html and '/recreate/' not in html
    return {'checkout_recarregado': 302, 'destino': '/cart/',
            'confirmacao_sem_qr_ou_acao_retomar_pagamento': True}


def card_retry(client, restaurant, category, item, city, neighborhood):
    add(client, item)
    context = place(client, city, neighborhood, payment_method='credit_card').json()
    rejected = client.post(context['card_pay_url'], {'token': 'MOCK-REJECT'})
    retry = place(client, city, neighborhood, payment_method='credit_card')
    assert rejected.json()['status'] == 'rejected' and retry.status_code == 302
    return {'primeira_tentativa': 'rejected', 'checkout_da_retentativa': 302,
            'destino': retry.url, 'carrinho_vazio': True}


def unpaid_queue(client, restaurant, category, item, city, neighborhood):
    order = order_for(client, restaurant)
    staff = get_user_model().objects.create(username='staff', is_staff=True)
    client.force_login(staff)
    before = client.get('/staff/orders/feed/').json()
    pedidos.marcar_pago(order)
    after = client.get('/staff/orders/feed/').json()
    assert before['active_count'] == 1 and before['signature'] == after['signature']
    return {'pedido_online_pendente_na_fila': True,
            'assinatura_inalterada_apos_pagamento': True,
            'cartao_da_fila_sem_status_pagamento': 'Pendente' not in before['active_html']}


def paid_refund(client, restaurant, category, item, city, neighborhood):
    order = order_for(client, restaurant, payment_status='paid')
    card = CardPayment.objects.create(order=order, amount=order.total, status='approved',
                                      mp_payment_id='CARD-TEST', external_reference=order.order_number)
    pedidos.aplicar_status_mp(card, 'refunded')
    order.refresh_from_db()
    card.refresh_from_db()
    assert order.payment_status == 'paid' and card.status == 'refunded'
    return {'pedido': order.payment_status, 'pagamento': card.status}


def expired_pix(client, restaurant, category, item, city, neighborhood):
    order = order_for(client, restaurant, payment_method='pix')
    pix = PixPayment.objects.create(order=order, amount=order.total,
                                   mp_payment_id='PIX-TEST', external_reference=order.order_number,
                                   expires_at=timezone.now() - timedelta(minutes=1))
    with override_settings(MERCADOPAGO_MOCK=False), patch(
        'orders.services.mercadopago.buscar_status', return_value={'status': 'approved'}
    ) as fetch:
        response = client.get('/orders/pix/PIX-TEST/status/').json()
        polling_calls = fetch.call_count
        pix.status = 'pending'
        pix.save()
        call_command('sync_pending_pix', stdout=StringIO())
        job_calls = fetch.call_count - polling_calls
    assert polling_calls == job_calls == 0
    return {'polling': response['status'], 'consultas_ao_provedor_no_polling': polling_calls,
            'consultas_ao_provedor_no_job': job_calls}


def cancelled_card(client, restaurant, category, item, city, neighborhood):
    order = order_for(client, restaurant, status='cancelled')
    response = client.post(f'/orders/card/{order.order_number}/pay/', {'token': 'MOCK-APPROVE'})
    order.refresh_from_db()
    assert response.json()['status'] == 'approved' and order.status == 'cancelled'
    return {'pedido_cancelado': True, 'cobranca_aceita': True}


def wrong_payment_method(client, restaurant, category, item, city, neighborhood):
    order = order_for(client, restaurant, payment_method='cash')
    response = client.post(f'/orders/pix/{order.order_number}/recreate/')
    assert response.json()['ok'] and PixPayment.objects.filter(order=order).exists()
    return {'metodo_pedido': 'cash', 'pix_criado': True}


def card_limit(client, restaurant, category, item, city, neighborhood):
    order = order_for(client, restaurant)
    responses = [client.post(f'/orders/card/{order.order_number}/pay/', {'token': 'MOCK-REJECT'})
                 for _ in range(5)]
    assert all(response.status_code == 200 for response in responses)
    return {'tentativas_no_mesmo_pedido': 5, 'http': [r.status_code for r in responses]}


def malformed_installments(client, restaurant, category, item, city, neighborhood):
    order = order_for(client, restaurant)
    response = client.post(f'/orders/card/{order.order_number}/pay/',
                           {'token': 'MOCK-APPROVE', 'installments': 'abc'})
    assert response.status_code == 500
    return {'parcelas_nao_numericas': 'abc', 'http': 500}


def duplicate_category(client, restaurant, category, item, city, neighborhood):
    staff = get_user_model().objects.create(username='staff', is_staff=True)
    client.force_login(staff)
    response = client.post('/staff/cardapio/categoria/nova/',
                           {'name': category.name, 'display_order': 0, 'is_active': 'on'})
    assert response.status_code == 500
    return {'nome_duplicado': category.name, 'http': 500}


def report_unpaid(client, restaurant, category, item, city, neighborhood):
    order_for(client, restaurant, payment_method='pix', payment_status='pending')
    today = timezone.localdate()
    report = get_sales_report(today, today)
    assert report['revenue'] == Decimal('20')
    return {'pagamento': 'pending', 'faturamento_reportado': str(report['revenue'])}


def payment_race(client, restaurant, category, item, city, neighborhood):
    order = order_for(client, restaurant)
    calls = []

    def simulate(**kwargs):
        payment_id = f'CARD-{len(calls) + 1}'
        calls.append(payment_id)
        if len(calls) == 1:
            nested = client.post(f'/orders/card/{order.order_number}/pay/', {'token': 'token-b'})
            assert nested.json()['status'] == 'approved'
        return card_data(payment_id)

    with patch('orders.services.mercadopago.criar_pagamento_cartao', side_effect=simulate):
        response = client.post(f'/orders/card/{order.order_number}/pay/', {'token': 'token-a'})
    assert response.status_code == 200 and len(calls) == 2
    assert CardPayment.objects.filter(order=order).count() == 1
    return {'intercalacao_simulada': True, 'cobrancas_solicitadas': calls,
            'registros_de_pagamento_preservados': 1}


def idempotency_keys(client, restaurant, category, item, city, neighborhood):
    keys = []
    sdk = Mock()

    def create(payload, options):
        keys.append(options.custom_headers['x-idempotency-key'])
        return {'status': 201, 'response': {'id': 'TEST', 'status': 'pending'}}

    sdk.payment.return_value.create.side_effect = create
    with override_settings(MERCADOPAGO_MOCK=False), patch('orders.services.mercadopago._sdk', return_value=sdk):
        for _ in range(2):
            mercadopago.criar_pagamento_cartao(amount=Decimal('20'), description='Teste',
                token='mesmo-token', external_reference='OM-TEST', installments=1)
        card_keys_change = keys[0] != keys[1]
        keys.clear()
        for _ in range(2):
            mercadopago.criar_pix(amount=Decimal('20'), description='Teste',
                payer_email='teste@example.invalid', external_reference='OM-TEST')
        pix_keys_repeat = keys[0] == keys[1]
    assert card_keys_change and pix_keys_repeat
    return {'cartao_mesma_tentativa_chave_diferente': True,
            'pix_recriacao_mesma_chave_do_pedido': True}


def challenge_data(client, restaurant, category, item, city, neighborhood):
    result = mercadopago._normalize_card_response({'id': 'TEST', 'status': 'pending',
        'status_detail': 'pending_challenge', 'three_ds_info': {
            'external_resource_url': 'https://example.invalid/challenge', 'creq': 'challenge-test'}})
    assert result['requires_action'] and 'creq' not in result
    return {'requer_3ds': True, 'creq_descartado': True}


def concurrent_signup(client, restaurant, category, item, city, neighborhood):
    data = {'full_name': 'Primeiro Cliente', 'email': 'teste@example.invalid',
            'phone': '(11) 99999-8888', 'cpf': '11111111111',
            'password1': 'SenhaDeAuditoria@123', 'password2': 'SenhaDeAuditoria@123'}
    first = SignupForm(data)
    second = SignupForm({**data, 'full_name': 'Segundo Cliente', 'cpf': '22222222222'})
    assert first.is_valid() and second.is_valid()
    first.save()
    second.save()
    assert get_user_model().objects.filter(email=data['email']).count() == 2
    assert authenticate(username=data['email'], password=data['password1']) is None
    return {'intercalacao_simulada': True, 'contas_com_mesmo_email': 2,
            'login_por_email': False}


def invalid_customer_data(client, restaurant, category, item, city, neighborhood):
    add(client, item)
    response = place(client, city, neighborhood, phone='abc', payment_method='pix',
                     customer_cpf='00000000000')
    assert response.status_code == 200 and response.json()['ok']
    return {'telefone': 'abc', 'cpf': '00000000000', 'checkout_aceitou': True}


def quantity_overflow(client, restaurant, category, item, city, neighborhood):
    add_response = add(client, item, quantity='999999999999999999999')
    response = place(client, city, neighborhood)
    assert add_response.status_code == 302 and response.status_code == 500
    return {'quantidade_sem_teto': True, 'checkout_http': 500}


def ddd_55(client, restaurant, category, item, city, neighborhood):
    result = whatsapp.formatar_numero('(55) 99999-8888')
    assert result == '55999998888'
    return {'numero_local_com_ddd_55': True, 'ddi_55_nao_adicionado': True}


with override_settings(PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher']):
    for identifier, run in [
        ('entrega_retirada_desativadas', disabled_fulfillment),
        ('restaurante_inativo', inactive_restaurant),
        ('itens_ocultos_e_outro_restaurante', hidden_items),
        ('complemento_obrigatorio_removido', obsolete_options),
        ('falha_pix_cria_pedidos_duplicados', pix_failure),
        ('pix_recarregado_sem_retomada', pix_resume),
        ('cartao_recusado_retentativa', card_retry),
        ('fila_online_sem_pagamento', unpaid_queue),
        ('estorno_pedido_continua_pago', paid_refund),
        ('pix_expirado_sem_consulta', expired_pix),
        ('cobranca_pedido_cancelado', cancelled_card),
        ('pix_em_pedido_dinheiro', wrong_payment_method),
        ('cartao_sem_limite_servidor', card_limit),
        ('parcelas_invalidas_http500', malformed_installments),
        ('categoria_duplicada_http500', duplicate_category),
        ('relatorio_conta_pix_nao_pago', report_unpaid),
        ('cartao_concorrente_duas_cobrancas', payment_race),
        ('chaves_idempotencia', idempotency_keys),
        ('3ds_descarta_creq', challenge_data),
        ('cadastro_concorrente_email_duplicado', concurrent_signup),
        ('checkout_dados_cliente_invalidos', invalid_customer_data),
        ('quantidade_excessiva_http500', quantity_overflow),
        ('whatsapp_ddd55', ddd_55),
    ]:
        probe(identifier, run)

payload = json.dumps({'banco': 'SQLite em memoria', 'servicos_externos': 'simulados',
                  'total': len(results), 'reproduzidos': sum(r['reproduzido'] for r in results),
                  'resultados': results}, ensure_ascii=False, indent=2)
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--output', type=Path, help='Grava tambem os resultados em JSON.')
arguments = parser.parse_args()
if arguments.output:
    arguments.output.write_text(payload + '\n', encoding='utf-8')
print(payload)
sys.exit(0 if all(r['reproduzido'] for r in results) else 1)

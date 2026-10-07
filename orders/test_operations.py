import hashlib
import hmac
import json
from datetime import timedelta
from decimal import Decimal
from io import StringIO
from unittest.mock import Mock, patch

import requests
from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from accounts.models import DataRequest
from menu.models import Restaurant
from orders.models import City, Neighborhood, Order, WhatsAppMessage
from orders.services import notificacoes
from orders.services.whatsapp_outbox import send_message
from .test_release import ReleaseFixture


class WhatsAppReleaseTests(ReleaseFixture):
    def message(self):
        order = self.order(payment_method='cash', whatsapp_opt_in='on')
        notificacoes.notificar_status_pedido(order)
        return WhatsAppMessage.objects.get()

    def test_no_whatsapp_without_order_consent(self):
        order = self.order(payment_method='cash')
        notificacoes.notificar_status_pedido(order)
        self.assertFalse(WhatsAppMessage.objects.exists())

    def test_status_update_queues_one_event_without_network(self):
        order = self.order(payment_method='cash', whatsapp_opt_in='on')
        with patch('orders.services.whatsapp_outbox.requests.post') as post:
            notificacoes.notificar_status_pedido(order)
            notificacoes.notificar_status_pedido(order)
        self.assertEqual(WhatsAppMessage.objects.count(), 1)
        post.assert_not_called()

    @override_settings(WHATSAPP_MOCK=False, WHATSAPP_TOKEN='test', WHATSAPP_PHONE_ID='123')
    def test_worker_sends_approved_template(self):
        message = self.message()
        response = Mock(status_code=200)
        response.json.return_value = {'messages':[{'id':'wamid.TEST'}]}
        with patch('orders.services.whatsapp_outbox.requests.post', return_value=response) as post:
            self.assertTrue(send_message(message))
        payload = post.call_args.kwargs['json']
        self.assertEqual(payload['type'], 'template')
        self.assertEqual(len(payload['template']['components'][0]['parameters']), 2)
        self.assertNotIn('text', payload)
        message.refresh_from_db()
        self.assertEqual(message.provider_id, 'wamid.TEST')

    @override_settings(WHATSAPP_MOCK=False, WHATSAPP_TOKEN='test', WHATSAPP_PHONE_ID='123')
    def test_timeout_does_not_blindly_resend(self):
        message = self.message()
        with patch('orders.services.whatsapp_outbox.requests.post', side_effect=requests.Timeout) as post:
            send_message(message)
            send_message(message)
        self.assertEqual(post.call_count, 1)
        message.refresh_from_db()
        self.assertEqual(message.status, 'uncertain')

    @override_settings(WHATSAPP_MOCK=False)
    def test_confirmed_rate_limit_schedules_retry(self):
        message = self.message()
        with patch('orders.services.whatsapp_outbox.requests.post', return_value=Mock(status_code=429)):
            send_message(message)
        message.refresh_from_db()
        self.assertEqual(message.status, 'pending')
        self.assertGreater(message.next_attempt_at, timezone.now())

    def test_opt_out_skips_queued_notifications(self):
        message = self.message()
        self.client.post(reverse('orders:stop_whatsapp', args=[message.order.order_number]))
        message.refresh_from_db()
        self.assertEqual(message.status, 'skipped')
        self.assertFalse(message.order.whatsapp_opt_in)

    def test_outdated_status_message_is_not_sent(self):
        message = self.message()
        message.order.status = 'delivered'
        message.order.save()
        with patch('orders.services.whatsapp_outbox.requests.post') as post:
            send_message(message)
        post.assert_not_called()
        message.refresh_from_db()
        self.assertEqual(message.status, 'skipped')

    @override_settings(WHATSAPP_APP_SECRET='test-secret')
    def test_delivery_webhook_requires_signature_and_updates_message(self):
        message = self.message()
        message.provider_id = 'wamid.TEST'
        message.status = 'sent'
        message.save()
        body = json.dumps({'entry':[{'changes':[{'value':{'statuses':[{'id':'wamid.TEST','status':'delivered'}]}}]}]})
        url = reverse('orders:webhook_whatsapp')
        self.assertEqual(self.client.post(url, body, content_type='application/json').status_code, 401)
        signature = 'sha256=' + hmac.new(b'test-secret', body.encode(), hashlib.sha256).hexdigest()
        response = self.client.post(url, body, content_type='application/json', HTTP_X_HUB_SIGNATURE_256=signature)
        self.assertEqual(response.status_code, 200)
        message.refresh_from_db()
        self.assertEqual(message.status, 'delivered')


class PrivacyReleaseTests(TestCase):
    def setUp(self):
        self.restaurant = Restaurant.objects.create(name='Restaurante')
        self.customer = User.objects.create_user(username='cliente', email='cliente@example.com')
        self.staff = User.objects.create_user(username='responsavel', is_staff=True, is_superuser=True)
        self.order = Order.objects.create(restaurant=self.restaurant, user=self.customer,
            customer_name='Nome privado', phone='11999998888', customer_cpf='39053344705',
            status='delivered', subtotal=20)
        self.client.force_login(self.customer)

    def test_privacy_and_terms_are_public(self):
        self.client.logout()
        self.assertEqual(self.client.get(reverse('menu:privacy')).status_code, 200)
        self.assertEqual(self.client.get(reverse('menu:terms')).status_code, 200)

    def test_export_is_private_and_contains_only_own_orders(self):
        other = User.objects.create_user(username='outro')
        Order.objects.create(restaurant=self.restaurant, user=other, customer_name='Outra pessoa', phone='11999997777')
        response = self.client.get(reverse('accounts:export_data'))
        self.assertEqual(len(response.json()['pedidos']), 1)
        self.assertEqual(response.json()['pedidos'][0]['numero'], self.order.order_number)
        self.assertEqual(response['Cache-Control'], 'no-store')
        self.client.logout()
        self.assertEqual(self.client.get(reverse('accounts:export_data')).status_code, 302)

    def test_deletion_requires_confirmation_and_deduplicates_requests(self):
        self.client.post(reverse('accounts:request_deletion'))
        self.assertFalse(DataRequest.objects.exists())
        for _ in range(2):
            self.client.post(reverse('accounts:request_deletion'), {'confirm_delete':'on'})
        self.assertEqual(DataRequest.objects.count(), 1)

    def test_customer_cannot_approve_own_deletion(self):
        data_request = DataRequest.objects.create(user=self.customer)
        self.assertEqual(self.client.post(reverse('accounts:complete_deletion', args=[data_request.pk]),
            {'confirm_delete':'on'}).status_code, 403)

    def test_deletion_anonymizes_orders_and_keeps_financial_totals(self):
        customer_id = self.customer.pk
        data_request = DataRequest.objects.create(user=self.customer)
        self.client.force_login(self.staff)
        self.client.post(reverse('accounts:complete_deletion', args=[data_request.pk]), {'confirm_delete':'on'})
        self.assertFalse(User.objects.filter(pk=customer_id).exists())
        self.order.refresh_from_db()
        self.assertEqual(self.order.customer_name, 'Cliente anonimizado')
        self.assertEqual(self.order.phone, '')
        self.assertEqual(self.order.customer_cpf, '')
        self.assertEqual(self.order.total, 20)
        data_request.refresh_from_db()
        self.assertEqual(data_request.status, 'completed')

    def test_active_order_prevents_deletion(self):
        data_request = DataRequest.objects.create(user=self.customer)
        self.order.status = 'preparing'
        self.order.save()
        self.client.force_login(self.staff)
        self.client.post(reverse('accounts:complete_deletion', args=[data_request.pk]), {'confirm_delete':'on'})
        data_request.refresh_from_db()
        self.assertEqual(data_request.status, 'pending')
        self.assertTrue(User.objects.filter(pk=self.customer.pk).exists())

    @override_settings(CUSTOMER_DATA_RETENTION_DAYS=365)
    def test_retention_anonymizes_old_finished_orders_only(self):
        Order.objects.filter(pk=self.order.pk).update(created_at=timezone.now()-timedelta(days=366))
        call_command('prune_customer_data', stdout=StringIO())
        self.order.refresh_from_db()
        self.assertEqual(self.order.customer_name, 'Cliente anonimizado')
        self.assertTrue(User.objects.filter(pk=self.customer.pk).exists())


class SetupReleaseTests(TestCase):
    def setUp(self):
        self.staff = User.objects.create_user(username='responsavel', is_staff=True)
        self.client.force_login(self.staff)

    def test_first_establishment_is_created_through_panel(self):
        response = self.client.post(reverse('menu:setup_restaurant'), {'name':'Meu Restaurante',
            'accepting_orders':'on', 'accepts_pickup':'on', 'contact_email':'contato@example.com'})
        self.assertEqual(response.status_code, 302)
        restaurant = Restaurant.objects.get()
        self.assertTrue(restaurant.accepts_pickup)
        self.assertFalse(restaurant.accepts_delivery)

    def test_staff_manages_delivery_fees_without_django_admin(self):
        url = reverse('orders:delivery_areas')
        self.client.post(url, {'kind':'city','name':'Cidade', 'delivery_fee':'2,50','is_active':'on'})
        city = City.objects.get()
        self.client.post(url, {'kind':'neighborhood','city':city.pk,'name':'Centro',
            'delivery_fee':'1,00','is_active':'on'})
        neighborhood = Neighborhood.objects.get()
        self.assertEqual(city.delivery_fee + neighborhood.delivery_fee, Decimal('3.50'))
        self.assertEqual(self.client.get(url).status_code, 200)

    def test_delivery_fees_cannot_be_negative(self):
        self.client.post(reverse('orders:delivery_areas'), {'kind':'city','name':'Cidade','delivery_fee':'-1'})
        self.assertFalse(City.objects.exists())

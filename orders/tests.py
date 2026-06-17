import json
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from menu.models import Category, MenuItem, Restaurant

from .models import CardPayment, City, Neighborhood, Order, PixPayment


class OrderViewsTests(TestCase):
    def setUp(self):
        self.restaurant = Restaurant.objects.create(
            name='Test Kitchen',
            slug='test-kitchen',
            delivery_fee=Decimal('5.00'),
        )
        category = Category.objects.create(
            restaurant=self.restaurant,
            name='Mains',
            slug='mains',
        )
        self.item = MenuItem.objects.create(
            category=category,
            name='Burger',
            slug='burger',
            price=Decimal('20.00'),
            is_available=True,
        )
        self.city = City.objects.create(name='Curitiba', delivery_fee=Decimal('4.00'))
        self.neighborhood = Neighborhood.objects.create(
            city=self.city, name='Centro', delivery_fee=Decimal('3.00'),
        )

    def _add_item_to_cart(self, quantity=2):
        self.client.post(
            reverse('cart:cart_add', args=[self.item.pk]),
            {'quantity': quantity},
        )

    def test_delivery_checkout_creates_order_with_delivery_fee(self):
        self._add_item_to_cart(quantity=2)

        response = self.client.post(
            reverse('orders:checkout'),
            {
                'fulfillment_method': Order.FulfillmentMethod.DELIVERY,
                'customer_name': 'Ada Lovelace',
                'phone': '555-0100',
                'city': self.city.pk,
                'neighborhood': self.neighborhood.pk,
                'address_street': 'Code Street',
                'address_number': '1',
                'address_complement': 'Apt 2',
                'notes': 'No onions',
                'payment_method': Order.PaymentMethod.PIX,
                'customer_cpf': '390.533.447-05',
            },
        )

        order = Order.objects.get()
        self.assertRedirects(
            response,
            reverse('orders:confirmation', args=[order.order_number]),
        )
        self.assertEqual(order.subtotal, Decimal('40.00'))
        # delivery fee = city (4.00) + neighborhood (3.00)
        self.assertEqual(order.delivery_fee, Decimal('7.00'))
        self.assertEqual(order.total, Decimal('47.00'))
        self.assertTrue(order.order_number.startswith('OM-'))
        self.assertEqual(order.items.count(), 1)
        # selected city/neighborhood names are snapshotted and composed into address
        self.assertEqual(order.address_street, 'Code Street')
        self.assertEqual(order.address_city, 'Curitiba')
        self.assertEqual(order.address_neighborhood, 'Centro')
        self.assertIn('Code Street, 1', order.address)
        self.assertIn('Centro - Curitiba', order.address)

    def test_delivery_checkout_requires_city_and_neighborhood(self):
        self._add_item_to_cart(quantity=1)

        response = self.client.post(
            reverse('orders:checkout'),
            {
                'fulfillment_method': Order.FulfillmentMethod.DELIVERY,
                'customer_name': 'Ada Lovelace',
                'phone': '555-0100',
                'address_street': 'Code Street',
                'address_number': '1',
                'payment_method': Order.PaymentMethod.PIX,
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(Order.objects.exists())
        self.assertContains(response, 'Selecione a cidade.')
        self.assertContains(response, 'Selecione o bairro.')

    def test_delivery_rejects_neighborhood_from_other_city(self):
        self._add_item_to_cart(quantity=1)
        other_city = City.objects.create(name='Pinhais', delivery_fee=Decimal('5.00'))

        response = self.client.post(
            reverse('orders:checkout'),
            {
                'fulfillment_method': Order.FulfillmentMethod.DELIVERY,
                'customer_name': 'Ada Lovelace',
                'phone': '555-0100',
                'city': other_city.pk,
                'neighborhood': self.neighborhood.pk,
                'address_street': 'Code Street',
                'address_number': '1',
                'payment_method': Order.PaymentMethod.PIX,
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(Order.objects.exists())
        self.assertContains(response, 'Selecione um bairro da cidade escolhida.')

    def test_pickup_checkout_has_no_delivery_fee(self):
        self._add_item_to_cart(quantity=1)

        self.client.post(
            reverse('orders:checkout'),
            {
                'fulfillment_method': Order.FulfillmentMethod.PICKUP,
                'customer_name': 'Grace Hopper',
                'phone': '555-0101',
                'address': '',
                'notes': '',
                'payment_method': Order.PaymentMethod.CASH,
            },
        )

        order = Order.objects.get()
        self.assertEqual(order.delivery_fee, Decimal('0.00'))
        self.assertEqual(order.total, Decimal('20.00'))

    def test_staff_can_update_order_status(self):
        order = Order.objects.create(
            restaurant=self.restaurant,
            customer_name='Katherine Johnson',
            phone='555-0102',
            fulfillment_method=Order.FulfillmentMethod.PICKUP,
            payment_method=Order.PaymentMethod.CARD_ON_DELIVERY,
            subtotal=Decimal('20.00'),
            total=Decimal('20.00'),
        )
        staff_user = User.objects.create_user(
            username='staff',
            password='password',
            is_staff=True,
        )
        self.client.force_login(staff_user)

        response = self.client.post(
            reverse('orders:staff_order_detail', args=[order.order_number]),
            {'status': Order.Status.PREPARING},
        )

        self.assertRedirects(
            response,
            reverse('orders:staff_order_detail', args=[order.order_number]) + '?updated=1',
        )
        order.refresh_from_db()
        self.assertEqual(order.status, Order.Status.PREPARING)

    def test_staff_can_bulk_update_order_status(self):
        orders = [
            Order.objects.create(
                restaurant=self.restaurant,
                customer_name=f'Cliente {i}',
                phone='555-0102',
                fulfillment_method=Order.FulfillmentMethod.PICKUP,
                payment_method=Order.PaymentMethod.CASH,
                subtotal=Decimal('20.00'),
                total=Decimal('20.00'),
            )
            for i in range(3)
        ]
        staff_user = User.objects.create_user(
            username='staff', password='password', is_staff=True,
        )
        self.client.force_login(staff_user)

        selected = orders[:2]
        response = self.client.post(
            reverse('orders:staff_orders_bulk_update'),
            {
                'order_numbers': [o.order_number for o in selected],
                'status': Order.Status.OUT_FOR_DELIVERY,
                'next': reverse('orders:staff_order_list'),
            },
        )

        self.assertRedirects(response, reverse('orders:staff_order_list'))
        for o in selected:
            o.refresh_from_db()
            self.assertEqual(o.status, Order.Status.OUT_FOR_DELIVERY)
        # O pedido não selecionado permanece inalterado.
        orders[2].refresh_from_db()
        self.assertEqual(orders[2].status, Order.Status.RECEIVED)


class PixPaymentTests(TestCase):
    def setUp(self):
        self.restaurant = Restaurant.objects.create(
            name='Test Kitchen', slug='test-kitchen', delivery_fee=Decimal('5.00'),
        )
        category = Category.objects.create(
            restaurant=self.restaurant, name='Mains', slug='mains',
        )
        self.item = MenuItem.objects.create(
            category=category, name='Burger', slug='burger',
            price=Decimal('20.00'), is_available=True,
        )

    def _add_item(self, quantity=1):
        self.client.post(reverse('cart:cart_add', args=[self.item.pk]), {'quantity': quantity})

    def _pix_post(self, **overrides):
        data = {
            'fulfillment_method': Order.FulfillmentMethod.PICKUP,
            'customer_name': 'Ada Lovelace',
            'phone': '555-0100',
            'payment_method': Order.PaymentMethod.PIX,
            'customer_cpf': '390.533.447-05',
        }
        data.update(overrides)
        return self.client.post(
            reverse('orders:checkout'), data,
            HTTP_X_REQUESTED_WITH='XMLHttpRequest',
        )

    def test_checkout_page_renders_pix_modal(self):
        self._add_item(quantity=1)
        response = self.client.get(reverse('orders:checkout'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="pix-modal"')
        self.assertContains(response, 'id="pix-fields"')
        self.assertContains(response, 'name="customer_cpf"')

    def test_pix_ajax_creates_order_and_charge(self):
        self._add_item(quantity=1)
        response = self._pix_post()

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload['ok'])
        self.assertTrue(payload['pixId'])
        self.assertTrue(payload['qrCodeBase64'])
        self.assertTrue(payload['qrCodeText'])

        order = Order.objects.get()
        self.assertEqual(order.payment_status, Order.PaymentStatus.PENDING)
        pix = PixPayment.objects.get()
        self.assertEqual(pix.order_id, order.id)
        self.assertEqual(pix.external_reference, order.order_number)
        self.assertEqual(pix.amount, order.total)

    def test_pix_requires_cpf_but_not_email(self):
        self._add_item(quantity=1)
        response = self._pix_post(customer_cpf='')

        self.assertEqual(response.status_code, 400)
        payload = response.json()
        self.assertFalse(payload['ok'])
        self.assertIn('customer_cpf', payload['errors'])
        self.assertNotIn('customer_email', payload['errors'])
        self.assertFalse(Order.objects.exists())

    def test_pix_rejects_invalid_cpf_length(self):
        self._add_item(quantity=1)
        response = self._pix_post(customer_cpf='123')

        self.assertEqual(response.status_code, 400)
        self.assertIn('customer_cpf', response.json()['errors'])
        self.assertFalse(Order.objects.exists())

    def test_pix_status_reports_paid_when_approved(self):
        self._add_item(quantity=1)
        self._pix_post()
        pix = PixPayment.objects.get()
        pix.status = PixPayment.Status.APPROVED
        pix.save(update_fields=['status'])

        response = self.client.get(reverse('orders:pix_status', args=[pix.mp_payment_id]))
        body = response.json()
        self.assertTrue(body['paid'])
        self.assertEqual(body['status'], PixPayment.Status.APPROVED)

    @patch('orders.services.mercadopago.buscar_status')
    def test_webhook_marks_order_paid(self, mock_status):
        self._add_item(quantity=1)
        self._pix_post()
        pix = PixPayment.objects.get()
        mock_status.return_value = {
            'id': pix.mp_payment_id,
            'status': 'approved',
            'external_reference': pix.external_reference,
        }

        response = self.client.post(
            reverse('orders:webhook_pix'),
            data=json.dumps({'type': 'payment', 'data': {'id': pix.mp_payment_id}}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        pix.refresh_from_db()
        self.assertEqual(pix.status, PixPayment.Status.APPROVED)
        self.assertEqual(pix.order.payment_status, Order.PaymentStatus.PAID)

    def test_pix_ajax_clears_cart(self):
        self._add_item(quantity=1)
        self._pix_post()
        cart_response = self.client.get(reverse('cart:cart_detail'))
        self.assertEqual(cart_response.status_code, 200)
        self.assertEqual(PixPayment.objects.count(), 1)


class CardPaymentTests(TestCase):
    def setUp(self):
        self.restaurant = Restaurant.objects.create(
            name='Test Kitchen', slug='test-kitchen', delivery_fee=Decimal('5.00'),
        )
        category = Category.objects.create(
            restaurant=self.restaurant, name='Mains', slug='mains',
        )
        self.item = MenuItem.objects.create(
            category=category, name='Burger', slug='burger',
            price=Decimal('20.00'), is_available=True,
        )

    def _add_item(self, quantity=1):
        self.client.post(reverse('cart:cart_add', args=[self.item.pk]), {'quantity': quantity})

    def _checkout_card(self):
        return self.client.post(
            reverse('orders:checkout'),
            {
                'fulfillment_method': Order.FulfillmentMethod.PICKUP,
                'customer_name': 'Ada Lovelace',
                'phone': '555-0100',
                'payment_method': Order.PaymentMethod.CREDIT_CARD,
            },
            HTTP_X_REQUESTED_WITH='XMLHttpRequest',
        )

    def test_checkout_page_renders_card_modal_and_sdk(self):
        self._add_item(quantity=1)
        response = self.client.get(reverse('orders:checkout'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="card-modal"')
        self.assertContains(response, 'id="card-section"')
        self.assertContains(response, 'id="card-brick-container"')
        # Formulário manual completo (modo sem credenciais)
        self.assertContains(response, 'id="card-number"')
        self.assertContains(response, 'id="card-expiry"')
        self.assertContains(response, 'id="card-cvv"')
        # Cartão é só à vista — sem seletor de parcelas.
        self.assertNotContains(response, 'id="card-installments"')
        self.assertContains(response, 'sdk.mercadopago.com/js/v2')
        self.assertContains(response, 'Cartão de crédito')

    def test_checkout_card_creates_pending_order_and_returns_card_mode(self):
        self._add_item(quantity=1)
        response = self._checkout_card()

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload['mode'], 'card')
        self.assertIn('public_key', payload)
        self.assertIn('card_pay_url', payload)

        order = Order.objects.get()
        self.assertEqual(order.payment_status, Order.PaymentStatus.PENDING)
        self.assertEqual(payload['order_number'], order.order_number)

    def _pay(self, order_number, token='MOCK-APPROVE'):
        return self.client.post(
            reverse('orders:card_pay', args=[order_number]),
            {'token': token, 'installments': 1, 'payment_method_id': 'visa'},
            HTTP_X_REQUESTED_WITH='XMLHttpRequest',
        )

    def test_card_pay_approved_marks_order_paid(self):
        self._add_item(quantity=1)
        order_number = self._checkout_card().json()['order_number']

        response = self._pay(order_number, token='MOCK-APPROVE')
        body = response.json()
        self.assertTrue(body['ok'])
        self.assertEqual(body['status'], 'approved')

        order = Order.objects.get()
        self.assertEqual(order.payment_status, Order.PaymentStatus.PAID)
        card = CardPayment.objects.get()
        self.assertEqual(card.status, CardPayment.Status.APPROVED)
        self.assertEqual(card.amount, order.total)

    def test_card_pay_rejected_shows_friendly_message(self):
        self._add_item(quantity=1)
        order_number = self._checkout_card().json()['order_number']

        body = self._pay(order_number, token='MOCK-REJECT').json()
        self.assertEqual(body['status'], 'rejected')
        self.assertTrue(body['message'])
        # Não expõe o código interno do MP.
        self.assertNotIn('cc_rejected', body['message'])

        order = Order.objects.get()
        self.assertEqual(order.payment_status, Order.PaymentStatus.REJECTED)

    def test_card_pay_is_idempotent_when_already_approved(self):
        self._add_item(quantity=1)
        order_number = self._checkout_card().json()['order_number']
        self._pay(order_number, token='MOCK-APPROVE')
        # Segunda chamada não deve criar outra cobrança.
        self._pay(order_number, token='MOCK-APPROVE')
        self.assertEqual(CardPayment.objects.count(), 1)

    def test_in_process_status_maps_to_order_in_analysis(self):
        from orders.services import pedidos as pedidos_service

        self._add_item(quantity=1)
        order = Order.objects.create(
            restaurant=self.restaurant,
            customer_name='Grace Hopper',
            phone='555-0101',
            fulfillment_method=Order.FulfillmentMethod.PICKUP,
            payment_method=Order.PaymentMethod.CREDIT_CARD,
            subtotal=Decimal('20.00'),
            total=Decimal('20.00'),
        )
        card = CardPayment.objects.create(
            order=order,
            external_reference=order.order_number,
            status=CardPayment.Status.PENDING,
            amount=order.total,
        )

        pedidos_service.aplicar_status_mp(card, 'in_process')
        card.refresh_from_db()
        self.assertEqual(card.status, CardPayment.Status.IN_PROCESS)
        self.assertEqual(card.order.payment_status, Order.PaymentStatus.IN_PROCESS)

    @patch('orders.services.mercadopago.buscar_status')
    def test_webhook_card_marks_order_paid(self, mock_status):
        self._add_item(quantity=1)
        order_number = self._checkout_card().json()['order_number']
        # Pagamento começa em análise para o webhook então confirmar.
        self._pay(order_number)
        card = CardPayment.objects.get()
        card.status = CardPayment.Status.IN_PROCESS
        card.save(update_fields=['status'])
        Order.objects.filter(pk=card.order_id).update(payment_status=Order.PaymentStatus.IN_PROCESS)

        mock_status.return_value = {
            'id': card.mp_payment_id,
            'status': 'approved',
            'status_detail': 'accredited',
            'external_reference': card.external_reference,
        }
        response = self.client.post(
            reverse('orders:webhook_card'),
            data=json.dumps({'type': 'payment', 'data': {'id': card.mp_payment_id}}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        card.refresh_from_db()
        self.assertEqual(card.status, CardPayment.Status.APPROVED)
        self.assertEqual(card.order.payment_status, Order.PaymentStatus.PAID)

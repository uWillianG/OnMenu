import json
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from menu.models import Category, MenuItem, Restaurant

from .models import City, Neighborhood, Order, PixPayment


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
            reverse('orders:staff_order_detail', args=[order.order_number]),
        )
        order.refresh_from_db()
        self.assertEqual(order.status, Order.Status.PREPARING)


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

from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from menu.models import Category, MenuItem, Restaurant

from .models import Order


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
                'address': '1 Code Street',
                'notes': 'No onions',
                'payment_method': Order.PaymentMethod.PIX,
            },
        )

        order = Order.objects.get()
        self.assertRedirects(
            response,
            reverse('orders:confirmation', args=[order.order_number]),
        )
        self.assertEqual(order.subtotal, Decimal('40.00'))
        self.assertEqual(order.delivery_fee, Decimal('5.00'))
        self.assertEqual(order.total, Decimal('45.00'))
        self.assertTrue(order.order_number.startswith('OM-'))
        self.assertEqual(order.items.count(), 1)

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

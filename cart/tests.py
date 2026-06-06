from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from menu.models import Category, MenuItem, Restaurant


class CartViewsTests(TestCase):
    def setUp(self):
        restaurant = Restaurant.objects.create(name='Test Kitchen', slug='test-kitchen')
        category = Category.objects.create(
            restaurant=restaurant,
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
        self.unavailable_item = MenuItem.objects.create(
            category=category,
            name='Soup',
            slug='soup',
            price=Decimal('12.00'),
            is_available=False,
        )

    def test_add_update_and_remove_cart_item(self):
        self.client.post(
            reverse('cart:cart_add', args=[self.item.pk]),
            {'quantity': 2},
        )
        response = self.client.get(reverse('cart:cart_detail'))
        self.assertContains(response, 'Burger')
        self.assertContains(response, '40.00')

        self.client.post(
            reverse('cart:cart_update', args=[self.item.pk]),
            {'quantity': 3},
        )
        response = self.client.get(reverse('cart:cart_detail'))
        self.assertContains(response, '60.00')

        self.client.post(reverse('cart:cart_remove', args=[self.item.pk]))
        response = self.client.get(reverse('cart:cart_detail'))
        self.assertContains(response, 'Your cart is empty')

    def test_unavailable_item_is_not_added(self):
        self.client.post(
            reverse('cart:cart_add', args=[self.unavailable_item.pk]),
            {'quantity': 1},
        )

        response = self.client.get(reverse('cart:cart_detail'))
        self.assertContains(response, 'Your cart is empty')

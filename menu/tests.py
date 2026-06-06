from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from .models import Category, MenuItem, Restaurant


class MenuViewsTests(TestCase):
    def setUp(self):
        self.restaurant = Restaurant.objects.create(
            name='Test Kitchen',
            slug='test-kitchen',
            delivery_fee=Decimal('5.00'),
        )
        self.category = Category.objects.create(
            restaurant=self.restaurant,
            name='Mains',
            slug='mains',
        )
        self.available_item = MenuItem.objects.create(
            category=self.category,
            name='Burger',
            slug='burger',
            description='A test burger.',
            price=Decimal('20.00'),
            is_available=True,
        )
        self.unavailable_item = MenuItem.objects.create(
            category=self.category,
            name='Soup',
            slug='soup',
            price=Decimal('12.00'),
            is_available=False,
        )

    def test_menu_shows_available_and_unavailable_items(self):
        response = self.client.get(reverse('menu:menu_list'))

        self.assertContains(response, 'Burger')
        self.assertContains(response, 'Soup')
        self.assertContains(response, 'Indispon')

    def test_item_detail_renders_item(self):
        response = self.client.get(
            reverse(
                'menu:item_detail',
                args=[self.available_item.pk, self.available_item.slug],
            ),
        )

        self.assertContains(response, 'A test burger.')
        self.assertContains(response, 'Adicionar ao carrinho')

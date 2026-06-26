from datetime import time
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from .models import BusinessHours, Category, MenuItem, Restaurant
from .selectors import get_open_status


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


class BusinessHoursTests(TestCase):
    def setUp(self):
        self.restaurant = Restaurant.objects.create(name='Test Kitchen', slug='tk')

    def test_open_status_unknown_without_today_hours(self):
        status = get_open_status(self.restaurant)
        self.assertIsNone(status['is_open'])

    def test_open_status_closed_when_day_marked_closed(self):
        from django.utils import timezone
        today = timezone.localtime().weekday()
        BusinessHours.objects.create(
            restaurant=self.restaurant, day_of_week=today, is_closed=True,
        )
        status = get_open_status(self.restaurant)
        self.assertFalse(status['is_open'])

    def test_edit_requires_staff(self):
        url = reverse('menu:edit_business_hours')
        # GET is gated too: non-staff are redirected to login.
        self.assertEqual(self.client.get(url).status_code, 302)
        response = self.client.post(url, {'closed_0': 'on'})
        self.assertEqual(response.status_code, 302)
        self.assertFalse(BusinessHours.objects.exists())

    def test_staff_sees_edit_page(self):
        user = get_user_model().objects.create_user(
            username='admin', password='pw', is_staff=True,
        )
        self.client.force_login(user)
        response = self.client.get(reverse('menu:edit_business_hours'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Horário de funcionamento')
        self.assertContains(response, 'Salvar horários')

    def test_staff_can_edit_business_hours(self):
        user = get_user_model().objects.create_user(
            username='admin', password='pw', is_staff=True,
        )
        self.client.force_login(user)
        response = self.client.post(
            reverse('menu:edit_business_hours'),
            {
                # Monday active with hours; Thursday/Tuesday left inactive (closed).
                'active_0': 'on',
                'open_0': '09:00',
                'close_0': '18:00',
            },
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        monday = BusinessHours.objects.get(day_of_week=0)
        self.assertFalse(monday.is_closed)
        self.assertEqual(monday.open_time, time(9, 0))
        self.assertEqual(monday.close_time, time(18, 0))
        # Thursday was not activated → stored as closed.
        self.assertTrue(BusinessHours.objects.get(day_of_week=3).is_closed)
        # Tuesday had no times and was not activated → stored as closed.
        self.assertTrue(BusinessHours.objects.get(day_of_week=1).is_closed)

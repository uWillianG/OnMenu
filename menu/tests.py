import io
import os
import tempfile
from datetime import datetime, time, timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse
from PIL import Image

from . import imaging
from .models import (
    BusinessHours,
    Category,
    ComplementChoice,
    ComplementGroup,
    MenuItem,
    Restaurant,
)
from .selectors import get_open_status, is_restaurant_open


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

    def test_menu_hides_unavailable_items(self):
        response = self.client.get(reverse('menu:menu_list'))

        self.assertContains(response, 'Burger')
        self.assertNotContains(response, 'Soup')

    def test_item_detail_renders_item(self):
        response = self.client.get(
            reverse(
                'menu:item_detail',
                args=[self.available_item.pk, self.available_item.slug],
            ),
        )

        self.assertContains(response, 'A test burger.')
        self.assertContains(response, 'Adicionar ao carrinho')


class RestaurantInfoTests(TestCase):
    def setUp(self):
        self.restaurant = Restaurant.objects.create(
            name='Test Kitchen', slug='tk',
            phone='1133334444', whatsapp_number='+55 (11) 99999-8888',
            address='Rua Teste, 123', delivery_fee=Decimal('7.50'),
            delivery_time_min=30, delivery_time_max=45,
        )

    def test_info_page_is_public_and_shows_data(self):
        response = self.client.get(reverse('menu:restaurant_info'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Test Kitchen')
        self.assertContains(response, 'Rua Teste, 123')
        self.assertContains(response, '1133334444')
        self.assertContains(response, 'Horário de funcionamento')
        # WhatsApp vira link wa.me com apenas dígitos.
        self.assertContains(response, 'https://wa.me/5511999998888')

    def test_info_page_lists_all_weekdays(self):
        response = self.client.get(reverse('menu:restaurant_info'))
        for _value, label in BusinessHours.DAY_CHOICES:
            self.assertContains(response, label)

    def test_delivery_fee_shown_as_range(self):
        from orders.models import City, Neighborhood
        city = City.objects.create(name='São Paulo', delivery_fee=Decimal('3.00'))
        Neighborhood.objects.create(city=city, name='Centro', delivery_fee=Decimal('2.00'))
        Neighborhood.objects.create(city=city, name='Zona Sul', delivery_fee=Decimal('6.00'))
        response = self.client.get(reverse('menu:restaurant_info'))
        # mín = 3+2 = 5,00 ; máx = 3+6 = 9,00
        self.assertContains(response, '5,00')
        self.assertContains(response, '9,00')
        self.assertContains(response, '–')

    def test_edit_info_requires_staff(self):
        url = reverse('menu:edit_restaurant_info')
        self.assertEqual(self.client.get(url).status_code, 302)
        response = self.client.post(url, {'address': 'Hack', 'phone': '', 'whatsapp_number': ''})
        self.assertEqual(response.status_code, 302)
        self.restaurant.refresh_from_db()
        self.assertEqual(self.restaurant.address, 'Rua Teste, 123')

    def test_staff_sees_edit_info_page(self):
        staff = get_user_model().objects.create_user(
            username='admin', password='pw', is_staff=True,
        )
        self.client.force_login(staff)
        response = self.client.get(reverse('menu:edit_restaurant_info'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Editar informações')
        self.assertContains(response, 'Rua Teste, 123')

    def test_staff_can_edit_info(self):
        staff = get_user_model().objects.create_user(
            username='admin', password='pw', is_staff=True,
        )
        self.client.force_login(staff)
        response = self.client.post(
            reverse('menu:edit_restaurant_info'),
            {
                'address': 'Av. Nova, 999',
                'phone': '1144445555',
                'whatsapp_number': '(11) 98888-7777',
                'delivery_time_min': 20,
                'delivery_time_max': 40,
            },
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        self.restaurant.refresh_from_db()
        self.assertEqual(self.restaurant.address, 'Av. Nova, 999')
        self.assertEqual(self.restaurant.phone, '1144445555')
        self.assertEqual(self.restaurant.delivery_time_min, 20)
        self.assertEqual(self.restaurant.delivery_time_max, 40)

    def test_edit_info_rejects_inverted_delivery_time(self):
        staff = get_user_model().objects.create_user(
            username='admin', password='pw', is_staff=True,
        )
        self.client.force_login(staff)
        response = self.client.post(
            reverse('menu:edit_restaurant_info'),
            {
                'address': 'Rua Teste, 123',
                'phone': '',
                'whatsapp_number': '',
                'delivery_time_min': 60,
                'delivery_time_max': 30,
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'maior ou igual')
        self.restaurant.refresh_from_db()
        self.assertEqual(self.restaurant.delivery_time_min, 30)

    def test_logo_upload_requires_staff(self):
        self.assertEqual(
            self.client.post(reverse('menu:update_logo')).status_code, 302
        )

    def test_staff_can_upload_logo(self):
        import io
        from PIL import Image
        from django.core.files.uploadedfile import SimpleUploadedFile

        buffer = io.BytesIO()
        Image.new('RGB', (10, 10), 'red').save(buffer, format='PNG')
        logo = SimpleUploadedFile('logo.png', buffer.getvalue(), content_type='image/png')

        staff = get_user_model().objects.create_user(
            username='admin', password='pw', is_staff=True,
        )
        self.client.force_login(staff)
        self.client.post(reverse('menu:update_logo'), {'logo': logo})
        self.restaurant.refresh_from_db()
        self.assertTrue(self.restaurant.logo)
        self.restaurant.logo.delete(save=False)  # limpa o arquivo de teste


class ManageMenuTests(TestCase):
    def setUp(self):
        self.restaurant = Restaurant.objects.create(name='Test Kitchen', slug='tk')
        self.category = Category.objects.create(
            restaurant=self.restaurant, name='Lanches', slug='lanches',
        )
        self.staff = get_user_model().objects.create_user(
            username='admin', password='pw', is_staff=True,
        )

    def test_requires_staff(self):
        url = reverse('menu:manage_menu')
        self.assertEqual(self.client.get(url).status_code, 302)

    def test_staff_sees_manage_page(self):
        self.client.force_login(self.staff)
        response = self.client.get(reverse('menu:manage_menu'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Gerenciar cardápio')
        self.assertContains(response, 'Lanches')

    def test_staff_can_create_category(self):
        self.client.force_login(self.staff)
        response = self.client.post(
            reverse('menu:category_create'),
            {'name': 'Bebidas', 'display_order': 0, 'is_active': 'on'},
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(
            Category.objects.filter(restaurant=self.restaurant, name='Bebidas').exists()
        )

    def test_item_form_renders(self):
        self.client.force_login(self.staff)
        response = self.client.get(reverse('menu:item_create'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Novo item')
        self.assertContains(response, 'Categoria')

    def test_staff_can_create_item(self):
        self.client.force_login(self.staff)
        response = self.client.post(
            reverse('menu:item_create'),
            {
                'category': self.category.pk,
                'name': 'X-Burger',
                'description': 'Delícia',
                'price': '25,50',
                'image_url': '',
                'display_order': 0,
                'is_available': 'on',
            },
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        item = MenuItem.objects.get(name='X-Burger')
        self.assertEqual(item.category, self.category)
        self.assertEqual(item.price, Decimal('25.50'))

    def test_price_accepts_br_format_with_thousands(self):
        self.client.force_login(self.staff)
        self.client.post(
            reverse('menu:item_create'),
            {
                'category': self.category.pk,
                'name': 'Combo Família',
                'description': '',
                'price': '1.234,56',
                'image_url': '',
                'display_order': 0,
                'is_available': 'on',
            },
            follow=True,
        )
        item = MenuItem.objects.get(name='Combo Família')
        self.assertEqual(item.price, Decimal('1234.56'))

    def test_staff_can_edit_item(self):
        item = MenuItem.objects.create(
            category=self.category, name='Old', slug='old', price=Decimal('10.00'),
        )
        self.client.force_login(self.staff)
        self.client.post(
            reverse('menu:item_edit', args=[item.pk]),
            {
                'category': self.category.pk,
                'name': 'New',
                'description': '',
                'price': '15,00',
                'image_url': '',
                'display_order': 0,
                'is_available': 'on',
            },
            follow=True,
        )
        item.refresh_from_db()
        self.assertEqual(item.name, 'New')
        self.assertEqual(item.price, Decimal('15.00'))

    def test_staff_can_toggle_availability(self):
        item = MenuItem.objects.create(
            category=self.category, name='Bloqueável', slug='bloq',
            price=Decimal('5.00'), is_available=True,
        )
        self.client.force_login(self.staff)
        self.client.post(reverse('menu:item_toggle_available', args=[item.pk]))
        item.refresh_from_db()
        self.assertFalse(item.is_available)
        self.client.post(reverse('menu:item_toggle_available', args=[item.pk]))
        item.refresh_from_db()
        self.assertTrue(item.is_available)

    def test_toggle_availability_ajax_returns_json(self):
        item = MenuItem.objects.create(
            category=self.category, name='Ajax', slug='ajax',
            price=Decimal('5.00'), is_available=True,
        )
        self.client.force_login(self.staff)
        response = self.client.post(
            reverse('menu:item_toggle_available', args=[item.pk]),
            HTTP_X_REQUESTED_WITH='XMLHttpRequest',
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['available'], False)
        item.refresh_from_db()
        self.assertFalse(item.is_available)

    def test_staff_can_delete_item(self):
        item = MenuItem.objects.create(
            category=self.category, name='Trash', slug='trash', price=Decimal('1.00'),
        )
        self.client.force_login(self.staff)
        self.client.post(reverse('menu:item_delete', args=[item.pk]))
        self.assertFalse(MenuItem.objects.filter(pk=item.pk).exists())


class ComplementTests(TestCase):
    def setUp(self):
        self.restaurant = Restaurant.objects.create(name='Test Kitchen', slug='tk')
        self.category = Category.objects.create(
            restaurant=self.restaurant, name='Lanches', slug='lanches',
        )
        self.item = MenuItem.objects.create(
            category=self.category, name='Burger', slug='burger',
            price=Decimal('20.00'), is_available=True,
        )
        self.staff = get_user_model().objects.create_user(
            username='admin', password='pw', is_staff=True,
        )

    def _group_with_choices(self):
        group = ComplementGroup.objects.create(
            restaurant=self.restaurant, name='Complementos',
            selection_type='multiple', required=False,
        )
        ComplementChoice.objects.create(group=group, name='Bacon', extra_price=Decimal('5.00'))
        return group

    def test_create_requires_staff(self):
        self.assertEqual(
            self.client.get(reverse('menu:complement_create')).status_code, 302,
        )

    def test_staff_can_create_complement_with_choices(self):
        self.client.force_login(self.staff)
        response = self.client.post(
            reverse('menu:complement_create'),
            {
                'name': 'Ponto do hambúrguer',
                'selection_type': 'single',
                'required': 'on',
                'display_order': 10,
                'choices-TOTAL_FORMS': 2,
                'choices-INITIAL_FORMS': 0,
                'choices-MIN_NUM_FORMS': 0,
                'choices-MAX_NUM_FORMS': 1000,
                'choices-0-name': 'Mal passado',
                'choices-0-extra_price': '',
                'choices-0-display_order': 0,
                'choices-1-name': 'Ao ponto',
                'choices-1-extra_price': '0,00',
                'choices-1-display_order': 0,
            },
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        group = ComplementGroup.objects.get(name='Ponto do hambúrguer')
        self.assertEqual(group.restaurant, self.restaurant)
        self.assertEqual(group.selection_type, 'single')
        self.assertTrue(group.required)
        self.assertEqual(group.choices.count(), 2)
        # Preço em branco vira 0,00 (campo NOT NULL).
        self.assertEqual(group.choices.get(name='Mal passado').extra_price, Decimal('0.00'))

    def test_create_ignores_blank_option_row(self):
        # Uma linha em branco (o que resta após adicionar e excluir uma opção
        # nova no formulário) não deve travar o salvamento.
        self.client.force_login(self.staff)
        response = self.client.post(
            reverse('menu:complement_create'),
            {
                'name': 'Molhos',
                'selection_type': 'multiple',
                'display_order': 0,
                'choices-TOTAL_FORMS': 2,
                'choices-INITIAL_FORMS': 0,
                'choices-MIN_NUM_FORMS': 0,
                'choices-MAX_NUM_FORMS': 1000,
                'choices-0-name': 'Barbecue',
                'choices-0-extra_price': '2,00',
                'choices-0-display_order': 0,
                # Linha 1 totalmente em branco (marcada como excluída pelo JS).
                'choices-1-name': '',
                'choices-1-extra_price': '',
                'choices-1-display_order': '',
                'choices-1-DELETE': 'on',
            },
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        group = ComplementGroup.objects.get(name='Molhos')
        self.assertEqual(group.choices.count(), 1)
        self.assertEqual(group.choices.first().name, 'Barbecue')

    def test_create_rejects_nameless_row_with_price(self):
        # Linha com preço (0,00) mas sem nome não pode ser salva "em branco":
        # o servidor rejeita exigindo o nome (não descarta silenciosamente).
        self.client.force_login(self.staff)
        response = self.client.post(
            reverse('menu:complement_create'),
            {
                'name': 'Ponto',
                'selection_type': 'single',
                'required': 'on',
                'display_order': 0,
                'choices-TOTAL_FORMS': 2,
                'choices-INITIAL_FORMS': 0,
                'choices-MIN_NUM_FORMS': 0,
                'choices-MAX_NUM_FORMS': 1000,
                'choices-0-name': 'Ao ponto',
                'choices-0-extra_price': '0,00',
                'choices-1-name': '',
                'choices-1-extra_price': '0,00',
            },
        )
        self.assertEqual(response.status_code, 200)  # re-renderiza com erro
        self.assertContains(response, 'obrigatório')
        self.assertFalse(ComplementGroup.objects.filter(name='Ponto').exists())

    def test_create_ignores_empty_untouched_row(self):
        # Linha totalmente em branco (sem nome e sem preço) e não excluída é
        # ignorada — desde que exista ao menos uma opção válida.
        self.client.force_login(self.staff)
        response = self.client.post(
            reverse('menu:complement_create'),
            {
                'name': 'Ponto',
                'selection_type': 'single',
                'required': 'on',
                'display_order': 0,
                'choices-TOTAL_FORMS': 2,
                'choices-INITIAL_FORMS': 0,
                'choices-MIN_NUM_FORMS': 0,
                'choices-MAX_NUM_FORMS': 1000,
                'choices-0-name': 'Ao ponto',
                'choices-0-extra_price': '0,00',
                'choices-1-name': '',
                'choices-1-extra_price': '',
            },
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        group = ComplementGroup.objects.get(name='Ponto')
        self.assertEqual(group.choices.count(), 1)

    def test_create_rejects_group_without_options(self):
        self.client.force_login(self.staff)
        response = self.client.post(
            reverse('menu:complement_create'),
            {
                'name': 'Vazio',
                'selection_type': 'single',
                'display_order': 0,
                'choices-TOTAL_FORMS': 1,
                'choices-INITIAL_FORMS': 0,
                'choices-MIN_NUM_FORMS': 0,
                'choices-MAX_NUM_FORMS': 1000,
                'choices-0-name': '',
                'choices-0-extra_price': '',
            },
        )
        self.assertEqual(response.status_code, 200)  # re-renderiza com erro
        self.assertContains(response, 'pelo menos uma opção')
        self.assertFalse(ComplementGroup.objects.filter(name='Vazio').exists())

    def test_edit_without_touching_options_keeps_them(self):
        group = self._group_with_choices()  # 1 opção: Bacon
        self.client.force_login(self.staff)
        response = self.client.post(
            reverse('menu:complement_edit', args=[group.pk]),
            {
                'name': 'Complementos renomeado',
                'selection_type': 'multiple',
                'display_order': 0,
                'choices-TOTAL_FORMS': 1,
                'choices-INITIAL_FORMS': 1,
                'choices-MIN_NUM_FORMS': 0,
                'choices-MAX_NUM_FORMS': 1000,
                'choices-0-id': group.choices.first().pk,
                'choices-0-name': 'Bacon',
                'choices-0-extra_price': '5,00',
            },
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        group.refresh_from_db()
        self.assertEqual(group.name, 'Complementos renomeado')
        self.assertEqual(group.choices.count(), 1)

    def test_manage_menu_lists_complements(self):
        self._group_with_choices()
        self.client.force_login(self.staff)
        response = self.client.get(reverse('menu:manage_menu'))
        self.assertContains(response, 'Complementos')
        self.assertContains(response, 'Novo complemento')

    def test_staff_can_edit_complement(self):
        group = self._group_with_choices()
        choice = group.choices.first()
        self.client.force_login(self.staff)
        self.client.post(
            reverse('menu:complement_edit', args=[group.pk]),
            {
                'name': 'Adicionais',
                'selection_type': 'multiple',
                'display_order': 5,
                'choices-TOTAL_FORMS': 1,
                'choices-INITIAL_FORMS': 1,
                'choices-MIN_NUM_FORMS': 0,
                'choices-MAX_NUM_FORMS': 1000,
                'choices-0-id': choice.pk,
                'choices-0-name': 'Bacon duplo',
                'choices-0-extra_price': '7,00',
                'choices-0-display_order': 0,
            },
            follow=True,
        )
        group.refresh_from_db()
        choice.refresh_from_db()
        self.assertEqual(group.name, 'Adicionais')
        self.assertEqual(choice.name, 'Bacon duplo')
        self.assertEqual(choice.extra_price, Decimal('7.00'))

    def test_staff_can_delete_complement_unlinks_item(self):
        group = self._group_with_choices()
        self.item.complement_groups.add(group)
        self.client.force_login(self.staff)
        self.client.post(reverse('menu:complement_delete', args=[group.pk]))
        self.assertFalse(ComplementGroup.objects.filter(pk=group.pk).exists())
        self.assertEqual(self.item.complement_groups.count(), 0)

    def test_item_form_links_complement_groups(self):
        group = self._group_with_choices()
        self.client.force_login(self.staff)
        self.client.post(
            reverse('menu:item_edit', args=[self.item.pk]),
            {
                'category': self.category.pk,
                'name': 'Burger',
                'description': '',
                'price': '20,00',
                'image_url': '',
                'display_order': 0,
                'is_available': 'on',
                'complement_groups': [group.pk],
            },
            follow=True,
        )
        self.assertIn(group, self.item.complement_groups.all())

    def test_cart_adds_choice_extra_price(self):
        group = self._group_with_choices()
        self.item.complement_groups.add(group)
        choice = group.choices.get(name='Bacon')
        response = self.client.post(
            reverse('cart:cart_add', args=[self.item.pk]),
            {'quantity': 1, f'option_group_{group.pk}': choice.pk},
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        # 20,00 (item) + 5,00 (Bacon) = 25,00
        self.assertContains(response, '25,00')


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


class OvernightBusinessHoursTests(TestCase):
    """Turnos que viram a madrugada, ex.: 18:00 → 02:00."""

    MONDAY = datetime(2026, 7, 20)  # segunda-feira

    def setUp(self):
        self.restaurant = Restaurant.objects.create(name='Night Kitchen', slug='nk')
        for day in range(7):
            BusinessHours.objects.create(
                restaurant=self.restaurant,
                day_of_week=day,
                open_time=time(18, 0),
                close_time=time(2, 0),
            )

    def _at(self, day_offset, hour, minute=0):
        """Congela o relógio em um momento da semana e devolve o status."""
        moment = self.MONDAY + timedelta(days=day_offset, hours=hour, minutes=minute)
        return patch('menu.selectors.timezone.localtime', return_value=moment)

    def test_open_during_the_evening(self):
        with self._at(0, 20):
            status = get_open_status(self.restaurant)
        self.assertTrue(status['is_open'])
        self.assertEqual(status['detail'], 'Fecha às 02:00')

    def test_open_after_midnight_on_previous_day_shift(self):
        # 00:30 de terça ainda é o turno que abriu na segunda às 18:00.
        with self._at(1, 0, 30):
            status = get_open_status(self.restaurant)
        self.assertTrue(status['is_open'])
        self.assertEqual(status['detail'], 'Fecha às 02:00')

    def test_closed_after_the_shift_ends(self):
        with self._at(1, 3):
            status = get_open_status(self.restaurant)
        self.assertFalse(status['is_open'])
        self.assertEqual(status['detail'], 'Abre hoje às 18:00')

    def test_closed_before_opening(self):
        with self._at(0, 10):
            status = get_open_status(self.restaurant)
        self.assertFalse(status['is_open'])
        self.assertEqual(status['detail'], 'Abre hoje às 18:00')

    def test_checkout_is_not_blocked_during_the_overnight_shift(self):
        with self._at(1, 1):
            self.assertTrue(is_restaurant_open(self.restaurant))

    def test_open_after_midnight_even_when_today_is_closed(self):
        # Terça marcada como fechada não encerra o turno que veio da segunda.
        BusinessHours.objects.filter(day_of_week=1).update(
            is_closed=True, open_time=None, close_time=None,
        )
        with self._at(1, 0, 30):
            status = get_open_status(self.restaurant)
        self.assertTrue(status['is_open'])

        with self._at(1, 5):
            status = get_open_status(self.restaurant)
        self.assertFalse(status['is_open'])
        self.assertEqual(status['detail'], 'Abre amanhã às 18:00')


class DaytimeBusinessHoursTests(TestCase):
    """Turno normal (não vira o dia) continua com o mesmo comportamento."""

    MONDAY = datetime(2026, 7, 20)

    def setUp(self):
        self.restaurant = Restaurant.objects.create(name='Day Kitchen', slug='dk')
        BusinessHours.objects.create(
            restaurant=self.restaurant,
            day_of_week=0,
            open_time=time(9, 0),
            close_time=time(18, 0),
        )

    def _at(self, hour, minute=0):
        moment = self.MONDAY + timedelta(hours=hour, minutes=minute)
        return patch('menu.selectors.timezone.localtime', return_value=moment)

    def test_open_inside_the_shift(self):
        with self._at(12):
            status = get_open_status(self.restaurant)
        self.assertTrue(status['is_open'])
        self.assertEqual(status['detail'], 'Fecha às 18:00')

    def test_closed_after_the_shift(self):
        with self._at(19):
            status = get_open_status(self.restaurant)
        self.assertFalse(status['is_open'])
        # Só a segunda tem horário: a próxima abertura é daqui a uma semana.
        self.assertEqual(status['detail'], 'Abre seg. às 09:00')

    def test_closed_after_midnight(self):
        # 00:30 de terça: o turno da segunda fechou às 18:00, não vira o dia.
        moment = self.MONDAY + timedelta(days=1, minutes=30)
        with patch('menu.selectors.timezone.localtime', return_value=moment):
            status = get_open_status(self.restaurant)
        self.assertIsNone(status['is_open'])


def _stored_image(field_file):
    """Abre a imagem gravada a partir dos bytes, sem deixar o arquivo aberto.

    No Windows um handle pendurado impede a remoção do diretório temporário no
    fim do teste.
    """
    with field_file.open('rb') as handle:
        data = handle.read()
    field_file.close()
    return Image.open(io.BytesIO(data))


class ImageCompressionTests(TestCase):
    """As fotos entram reduzidas: quem paga o peso é o cliente no 4G.

    As imagens de teste são ruído aleatório de propósito — uma imagem de cor
    sólida comprimiria a quase nada e o teste passaria sem provar nada.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        media = override_settings(MEDIA_ROOT=self.tmp.name)
        media.enable()
        self.addCleanup(media.disable)

        self.restaurant = Restaurant.objects.create(name='Foto Kitchen', slug='foto')
        self.category = Category.objects.create(
            restaurant=self.restaurant, name='Lanches', slug='lanches',
        )

    def _upload(self, size=(1200, 900), fmt='JPEG', name='foto.jpg'):
        image = Image.frombytes('RGB', size, os.urandom(size[0] * size[1] * 3))
        buffer = io.BytesIO()
        image.save(buffer, fmt, quality=95)
        return SimpleUploadedFile(name, buffer.getvalue(), content_type='image/jpeg')

    def _item(self, upload):
        return MenuItem.objects.create(
            category=self.category, name='Burger', price=Decimal('20.00'),
            image=upload,
        )

    def test_large_photo_is_resized_on_upload(self):
        upload = self._upload(size=(1200, 900))
        original_bytes = upload.size

        item = self._item(upload)

        with _stored_image(item.image) as stored:
            self.assertLessEqual(max(stored.size), max(imaging.MENU_PHOTO_SIZE))
            self.assertEqual(stored.size, (1000, 750))   # proporção preservada
        self.assertLess(item.image.size, original_bytes)

    def test_logo_uses_a_tighter_cap_than_menu_photos(self):
        """O logo aparece a 80px na tela; guardar 1000px é desperdício puro."""
        self.restaurant.logo = self._upload(size=(1000, 1000), name='logo.jpg')
        self.restaurant.save()

        with _stored_image(self.restaurant.logo) as stored:
            self.assertLessEqual(max(stored.size), max(imaging.LOGO_SIZE))

    def test_small_file_is_left_alone(self):
        """Abaixo do limiar o reprocessamento não paga o próprio custo."""
        buffer = io.BytesIO()
        Image.new('RGB', (40, 40), 'red').save(buffer, 'JPEG')
        buffer.seek(0)
        self.assertIsNone(imaging.compress(buffer))

    def test_unreadable_file_does_not_raise(self):
        """Imagem é acessório: um arquivo corrompido não pode derrubar o cadastro."""
        garbage = io.BytesIO(os.urandom(imaging.MIN_BYTES + 1000))
        with self.assertLogs('menu.imaging', level='WARNING'):
            self.assertIsNone(imaging.compress(garbage))

    def test_already_optimized_image_is_not_reprocessed(self):
        upload = self._upload(size=(1200, 900))
        item = self._item(upload)
        first_size = item.image.size

        # Salvar de novo (mudando outro campo) não pode reabrir nem regravar a
        # foto que já está no disco.
        item.name = 'Burger Duplo'
        item.save()

        item.refresh_from_db()
        self.assertEqual(item.image.size, first_size)

    def test_compress_preserves_the_original_format(self):
        """Trocar de formato obrigaria a mexer na extensão do arquivo."""
        image = Image.frombytes('RGB', (900, 700), os.urandom(900 * 700 * 3))
        buffer = io.BytesIO()
        image.save(buffer, 'PNG')
        buffer.seek(0)

        compressed = imaging.compress(buffer, max_size=(400, 400))
        self.assertIsNotNone(compressed)
        with Image.open(compressed) as result:
            self.assertEqual(result.format, 'PNG')


class CompressImagesCommandTests(TestCase):
    """Comando que trata o acervo já gravado, não só os uploads novos."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        media = override_settings(MEDIA_ROOT=self.tmp.name)
        media.enable()
        self.addCleanup(media.disable)

        self.restaurant = Restaurant.objects.create(name='Acervo', slug='acervo')
        self.category = Category.objects.create(
            restaurant=self.restaurant, name='Lanches', slug='lanches',
        )

    def _item_with_untouched_photo(self):
        """Cria um item cuja foto foi gravada sem passar pela compressão."""
        image = Image.frombytes('RGB', (1600, 1200), os.urandom(1600 * 1200 * 3))
        buffer = io.BytesIO()
        image.save(buffer, 'JPEG', quality=95)

        item = MenuItem.objects.create(
            category=self.category, name='Burger', price=Decimal('20.00'),
        )
        item.image.save('grande.jpg', ContentFile(buffer.getvalue()), save=True)
        return item

    def test_dry_run_reports_without_writing(self):
        item = self._item_with_untouched_photo()
        before = item.image.size

        out = io.StringIO()
        call_command('compress_images', '--dry-run', stdout=out)

        item.refresh_from_db()
        self.assertEqual(item.image.size, before)
        self.assertIn('simulação', out.getvalue())

    def test_command_shrinks_stored_images_in_place(self):
        item = self._item_with_untouched_photo()
        before = item.image.size
        name_before = item.image.name

        call_command('compress_images', stdout=io.StringIO())

        item.refresh_from_db()
        # O nome não muda: URLs já compartilhadas continuam válidas.
        self.assertEqual(item.image.name, name_before)
        self.assertLess(item.image.size, before)
        with _stored_image(item.image) as stored:
            self.assertLessEqual(max(stored.size), max(imaging.MENU_PHOTO_SIZE))

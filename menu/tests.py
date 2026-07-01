from datetime import time
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from .models import (
    BusinessHours,
    Category,
    ComplementChoice,
    ComplementGroup,
    MenuItem,
    Restaurant,
)
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

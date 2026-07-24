import hashlib
import hmac
import json
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.admin.sites import site as admin_site
from django.contrib.auth.models import User
from django.db import IntegrityError
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from menu.models import Category, MenuItem, Restaurant

from . import models, selectors, views
from .admin import OrderItemAdmin
from .models import (
    generate_order_number,
    CardPayment,
    City,
    Neighborhood,
    Notification,
    Order,
    OrderItem,
    PixPayment,
)
from .selectors import SESSION_ORDERS_KEY


class OrderNumberSequenceTests(TestCase):
    def setUp(self):
        self.restaurant = Restaurant.objects.create(name='Seq Kitchen', slug='seq')

    def _make_order(self):
        return Order.objects.create(
            restaurant=self.restaurant, customer_name='Cliente', phone='000',
        )

    def test_numbers_are_sequential_starting_at_one(self):
        first = self._make_order()
        second = self._make_order()
        third = self._make_order()
        self.assertEqual(first.order_number, 'OM-1')
        self.assertEqual(second.order_number, 'OM-2')
        self.assertEqual(third.order_number, 'OM-3')

    def test_legacy_format_orders_are_ignored(self):
        # Pedido antigo no formato OM-AAAAMMDD-XXXXXX não interfere na sequência.
        Order.objects.create(
            restaurant=self.restaurant, customer_name='Antigo', phone='000',
            order_number='OM-20250101-ABC123',
        )
        nxt = self._make_order()
        self.assertEqual(nxt.order_number, 'OM-1')

    def test_sequence_continues_after_highest(self):
        self._make_order()  # OM-1
        self._make_order()  # OM-2
        # Remove o último; o próximo deve seguir a partir do maior existente (OM-1).
        Order.objects.get(order_number='OM-2').delete()
        self.assertEqual(self._make_order().order_number, 'OM-2')

    def test_retries_when_the_number_was_taken(self):
        """Dois checkouts simultâneos calculam o mesmo número: o 2º tenta de novo."""
        taken = self._make_order()  # OM-1
        with patch(
            'orders.models.generate_order_number', side_effect=['OM-1', 'OM-2'],
        ) as generate:
            order = self._make_order()
        self.assertEqual(generate.call_count, 2)
        self.assertEqual(order.order_number, 'OM-2')
        self.assertNotEqual(order.pk, taken.pk)

    def test_gives_up_after_the_attempt_limit(self):
        self._make_order()  # OM-1
        with patch('orders.models.generate_order_number', return_value='OM-1'):
            with self.assertRaises(IntegrityError):
                self._make_order()
        self.assertEqual(Order.objects.count(), 1)

    def test_number_is_found_in_a_single_query(self):
        """Antes era um filtro por regex — varredura da tabela a cada checkout."""
        for _ in range(5):
            self._make_order()
        with self.assertNumQueries(1):
            self.assertEqual(generate_order_number(), 'OM-6')

    def test_sequence_survives_a_legacy_order_on_top(self):
        """Pedido no formato antigo mais recente não zera a sequência."""
        self._make_order()  # OM-1
        self._make_order()  # OM-2
        Order.objects.create(
            restaurant=self.restaurant, customer_name='Antigo', phone='000',
            order_number='OM-20250101-ABC123',
        )
        self.assertEqual(self._make_order().order_number, 'OM-3')

    def test_lookback_window_is_wide_enough_for_a_transition(self):
        """A janela cobre uma base em transição, com formatos misturados."""
        self._make_order()  # OM-1
        for index in range(models.ORDER_NUMBER_LOOKBACK - 2):
            Order.objects.create(
                restaurant=self.restaurant, customer_name='Antigo', phone='000',
                order_number=f'OM-20250101-{index:06d}',
            )
        self.assertEqual(self._make_order().order_number, 'OM-2')


class OrderVisibilityTests(TestCase):
    """Os números são sequenciais: as páginas de pedido não podem ser varridas."""

    def setUp(self):
        self.restaurant = Restaurant.objects.create(name='Vis Kitchen', slug='vis')
        self.order = Order.objects.create(
            restaurant=self.restaurant,
            customer_name='Ada Lovelace',
            phone='555-0100',
            subtotal=Decimal('30.00'),
        )

    def _remember_in_session(self, order_number):
        session = self.client.session
        session[SESSION_ORDERS_KEY] = [order_number]
        session.save()

    def test_stranger_gets_404_on_confirmation(self):
        response = self.client.get(
            reverse('orders:confirmation', args=[self.order.order_number]),
        )
        self.assertEqual(response.status_code, 404)

    def test_stranger_gets_404_on_tracking(self):
        response = self.client.get(
            reverse('orders:track_order', args=[self.order.order_number]),
        )
        self.assertEqual(response.status_code, 404)

    def test_session_owner_sees_the_order(self):
        self._remember_in_session(self.order.order_number)
        response = self.client.get(
            reverse('orders:confirmation', args=[self.order.order_number]),
        )
        self.assertEqual(response.status_code, 200)

    def test_account_owner_sees_the_order(self):
        user = User.objects.create_user(username='ada', password='pw')
        self.order.user = user
        self.order.save(update_fields=['user'])

        self.client.force_login(user)
        response = self.client.get(
            reverse('orders:track_order', args=[self.order.order_number]),
        )
        self.assertEqual(response.status_code, 200)

    def test_logged_in_customer_cannot_see_someone_elses_order(self):
        other = User.objects.create_user(username='bob', password='pw')
        self.client.force_login(other)
        response = self.client.get(
            reverse('orders:confirmation', args=[self.order.order_number]),
        )
        self.assertEqual(response.status_code, 404)

    def test_staff_sees_any_order(self):
        staff = User.objects.create_user(username='chef', password='pw', is_staff=True)
        self.client.force_login(staff)
        response = self.client.get(
            reverse('orders:confirmation', args=[self.order.order_number]),
        )
        self.assertEqual(response.status_code, 200)

    def test_checkout_keeps_access_to_the_new_order(self):
        """O fluxo normal continua funcionando: o checkout guarda o pedido na sessão."""
        category = Category.objects.create(
            restaurant=self.restaurant, name='Mains', slug='mains',
        )
        item = MenuItem.objects.create(
            category=category, name='Burger', slug='burger',
            price=Decimal('20.00'), is_available=True,
        )
        self.client.post(reverse('cart:cart_add', args=[item.pk]), {'quantity': 1})
        response = self.client.post(
            reverse('orders:checkout'),
            {
                'fulfillment_method': Order.FulfillmentMethod.PICKUP,
                'customer_name': 'Ada Lovelace',
                'phone': '555-0100',
                'payment_method': Order.PaymentMethod.CASH,
            },
            follow=True,
        )
        self.assertEqual(response.status_code, 200)


@override_settings(
    DEBUG=False,
    MERCADOPAGO_MOCK=False,
    MERCADOPAGO_ACCESS_TOKEN='token-de-producao',
)
class WebhookSignatureTests(TestCase):
    """Em produção o webhook exige assinatura verificável."""

    def _post(self):
        return self.client.post(
            reverse('orders:webhook_pix'),
            data=json.dumps({'type': 'payment', 'data': {'id': '123'}}),
            content_type='application/json',
        )

    @override_settings(MERCADOPAGO_WEBHOOK_SECRET='')
    def test_rejected_without_secret_configured(self):
        with self.assertLogs('orders.views', level='ERROR') as logs:
            self.assertEqual(self._post().status_code, 401)
        self.assertIn('MERCADOPAGO_WEBHOOK_SECRET', logs.output[0])

    @override_settings(MERCADOPAGO_WEBHOOK_SECRET='segredo')
    def test_rejected_with_wrong_signature(self):
        self.assertEqual(self._post().status_code, 401)

    @override_settings(MERCADOPAGO_WEBHOOK_SECRET='segredo')
    @patch('orders.services.mercadopago.buscar_status')
    def test_accepted_with_valid_signature(self, mock_status):
        mock_status.return_value = {'id': '123', 'status': 'approved', 'external_reference': 'OM-1'}
        ts = '1700000000'
        request_id = 'req-1'
        manifest = f'id:123;request-id:{request_id};ts:{ts};'
        v1 = hmac.new(b'segredo', manifest.encode(), hashlib.sha256).hexdigest()

        response = self.client.post(
            reverse('orders:webhook_pix'),
            data=json.dumps({'type': 'payment', 'data': {'id': '123'}}),
            content_type='application/json',
            headers={'x-signature': f'ts={ts},v1={v1}', 'x-request-id': request_id},
        )
        self.assertEqual(response.status_code, 200)


class DeliveryRulesTests(TestCase):
    """Pedido mínimo e entrega grátis a partir de um valor."""

    def setUp(self):
        self.restaurant = Restaurant.objects.create(
            name='Rules Kitchen', slug='rules', minimum_order=Decimal('40.00'),
            free_delivery_above=Decimal('60.00'),
        )
        category = Category.objects.create(
            restaurant=self.restaurant, name='Mains', slug='mains',
        )
        self.item = MenuItem.objects.create(
            category=category, name='Burger', slug='burger',
            price=Decimal('20.00'), is_available=True,
        )
        self.city = City.objects.create(name='Curitiba', delivery_fee=Decimal('4.00'))
        self.neighborhood = Neighborhood.objects.create(
            city=self.city, name='Centro', delivery_fee=Decimal('3.00'),
        )

    def _add(self, quantity):
        self.client.post(reverse('cart:cart_add', args=[self.item.pk]), {'quantity': quantity})

    def _checkout(self, **overrides):
        data = {
            'fulfillment_method': Order.FulfillmentMethod.DELIVERY,
            'customer_name': 'Ada Lovelace',
            'phone': '555-0100',
            'city': self.city.pk,
            'neighborhood': self.neighborhood.pk,
            'address_street': 'Code Street',
            'address_number': '1',
            'payment_method': Order.PaymentMethod.CASH,
        }
        data.update(overrides)
        return self.client.post(reverse('orders:checkout'), data)

    def test_delivery_below_minimum_is_blocked(self):
        self._add(1)  # 20,00 < 40,00

        response = self._checkout()

        self.assertEqual(response.status_code, 200)
        self.assertFalse(Order.objects.exists())
        self.assertContains(response, 'pedido mínimo para entrega')
        self.assertContains(response, 'Faltam')

    def test_pickup_ignores_the_minimum(self):
        self._add(1)

        self._checkout(
            fulfillment_method=Order.FulfillmentMethod.PICKUP, city='', neighborhood='',
        )

        order = Order.objects.get()
        self.assertEqual(order.subtotal, Decimal('20.00'))

    def test_delivery_above_minimum_charges_the_fee(self):
        self._add(2)  # 40,00 — atinge o mínimo, mas não o frete grátis

        self._checkout()

        order = Order.objects.get()
        self.assertEqual(order.delivery_fee, Decimal('7.00'))
        self.assertEqual(order.total, Decimal('47.00'))

    def test_free_delivery_above_threshold(self):
        self._add(3)  # 60,00 — atinge o frete grátis

        self._checkout()

        order = Order.objects.get()
        self.assertEqual(order.subtotal, Decimal('60.00'))
        self.assertEqual(order.delivery_fee, Decimal('0.00'))
        self.assertEqual(order.total, Decimal('60.00'))

    def test_cart_shows_how_much_is_missing(self):
        self._add(1)

        response = self.client.get(reverse('cart:cart_detail'))

        self.assertContains(response, 'para a <strong>entrega grátis</strong>', html=False)
        self.assertContains(response, 'Pedido mínimo')

    def test_restaurant_helpers(self):
        self.assertEqual(self.restaurant.missing_for_minimum(Decimal('10.00')), Decimal('30.00'))
        self.assertIsNone(self.restaurant.missing_for_minimum(Decimal('40.00')))
        self.assertEqual(
            self.restaurant.missing_for_free_delivery(Decimal('50.00')), Decimal('10.00'),
        )
        self.assertIsNone(self.restaurant.missing_for_free_delivery(Decimal('60.00')))
        self.assertEqual(
            self.restaurant.delivery_fee_for(Decimal('60.00'), Decimal('7.00')), Decimal('0.00'),
        )
        self.assertEqual(
            self.restaurant.delivery_fee_for(Decimal('20.00'), Decimal('7.00')), Decimal('7.00'),
        )


class OrderStatusHistoryTests(TestCase):
    """Auditoria: quem mudou a situação do pedido, e quando."""

    def setUp(self):
        self.restaurant = Restaurant.objects.create(name='Hist Kitchen', slug='hist')
        category = Category.objects.create(
            restaurant=self.restaurant, name='Mains', slug='mains',
        )
        self.item = MenuItem.objects.create(
            category=category, name='Burger', slug='burger',
            price=Decimal('20.00'), is_available=True,
        )
        self.staff = User.objects.create_user(
            username='chef', password='pw', is_staff=True, first_name='Chef',
        )

    def _place_order(self):
        self.client.post(reverse('cart:cart_add', args=[self.item.pk]), {'quantity': 1})
        self.client.post(
            reverse('orders:checkout'),
            {
                'fulfillment_method': Order.FulfillmentMethod.PICKUP,
                'customer_name': 'Ada Lovelace',
                'phone': '555-0100',
                'payment_method': Order.PaymentMethod.CASH,
            },
        )
        return Order.objects.get()

    def test_checkout_records_the_first_entry(self):
        order = self._place_order()

        entries = list(order.status_changes.all())
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].from_status, '')
        self.assertEqual(entries[0].to_status, Order.Status.RECEIVED)
        self.assertIsNone(entries[0].changed_by)

    def test_staff_update_records_who_changed_it(self):
        order = self._place_order()
        self.client.force_login(self.staff)

        self.client.post(
            reverse('orders:staff_order_detail', args=[order.order_number]),
            {'status': Order.Status.PREPARING},
        )

        last = order.status_changes.last()
        self.assertEqual(last.from_status, Order.Status.RECEIVED)
        self.assertEqual(last.to_status, Order.Status.PREPARING)
        self.assertEqual(last.changed_by, self.staff)
        self.assertEqual(last.author_display, 'Chef')

    def test_same_status_is_not_recorded_twice(self):
        order = self._place_order()
        self.client.force_login(self.staff)

        self.client.post(
            reverse('orders:staff_order_detail', args=[order.order_number]),
            {'status': Order.Status.RECEIVED},
        )

        self.assertEqual(order.status_changes.count(), 1)

    def test_bulk_update_records_the_change(self):
        order = self._place_order()
        self.client.force_login(self.staff)

        self.client.post(
            reverse('orders:staff_orders_bulk_update'),
            {'order_numbers': [order.order_number], 'status': Order.Status.OUT_FOR_DELIVERY},
        )

        last = order.status_changes.last()
        self.assertEqual(last.to_status, Order.Status.OUT_FOR_DELIVERY)
        self.assertEqual(last.changed_by, self.staff)

    def test_history_shows_up_on_the_detail_page(self):
        order = self._place_order()
        self.client.force_login(self.staff)

        response = self.client.get(
            reverse('orders:staff_order_detail', args=[order.order_number]),
        )

        self.assertContains(response, 'Histórico')
        self.assertContains(response, 'Pedido recebido')


class StaffReportsTests(TestCase):
    """Relatórios de venda do painel."""

    def setUp(self):
        self.restaurant = Restaurant.objects.create(name='Rep Kitchen', slug='rep')
        self.staff = User.objects.create_user(username='dono', password='pw', is_staff=True)
        self.url = reverse('orders:staff_reports')

    def _order(self, total, status=Order.Status.DELIVERED, **extra):
        order = Order.objects.create(
            restaurant=self.restaurant,
            customer_name='Cliente',
            phone='000',
            subtotal=total,
            status=status,
            **extra,
        )
        OrderItem.objects.create(
            order=order, item_name='Burger', unit_price=total, quantity=1, line_total=total,
        )
        return order

    def test_requires_staff(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 302)

    def test_shows_revenue_orders_and_average_ticket(self):
        self._order(Decimal('30.00'))
        self._order(Decimal('50.00'))
        self.client.force_login(self.staff)

        report = self.client.get(self.url).context['report']

        self.assertEqual(report['revenue'], Decimal('80.00'))
        self.assertEqual(report['order_count'], 2)
        self.assertEqual(report['average_ticket'], Decimal('40.00'))

    def test_cancelled_orders_are_left_out(self):
        self._order(Decimal('30.00'))
        self._order(Decimal('90.00'), status=Order.Status.CANCELLED)
        self.client.force_login(self.staff)

        report = self.client.get(self.url).context['report']

        self.assertEqual(report['revenue'], Decimal('30.00'))
        self.assertEqual(report['order_count'], 1)
        self.assertEqual(report['cancelled_count'], 1)

    def test_top_items_ranking(self):
        order = self._order(Decimal('30.00'))
        OrderItem.objects.create(
            order=order, item_name='Batata', unit_price=Decimal('10.00'),
            quantity=5, line_total=Decimal('50.00'),
        )
        self.client.force_login(self.staff)

        top = self.client.get(self.url).context['report']['top_items']

        self.assertEqual(top[0]['item_name'], 'Batata')
        self.assertEqual(top[0]['quantity'], 5)
        self.assertEqual(top[0]['revenue'], Decimal('50.00'))

    def test_breakdown_by_payment_method(self):
        self._order(Decimal('30.00'), payment_method=Order.PaymentMethod.PIX)
        self._order(Decimal('10.00'), payment_method=Order.PaymentMethod.PIX)
        self._order(Decimal('60.00'), payment_method=Order.PaymentMethod.CASH)
        self.client.force_login(self.staff)

        rows = self.client.get(self.url).context['report']['by_payment']

        self.assertEqual(rows[0]['label'], 'PIX')
        self.assertEqual(rows[0]['order_count'], 2)
        self.assertEqual(rows[0]['percent'], 67)

    def test_period_filter_limits_the_window(self):
        old = self._order(Decimal('100.00'))
        Order.objects.filter(pk=old.pk).update(
            created_at=timezone.now() - timedelta(days=10),
        )
        self._order(Decimal('25.00'))
        self.client.force_login(self.staff)

        today = self.client.get(self.url).context['report']
        month = self.client.get(self.url, {'periodo': '30d'}).context['report']

        self.assertEqual(today['revenue'], Decimal('25.00'))
        self.assertEqual(month['revenue'], Decimal('125.00'))

    def test_empty_period_renders_the_placeholder(self):
        self.client.force_login(self.staff)

        response = self.client.get(self.url)

        self.assertContains(response, 'Nenhum pedido no período')


class OrderTotalsTests(TestCase):
    """Mexer nos itens depois do checkout precisa refazer o valor do pedido."""

    def setUp(self):
        self.restaurant = Restaurant.objects.create(name='Totals Kitchen', slug='tot')
        self.order = Order.objects.create(
            restaurant=self.restaurant,
            customer_name='Cliente',
            phone='000',
            subtotal=Decimal('20.00'),
            delivery_fee=Decimal('5.00'),
        )
        self.order_item = OrderItem.objects.create(
            order=self.order,
            item_name='Burger',
            unit_price=Decimal('20.00'),
            quantity=1,
            line_total=Decimal('20.00'),
        )

    def _admin(self):
        return OrderItemAdmin(OrderItem, admin_site)

    def test_checkout_total_is_subtotal_plus_fee(self):
        self.assertEqual(self.order.total, Decimal('25.00'))

    def test_recalculate_after_quantity_change(self):
        self.order_item.quantity = 3
        self.order_item.save()

        self.order.recalculate_totals()

        self.order.refresh_from_db()
        self.assertEqual(self.order.subtotal, Decimal('60.00'))
        self.assertEqual(self.order.total, Decimal('65.00'))

    def test_admin_edit_refreshes_the_order_total(self):
        self.order_item.quantity = 2
        self._admin().save_model(
            request=None,
            obj=self.order_item,
            form=SimpleNamespace(initial={}),
            change=True,
        )

        self.order.refresh_from_db()
        self.assertEqual(self.order.subtotal, Decimal('40.00'))
        self.assertEqual(self.order.total, Decimal('45.00'))

    def test_admin_delete_refreshes_the_order_total(self):
        self._admin().delete_model(request=None, obj=self.order_item)

        self.order.refresh_from_db()
        self.assertEqual(self.order.subtotal, Decimal('0.00'))
        self.assertEqual(self.order.total, Decimal('5.00'))


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

    def _staff_login(self):
        staff_user = User.objects.create_user(
            username='staff', password='password', is_staff=True,
        )
        self.client.force_login(staff_user)
        return staff_user

    @patch('orders.services.notificacoes.whatsapp_service.enviar_texto')
    def test_status_change_creates_notification_and_sends_whatsapp(self, mock_send):
        customer = User.objects.create_user(username='ada', password='password')
        order = Order.objects.create(
            restaurant=self.restaurant,
            user=customer,
            customer_name='Ada Lovelace',
            phone='41999990000',
            fulfillment_method=Order.FulfillmentMethod.PICKUP,
            payment_method=Order.PaymentMethod.CASH,
            subtotal=Decimal('20.00'),
            total=Decimal('20.00'),
        )
        self._staff_login()

        self.client.post(
            reverse('orders:staff_order_detail', args=[order.order_number]),
            {'status': Order.Status.PREPARING},
        )

        # Notificação no sistema criada para o dono do pedido.
        notif = Notification.objects.get(user=customer, order=order)
        self.assertIn('Em preparo', notif.message)
        self.assertFalse(notif.is_read)
        # WhatsApp disparado com o telefone do pedido.
        mock_send.assert_called_once()
        self.assertEqual(mock_send.call_args.args[0], '41999990000')

    @patch('orders.services.notificacoes.whatsapp_service.enviar_texto')
    def test_no_notification_when_status_unchanged(self, mock_send):
        customer = User.objects.create_user(username='ada', password='password')
        order = Order.objects.create(
            restaurant=self.restaurant,
            user=customer,
            customer_name='Ada',
            phone='41999990000',
            fulfillment_method=Order.FulfillmentMethod.PICKUP,
            payment_method=Order.PaymentMethod.CASH,
            subtotal=Decimal('20.00'),
            total=Decimal('20.00'),
            status=Order.Status.RECEIVED,
        )
        self._staff_login()

        self.client.post(
            reverse('orders:staff_order_detail', args=[order.order_number]),
            {'status': Order.Status.RECEIVED},  # mesmo status
        )

        self.assertFalse(Notification.objects.exists())
        mock_send.assert_not_called()

    @patch('orders.services.notificacoes.whatsapp_service.enviar_texto')
    def test_bulk_update_notifies_only_changed_orders(self, mock_send):
        customer = User.objects.create_user(username='ada', password='password')
        orders = [
            Order.objects.create(
                restaurant=self.restaurant,
                user=customer,
                customer_name=f'Cliente {i}',
                phone='41999990000',
                fulfillment_method=Order.FulfillmentMethod.PICKUP,
                payment_method=Order.PaymentMethod.CASH,
                subtotal=Decimal('20.00'),
                total=Decimal('20.00'),
                status=Order.Status.RECEIVED,
            )
            for i in range(2)
        ]
        # Um já está no status alvo: não deve notificar.
        orders[1].status = Order.Status.PREPARING
        orders[1].save(update_fields=['status'])
        self._staff_login()

        self.client.post(
            reverse('orders:staff_orders_bulk_update'),
            {
                'order_numbers': [o.order_number for o in orders],
                'status': Order.Status.PREPARING,
                'next': reverse('orders:staff_order_list'),
            },
        )

        # Só o pedido que realmente mudou gera notificação/WhatsApp.
        self.assertEqual(Notification.objects.count(), 1)
        self.assertEqual(mock_send.call_count, 1)

    def test_notifications_page_lists_and_marks_read(self):
        customer = User.objects.create_user(username='ada', password='password')
        order = Order.objects.create(
            restaurant=self.restaurant,
            user=customer,
            customer_name='Ada',
            phone='41999990000',
            fulfillment_method=Order.FulfillmentMethod.PICKUP,
            payment_method=Order.PaymentMethod.CASH,
            subtotal=Decimal('20.00'),
            total=Decimal('20.00'),
        )
        Notification.objects.create(
            user=customer, order=order, message='Seu pedido mudou.',
        )
        self.client.force_login(customer)

        response = self.client.get(reverse('orders:notifications'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Seu pedido mudou.')
        # Abrir a página marca como lidas.
        self.assertFalse(
            Notification.objects.filter(user=customer, is_read=False).exists()
        )

    def test_notifications_page_requires_login(self):
        response = self.client.get(reverse('orders:notifications'))
        self.assertEqual(response.status_code, 302)


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

    def test_checkout_prefills_cpf_from_profile(self):
        customer = User.objects.create_user(username='@cpfcliente', password='password')
        customer.profile.cpf = '39053344705'
        customer.profile.save()
        self.client.force_login(customer)
        self._add_item(quantity=1)
        response = self.client.get(reverse('orders:checkout'))
        self.assertContains(response, 'value="390.533.447-05"')

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


class AdminNewOrderNotificationTests(TestCase):
    """Admins recebem uma notificação quando um pedido é realizado."""

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
        self.admin1 = User.objects.create_user(
            username='admin1', password='pw', is_staff=True,
        )
        self.admin2 = User.objects.create_user(
            username='admin2', password='pw', is_staff=True,
        )

    def _add_item(self, quantity=1):
        self.client.post(reverse('cart:cart_add', args=[self.item.pk]), {'quantity': quantity})

    def test_offline_checkout_notifies_all_admins(self):
        self._add_item(quantity=1)
        self.client.post(
            reverse('orders:checkout'),
            {
                'fulfillment_method': Order.FulfillmentMethod.PICKUP,
                'customer_name': 'Grace Hopper',
                'phone': '555-0101',
                'payment_method': Order.PaymentMethod.CASH,
            },
        )
        order = Order.objects.get()
        # Uma notificação por admin, referenciando o pedido recém-criado.
        self.assertEqual(Notification.objects.filter(order=order).count(), 2)
        for admin in (self.admin1, self.admin2):
            notif = Notification.objects.get(user=admin, order=order)
            self.assertIn(order.order_number, notif.message)
            self.assertFalse(notif.is_read)

    def test_pix_checkout_only_notifies_after_payment_confirmed(self):
        from orders.services import pedidos

        self._add_item(quantity=1)
        self.client.post(
            reverse('orders:checkout'),
            {
                'fulfillment_method': Order.FulfillmentMethod.PICKUP,
                'customer_name': 'Ada Lovelace',
                'phone': '555-0100',
                'payment_method': Order.PaymentMethod.PIX,
                'customer_cpf': '390.533.447-05',
            },
            HTTP_X_REQUESTED_WITH='XMLHttpRequest',
        )
        order = Order.objects.get()
        # Pedido online pendente ainda não avisa a equipe.
        self.assertFalse(Notification.objects.filter(order=order).exists())

        # A confirmação do pagamento avisa os admins — uma única vez (idempotente).
        pedidos.marcar_pago(order)
        self.assertEqual(Notification.objects.filter(order=order).count(), 2)
        pedidos.marcar_pago(order)
        self.assertEqual(Notification.objects.filter(order=order).count(), 2)

    def test_notifications_feed_returns_count_and_latest_id(self):
        self.client.force_login(self.admin1)
        order = Order.objects.create(
            restaurant=self.restaurant,
            customer_name='X', phone='p',
            fulfillment_method=Order.FulfillmentMethod.PICKUP,
            payment_method=Order.PaymentMethod.CASH,
            subtotal=Decimal('20.00'), total=Decimal('20.00'),
        )
        Notification.objects.create(user=self.admin1, order=order, message='a')
        latest = Notification.objects.create(user=self.admin1, order=order, message='b')
        # Notificação de outro admin não conta para este usuário.
        Notification.objects.create(user=self.admin2, order=order, message='c')

        body = self.client.get(reverse('orders:notifications_feed')).json()
        self.assertTrue(body['ok'])
        self.assertEqual(body['count'], 2)
        self.assertEqual(body['latest_id'], latest.id)

    def test_notifications_feed_requires_login(self):
        response = self.client.get(reverse('orders:notifications_feed'))
        self.assertEqual(response.status_code, 302)


class StaffOrdersFeedTests(TestCase):
    """Feed de atualização em tempo real do painel de pedidos."""

    def setUp(self):
        self.restaurant = Restaurant.objects.create(
            name='Test Kitchen', slug='test-kitchen', delivery_fee=Decimal('5.00'),
        )
        self.staff = User.objects.create_user(
            username='staff', password='pw', is_staff=True,
        )

    def _make_order(self, name='Cliente', status=Order.Status.RECEIVED):
        return Order.objects.create(
            restaurant=self.restaurant, customer_name=name, phone='555-0000',
            fulfillment_method=Order.FulfillmentMethod.PICKUP,
            payment_method=Order.PaymentMethod.CASH,
            subtotal=Decimal('20.00'), total=Decimal('20.00'), status=status,
        )

    def test_feed_requires_staff(self):
        response = self.client.get(reverse('orders:staff_orders_feed'))
        self.assertEqual(response.status_code, 302)  # redireciona ao login

    def test_staff_page_renders_with_realtime_hooks(self):
        order = self._make_order('Ativo', Order.Status.RECEIVED)
        self.client.force_login(self.staff)

        response = self.client.get(reverse('orders:staff_order_list'))
        self.assertEqual(response.status_code, 200)
        # Ganchos usados pelo polling em tempo real.
        self.assertContains(response, 'data-feed-url')
        self.assertContains(response, 'data-signature=')
        self.assertContains(response, 'id="cards-active"')
        self.assertContains(response, 'id="cards-finished"')
        self.assertContains(response, reverse('orders:staff_orders_feed'))
        self.assertContains(response, order.order_number)

    def test_feed_returns_cards_counts_and_signature(self):
        active = self._make_order('Ativo', Order.Status.RECEIVED)
        done = self._make_order('Feito', Order.Status.DELIVERED)
        self.client.force_login(self.staff)

        body = self.client.get(reverse('orders:staff_orders_feed')).json()
        self.assertTrue(body['ok'])
        self.assertEqual(body['active_count'], 1)
        self.assertEqual(body['inactive_count'], 1)
        self.assertIn(active.order_number, body['active_html'])
        self.assertIn(done.order_number, body['inactive_html'])
        self.assertTrue(body['signature'])
        # Os cartões do feed são renderizados com os context processors (moeda).
        self.assertIn('R$', body['active_html'])

    def test_signature_changes_when_new_order_arrives(self):
        self._make_order('Primeiro')
        self.client.force_login(self.staff)
        sig1 = self.client.get(reverse('orders:staff_orders_feed')).json()['signature']

        self._make_order('Segundo')
        sig2 = self.client.get(reverse('orders:staff_orders_feed')).json()['signature']
        self.assertNotEqual(sig1, sig2)

    def test_signature_changes_when_status_changes(self):
        order = self._make_order('X', Order.Status.RECEIVED)
        self.client.force_login(self.staff)
        sig1 = self.client.get(reverse('orders:staff_orders_feed')).json()['signature']

        order.status = Order.Status.PREPARING
        order.save(update_fields=['status'])
        sig2 = self.client.get(reverse('orders:staff_orders_feed')).json()['signature']
        self.assertNotEqual(sig1, sig2)

    def test_feed_respects_status_filter(self):
        self._make_order('Recebido', Order.Status.RECEIVED)
        self._make_order('Preparando', Order.Status.PREPARING)
        self.client.force_login(self.staff)

        body = self.client.get(
            reverse('orders:staff_orders_feed'), {'status': Order.Status.PREPARING}
        ).json()
        self.assertEqual(body['active_count'], 1)
        self.assertIn('Preparando', body['active_html'])
        self.assertNotIn('Recebido', body['active_html'])


class StaffOrderPrintTests(TestCase):
    """Impressão de comandas nos três tipos: caixa, cozinha e entregador."""

    def setUp(self):
        self.restaurant = Restaurant.objects.create(
            name='Test Kitchen', slug='test-kitchen', delivery_fee=Decimal('5.00'),
        )
        self.staff = User.objects.create_user(
            username='staff', password='pw', is_staff=True,
        )
        self.client.force_login(self.staff)

    def _make_order(self, **kwargs):
        defaults = dict(
            restaurant=self.restaurant,
            customer_name='Ada Lovelace',
            phone='41999990000',
            fulfillment_method=Order.FulfillmentMethod.DELIVERY,
            payment_method=Order.PaymentMethod.CASH,
            address_street='Rua Um', address_number='10',
            address_neighborhood='Centro', address_city='Curitiba',
            subtotal=Decimal('20.00'), delivery_fee=Decimal('5.00'),
        )
        defaults.update(kwargs)
        order = Order.objects.create(**defaults)
        OrderItem.objects.create(
            order=order, item_name='Burger', unit_price=Decimal('20.00'),
            quantity=2, line_total=Decimal('40.00'), notes='sem cebola',
        )
        return order

    def _print(self, order, tipo=None):
        url = reverse('orders:staff_order_print', args=[order.order_number])
        if tipo:
            url += '?tipo=' + tipo
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        return response.content.decode()

    def test_default_print_is_full_receipt_for_cashier(self):
        html = self._print(self._make_order())
        # Comanda completa: preços, pagamento e totais.
        self.assertIn('Subtotal', html)
        self.assertIn('Pagamento', html)
        self.assertIn('R$ 40,00', html)          # preço da linha do item (pt-BR)
        self.assertNotIn('banner cozinha', html)
        self.assertNotIn('banner entregador', html)

    def test_kitchen_print_hides_prices_and_payment(self):
        html = self._print(self._make_order(), tipo='cozinha')
        self.assertIn('banner cozinha', html)     # faixa da cozinha
        self.assertIn('2× Burger', html)
        self.assertIn('sem cebola', html)          # observação do item
        self.assertNotIn('Subtotal', html)
        self.assertNotIn('Pagamento', html)
        self.assertNotIn('R$', html)               # cozinha não mostra preços

    def test_delivery_print_shows_address_and_amount_due(self):
        html = self._print(self._make_order(), tipo='entregador')  # dinheiro, não pago
        self.assertIn('banner entregador', html)
        self.assertIn('Rua Um', html)
        self.assertIn('41999990000', html)
        self.assertIn('A COBRAR', html)
        self.assertIn('R$ 25,00', html)           # total = 20 + 5 (pt-BR)
        self.assertNotIn('Subtotal', html)

    def test_delivery_print_marks_paid_orders(self):
        order = self._make_order(
            payment_method=Order.PaymentMethod.PIX,
            payment_status=Order.PaymentStatus.PAID,
        )
        html = self._print(order, tipo='entregador')
        self.assertIn('PAGO', html)
        self.assertNotIn('A COBRAR', html)

    def test_invalid_variant_falls_back_to_full(self):
        html = self._print(self._make_order(), tipo='hacker')
        self.assertIn('Subtotal', html)
        self.assertNotIn('banner cozinha', html)
        self.assertNotIn('banner entregador', html)

    def test_print_active_supports_variant(self):
        self._make_order(status=Order.Status.RECEIVED)
        url = reverse('orders:staff_orders_print_active') + '?tipo=cozinha'
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertIn('banner cozinha', response.content.decode())

    def test_order_card_offers_three_print_variants(self):
        order = self._make_order(status=Order.Status.RECEIVED)
        html = self.client.get(reverse('orders:staff_order_list')).content.decode()
        base = reverse('orders:staff_order_print', args=[order.order_number])
        self.assertIn(base + '?tipo=completa', html)
        self.assertIn(base + '?tipo=cozinha', html)
        self.assertIn(base + '?tipo=entregador', html)

    def test_summary_modal_offers_three_print_variants(self):
        order = self._make_order()
        html = self.client.get(
            reverse('orders:staff_order_summary', args=[order.order_number])
        ).content.decode()
        base = reverse('orders:staff_order_print', args=[order.order_number])
        self.assertIn(base + '?tipo=completa', html)
        self.assertIn(base + '?tipo=cozinha', html)
        self.assertIn(base + '?tipo=entregador', html)


class StaffOrdersPaginationTests(TestCase):
    """O painel não pode carregar a tabela inteira: ela só cresce.

    Este é o mesmo caminho percorrido pelo polling do painel a cada 12 s, então
    o custo por requisição precisa ficar preso ao tamanho da página.
    """

    def setUp(self):
        self.restaurant = Restaurant.objects.create(name='Paginada', slug='pag')
        self.staff = User.objects.create_user(
            username='staff', password='pw', is_staff=True,
        )
        self.client.force_login(self.staff)
        self.page_size = views.STAFF_FINISHED_PAGE_SIZE

    def _make_orders(self, count, status):
        for index in range(count):
            Order.objects.create(
                restaurant=self.restaurant, customer_name=f'Cliente {index}',
                phone='555-0000', status=status,
                subtotal=Decimal('10.00'), total=Decimal('10.00'),
            )

    def test_finished_orders_are_paginated(self):
        total = self.page_size + 5
        self._make_orders(total, Order.Status.DELIVERED)

        response = self.client.get(reverse('orders:staff_order_list'))
        self.assertEqual(len(response.context['inactive_orders']), self.page_size)
        # A contagem em tela continua sendo o total, não o tamanho da página.
        self.assertEqual(response.context['inactive_count'], total)
        self.assertEqual(response.context['finished_page'].paginator.num_pages, 2)

    def test_second_page_returns_the_remainder(self):
        self._make_orders(self.page_size + 5, Order.Status.DELIVERED)
        response = self.client.get(reverse('orders:staff_order_list'), {'page': 2})
        self.assertEqual(len(response.context['inactive_orders']), 5)

    def test_pagination_neither_repeats_nor_skips_orders(self):
        """created_at é igual para pedidos do mesmo instante; o id desempata."""
        total = self.page_size + 5
        self._make_orders(total, Order.Status.DELIVERED)

        url = reverse('orders:staff_order_list')
        first = self.client.get(url).context['inactive_orders']
        second = self.client.get(url, {'page': 2}).context['inactive_orders']

        numbers = [o.order_number for o in first] + [o.order_number for o in second]
        self.assertEqual(len(numbers), total)
        self.assertEqual(len(set(numbers)), total)

    def test_active_orders_are_not_paginated(self):
        """A fila da cozinha precisa estar toda à vista — e é curta por natureza."""
        total = self.page_size + 5
        self._make_orders(total, Order.Status.RECEIVED)
        response = self.client.get(reverse('orders:staff_order_list'))
        self.assertEqual(len(response.context['active_orders']), total)

    def test_invalid_page_falls_back_instead_of_failing(self):
        self._make_orders(3, Order.Status.DELIVERED)
        response = self.client.get(reverse('orders:staff_order_list'), {'page': 'abc'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['finished_page'].number, 1)

    def test_feed_returns_the_pager(self):
        self._make_orders(self.page_size + 1, Order.Status.DELIVERED)
        body = self.client.get(reverse('orders:staff_orders_feed')).json()
        self.assertIn('pager_html', body)
        self.assertIn('1 de 2', body['pager_html'])

    def test_signature_covers_changes_outside_the_current_page(self):
        """Um pedido removido na 2ª página não muda a 1ª, mas muda a contagem."""
        self._make_orders(self.page_size + 5, Order.Status.DELIVERED)
        url = reverse('orders:staff_orders_feed')

        before = self.client.get(url).json()
        Order.objects.order_by('created_at', 'id').first().delete()
        after = self.client.get(url).json()

        self.assertEqual(before['inactive_html'], after['inactive_html'])
        self.assertNotEqual(before['signature'], after['signature'])


class OrderLocalDateFilterTests(TestCase):
    """Filtros por data comparam datetime, para o índice de created_at valer.

    A conversão precisa ser feita no fuso local: 23h30 em São Paulo já é o dia
    seguinte em UTC, e o pedido apareceria na data errada para a equipe.
    """

    def setUp(self):
        self.restaurant = Restaurant.objects.create(name='Fuso', slug='fuso')
        self.staff = User.objects.create_user(
            username='staff', password='pw', is_staff=True,
        )
        self.client.force_login(self.staff)

    def _order_at(self, local_dt):
        order = Order.objects.create(
            restaurant=self.restaurant, customer_name='Noturno', phone='555',
            subtotal=Decimal('30.00'), total=Decimal('30.00'),
        )
        Order.objects.filter(pk=order.pk).update(created_at=local_dt)
        return order

    def test_bounds_cover_the_whole_local_day(self):
        day = date(2026, 3, 10)
        first, last = selectors.local_day_bounds(day)
        tz = timezone.get_current_timezone()
        self.assertEqual(timezone.localtime(first, tz).date(), day)
        self.assertEqual(timezone.localtime(last, tz).date(), day)
        self.assertEqual(timezone.localtime(first, tz).hour, 0)
        self.assertEqual(timezone.localtime(last, tz).hour, 23)

    def test_late_night_order_stays_on_its_local_date(self):
        tz = timezone.get_current_timezone()
        order = self._order_at(timezone.make_aware(datetime(2026, 3, 10, 23, 30), tz))

        response = self.client.get(
            reverse('orders:staff_order_list'), {'date': '2026-03-10'},
        )
        numbers = [o.order_number for o in response.context['active_orders']]
        self.assertIn(order.order_number, numbers)

    def test_late_night_order_is_not_counted_on_the_next_day(self):
        tz = timezone.get_current_timezone()
        self._order_at(timezone.make_aware(datetime(2026, 3, 10, 23, 30), tz))

        response = self.client.get(
            reverse('orders:staff_order_list'), {'date': '2026-03-11'},
        )
        self.assertEqual(response.context['active_orders'], [])

    def test_report_counts_the_order_on_its_local_day(self):
        tz = timezone.get_current_timezone()
        day = date(2026, 3, 10)
        order = self._order_at(
            timezone.make_aware(datetime.combine(day, time(23, 30)), tz),
        )
        Order.objects.filter(pk=order.pk).update(status=Order.Status.DELIVERED)

        report = selectors.get_sales_report(day, day)
        self.assertEqual(report['order_count'], 1)
        self.assertEqual(report['revenue'], Decimal('30.00'))

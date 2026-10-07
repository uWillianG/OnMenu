"""Regressões de compra, pagamento e operação identificadas na auditoria."""
from datetime import timedelta
from decimal import Decimal
from io import StringIO
from unittest.mock import patch

from django.contrib.auth.models import Permission, User
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.db import IntegrityError, transaction
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from accounts.forms import SignupForm
from menu.models import Category, ComplementChoice, ComplementGroup, MenuItem, Restaurant
from orders.models import Order, PaymentAttempt, PixPayment
from orders.selectors import get_sales_report
from orders.services import mercadopago, payments, pedidos, whatsapp


@override_settings(MERCADOPAGO_MOCK=True, MERCADOPAGO_MOCK_ALLOWED=True,
                   PAYMENT_PIX_ENABLED=True, PAYMENT_CARD_ENABLED=True, RATELIMIT_ENABLED=False)
class ReleaseFixture(TestCase):
    def setUp(self):
        self.restaurant = Restaurant.objects.create(name='Restaurante de teste')
        self.category = Category.objects.create(restaurant=self.restaurant, name='Lanches')
        self.item = MenuItem.objects.create(category=self.category, name='Lanche', price=Decimal('20'))

    def add(self, **extra):
        return self.client.post(reverse('cart:cart_add', args=[self.item.pk]), {'quantity': 1, **extra})

    def checkout(self, **extra):
        data = {'fulfillment_method': 'pickup', 'customer_name': 'Cliente Teste',
                'customer_email':'cliente@example.com',
                'phone': '(11) 99999-8888', 'payment_method': 'credit_card', 'customer_cpf': ''}
        data.update(extra)
        return self.client.post(reverse('orders:checkout'), data, HTTP_X_REQUESTED_WITH='XMLHttpRequest')

    def order(self, **extra):
        self.add()
        result = self.checkout(**extra)
        expected = 302 if extra.get('payment_method') in ('cash', 'card_on_delivery') else 200
        self.assertEqual(result.status_code, expected, result.content)
        return Order.objects.get()

    def card(self, order, token='MOCK-APPROVE', **extra):
        data = {'token': token, 'installments': 1}
        data.update(extra)
        return self.client.post(reverse('orders:card_pay', args=[order.order_number]),
            data)


class CommerceReleaseTests(ReleaseFixture):
    def test_disabled_pickup_is_rejected(self):
        self.restaurant.accepts_pickup = False
        self.restaurant.save()
        self.add()
        self.assertEqual(self.checkout().status_code, 400)
        self.assertFalse(Order.objects.exists())

    def test_inactive_restaurant_cannot_finish_old_cart(self):
        self.add()
        self.restaurant.is_active = False
        self.restaurant.save()
        self.assertEqual(self.checkout().status_code, 400)
        self.assertFalse(Order.objects.exists())

    def test_inactive_category_cannot_be_sold_through_direct_post(self):
        self.category.is_active = False
        self.category.save()
        self.add()
        self.assertFalse(self.client.session.get('onmenu_cart'))

    def test_other_restaurant_item_is_not_added(self):
        other = Restaurant.objects.create(name='Outro', is_active=False)
        self.item.category = Category.objects.create(restaurant=other, name='Lanches')
        self.item.save()
        self.add()
        self.assertFalse(self.client.session.get('onmenu_cart'))

    def test_required_choice_removed_after_add_blocks_checkout(self):
        group = ComplementGroup.objects.create(restaurant=self.restaurant, name='Ponto', required=True)
        choice = ComplementChoice.objects.create(group=group, name='Ao ponto')
        self.item.complement_groups.add(group)
        self.add(**{f'option_group_{group.pk}': choice.pk})
        choice.delete()
        self.assertEqual(self.checkout().status_code, 400)
        self.assertFalse(Order.objects.exists())

    def test_quantity_is_limited_before_order_creation(self):
        self.add(quantity='999999999999999999999')
        self.assertFalse(self.client.session.get('onmenu_cart'))
        self.assertEqual(self.checkout().status_code, 400)

    def test_checkout_rejects_invalid_phone_and_cpf(self):
        self.add()
        response = self.checkout(phone='abc', payment_method='pix', customer_cpf='00000000000')
        self.assertEqual(response.status_code, 400)
        self.assertIn('phone', response.json()['errors'])
        self.assertIn('customer_cpf', response.json()['errors'])
        self.assertFalse(Order.objects.exists())

    def test_duplicate_category_and_item_receive_distinct_slugs(self):
        duplicate = Category.objects.create(restaurant=self.restaurant, name=self.category.name)
        item = MenuItem.objects.create(category=self.category, name=self.item.name, price=Decimal('20'))
        self.assertNotEqual(duplicate.slug, self.category.slug)
        self.assertNotEqual(item.slug, self.item.slug)

    def test_local_ddd55_gets_country_code(self):
        self.assertEqual(whatsapp.formatar_numero('(55) 99999-8888'), '5555999998888')
        self.assertEqual(whatsapp.formatar_numero('+55 55 99999-8888'), '5555999998888')


class PaymentReleaseTests(ReleaseFixture):
    def test_checkout_replay_keeps_one_order(self):
        self.add()
        key = self.client.get(reverse('orders:checkout')).context['checkout_token']
        first = self.checkout(checkout_token=key).json()
        second = self.checkout(checkout_token=key).json()
        self.assertEqual(first['order_number'], second['order_number'])
        self.assertEqual(Order.objects.count(), 1)

    def test_changed_price_requires_customer_to_review_new_total(self):
        self.add()
        self.client.get(reverse('orders:checkout'))
        self.item.price = Decimal('25')
        self.item.save()
        response = self.checkout(expected_total='20.00')
        self.assertEqual(response.status_code, 400)
        self.assertFalse(Order.objects.exists())

    def test_payment_method_can_change_after_confirmed_rejection(self):
        order = self.order()
        self.card(order, 'MOCK-REJECT')
        self.client.post(reverse('orders:payment_change', args=[order.order_number]), {'payment_method':'cash'})
        order.refresh_from_db()
        self.assertEqual(order.payment_method, 'cash')
        self.assertEqual(Order.objects.count(), 1)

    def test_uncertain_payment_blocks_payment_method_change(self):
        order = self.order()
        PaymentAttempt.objects.create(order=order, method='credit_card', amount=order.total, status='uncertain')
        self.client.post(reverse('orders:payment_change', args=[order.order_number]), {'payment_method':'cash'})
        order.refresh_from_db()
        self.assertEqual(order.payment_method, 'credit_card')

    def test_card_rejection_then_approval_keeps_order_and_both_attempts(self):
        order = self.order()
        self.assertEqual(self.card(order, 'MOCK-REJECT').json()['status'], 'rejected')
        self.assertEqual(self.card(order).json()['status'], 'approved')
        self.assertEqual(Order.objects.count(), 1)
        self.assertEqual(order.payment_attempts.count(), 2)
        order.refresh_from_db()
        self.assertTrue(order.is_paid)

    def test_same_rejected_token_reuses_result(self):
        order = self.order()
        self.card(order, 'MOCK-REJECT')
        self.card(order, 'MOCK-REJECT')
        self.assertEqual(order.payment_attempts.count(), 1)

    def test_concurrent_request_cannot_initiate_second_charge(self):
        order = self.order()
        nested_statuses = []
        def gateway(**kwargs):
            nested_statuses.append(self.card(order, 'another-token').status_code)
            return {'id': 'MP-ONE', 'status': 'approved', 'installments': 1}
        with patch('orders.services.mercadopago.criar_pagamento_cartao', side_effect=gateway) as create:
            response = self.card(order, 'first-token')
        self.assertEqual(response.json()['status'], 'approved')
        self.assertEqual(nested_statuses, [409])
        self.assertEqual(create.call_count, 1)
        self.assertEqual(order.payment_attempts.count(), 1)

    def test_card_attempt_limit_is_enforced_by_server(self):
        order = self.order()
        for index in range(3):
            self.assertEqual(self.card(order, f'MOCK-REJECT-{index}').status_code, 200)
        self.assertEqual(self.card(order, 'MOCK-REJECT-4').status_code, 429)
        self.assertEqual(order.payment_attempts.count(), 3)

    def test_invalid_installments_are_validation_error(self):
        order = self.order()
        self.assertEqual(self.card(order, installments='abc').status_code, 400)
        self.assertFalse(order.payment_attempts.exists())

    def test_cancelled_order_is_not_charged(self):
        order = self.order()
        order.status = 'cancelled'
        order.save()
        self.assertEqual(self.card(order).status_code, 409)
        self.assertFalse(order.payment_attempts.exists())

    def test_cash_order_cannot_create_pix(self):
        order = self.order(payment_method='cash')
        response = self.client.post(reverse('orders:pix_recreate', args=[order.order_number]))
        self.assertEqual(response.status_code, 400)
        self.assertFalse(PixPayment.objects.exists())

    def test_pix_failure_keeps_access_and_does_not_duplicate_checkout(self):
        self.add()
        key = self.client.get(reverse('orders:checkout')).context['checkout_token']
        with patch('orders.services.mercadopago.criar_pix', side_effect=mercadopago.PixError('Timeout')) as create:
            first = self.checkout(payment_method='pix', customer_cpf='39053344705', checkout_token=key)
            second = self.checkout(payment_method='pix', customer_cpf='39053344705', checkout_token=key)
        self.assertEqual(first.status_code, 409)
        self.assertEqual(second.status_code, 409)
        self.assertEqual(Order.objects.count(), 1)
        self.assertEqual(create.call_count, 1)
        order = Order.objects.get()
        self.assertEqual(order.payment_attempts.get().status, 'uncertain')
        self.assertEqual(self.client.get(first.json()['payment_url']).status_code, 200)

    def test_confirmation_has_payment_resume_link(self):
        order = self.order(payment_method='pix', customer_cpf='39053344705')
        url = reverse('orders:payment_resume', args=[order.order_number])
        self.assertContains(self.client.get(reverse('orders:confirmation', args=[order.order_number])), url)
        self.assertContains(self.client.get(url), 'data-payment-resume="true"')

    def test_pix_requires_real_payer_email_before_creating_charge(self):
        self.add()
        response = self.checkout(payment_method='pix', customer_cpf='39053344705', customer_email='')
        self.assertEqual(response.status_code, 400)
        self.assertIn('customer_email', response.json()['errors'])
        self.assertFalse(Order.objects.exists())

    def test_expired_pix_queries_provider_before_marking_expired(self):
        order = self.order(payment_method='pix', customer_cpf='39053344705')
        attempt = order.payment_attempts.get()
        attempt.expires_at = timezone.now() - timedelta(minutes=1)
        attempt.save()
        with override_settings(MERCADOPAGO_MOCK=False), patch(
            'orders.services.mercadopago.buscar_status', return_value={'status': 'approved'}) as fetch:
            call_command('sync_pending_pix', stdout=StringIO())
        self.assertEqual(fetch.call_count, 1)
        order.refresh_from_db()
        self.assertTrue(order.is_paid)

    def test_pix_recreation_preserves_old_charge_and_uses_new_identity(self):
        order = self.order(payment_method='pix', customer_cpf='39053344705')
        first = order.payment_attempts.get()
        first.expires_at = timezone.now() - timedelta(minutes=1)
        first.save()
        second = payments.create_pix(order)
        self.assertNotEqual(first.pk, second.pk)
        self.assertNotEqual(first.external_reference, second.external_reference)
        self.assertEqual(order.payment_attempts.count(), 2)

    def test_refund_updates_order_and_preserves_final_state(self):
        order = self.order()
        self.card(order)
        attempt = payments.refund(order)
        order.refresh_from_db()
        self.assertEqual(attempt.status, 'refunded')
        self.assertEqual(order.payment_status, 'refunded')

    def test_partial_refund_is_accounted_and_full_refund_uses_remaining_amount(self):
        order = self.order()
        self.card(order)
        attempt = order.payment_attempts.get()
        payments.apply_provider_info(attempt, {'status':'approved', 'refund_amount':'5.00'})
        order.refresh_from_db()
        self.assertTrue(order.is_paid)
        self.assertEqual(order.payment_status, 'partially_refunded')
        today = timezone.localdate()
        self.assertEqual(get_sales_report(today,today)['revenue'], Decimal('15.00'))
        with patch('orders.services.mercadopago.reembolsar', return_value={'status':'refunded'}) as refund:
            payments.refund(order)
        self.assertEqual(refund.call_args.kwargs['amount'], Decimal('15.00'))
        order.refresh_from_db()
        self.assertEqual(order.payment_status, 'refunded')
        pedidos.aplicar_status_mp(attempt, 'approved')
        order.refresh_from_db()
        self.assertEqual(order.payment_status, 'refunded')

    def test_3ds_challenge_data_is_preserved(self):
        result = mercadopago._normalize_card_response({'id': 1, 'status': 'pending',
            'status_detail': 'pending_challenge', 'three_ds_info':{
                'creq': 'challenge', 'external_resource_url': 'https://example.invalid/challenge'}})
        self.assertEqual(result['creq'], 'challenge')
        self.assertTrue(result['requires_action'])

    def test_production_never_uses_mock(self):
        with override_settings(MERCADOPAGO_MOCK_ALLOWED=False):
            self.assertNotIn('pix', payments.enabled_methods())
            self.assertNotIn('credit_card', payments.enabled_methods())
            with self.assertRaises(mercadopago.PixError):
                mercadopago.criar_pagamento_cartao(amount=Decimal('20'), description='Teste',
                    token='MOCK-APPROVE', external_reference='TEST')

    def test_provider_amount_mismatch_is_not_marked_paid(self):
        order = self.order(payment_method='pix', customer_cpf='39053344705')
        attempt = order.payment_attempts.get()
        with self.assertRaises(payments.PaymentError):
            payments.apply_provider_info(attempt, {'status': 'approved', 'amount': '0.01'})
        order.refresh_from_db()
        self.assertFalse(order.is_paid)


class OperationsReleaseTests(ReleaseFixture):
    def staff(self):
        staff = User.objects.create_user(username='equipe', is_staff=True)
        self.client.force_login(staff)
        return staff

    def test_unpaid_online_order_stays_out_of_kitchen_queue(self):
        order = self.order()
        self.staff()
        before = self.client.get(reverse('orders:staff_orders_feed')).json()
        self.assertEqual(before['active_count'], 0)
        self.assertEqual(before['waiting_count'], 1)
        pedidos.marcar_pago(order)
        after = self.client.get(reverse('orders:staff_orders_feed')).json()
        self.assertEqual(after['active_count'], 1)
        self.assertEqual(after['waiting_count'], 0)
        self.assertNotEqual(before['signature'], after['signature'])

    def test_staff_cannot_prepare_unpaid_online_order(self):
        order = self.order()
        self.staff()
        self.client.post(reverse('orders:staff_order_detail', args=[order.order_number]), {'status':'preparing'})
        order.refresh_from_db()
        self.assertEqual(order.status, 'received')

    def test_status_update_reloads_payment_and_does_not_overwrite_approval(self):
        from orders.services.status import change_status
        order = self.order()
        stale = Order.objects.get(pk=order.pk)
        self.card(order)
        staff = self.staff()
        change_status(stale, 'preparing', staff)
        order.refresh_from_db()
        self.assertEqual(order.payment_status, 'paid')
        self.assertEqual(order.status, 'preparing')

    def test_report_excludes_unpaid_pix_from_revenue(self):
        self.order(payment_method='pix', customer_cpf='39053344705')
        today = timezone.localdate()
        report = get_sales_report(today, today)
        self.assertEqual(report['revenue'], 0)
        self.assertEqual(report['online_pending'], Decimal('20'))

    def test_abandoned_orders_without_charge_are_finalized(self):
        order = self.order()
        Order.objects.filter(pk=order.pk).update(created_at=timezone.now()-timedelta(hours=2))
        call_command('expire_abandoned_orders', stdout=StringIO())
        order.refresh_from_db()
        self.assertEqual(order.status, 'cancelled')

    def test_unknown_payment_is_not_discarded_by_abandonment_job(self):
        order = self.order()
        Order.objects.filter(pk=order.pk).update(created_at=timezone.now()-timedelta(hours=2))
        PaymentAttempt.objects.create(order=order, method='credit_card', amount=order.total, status='uncertain')
        call_command('expire_abandoned_orders', stdout=StringIO())
        order.refresh_from_db()
        self.assertEqual(order.status, 'received')


class IdentityReleaseTests(TestCase):
    def data(self, **extra):
        data = {'full_name': 'Cliente Teste', 'email':'pessoa@example.com', 'phone':'(11) 99999-8888',
            'cpf':'39053344705', 'password1':'SenhaDeTeste@123', 'password2':'SenhaDeTeste@123'}
        data.update(extra)
        return data

    def test_two_validated_signups_cannot_create_duplicate_email(self):
        first = SignupForm(self.data())
        second = SignupForm(self.data(full_name='Outra Pessoa', cpf='52998224725'))
        self.assertTrue(first.is_valid())
        self.assertTrue(second.is_valid())
        first.save()
        with self.assertRaises(ValidationError):
            second.save()
        self.assertEqual(User.objects.filter(email='pessoa@example.com').count(), 1)
        self.assertEqual(User.objects.count(), 1)

    def test_database_rejects_case_insensitive_duplicate_email(self):
        User.objects.create_user(username='one', email='Person@Example.com')
        with self.assertRaises(IntegrityError), transaction.atomic():
            User.objects.create_user(username='two', email='PERSON@example.COM')

    def test_repeated_digit_cpf_is_invalid(self):
        form = SignupForm(self.data(cpf='11111111111'))
        self.assertFalse(form.is_valid())
        self.assertIn('cpf', form.errors)

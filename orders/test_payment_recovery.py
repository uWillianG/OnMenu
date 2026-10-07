"""Corridas entre cancelamento, cobranças antigas e confirmação do provedor."""
from io import StringIO
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.test import Client, override_settings
from django.urls import reverse
from django.utils import timezone

from orders.models import CardPayment, Notification, Order, PaymentAttempt
from orders.selectors import get_sales_report
from orders.services import payments
from orders.services.status import change_status
from .test_release import ReleaseFixture


class PaymentRecoveryTests(ReleaseFixture):
    def staff(self):
        return User.objects.create_user(username='responsavel', is_staff=True, is_superuser=True)

    def test_staff_cannot_cancel_while_charge_result_is_unknown(self):
        order = self.order()
        staff = self.staff()
        attempt = PaymentAttempt.objects.create(order=order, method='credit_card', amount=order.total)
        for status in PaymentAttempt.BUSY_STATUSES:
            with self.subTest(status=status):
                attempt.status = status
                attempt.save()
                with self.assertRaises(ValidationError):
                    change_status(order, 'cancelled', staff)
                order.refresh_from_db()
                self.assertEqual(order.status, 'received')

    def test_late_approval_of_cancelled_order_is_queued_for_refund(self):
        order = self.order()
        self.staff()
        order.status = 'cancelled'
        order.save()
        attempt = PaymentAttempt.objects.create(order=order, method='credit_card', amount=order.total,
            status='rejected', mp_payment_id='LATE-CARD')
        payments.apply_provider_info(attempt, {'status':'approved'})
        attempt.refresh_from_db()
        self.assertIsNotNone(attempt.refund_requested_at)
        self.assertEqual(attempt.requested_refund_amount, order.total)
        self.assertFalse(Notification.objects.exists())
        call_command('sync_pending_card', stdout=StringIO())
        order.refresh_from_db()
        self.assertEqual(order.status, 'cancelled')
        self.assertEqual(order.payment_status, 'refunded')

    def test_duplicate_approval_refunds_only_extra_charge_and_keeps_revenue(self):
        order = self.order()
        old = PaymentAttempt.objects.create(order=order, method='pix', amount=order.total,
            status='cancelled', mp_payment_id='OLD-PIX')
        self.card(order)
        paid = order.payment_attempts.get(method='credit_card')
        payments.apply_provider_info(old, {'status':'approved'})
        old.refresh_from_db()
        self.assertIsNotNone(old.refund_requested_at)
        with patch('orders.services.mercadopago.reembolsar', return_value={'status':'refunded'}) as refund:
            call_command('sync_pending_pix', stdout=StringIO())
        self.assertEqual(refund.call_count, 1)
        self.assertEqual(refund.call_args.args[0], 'OLD-PIX')
        paid.refresh_from_db()
        self.assertEqual(paid.status, 'approved')
        order.refresh_from_db()
        self.assertTrue(order.is_paid)
        today = timezone.localdate()
        self.assertEqual(get_sales_report(today, today)['revenue'], order.total)

    def test_reference_search_rejects_mismatched_or_ambiguous_charge(self):
        order = self.order()
        attempt = PaymentAttempt.objects.create(order=order, method='credit_card', amount=order.total,
            status='uncertain')
        valid = {'id':'SEARCH-CARD', 'status':'approved', 'transaction_amount':str(order.total),
            'currency_id':'BRL', 'external_reference':attempt.external_reference,
            'payment_method_id':'visa', 'payment_type_id':'credit_card'}
        bad_results = [
            [{**valid, 'external_reference':'another-order'}],
            [{**valid, 'currency_id':'USD'}],
            [{**valid, 'payment_method_id':'pix', 'payment_type_id':'bank_transfer'}],
            [{**valid, 'payment_type_id':'debit_card'}],
            [{**valid, 'transaction_amount':'invalid'}],
            [valid, {**valid, 'id':'ANOTHER-CARD'}],
        ]
        for results in bad_results:
            with self.subTest(results=results), override_settings(MERCADOPAGO_MOCK=False), patch(
                'orders.services.mercadopago.buscar_por_referencia', return_value=results):
                with self.assertRaises(payments.PaymentError):
                    payments.reconcile(attempt, force=True)
                attempt.refresh_from_db()
                self.assertEqual(attempt.status, 'uncertain')
                self.assertEqual(attempt.mp_payment_id, '')

    def test_old_pix_polling_survives_recreation_and_remains_private(self):
        order = self.order(payment_method='pix', customer_cpf='39053344705')
        old = order.payment_attempts.get()
        payments.apply_provider_info(old, {'status':'expired'})
        payments.create_pix(order)
        url = reverse('orders:pix_status', args=[old.mp_payment_id])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['status'], 'expired')
        self.assertEqual(Client().get(url).status_code, 404)

    def test_old_card_polling_survives_replacement(self):
        order = self.order()
        self.card(order, 'MOCK-REJECT')
        old = order.payment_attempts.get()
        self.card(order)
        response = self.client.get(reverse('orders:card_status', args=[old.mp_payment_id]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['status'], 'rejected')

    def test_3ds_callback_requires_order_ownership_before_provider_lookup(self):
        order = self.order()
        self.card(order)
        card = CardPayment.objects.get()
        url = reverse('orders:card_3ds_callback') + '?payment_id=' + card.mp_payment_id
        with override_settings(MERCADOPAGO_MOCK=False), patch(
            'orders.services.mercadopago.buscar_status', return_value={'status':'approved'}) as fetch:
            self.assertEqual(Client().get(url).status_code, 404)
        fetch.assert_not_called()

    def test_staff_can_see_refund_history_and_retry_action(self):
        order = self.order()
        self.card(order)
        order.refresh_from_db()
        order.status = 'cancelled'
        order.save(update_fields=['status', 'updated_at'])
        attempt = order.payment_attempts.get()
        attempt.refund_requested_at = timezone.now()
        attempt.requested_refund_amount = order.total
        attempt.save()
        self.client.force_login(self.staff())
        response = self.client.get(reverse('orders:staff_order_detail', args=[order.order_number]))
        self.assertContains(response, 'Estorno em verificação')
        self.assertContains(response, reverse('orders:staff_order_refund', args=[order.order_number]))

    def test_valid_recovery_keeps_partial_refund_and_reconciles_only_remainder(self):
        order = self.order()
        order.status = 'cancelled'
        order.save(update_fields=['status', 'updated_at'])
        attempt = PaymentAttempt.objects.create(order=order, method='credit_card', amount=order.total,
            status='uncertain')
        data = {'id':'RECOVERED', 'status':'approved', 'transaction_amount':'20.00',
            'transaction_amount_refunded':'5.00', 'currency_id':'BRL',
            'external_reference':attempt.external_reference, 'payment_method_id':'visa',
            'payment_type_id':'credit_card'}
        with override_settings(MERCADOPAGO_MOCK=False), patch(
            'orders.services.mercadopago.buscar_por_referencia', return_value=[data]):
            recovered = payments.reconcile(attempt, force=True)
        self.assertEqual(recovered.requested_refund_amount, 15)
        self.assertEqual(recovered.refunded_amount, 5)
        self.assertEqual(recovered.mp_payment_id, 'RECOVERED')

    def test_invalid_webhook_cannot_bind_charge_identity(self):
        order = self.order()
        attempt = PaymentAttempt.objects.create(order=order, method='credit_card', amount=order.total,
            status='uncertain')
        from orders.views.payments import _process_payment_webhook
        with patch('orders.services.mercadopago.buscar_status', return_value={
                'status':'approved', 'amount':'0.01', 'external_reference':attempt.external_reference}):
            with self.assertRaises(payments.PaymentError):
                _process_payment_webhook('INVALID-ID')
        attempt.refresh_from_db()
        self.assertEqual(attempt.mp_payment_id, '')
        self.assertEqual(attempt.status, 'uncertain')

    def test_duplicate_refund_does_not_reverse_collected_cash_order(self):
        order = self.order(payment_method='cash')
        order.status = 'delivered'
        order.payment_status = 'paid'
        order.save(update_fields=['status', 'payment_status', 'updated_at'])
        late = PaymentAttempt.objects.create(order=order, method='pix', amount=order.total,
            status='cancelled', mp_payment_id='LATE-PIX')
        payments.apply_provider_info(late, {'status':'approved'})
        call_command('sync_pending_pix', stdout=StringIO())
        order.refresh_from_db()
        self.assertTrue(order.is_paid)
        self.assertEqual(order.payment_method, 'cash')
        today = timezone.localdate()
        self.assertEqual(get_sales_report(today, today)['revenue'], order.total)

    def test_gateway_completion_cannot_bypass_cancellation_guard(self):
        order = self.order()
        staff = self.staff()
        def provider(**kwargs):
            with self.assertRaises(ValidationError):
                change_status(order, 'cancelled', staff)
            return {'id':'CARD-RACE', 'status':'approved'}
        with patch('orders.services.mercadopago.criar_pagamento_cartao', side_effect=provider):
            self.assertEqual(self.card(order).status_code, 200)
        order.refresh_from_db()
        self.assertEqual(order.status, 'received')
        self.assertTrue(order.is_paid)

    def test_confirmed_paid_cancellation_still_refunds_with_authorization(self):
        order = self.order()
        self.card(order)
        current, changed = change_status(order, 'cancelled', self.staff(), confirm_refund=True)
        current.refresh_from_db()
        self.assertTrue(changed)
        self.assertEqual(current.status, 'cancelled')
        self.assertEqual(current.payment_status, 'refunded')

    def test_owned_3ds_callback_validates_amount_before_approval(self):
        order = self.order()
        self.card(order, 'MOCK-3DS')
        attempt = order.payment_attempts.get()
        url = reverse('orders:card_3ds_callback') + '?payment_id=' + attempt.mp_payment_id
        with override_settings(MERCADOPAGO_MOCK=False), patch(
            'orders.services.mercadopago.buscar_status', return_value={'status':'approved', 'amount':'0.01'}):
            self.assertEqual(self.client.get(url).status_code, 302)
        order.refresh_from_db()
        self.assertFalse(order.is_paid)

    def test_malformed_webhook_data_is_rejected_without_crashing(self):
        self.assertEqual(self.client.post(reverse('orders:webhook_card'),
            '{"data":[1]}', content_type='application/json').status_code, 400)

    def test_partial_refund_of_extra_charge_does_not_reduce_valid_payment(self):
        order = self.order()
        extra = PaymentAttempt.objects.create(order=order, method='pix', amount=order.total,
            status='cancelled', mp_payment_id='EXTRA-PARTIAL')
        self.card(order)
        payments.apply_provider_info(extra, {'status':'approved', 'refund_amount':'5.00'})
        order.refresh_from_db()
        self.assertEqual(order.payment_status, 'paid')
        extra.refresh_from_db()
        self.assertEqual(extra.requested_refund_amount, 15)

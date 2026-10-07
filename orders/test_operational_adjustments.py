"""Regressões de confirmação repetida, cancelamento e retomada de estornos."""
from io import StringIO
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import override_settings
from django.urls import reverse

from orders.models import PaymentAttempt
from orders.services import mercadopago, payments
from orders.services.status import change_status
from .test_release import ReleaseFixture


class OperationalPaymentTests(ReleaseFixture):
    def staff(self, *, can_refund=True):
        staff = User.objects.create_user(username='operador', is_staff=True, is_superuser=can_refund)
        self.client.force_login(staff)
        return staff

    def test_repeated_approval_keeps_valid_charge_while_extra_refund_is_pending(self):
        order = self.order()
        extra = PaymentAttempt.objects.create(order=order, method='pix', amount=order.total,
            status='cancelled', mp_payment_id='EXTRA-PIX')
        self.card(order)
        valid = order.payment_attempts.get(method='credit_card')
        payments.apply_provider_info(extra, {'status':'approved'})
        payments.apply_provider_info(valid, {'status':'approved'})
        valid.refresh_from_db()
        self.assertIsNone(valid.refund_requested_at)
        call_command('sync_pending_card', stdout=StringIO())
        call_command('sync_pending_pix', stdout=StringIO())
        valid.refresh_from_db()
        self.assertEqual(valid.status, 'approved')
        order.refresh_from_db()
        self.assertTrue(order.is_paid)

    def test_refund_intent_survives_interruption_after_cancellation(self):
        order = self.order()
        self.card(order)
        staff = self.staff()
        with patch('orders.services.status.notificacoes.notificar_status_pedido', side_effect=RuntimeError('Interrupted')):
            with self.assertRaises(RuntimeError):
                change_status(order, 'cancelled', staff, confirm_refund=True)
        order.refresh_from_db()
        self.assertEqual(order.status, 'cancelled')
        self.assertIsNotNone(order.payment_attempts.get().refund_requested_at)
        call_command('sync_pending_card', stdout=StringIO())
        order.refresh_from_db()
        self.assertEqual(order.payment_status, 'refunded')

    def test_refund_button_closes_order_and_removes_it_from_queue(self):
        order = self.order()
        self.card(order)
        self.staff()
        self.client.post(reverse('orders:staff_order_refund', args=[order.order_number]), {'confirm_refund':'on'})
        order.refresh_from_db()
        self.assertEqual(order.status, 'cancelled')
        self.assertEqual(order.payment_status, 'refunded')
        feed = self.client.get(reverse('orders:staff_orders_feed')).json()
        self.assertEqual(feed['active_count'], 0)
        self.assertEqual(feed['waiting_count'], 0)

    def test_unconfirmed_refund_does_not_close_order_or_call_provider(self):
        order = self.order()
        self.card(order)
        self.staff()
        with patch('orders.services.mercadopago.reembolsar') as refund:
            self.client.post(reverse('orders:staff_order_refund', args=[order.order_number]))
        refund.assert_not_called()
        order.refresh_from_db()
        self.assertEqual(order.status, 'received')

    def test_staff_without_refund_permission_cannot_close_paid_order(self):
        order = self.order()
        self.card(order)
        self.staff(can_refund=False)
        with patch('orders.services.mercadopago.reembolsar') as refund:
            self.client.post(reverse('orders:staff_order_refund', args=[order.order_number]), {'confirm_refund':'on'})
        refund.assert_not_called()
        order.refresh_from_db()
        self.assertEqual(order.status, 'received')

    def test_staff_can_cancel_pix_when_provider_confirms_cancellation(self):
        order = self.order(payment_method='pix', customer_cpf='39053344705')
        attempt = order.payment_attempts.get()
        self.staff()
        with patch('orders.services.mercadopago.cancelar', return_value={'status':'cancelled'}) as cancel:
            response = self.client.post(reverse('orders:staff_order_detail', args=[order.order_number]), {'status':'cancelled'})
        self.assertEqual(response.status_code, 302)
        cancel.assert_called_once_with(attempt.mp_payment_id)
        order.refresh_from_db()
        self.assertEqual(order.status, 'cancelled')

    def test_pix_approval_race_requires_refund_confirmation(self):
        order = self.order(payment_method='pix', customer_cpf='39053344705')
        self.staff()
        with patch('orders.services.mercadopago.cancelar', return_value={'status':'approved'}):
            response = self.client.post(reverse('orders:staff_order_detail', args=[order.order_number]), {'status':'cancelled'})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Confirme o cancelamento e o estorno integral.')
        order.refresh_from_db()
        self.assertEqual(order.status, 'received')
        self.assertTrue(order.is_paid)
        self.assertIsNone(order.payment_attempts.get().refund_requested_at)

    def test_provider_cancellation_failure_keeps_pix_and_order_pending(self):
        order = self.order(payment_method='pix', customer_cpf='39053344705')
        self.staff()
        with patch('orders.services.mercadopago.cancelar', side_effect=mercadopago.PixError('Temporary failure')):
            self.client.post(reverse('orders:staff_order_detail', args=[order.order_number]), {'status':'cancelled'})
        order.refresh_from_db()
        self.assertEqual(order.status, 'received')
        self.assertEqual(order.payment_attempts.get().status, 'pending')

    def test_unknown_charge_prevents_pending_pix_cancellation(self):
        order = self.order(payment_method='pix', customer_cpf='39053344705')
        PaymentAttempt.objects.create(order=order, method='credit_card', amount=order.total, status='uncertain')
        self.staff()
        with patch('orders.services.mercadopago.cancelar') as cancel:
            self.client.post(reverse('orders:staff_order_detail', args=[order.order_number]), {'status':'cancelled'})
        cancel.assert_not_called()
        order.refresh_from_db()
        self.assertEqual(order.status, 'received')

    def test_bulk_cancellation_does_not_call_provider_inside_transaction(self):
        order = self.order(payment_method='pix', customer_cpf='39053344705')
        self.staff()
        with patch('orders.services.mercadopago.cancelar') as cancel:
            self.client.post(reverse('orders:staff_orders_bulk_update'),
                {'order_numbers':[order.order_number], 'status':'cancelled'})
        cancel.assert_not_called()
        order.refresh_from_db()
        self.assertEqual(order.status, 'received')

    def test_offline_payment_is_not_cancelled_by_online_refund_route(self):
        order = self.order(payment_method='cash')
        order.status = 'delivered'
        order.payment_status = 'paid'
        order.save(update_fields=['status', 'payment_status', 'updated_at'])
        self.staff()
        with patch('orders.services.mercadopago.reembolsar') as refund:
            self.client.post(reverse('orders:staff_order_refund', args=[order.order_number]), {'confirm_refund':'on'})
        refund.assert_not_called()
        order.refresh_from_db()
        self.assertEqual(order.status, 'delivered')

    def test_failed_refund_stays_queued_after_order_closes(self):
        order = self.order()
        self.card(order)
        self.staff()
        with patch('orders.services.mercadopago.reembolsar', side_effect=mercadopago.PixError('Timeout')):
            self.client.post(reverse('orders:staff_order_refund', args=[order.order_number]), {'confirm_refund':'on'})
        order.refresh_from_db()
        self.assertEqual(order.status, 'cancelled')
        self.assertIsNotNone(order.payment_attempts.get().refund_requested_at)
        call_command('sync_pending_card', stdout=StringIO())
        order.refresh_from_db()
        self.assertEqual(order.payment_status, 'refunded')

    @override_settings(MERCADOPAGO_MOCK=False)
    def test_provider_private_error_body_is_not_exposed_in_refund_diagnostic(self):
        with patch('orders.services.mercadopago._sdk') as sdk:
            sdk.return_value.refund.return_value.create.return_value = {
                'status':400, 'response':{'error':'private-provider-detail'}}
            with self.assertRaises(mercadopago.PixError) as error:
                mercadopago.reembolsar('TEST-ID')
        self.assertNotIn('private-provider-detail', str(error.exception))

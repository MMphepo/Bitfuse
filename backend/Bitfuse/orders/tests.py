from datetime import timedelta
from decimal import Decimal
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import Notification, PlatformAccount, Rate, Transaction, Wallet
from .models import Order, OrderAuditLog, OrderSettlement
from .services import OrderError, submit_payment, verify_payment

User = get_user_model()


def make_platform_account():
    return PlatformAccount.objects.create(
        ledger_id="ledger",
        mwk_float_balance_id="mwk-float",
        usdt_float_balance_id="usdt-float",
        mwk_external_contra_id="mwk-contra",
        usdt_external_contra_id="usdt-contra",
        usdt_frozen_balance_id="usdt-frozen",
    )


class BuyPaymentFlowTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="john", email="john@example.com", password="pw", phone_number="+265991000000",
            verification_status="verified",
        )
        self.admin = User.objects.create_superuser(
            username="admin", email="admin@example.com", password="pw", phone_number="+265991000001",
        )
        Rate.objects.create(
            buy_rate=Decimal("1850.00"), sell_rate=Decimal("1800.00"),
            buy_fee_percent=Decimal("1.00"), sell_fee_percent=Decimal("1.00"),
        )
        make_platform_account()
        Wallet.objects.create(user=self.user, currency="USDT", blnk_balance_id="user-usdt")
        Wallet.objects.create(user=self.user, currency="MWK", blnk_balance_id="user-mwk")

        self.client = APIClient()
        self.client.force_authenticate(self.user)

        self.blnk = mock.patch("orders.services.BlnkClient").start()
        self.blnk.return_value.create_transaction.side_effect = [
            {"transaction_id": "blnk-1", "status": "APPLIED"},
            {"transaction_id": "blnk-2", "status": "APPLIED"},
        ]
        self.blnk.return_value.get_transaction.return_value = {"status": "APPLIED"}
        mock.patch(
            "orders.services.ensure_user_wallets",
            return_value=(
                Wallet.objects.get(user=self.user, currency="MWK"),
                Wallet.objects.get(user=self.user, currency="USDT"),
            ),
        ).start()
        self.addCleanup(mock.patch.stopall)

    def create_order(self, usdt="50"):
        response = self.client.post(
            reverse("order-buy"), {"amount_usdt": usdt, "payment_method": "airtel_money"}, format="json"
        )
        self.assertEqual(response.status_code, 201, response.data)
        return Order.objects.get(id=response.data["id"])

    def test_buy_order_locks_rate_and_quotes_total_payable(self):
        order = self.create_order()

        self.assertEqual(order.rate, Decimal("1850.00"))
        self.assertEqual(order.mwk_amount, Decimal("92500.00"))
        self.assertEqual(order.fee_amount, Decimal("925.00"))
        self.assertEqual(order.total_payable_mwk, Decimal("93425.00"))
        self.assertEqual(order.payment_reference, order.reference_number.replace("-", ""))
        self.assertIsNotNone(order.expires_at)
        self.assertEqual(order.status, Order.AWAITING_PAYMENT)

    def test_payment_instructions_expose_merchant_details(self):
        order = self.create_order()
        with self.settings(AIRTEL_MONEY_BUSINESS_CODE="123456"):
            response = self.client.get(reverse("order-detail", args=[order.id]))

        instructions = response.data["payment_instructions"]
        self.assertEqual(instructions["business_code"], "123456")
        self.assertEqual(instructions["amount_to_pay"], "93425.00")
        self.assertEqual(instructions["reference"], order.payment_reference)

    def test_submitting_transaction_id_queues_for_verification_without_crediting(self):
        order = self.create_order()

        response = self.client.post(
            reverse("order-submit-payment", args=[order.id]),
            {"transaction_id": "cm123456789"}, format="json",
        )

        order.refresh_from_db()
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(order.status, Order.PAYMENT_SUBMITTED)
        self.assertEqual(order.payment_transaction_id, "CM123456789")
        self.blnk.return_value.create_transaction.assert_not_called()
        self.assertTrue(
            Notification.objects.filter(user=self.user, title="Payment submitted").exists()
        )

    def test_junk_transaction_id_is_rejected(self):
        order = self.create_order()

        response = self.client.post(
            reverse("order-submit-payment", args=[order.id]), {"transaction_id": "hello"}, format="json"
        )

        order.refresh_from_db()
        self.assertEqual(response.status_code, 400)
        self.assertEqual(order.status, Order.AWAITING_PAYMENT)

    def test_transaction_id_cannot_be_reused_across_orders(self):
        first = self.create_order()
        second = self.create_order("20")
        submit_payment(first, self.user, "CM123456789")

        with self.assertRaises(OrderError):
            submit_payment(second, self.user, "CM123456789")

    def test_expired_order_cannot_be_paid(self):
        order = self.create_order()
        Order.objects.filter(pk=order.pk).update(expires_at=timezone.now() - timedelta(minutes=1))
        order.refresh_from_db()

        response = self.client.post(
            reverse("order-submit-payment", args=[order.id]),
            {"transaction_id": "CM123456789"}, format="json",
        )

        order.refresh_from_db()
        self.assertEqual(response.status_code, 400)
        self.assertEqual(order.status, Order.EXPIRED)

    def test_verification_settles_once_even_if_approved_twice(self):
        order = self.create_order()
        submit_payment(order, self.user, "CM123456789")

        verify_payment(order, self.admin)
        verify_payment(Order.objects.get(pk=order.pk), self.admin)

        order.refresh_from_db()
        self.assertEqual(order.status, Order.COMPLETED)
        self.assertEqual(OrderSettlement.objects.filter(order=order).count(), 1)
        self.assertEqual(self.blnk.return_value.create_transaction.call_count, 2)
        self.assertEqual(Transaction.objects.filter(reference=order.reference_number).count(), 1)

    def test_settlement_credits_the_order_amount_and_records_the_ledger_refs(self):
        order = self.create_order()
        submit_payment(order, self.user, "CM123456789")

        verify_payment(order, self.admin)

        order.refresh_from_db()
        settlement = OrderSettlement.objects.get(order=order)
        self.assertEqual(settlement.usdt_credited, order.usdt_amount)
        self.assertEqual(settlement.mwk_received, Decimal("93425.00"))
        self.assertEqual(order.blnk_transaction_refs, ["blnk-1", "blnk-2"])
        self.assertTrue(
            OrderAuditLog.objects.filter(order=order, action="settled").exists()
        )

    def test_wrong_amount_goes_to_mismatch_rather_than_crediting(self):
        order = self.create_order()
        submit_payment(order, self.user, "CM123456789")

        verify_payment(order, self.admin, received_amount=Decimal("90000.00"))

        order.refresh_from_db()
        self.assertEqual(order.status, Order.PAYMENT_MISMATCH)
        self.assertFalse(OrderSettlement.objects.filter(order=order).exists())
        self.blnk.return_value.create_transaction.assert_not_called()

    def test_rejection_records_reason_and_notifies_user(self):
        order = self.create_order()
        submit_payment(order, self.user, "CM123456789")

        admin_client = APIClient()
        admin_client.force_authenticate(self.admin)
        response = admin_client.post(
            reverse("order-reject-payment", args=[order.id]),
            {"reason": "No matching Airtel transaction."}, format="json",
        )

        order.refresh_from_db()
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(order.status, Order.REJECTED)
        self.assertEqual(order.rejection_reason, "No matching Airtel transaction.")
        self.assertTrue(
            Notification.objects.filter(user=self.user, title="Payment could not be verified").exists()
        )

    def test_verification_queue_is_closed_to_regular_users(self):
        self.create_order()

        response = self.client.get(reverse("payment-verification-queue"))

        self.assertEqual(response.status_code, 403)

    def test_verification_queue_lists_submitted_payments_for_admins(self):
        order = self.create_order()
        submit_payment(order, self.user, "CM123456789")

        admin_client = APIClient()
        admin_client.force_authenticate(self.admin)
        response = admin_client.get(reverse("payment-verification-queue"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]["payment_transaction_id"], "CM123456789")
        self.assertEqual(response.data[0]["customer"]["kyc_status"], "verified")

    def test_approval_requires_explicit_confirmation(self):
        order = self.create_order()
        submit_payment(order, self.user, "CM123456789")

        admin_client = APIClient()
        admin_client.force_authenticate(self.admin)
        response = admin_client.post(
            reverse("order-verify-payment", args=[order.id]), {"confirm": False}, format="json"
        )

        order.refresh_from_db()
        self.assertEqual(response.status_code, 400)
        self.assertEqual(order.status, Order.PAYMENT_SUBMITTED)

    def test_queued_transaction_remains_settling_until_applied(self):
        order = self.create_order()
        submit_payment(order, self.user, "CM123456789")

        self.blnk.return_value.create_transaction.side_effect = [
            {"transaction_id": "blnk-1", "status": "QUEUED"},
            {"transaction_id": "blnk-2", "status": "QUEUED"},
        ]
        self.blnk.return_value.get_transaction.return_value = {"status": "QUEUED"}

        verify_payment(order, self.admin)
        order.refresh_from_db()
        self.assertEqual(order.status, Order.SETTLING)

        # Now simulate Blnk worker finishing queue processing to APPLIED
        self.blnk.return_value.get_transaction.return_value = {"status": "APPLIED"}
        response = self.client.get(reverse("order-detail", args=[order.id]))
        self.assertEqual(response.status_code, 200)

        order.refresh_from_db()
        self.assertEqual(order.status, Order.COMPLETED)

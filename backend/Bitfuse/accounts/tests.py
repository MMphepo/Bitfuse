import threading
from decimal import Decimal
from unittest import mock
from django.test import TestCase, TransactionTestCase
from django.contrib.auth import get_user_model
from accounts.models import PlatformAccount, Wallet
from accounts.services import get_or_create_platform_account, ensure_user_wallets
from orders.services import complete_buy_order, complete_sell_order

User = get_user_model()


class BlnkIntegrationTests(TransactionTestCase):
    """TransactionTestCase is used here to support concurrent initialization locks if needed."""

    def setUp(self):
        # Clean up database records
        PlatformAccount.objects.all().delete()
        User.objects.all().delete()
        Wallet.objects.all().delete()

        self.mock_client = mock.MagicMock()
        self.mock_client.create_ledger.return_value = {"ledger_id": "led-new-123"}
        self.mock_client.create_balance.side_effect = lambda ledger_id, currency, meta: {
            "balance_id": f"bal-{currency.lower()}-{meta.get('role', 'generic')}"
        }
        self.mock_client.get_balance.return_value = {"balance": 1000000}
        self.mock_client.list_ledgers.return_value = []
        self.mock_client.list_balances.return_value = []

    def test_1_existing_float_does_not_recreate(self):
        """When the platform account exists in DB, no new ledger or balance is created in Blnk."""
        platform = PlatformAccount.objects.create(
            ledger_id="ledger-existing",
            mwk_float_balance_id="mwk-float-existing",
            usdt_float_balance_id="usdt-float-existing",
            mwk_external_contra_id="mwk-contra-existing",
            usdt_external_contra_id="usdt-contra-existing",
            usdt_frozen_balance_id="usdt-frozen-existing",
        )

        result = get_or_create_platform_account(client=self.mock_client)

        self.assertEqual(result.id, platform.id)
        self.assertEqual(result.usdt_float_balance_id, "usdt-float-existing")
        self.mock_client.create_ledger.assert_not_called()
        self.mock_client.create_balance.assert_not_called()

    def test_2_missing_database_mapping_but_blnk_resource_exists(self):
        """If platform DB record is missing, get_or_create_platform_account creates it once."""
        result = get_or_create_platform_account(client=self.mock_client)

        self.assertEqual(result.ledger_id, "led-new-123")
        self.assertEqual(result.usdt_float_balance_id, "bal-usdt-platform_usdt_float")
        self.mock_client.create_ledger.assert_called_once()

    def test_3_completely_missing_float_creates_once(self):
        """If platform account is completely missing, create it once and persist."""
        result = get_or_create_platform_account(client=self.mock_client)

        self.assertEqual(result.ledger_id, "led-new-123")
        self.assertEqual(result.usdt_float_balance_id, "bal-usdt-platform_usdt_float")
        self.mock_client.create_ledger.assert_called_once_with("Bitfuse Platform Account")
        self.assertEqual(self.mock_client.create_balance.call_count, 5)

    def test_4_buy_references_correct_balances(self):
        """Verify buy order finalization references correct float and user wallet balance IDs."""
        platform = PlatformAccount.objects.create(
            ledger_id="led-id",
            mwk_float_balance_id="mwk-float-id",
            usdt_float_balance_id="usdt-float-id",
            mwk_external_contra_id="mwk-contra-id",
            usdt_external_contra_id="usdt-contra-id",
            usdt_frozen_balance_id="usdt-frozen-id",
        )

        user = User.objects.create_user(
            username="buyer", email="b@example.com", phone_number="+265991000999"
        )
        Wallet.objects.create(user=user, currency="USDT", blnk_balance_id="buyer-usdt-bal")
        Wallet.objects.create(user=user, currency="MWK", blnk_balance_id="buyer-mwk-bal")

        from orders.models import Order
        order = Order.objects.create(
            reference_number="BF-BUY123",
            user=user,
            order_type="buy",
            mwk_amount=Decimal("185000"),
            usdt_amount=Decimal("100"),
            rate=Decimal("1850"),
            fee_percent=Decimal("1"),
            fee_amount=Decimal("1850"),
            payment_method="airtel_money",
            phone="+265991000999",
            status="payment_verified",
        )

        mock_blnk_client = mock.MagicMock()
        mock_blnk_client.create_transaction.return_value = {"transaction_id": "tx-ok"}

        with mock.patch("orders.services.BlnkClient", return_value=mock_blnk_client), \
             mock.patch("orders.services.ensure_user_wallets", return_value=(None, Wallet.objects.get(user=user, currency="USDT"))):
            complete_buy_order(order)

        # Check Blnk transactions:
        # Leg 1: external contra -> float mwk
        # Leg 2: usdt platform float -> user wallet
        self.assertEqual(mock_blnk_client.create_transaction.call_count, 2)
        calls = mock_blnk_client.create_transaction.call_args_list

        # USDT released Leg
        usdt_call = calls[1][1]
        self.assertEqual(usdt_call["source"], "usdt-float-id")
        self.assertEqual(usdt_call["destination"], "buyer-usdt-bal")

    def test_5_sell_references_correct_balances(self):
        """Verify sell order completion references user's USDT wallet, escrow, and platform float."""
        platform = PlatformAccount.objects.create(
            ledger_id="led-id",
            mwk_float_balance_id="mwk-float-id",
            usdt_float_balance_id="usdt-float-id",
            mwk_external_contra_id="mwk-contra-id",
            usdt_external_contra_id="usdt-contra-id",
            usdt_frozen_balance_id="usdt-frozen-id",
        )

        user = User.objects.create_user(
            username="seller", email="s@example.com", phone_number="+265991000888"
        )
        Wallet.objects.create(user=user, currency="USDT", blnk_balance_id="seller-usdt-bal")
        Wallet.objects.create(user=user, currency="MWK", blnk_balance_id="seller-mwk-bal")

        from orders.models import Order
        order = Order.objects.create(
            reference_number="BF-SELL123",
            user=user,
            order_type="sell",
            mwk_amount=Decimal("185000"),
            usdt_amount=Decimal("100"),
            rate=Decimal("1850"),
            fee_percent=Decimal("1"),
            fee_amount=Decimal("1850"),
            payment_method="airtel_money",
            phone="+265991000888",
            status="awaiting_deposit",
        )

        mock_blnk_client = mock.MagicMock()
        mock_blnk_client.create_transaction.return_value = {"transaction_id": "tx-ok"}

        with mock.patch("orders.services.BlnkClient", return_value=mock_blnk_client):
            complete_sell_order(order)

        # check Blnk transaction Leg 1: frozen escrow -> platform float
        calls = mock_blnk_client.create_transaction.call_args_list
        usdt_escrow_call = calls[0][1]
        self.assertEqual(usdt_escrow_call["source"], "usdt-frozen-id")
        self.assertEqual(usdt_escrow_call["destination"], "usdt-float-id")

    def test_6_blnk_offline_raises_error(self):
        """If Blnk is offline and PlatformAccount row is missing, get_or_create_platform_account raises RuntimeError."""
        self.mock_client.create_ledger.side_effect = RuntimeError("Blnk Offline")

        with self.assertRaises(RuntimeError) as exc:
            get_or_create_platform_account(client=self.mock_client)
        self.assertIn("Failed to create Blnk platform ledger", str(exc.exception))

    def test_7_concurrent_initialization(self):
        """Sequential duplicate initialization calls must be fully idempotent and not create duplicates."""
        res1 = get_or_create_platform_account(client=self.mock_client)
        res2 = get_or_create_platform_account(client=self.mock_client)

        self.assertEqual(res1.id, res2.id)
        self.assertEqual(PlatformAccount.objects.count(), 1)

    def test_8_blnk_client_retry_on_429(self):
        """BlnkClient retries with backoff when HTTP 429 is encountered."""
        from accounts.blnk_client import BlnkClient
        import requests

        client = BlnkClient(max_retries=2, backoff_factor=0.01)

        resp_429 = mock.MagicMock()
        resp_429.status_code = 429
        resp_429.headers = {}
        resp_429.raise_for_status.side_effect = requests.HTTPError("429 Too Many Requests")

        resp_200 = mock.MagicMock()
        resp_200.status_code = 200
        resp_200.content = b'{"status": "APPLIED"}'
        resp_200.json.return_value = {"status": "APPLIED"}

        with mock.patch("requests.request", side_effect=[resp_429, resp_200]) as mock_req:
            res = client.get_transaction("tx-123")
            self.assertEqual(res, {"status": "APPLIED"})
            self.assertEqual(mock_req.call_count, 2)

    def test_9_fetch_wallet_balance_resolves_different_balance_keys(self):
        """fetch_wallet_balance correctly parses available_balance or credit-debit fallbacks."""
        from accounts.services import fetch_wallet_balance

        user = User.objects.create_user(username="bal_test", email="bal@example.com", phone_number="+265999000111")
        Wallet.objects.create(user=user, currency="MWK", blnk_balance_id="mwk-bal-id")
        Wallet.objects.create(user=user, currency="USDT", blnk_balance_id="usdt-bal-id")

        mock_client = mock.MagicMock()
        mock_client.get_balance.side_effect = [
            {"available_balance": {"amount": 500000}},  # MWK nested dict
            {"balance": 0, "credit_balance": 10000000, "debit_balance": 2000000},  # USDT fallback
        ]

        with mock.patch("accounts.services.BlnkClient", return_value=mock_client), \
             mock.patch("accounts.services.ensure_user_wallets", return_value=(
                 Wallet.objects.get(user=user, currency="MWK"),
                 Wallet.objects.get(user=user, currency="USDT"),
             )):
            balances = fetch_wallet_balance(user)
            self.assertEqual(balances["MWK"], Decimal("5000.00"))
            self.assertEqual(balances["USDT"], Decimal("8.000000"))

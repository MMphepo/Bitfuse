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

    def test_10_p2p_transfer_success_and_idempotency(self):
        """P2P transfer debits sender, credits recipient, and obeys idempotency."""
        from accounts.services import perform_p2p_transfer
        from accounts.models import Transfer

        sender = User.objects.create_user(username="sender1", email="s1@example.com", phone_number="+265999111222")
        recipient = User.objects.create_user(username="recip1", email="r1@example.com", phone_number="+265999111333")

        Wallet.objects.create(user=sender, currency="USDT", blnk_balance_id="sender-usdt")
        Wallet.objects.create(user=sender, currency="MWK", blnk_balance_id="sender-mwk")
        Wallet.objects.create(user=recipient, currency="USDT", blnk_balance_id="recip-usdt")
        Wallet.objects.create(user=recipient, currency="MWK", blnk_balance_id="recip-mwk")

        mock_blnk_client = mock.MagicMock()
        mock_blnk_client.create_transaction.return_value = {"transaction_id": "tx-p2p-1"}

        with mock.patch("accounts.services.BlnkClient", return_value=mock_blnk_client), \
             mock.patch("accounts.services.fetch_wallet_balance", return_value={"USDT": Decimal("100.000000"), "MWK": Decimal("0")}):
            t1 = perform_p2p_transfer(
                sender=sender,
                recipient_username="recip1",
                amount=Decimal("25.00"),
                currency="USDT",
                idempotency_key="idemp-key-100",
            )
            self.assertEqual(t1.status, "Completed")
            self.assertEqual(t1.amount, Decimal("25.00"))
            self.assertEqual(t1.sender, sender)
            self.assertEqual(t1.recipient, recipient)

            # Re-submitting with identical idempotency key returns existing transfer without re-executing Blnk txn
            t2 = perform_p2p_transfer(
                sender=sender,
                recipient_username="recip1",
                amount=Decimal("25.00"),
                currency="USDT",
                idempotency_key="idemp-key-100",
            )
            self.assertEqual(t1.id, t2.id)
            self.assertEqual(mock_blnk_client.create_transaction.call_count, 1)

    def test_11_p2p_transfer_validation_errors(self):
        """P2P transfer rejects self-transfer, non-existent recipient, and insufficient balance."""
        from accounts.services import perform_p2p_transfer

        sender = User.objects.create_user(username="sender2", email="s2@example.com", phone_number="+265999111444")
        Wallet.objects.create(user=sender, currency="USDT", blnk_balance_id="sender2-usdt")
        Wallet.objects.create(user=sender, currency="MWK", blnk_balance_id="sender2-mwk")

        with mock.patch("accounts.services.fetch_wallet_balance", return_value={"USDT": Decimal("10.000000"), "MWK": Decimal("0")}):
            # Self-transfer
            with self.assertRaises(ValueError) as exc:
                perform_p2p_transfer(sender, "sender2", Decimal("5.00"), "USDT", "idemp-self")
            self.assertIn("Cannot transfer funds to yourself", str(exc.exception))

            # Nonexistent recipient
            with self.assertRaises(ValueError) as exc:
                perform_p2p_transfer(sender, "ghost", Decimal("5.00"), "USDT", "idemp-ghost")
            self.assertIn("Recipient user 'ghost' not found", str(exc.exception))

            # Insufficient balance
            User.objects.create_user(username="validrecip", email="vr@example.com", phone_number="+265999111555")
            with self.assertRaises(ValueError) as exc:
                perform_p2p_transfer(sender, "validrecip", Decimal("50.00"), "USDT", "idemp-overbound")
            self.assertIn("Insufficient USDT balance", str(exc.exception))

    def test_12_wallet_balance_cache_hit_and_invalidation(self):
        """Verify normal balance fetching populates cache, cache hit prevents Blnk calls, and mutation invalidates cache."""
        from django.core.cache import cache
        from accounts.services import fetch_wallet_balance, invalidate_wallet_balance_cache

        user = User.objects.create_user(username="cache_user", email="cu@example.com", phone_number="+265999222111")
        Wallet.objects.create(user=user, currency="MWK", blnk_balance_id="cu-mwk")
        Wallet.objects.create(user=user, currency="USDT", blnk_balance_id="cu-usdt")

        mock_client = mock.MagicMock()
        mock_client.get_balance.side_effect = [
            {"balance": 100000},  # 1000 MWK
            {"balance": 50000000},  # 50 USDT
        ]

        with mock.patch("accounts.services.BlnkClient", return_value=mock_client), \
             mock.patch("accounts.services.ensure_user_wallets", return_value=(
                 Wallet.objects.get(user=user, currency="MWK"),
                 Wallet.objects.get(user=user, currency="USDT"),
             )):
            # 1. First fetch — cache miss, calls Blnk twice (MWK + USDT)
            bals1 = fetch_wallet_balance(user)
            self.assertEqual(bals1["MWK"], Decimal("1000.00"))
            self.assertEqual(bals1["USDT"], Decimal("50.000000"))
            self.assertEqual(mock_client.get_balance.call_count, 2)

            # 2. Second fetch — cache hit, does NOT call Blnk again
            bals2 = fetch_wallet_balance(user)
            self.assertEqual(bals2["MWK"], Decimal("1000.00"))
            self.assertEqual(bals2["USDT"], Decimal("50.000000"))
            self.assertEqual(mock_client.get_balance.call_count, 2)

            # 3. Invalidate cache
            invalidate_wallet_balance_cache(user.id)

            # Prepare new mock return values for fresh Blnk call
            mock_client.get_balance.side_effect = [
                {"balance": 200000},  # 2000 MWK
                {"balance": 100000000},  # 100 USDT
            ]

            # 4. Third fetch — cache miss after invalidation, calls Blnk again
            bals3 = fetch_wallet_balance(user)
            self.assertEqual(bals3["MWK"], Decimal("2000.00"))
            self.assertEqual(bals3["USDT"], Decimal("100.000000"))
            self.assertEqual(mock_client.get_balance.call_count, 4)

    def test_13_blnk_failure_returns_503(self):
        """Verify Blnk unavailable/timeout returns HTTP 503 structured response from WalletBalanceView."""
        from rest_framework.test import APIClient
        from rest_framework import status

        user = User.objects.create_user(username="fail_user", email="fu@example.com", phone_number="+265999333111")
        client = APIClient()
        client.force_authenticate(user=user)

        with mock.patch("accounts.services.fetch_wallet_balance", side_effect=RuntimeError("Blnk Timeout")):
            response = client.get("/api/v1/auth/wallets/")
            self.assertEqual(response.status_code, status.HTTP_503_SERVICE_UNAVAILABLE)
            self.assertEqual(response.data["code"], "BALANCE_SERVICE_UNAVAILABLE")
            self.assertIn("temporarily unavailable", response.data["message"])

    def test_14_concurrent_balance_requests_coalescing(self):
        """Verify concurrent requests for same user balance are coalesced into a single Blnk fetch."""
        from django.core.cache import cache
        from accounts.services import fetch_wallet_balance

        user = User.objects.create_user(username="coal_user", email="coal@example.com", phone_number="+265999444111")
        Wallet.objects.create(user=user, currency="MWK", blnk_balance_id="coal-mwk")
        Wallet.objects.create(user=user, currency="USDT", blnk_balance_id="coal-usdt")

        cache.delete(f"wallet_balance:{user.id}")

        mock_client = mock.MagicMock()
        mock_client.get_balance.side_effect = [
            {"balance": 500000},  # 5000 MWK
            {"balance": 20000000},  # 20 USDT
        ]

        results = []

        def worker():
            with mock.patch("accounts.services.BlnkClient", return_value=mock_client), \
                 mock.patch("accounts.services.ensure_user_wallets", return_value=(
                     Wallet.objects.get(user=user, currency="MWK"),
                     Wallet.objects.get(user=user, currency="USDT"),
                 )):
                bals = fetch_wallet_balance(user)
                results.append(bals)

        t1 = threading.Thread(target=worker)
        t2 = threading.Thread(target=worker)
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        self.assertEqual(len(results), 2)
        self.assertEqual(results[0]["USDT"], Decimal("20.000000"))
        self.assertEqual(results[1]["USDT"], Decimal("20.000000"))
        # Blnk get_balance should only be called twice total (1 fetch for MWK, 1 for USDT)
        self.assertEqual(mock_client.get_balance.call_count, 2)

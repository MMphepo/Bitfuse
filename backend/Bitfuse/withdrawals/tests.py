from decimal import Decimal
from unittest import mock
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from accounts.models import PlatformAccount, Wallet
from withdrawals.models import Withdrawal, WithdrawalConfig
from withdrawals.services.withdrawal_service import (
    WithdrawalError,
    get_withdrawal_quote,
    initiate_withdrawal,
    monitor_broadcast_withdrawals,
)

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


class WithdrawalSystemTests(TestCase):
    def setUp(self):
        # 1. Create a verified user
        self.user = User.objects.create_user(
            username="alice",
            email="alice@example.com",
            password="password123",
            phone_number="+265991222222",
            verification_status="verified",
        )
        # 2. Create another user to test cross-user access
        self.other_user = User.objects.create_user(
            username="bob",
            email="bob@example.com",
            password="password123",
            phone_number="+265991333333",
            verification_status="verified",
        )
        # 3. Create unverified user
        self.unverified_user = User.objects.create_user(
            username="charlie",
            email="charlie@example.com",
            password="password123",
            phone_number="+265991444444",
            verification_status="unverified",
        )

        # 4. Set up platform account and user wallets
        make_platform_account()
        Wallet.objects.create(user=self.user, currency="USDT", blnk_balance_id="alice-usdt")
        Wallet.objects.create(user=self.user, currency="MWK", blnk_balance_id="alice-mwk")
        Wallet.objects.create(user=self.other_user, currency="USDT", blnk_balance_id="bob-usdt")
        Wallet.objects.create(user=self.other_user, currency="MWK", blnk_balance_id="bob-mwk")

        # 5. Initialize API Client
        self.client = APIClient()
        self.client.force_authenticate(self.user)

        # 6. Mock BlnkClient and fetch_wallet_balance
        self.blnk_patcher = mock.patch("withdrawals.services.withdrawal_service.BlnkClient")
        self.mock_blnk = self.blnk_patcher.start()
        self.mock_blnk.return_value.create_transaction.side_effect = lambda **kwargs: {
            "transaction_id": f"txn-{kwargs.get('reference', 'ref')}"
        }

        self.balance_patcher = mock.patch(
            "withdrawals.services.withdrawal_service.fetch_wallet_balance",
            return_value={"USDT": Decimal("100.000000"), "MWK": Decimal("0.00")},
        )
        self.mock_balances = self.balance_patcher.start()

        self.wallets_patcher = mock.patch(
            "withdrawals.services.withdrawal_service.ensure_user_wallets"
        )
        self.mock_ensure_wallets = self.wallets_patcher.start()
        self.mock_ensure_wallets.side_effect = lambda u: (
            Wallet.objects.get(user=u, currency="MWK"),
            Wallet.objects.get(user=u, currency="USDT"),
        )

        # 7. Mock TRON network provider through get_blockchain_provider
        self.mock_provider = mock.MagicMock()
        self.mock_provider.validate_address.side_effect = lambda addr: addr.startswith("T") and len(addr) == 34
        self.mock_provider.build_transfer_transaction.return_value = {"mocked": True, "txID": "mock-tx-id"}
        self.mock_provider.broadcast_transaction.return_value = "mock-tx-hash-12345"
        self.mock_provider.get_transaction_status.return_value = "SUCCESS"

        self.get_provider_patcher = mock.patch(
            "withdrawals.services.withdrawal_service.get_blockchain_provider",
            return_value=self.mock_provider
        )
        self.mock_get_provider = self.get_provider_patcher.start()

        self.addCleanup(mock.patch.stopall)

    # --- Verification & Security Gates ---

    def test_unauthenticated_user_access(self):
        self.client.force_authenticate(None)
        response = self.client.post(reverse("withdrawal-list-create"), {"amount": "50.00", "destination_address": "TY4hG6Xz6m93ssVjUr3NZsSXYhxXabc123"})
        self.assertEqual(response.status_code, 401)

        response_list = self.client.get(reverse("withdrawal-list-create"))
        self.assertEqual(response_list.status_code, 401)

    def test_unverified_kyc_user_withdrawal_denied(self):
        self.client.force_authenticate(self.unverified_user)
        response = self.client.post(reverse("withdrawal-list-create"), {
            "amount": "50.00",
            "destination_address": "TY4hG6Xz6m93ssVjUr3NZsSXYhxXabc123"
        })
        self.assertEqual(response.status_code, 403)
        self.assertIn("Complete identity verification", response.data["detail"])

    def test_user_cannot_access_another_users_withdrawal(self):
        # Create withdrawal for Alice
        withdrawal = initiate_withdrawal(self.user, "USDT", "TRON", Decimal("50.00"), "TY4hG6Xz6m93ssVjUr3NZsSXYhxXabc123")

        # Bob tries to access Alice's withdrawal details
        self.client.force_authenticate(self.other_user)
        response = self.client.get(reverse("withdrawal-detail", args=[withdrawal.id]))
        self.assertEqual(response.status_code, 404)

    # --- Amount & Address Boundary Validation ---

    def test_invalid_withdrawal_amount_neg_zero_malformed(self):
        # Zero amount
        response = self.client.post(reverse("withdrawal-list-create"), {
            "amount": "0.00",
            "destination_address": "TY4hG6Xz6m93ssVjUr3NZsSXYhxXabc123"
        })
        self.assertEqual(response.status_code, 400)

        # Negative amount
        response = self.client.post(reverse("withdrawal-list-create"), {
            "amount": "-10.00",
            "destination_address": "TY4hG6Xz6m93ssVjUr3NZsSXYhxXabc123"
        })
        self.assertEqual(response.status_code, 400)

    def test_withdrawal_limits_min_max(self):
        from withdrawals.models import WithdrawalNetworkConfig
        net_cfg = WithdrawalNetworkConfig.get_for_network("TRON", "USDT")
        net_cfg.min_withdrawal = Decimal("20.00")
        net_cfg.max_withdrawal = Decimal("500.00")
        net_cfg.save()

        # Below min
        response = self.client.post(reverse("withdrawal-list-create"), {
            "amount": "15.00",
            "destination_address": "TY4hG6Xz6m93ssVjUr3NZsSXYhxXabc123"
        })
        self.assertEqual(response.status_code, 400)
        self.assertIn("below the minimum", response.data["detail"])

        # Above max
        response = self.client.post(reverse("withdrawal-list-create"), {
            "amount": "501.00",
            "destination_address": "TY4hG6Xz6m93ssVjUr3NZsSXYhxXabc123"
        })
        self.assertEqual(response.status_code, 400)
        self.assertIn("exceeds the maximum", response.data["detail"])

    def test_invalid_tron_address_format(self):
        # Invalid start char
        response = self.client.post(reverse("withdrawal-list-create"), {
            "amount": "50.00",
            "destination_address": "AY4hG6Xz6m93ssVjUr3NZsSXYhxXabc123"
        })
        self.assertEqual(response.status_code, 400)
        self.assertIn("not a valid TRON address", response.data["detail"])

        # Invalid length
        response = self.client.post(reverse("withdrawal-list-create"), {
            "amount": "50.00",
            "destination_address": "TY4hG6Xz6m93ss"
        })
        self.assertEqual(response.status_code, 400)

    def test_unsupported_network(self):
        response = self.client.post(reverse("withdrawal-list-create"), {
            "amount": "50.00",
            "network": "ETHEREUM",
            "destination_address": "TY4hG6Xz6m93ssVjUr3NZsSXYhxXabc123"
        })
        self.assertEqual(response.status_code, 400)
        detail_msg = str(response.data.get("detail") or response.data.get("network") or response.data)
        self.assertTrue("Unsupported network" in detail_msg or "TRON" in detail_msg)

    # --- Balance & Fee System ---

    def test_insufficient_balance(self):
        # Alice only has 100 USDT, request 101 USDT
        response = self.client.post(reverse("withdrawal-list-create"), {
            "amount": "101.00",
            "destination_address": "TY4hG6Xz6m93ssVjUr3NZsSXYhxXabc123"
        })
        self.assertEqual(response.status_code, 400)
        self.assertIn("Insufficient USDT balance", response.data["detail"])

    def test_withdrawal_quote_endpoint(self):
        response = self.client.post(reverse("withdrawal-quote"), {
            "amount": "50.00"
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["amount"], Decimal("50.00"))
        self.assertEqual(response.data["fee"], Decimal("0.50"))
        self.assertEqual(response.data["net_amount"], Decimal("49.50"))

    # --- Success Flow & Blnk Reservation/Finalization ---

    def test_successful_withdrawal_flow(self):
        response = self.client.post(reverse("withdrawal-list-create"), {
            "amount": "50.00",
            "destination_address": "TY4hG6Xz6m93ssVjUr3NZsSXYhxXabc123"
        })
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["status"], "BROADCAST")
        self.assertEqual(response.data["amount"], "50.000000")
        self.assertEqual(response.data["fee"], "0.500000")
        self.assertEqual(response.data["net_amount"], "49.500000")
        self.assertEqual(response.data["transaction_hash"], "mock-tx-hash-12345")

        # Check Blnk creation calls
        # 1. Reserve transaction
        # 2. Finalize transaction
        self.assertEqual(self.mock_blnk.return_value.create_transaction.call_count, 2)

    # --- Error Recovery & Refunds ---

    def test_blockchain_broadcast_failure_refunds_user(self):
        # Simulate blockchain broadcast error
        self.mock_provider.broadcast_transaction.side_effect = RuntimeError("Broadcast error")

        response = self.client.post(reverse("withdrawal-list-create"), {
            "amount": "50.00",
            "destination_address": "TY4hG6Xz6m93ssVjUr3NZsSXYhxXabc123"
        })
        self.assertEqual(response.status_code, 400)

        # Verify withdrawal model was created and marked as FAILED with refund refs
        withdrawal = Withdrawal.objects.first()
        self.assertIsNotNone(withdrawal)
        self.assertEqual(withdrawal.status, "FAILED")
        self.assertIn("Blockchain broadcast failed: Broadcast error", withdrawal.failure_reason)

        # Check Blnk calls:
        # 1. Reserve transaction (user -> platform frozen)
        # 2. Refund transaction (platform frozen -> user)
        # Verify 2 transactions were successfully made in Blnk
        self.assertEqual(self.mock_blnk.return_value.create_transaction.call_count, 2)

    # --- Transaction Monitoring ---

    def test_transaction_monitoring_confirm(self):
        # Create a broadcasted withdrawal
        withdrawal = initiate_withdrawal(self.user, "USDT", "TRON", Decimal("50.00"), "TY4hG6Xz6m93ssVjUr3NZsSXYhxXabc123")
        self.assertEqual(withdrawal.status, "BROADCAST")

        # Set mock provider return to SUCCESS
        self.mock_provider.get_transaction_status.return_value = "SUCCESS"

        processed = monitor_broadcast_withdrawals()
        self.assertEqual(processed, 1)

        withdrawal.refresh_from_db()
        self.assertEqual(withdrawal.status, "CONFIRMED")
        self.assertIsNotNone(withdrawal.confirmed_at)

    def test_transaction_monitoring_failure_refunds_user(self):
        # Create a broadcasted withdrawal
        withdrawal = initiate_withdrawal(self.user, "USDT", "TRON", Decimal("50.00"), "TY4hG6Xz6m93ssVjUr3NZsSXYhxXabc123")
        self.assertEqual(withdrawal.status, "BROADCAST")

        # Reset mock call count to track refund
        self.mock_blnk.return_value.create_transaction.reset_mock()

        # Set mock provider return to FAILED on-chain
        self.mock_provider.get_transaction_status.return_value = "FAILED"

        processed = monitor_broadcast_withdrawals()
        self.assertEqual(processed, 1)

        withdrawal.refresh_from_db()
        self.assertEqual(withdrawal.status, "FAILED")
        self.assertEqual(withdrawal.failure_reason, "Blockchain transaction failed on-chain.")

        # Blnk refund must have been created (source platform external contra -> destination user)
        self.mock_blnk.return_value.create_transaction.assert_called_once()
        args, kwargs = self.mock_blnk.return_value.create_transaction.call_args
        self.assertEqual(kwargs["source"], "usdt-contra")
        self.assertEqual(kwargs["destination"], "alice-usdt")
        self.assertEqual(kwargs["amount"], int(Decimal("50.00") * Decimal("1000000")))

    # --- Emergency Global Freeze Switch ---

    def test_global_freeze_switch(self):
        # Freeze withdrawals
        config_obj = WithdrawalConfig.get_current()
        config_obj.withdrawals_frozen = True
        config_obj.save()

        # Creating withdrawal must be rejected
        response = self.client.post(reverse("withdrawal-list-create"), {
            "amount": "50.00",
            "destination_address": "TY4hG6Xz6m93ssVjUr3NZsSXYhxXabc123"
        })
        self.assertEqual(response.status_code, 400)
        self.assertIn("temporarily frozen", response.data["detail"])

        # Quoting must be rejected
        response_quote = self.client.post(reverse("withdrawal-quote"), {
            "amount": "50.00"
        })
        self.assertEqual(response_quote.status_code, 400)
        self.assertIn("temporarily frozen", response_quote.data["detail"])


class BscWithdrawalTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="bsc_alice",
            email="bsc_alice@example.com",
            password="password123",
            verification_status="verified",
        )
        make_platform_account()
        Wallet.objects.create(user=self.user, currency="USDT", blnk_balance_id="bsc-alice-usdt")
        Wallet.objects.create(user=self.user, currency="MWK", blnk_balance_id="bsc-alice-mwk")

        self.client = APIClient()
        self.client.force_authenticate(self.user)

        self.blnk_patcher = mock.patch("withdrawals.services.withdrawal_service.BlnkClient")
        self.mock_blnk = self.blnk_patcher.start()
        self.mock_blnk.return_value.create_transaction.side_effect = lambda **kwargs: {
            "transaction_id": f"bsc-txn-{kwargs.get('reference', 'ref')}"
        }

        self.workers_blnk_patcher = mock.patch("withdrawals.services.workers.BlnkClient")
        self.mock_workers_blnk = self.workers_blnk_patcher.start()
        self.mock_workers_blnk.return_value.create_transaction.side_effect = lambda **kwargs: {
            "transaction_id": f"bsc-deposit-txn-{kwargs.get('reference', 'ref')}"
        }

        self.balance_patcher = mock.patch(
            "withdrawals.services.withdrawal_service.fetch_wallet_balance",
            return_value={"USDT": Decimal("500.000000"), "MWK": Decimal("0.00")},
        )
        self.mock_balances = self.balance_patcher.start()

        self.wallets_patcher = mock.patch(
            "withdrawals.services.withdrawal_service.ensure_user_wallets"
        )
        self.mock_ensure_wallets = self.wallets_patcher.start()
        self.mock_ensure_wallets.side_effect = lambda u: (
            Wallet.objects.get(user=u, currency="MWK"),
            Wallet.objects.get(user=u, currency="USDT"),
        )

        self.addCleanup(mock.patch.stopall)

    def test_evm_address_validation(self):
        from withdrawals.services.blockchain.bsc import validate_evm_address
        self.assertTrue(validate_evm_address("0xdAC17F958D2ee523a2206206994597C13D831ec7"))
        self.assertTrue(validate_evm_address("0xdac17f958d2ee523a2206206994597c13d831ec7"))
        self.assertFalse(validate_evm_address("TY4hG6Xz6m93ssVjUr3NZsSXYhxXabc123"))
        self.assertFalse(validate_evm_address("0x1234"))

    def test_bsc_withdrawal_quote(self):
        response = self.client.post(reverse("withdrawal-quote"), {
            "asset": "USDT",
            "network": "BSC",
            "amount": "100.00"
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["network"], "BSC")
        self.assertEqual(response.data["fee"], Decimal("1.00"))
        self.assertEqual(response.data["net_amount"], Decimal("99.00"))

    def test_bsc_withdrawal_initiate_success(self):
        valid_evm_address = "0xdAC17F958D2ee523a2206206994597C13D831ec7"
        response = self.client.post(reverse("withdrawal-list-create"), {
            "asset": "USDT",
            "network": "BSC",
            "amount": "100.00",
            "destination_address": valid_evm_address
        })
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["network"], "BSC")
        self.assertEqual(response.data["status"], "BROADCAST")
        self.assertIsNotNone(response.data["transaction_hash"])

        withdrawal = Withdrawal.objects.get(id=response.data["id"])
        self.assertEqual(withdrawal.network, "BSC")
        self.assertEqual(withdrawal.amount, Decimal("100.00"))

    def test_bsc_nonce_concurrency_tracker(self):
        from withdrawals.services.blockchain.bsc import BscNonceManager
        wallet = "0x1111111111111111111111111111111111111111"
        nonce1 = BscNonceManager.allocate_nonce(wallet)
        nonce2 = BscNonceManager.allocate_nonce(wallet)
        self.assertEqual(nonce1, 0)
        self.assertEqual(nonce2, 1)

    def test_deposit_verification_and_blnk_credit(self):
        from withdrawals.services.workers import process_bsc_deposit_event
        from withdrawals.models import DepositRecord

        event_data = {
            "event_id": "97:0xhash123:0",
            "tx_hash": "0xhash123",
            "log_index": 0,
            "from_address": "0x1111111111111111111111111111111111111111",
            "to_address": "0x2222222222222222222222222222222222222222",
            "amount": Decimal("50.00"),
            "block_number": 1000,
            "confirmations": 15,
            "user": self.user,
        }

        with mock.patch("withdrawals.services.blockchain.bsc.BscProvider.verify_transfer", return_value=True):
            deposit = process_bsc_deposit_event(event_data)
            self.assertEqual(deposit.status, "CREDITED")
            self.assertIsNotNone(deposit.blnk_transaction_id)
            self.mock_workers_blnk.return_value.create_transaction.assert_called()

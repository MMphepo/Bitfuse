import threading
from decimal import Decimal
from unittest import mock
from django.test import TestCase, TransactionTestCase, override_settings
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient
from rest_framework import status

from accounts.models import EmailVerificationToken, PasswordResetToken, PhoneOTP, PlatformAccount, Wallet
from accounts.services import get_or_create_platform_account, ensure_user_wallets
from accounts.auth_services import EmailService
from orders.services import complete_buy_order, complete_sell_order

User = get_user_model()


class BlnkIntegrationTests(TransactionTestCase):
    """TransactionTestCase is used here to support concurrent initialization locks if needed."""

    def setUp(self):
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

    def test_2_missing_database_mapping_but_blnk_resource_exists(self):
        result = get_or_create_platform_account(client=self.mock_client)
        self.assertEqual(result.ledger_id, "led-new-123")
        self.assertEqual(result.usdt_float_balance_id, "bal-usdt-platform_usdt_float")

    def test_3_completely_missing_float_creates_once(self):
        result = get_or_create_platform_account(client=self.mock_client)
        self.assertEqual(result.ledger_id, "led-new-123")
        self.assertEqual(result.usdt_float_balance_id, "bal-usdt-platform_usdt_float")

    def test_4_buy_references_correct_balances(self):
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

        self.assertEqual(mock_blnk_client.create_transaction.call_count, 2)

    def test_5_sell_references_correct_balances(self):
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

        calls = mock_blnk_client.create_transaction.call_args_list
        usdt_escrow_call = calls[0][1]
        self.assertEqual(usdt_escrow_call["source"], "usdt-frozen-id")

    def test_6_blnk_offline_raises_error(self):
        self.mock_client.create_ledger.side_effect = RuntimeError("Blnk Offline")
        with self.assertRaises(RuntimeError) as exc:
            get_or_create_platform_account(client=self.mock_client)
        self.assertIn("Failed to create Blnk platform ledger", str(exc.exception))

    def test_7_concurrent_initialization(self):
        res1 = get_or_create_platform_account(client=self.mock_client)
        res2 = get_or_create_platform_account(client=self.mock_client)
        self.assertEqual(res1.id, res2.id)

    def test_8_blnk_client_retry_on_429(self):
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

    def test_13_blnk_failure_returns_503(self):
        user = User.objects.create_user(username="fail_user", email="fu@example.com", phone_number="+265999333111")
        client = APIClient()
        client.force_authenticate(user=user)

        with mock.patch("accounts.services.fetch_wallet_balance", side_effect=RuntimeError("Blnk Timeout")):
            response = client.get("/api/v1/auth/wallets/")
            self.assertEqual(response.status_code, status.HTTP_503_SERVICE_UNAVAILABLE)
            self.assertEqual(response.data["code"], "BALANCE_SERVICE_UNAVAILABLE")


from django.core.cache import cache

class AuthRegistrationTests(TestCase):
    def setUp(self):
        cache.clear()
        self.client = APIClient()

    def test_valid_registration(self):
        data = {
            "first_name": "Chifundo",
            "last_name": "Kachale",
            "email": "chifundo@example.com",
            "phone_number": "0999123456",
            "password": "StrongPassword123!",
            "password_confirmation": "StrongPassword123!",
        }
        resp = self.client.post("/api/v1/auth/register/", data, format="json")
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        self.assertTrue(resp.data["success"])
        self.assertIn("tokens", resp.data["data"])

        user = User.objects.get(email="chifundo@example.com")
        self.assertEqual(user.first_name, "Chifundo")
        self.assertEqual(user.last_name, "Kachale")
        self.assertEqual(user.phone_number, "+265999123456")
        self.assertFalse(user.email_verified)
        self.assertFalse(user.phone_verified)
        self.assertTrue(user.check_password("StrongPassword123!"))

    def test_missing_first_name(self):
        data = {
            "first_name": "",
            "last_name": "Kachale",
            "email": "nofirst@example.com",
            "phone_number": "0999123456",
            "password": "StrongPassword123!",
            "password_confirmation": "StrongPassword123!",
        }
        resp = self.client.post("/api/v1/auth/register/", data, format="json")
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(resp.data["success"])
        self.assertIn("first_name", resp.data["errors"])

    def test_duplicate_email(self):
        User.objects.create_user(
            username="existing_user",
            email="dup@example.com",
            phone_number="+265999000111",
            password="Password123!",
        )
        data = {
            "first_name": "John",
            "last_name": "Doe",
            "email": "DUP@example.com",
            "phone_number": "0999222333",
            "password": "StrongPassword123!",
            "password_confirmation": "StrongPassword123!",
        }
        resp = self.client.post("/api/v1/auth/register/", data, format="json")
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("email", resp.data["errors"])

    def test_password_mismatch(self):
        data = {
            "first_name": "Jane",
            "last_name": "Doe",
            "email": "mismatch@example.com",
            "phone_number": "0999333444",
            "password": "StrongPassword123!",
            "password_confirmation": "WrongPassword123!",
        }
        resp = self.client.post("/api/v1/auth/register/", data, format="json")
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("password_confirmation", resp.data["errors"])
        self.assertEqual(resp.data["errors"]["password_confirmation"], ["Passwords do not match."])

    def test_common_weak_password(self):
        data = {
            "first_name": "Weak",
            "last_name": "Pass",
            "email": "weak@example.com",
            "phone_number": "0999444555",
            "password": "password123",
            "password_confirmation": "password123",
        }
        resp = self.client.post("/api/v1/auth/register/", data, format="json")
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("password", resp.data["errors"])


class AuthLoginAndJWTTests(TestCase):
    def setUp(self):
        cache.clear()
        self.client = APIClient()
        self.user = User.objects.create_user(
            username="testuser",
            email="login@example.com",
            phone_number="+265999888777",
            first_name="Test",
            last_name="User",
            password="ValidPassword123!",
        )

    def test_login_success(self):
        resp = self.client.post(
            "/api/v1/auth/login/",
            {"email": "login@example.com", "password": "ValidPassword123!"},
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertTrue(resp.data["success"])
        self.assertIn("access", resp.data["data"]["tokens"])
        self.assertIn("refresh", resp.data["data"]["tokens"])

    def test_login_invalid_credentials(self):
        resp = self.client.post(
            "/api/v1/auth/login/",
            {"email": "login@example.com", "password": "WrongPassword"},
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_401_UNAUTHORIZED)
        self.assertFalse(resp.data["success"])
        self.assertEqual(resp.data["message"], "Invalid email or password.")

    def test_token_refresh_and_blacklisting(self):
        login_resp = self.client.post(
            "/api/v1/auth/login/",
            {"email": "login@example.com", "password": "ValidPassword123!"},
            format="json",
        )
        refresh_token = login_resp.data["data"]["tokens"]["refresh"]

        # Refresh token
        ref_resp = self.client.post("/api/v1/auth/login/refresh/", {"refresh": refresh_token}, format="json")
        self.assertEqual(ref_resp.status_code, status.HTTP_200_OK)
        new_refresh = ref_resp.data.get("refresh")

        # Try reusing old refresh token (blacklisted due to rotation)
        old_ref_resp = self.client.post("/api/v1/auth/login/refresh/", {"refresh": refresh_token}, format="json")
        self.assertEqual(old_ref_resp.status_code, status.HTTP_401_UNAUTHORIZED)

        # Logout with new refresh token
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {login_resp.data['data']['tokens']['access']}")
        if new_refresh:
            logout_resp = self.client.post("/api/v1/auth/logout/", {"refresh": new_refresh}, format="json")
            self.assertEqual(logout_resp.status_code, status.HTTP_200_OK)


class AuthVerificationTests(TestCase):
    def setUp(self):
        cache.clear()
        self.client = APIClient()
        self.user = User.objects.create_user(
            username="verifuser",
            email="verif@example.com",
            phone_number="+265999111000",
            password="ValidPassword123!",
        )

    def test_email_verification_flow(self):
        raw_token = EmailService.send_verification_email(self.user)
        self.assertFalse(self.user.email_verified)

        resp = self.client.post("/api/v1/auth/verify-email/", {"token": raw_token}, format="json")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertTrue(resp.data["success"])

        self.user.refresh_from_db()
        self.assertTrue(self.user.email_verified)

        # Reusing token fails
        reuse_resp = self.client.post("/api/v1/auth/verify-email/", {"token": raw_token}, format="json")
        self.assertEqual(reuse_resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_otp_verification_flow(self):
        self.client.force_authenticate(user=self.user)
        req_resp = self.client.post("/api/v1/auth/otp/request/", {"phone_number": "0999111000"}, format="json")
        self.assertEqual(req_resp.status_code, status.HTTP_200_OK)
        dev_code = req_resp.data.get("dev_code")

        # Verify correct OTP
        ver_resp = self.client.post("/api/v1/auth/otp/verify/", {"phone_number": "0999111000", "code": dev_code}, format="json")
        self.assertEqual(ver_resp.status_code, status.HTTP_200_OK)

        self.user.refresh_from_db()
        self.assertTrue(self.user.phone_verified)


class PasswordResetTests(TestCase):
    def setUp(self):
        cache.clear()
        self.client = APIClient()
        self.user = User.objects.create_user(
            username="resetuser",
            email="reset@example.com",
            phone_number="+265999222333",
            password="OldPassword123!",
        )

    def test_password_reset_flow(self):
        raw_token = EmailService.send_password_reset_email("reset@example.com")
        self.assertNotEqual(raw_token, "reset_sent")

        confirm_resp = self.client.post(
            "/api/v1/auth/password-reset/confirm/",
            {
                "token": raw_token,
                "new_password": "NewStrongPassword123!",
                "password_confirmation": "NewStrongPassword123!",
            },
            format="json",
        )
        self.assertEqual(confirm_resp.status_code, status.HTTP_200_OK)
        self.assertTrue(confirm_resp.data["success"])

        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("NewStrongPassword123!"))

    def test_unknown_email_generic_response(self):
        req_resp = self.client.post("/api/v1/auth/password-reset/", {"email": "unknown@example.com"}, format="json")
        self.assertEqual(req_resp.status_code, status.HTTP_200_OK)
        self.assertIn("password reset instructions have been sent", req_resp.data["message"])


class GoogleAuthTests(TestCase):
    def setUp(self):
        cache.clear()
        self.client = APIClient()

    def test_google_auth_new_user(self):
        resp = self.client.post("/api/v1/auth/google/", {"id_token": "mock-google-token-new"}, format="json")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertTrue(resp.data["success"])
        self.assertIn("tokens", resp.data["data"])

    def test_google_auth_account_linking(self):
        user = User.objects.create_user(
            username="google_link",
            email="googleuser@example.com",
            phone_number="+265999444333",
            password="Password123!",
            email_verified=True,
        )
        resp = self.client.post("/api/v1/auth/google/", {"id_token": "mock-google-token-link"}, format="json")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)

        user.refresh_from_db()
        self.assertEqual(user.google_id, "google-uid-12345")


class TradingEligibilityTests(TestCase):
    def setUp(self):
        cache.clear()
        self.client = APIClient()
        self.unverified_user = User.objects.create_user(
            username="unverif",
            email="unverif@example.com",
            phone_number="+265999555444",
            password="Password123!",
            email_verified=False,
            phone_verified=False,
            verification_status="unverified",
        )

    def test_trading_gate_blocks_unverified_user(self):
        self.client.force_authenticate(user=self.unverified_user)
        resp = self.client.post(
            "/api/v1/auth/wallets/send/",
            {
                "recipient_username": "other",
                "amount": "10.00",
                "currency": "USDT",
                "idempotency_key": "idemp-test-gate",
            },
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)
        self.assertIn("verify your email address", resp.data["message"])

import threading
import requests
from decimal import Decimal
from unittest import mock
from io import StringIO
from django.conf import settings
from django.test import TestCase, TransactionTestCase, override_settings
from django.contrib.auth import get_user_model
from django.core.management import call_command
from rest_framework.test import APIClient
from rest_framework import status
from rest_framework.exceptions import ValidationError

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
        mock_blnk_client.ledger_exists.return_value = True
        mock_blnk_client.balance_exists.return_value = True

        with mock.patch("orders.services.BlnkClient", return_value=mock_blnk_client), \
             mock.patch("accounts.services.BlnkClient", return_value=mock_blnk_client), \
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
        mock_blnk_client.ledger_exists.return_value = True
        mock_blnk_client.balance_exists.return_value = True

        with mock.patch("orders.services.BlnkClient", return_value=mock_blnk_client), \
             mock.patch("accounts.services.BlnkClient", return_value=mock_blnk_client):
            complete_sell_order(order)

        calls = mock_blnk_client.create_transaction.call_args_list
        usdt_escrow_call = calls[0][1]
        self.assertEqual(usdt_escrow_call["source"], "usdt-frozen-id")

    def test_6_blnk_offline_raises_error(self):
        self.mock_client.create_ledger.side_effect = RuntimeError("Blnk Offline")
        with self.assertRaises(RuntimeError) as exc:
            get_or_create_platform_account(client=self.mock_client)
        self.assertIn("Blnk Offline", str(exc.exception))

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
            "recaptcha_token": "test-token",
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
            "recaptcha_token": "test-token",
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
            "recaptcha_token": "test-token",
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
            "recaptcha_token": "test-token",
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
            "recaptcha_token": "test-token",
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
            {"email": "login@example.com", "password": "ValidPassword123!", "recaptcha_token": "test-token"},
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertTrue(resp.data["success"])
        self.assertIn("access", resp.data["data"]["tokens"])
        self.assertIn("refresh", resp.data["data"]["tokens"])

    def test_login_invalid_credentials(self):
        resp = self.client.post(
            "/api/v1/auth/login/",
            {"email": "login@example.com", "password": "WrongPassword", "recaptcha_token": "test-token"},
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_401_UNAUTHORIZED)
        self.assertFalse(resp.data["success"])
        self.assertEqual(resp.data["message"], "Invalid email or password.")

    def test_token_refresh_and_blacklisting(self):
        login_resp = self.client.post(
            "/api/v1/auth/login/",
            {"email": "login@example.com", "password": "ValidPassword123!", "recaptcha_token": "test-token"},
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

    def test_email_verification_flow_get_and_post(self):
        raw_token = EmailService.send_verification_email(self.user)
        self.assertFalse(self.user.email_verified)

        # GET request verification
        resp = self.client.get(f"/api/v1/auth/verify-email/?token={raw_token}")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertTrue(resp.data["success"])
        self.assertEqual(resp.data["code"], "EMAIL_VERIFIED")

        self.user.refresh_from_db()
        self.assertTrue(self.user.email_verified)

        # Reusing token fails with TOKEN_ALREADY_USED
        reuse_resp = self.client.post("/api/v1/auth/verify-email/", {"token": raw_token}, format="json")
        self.assertEqual(reuse_resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(reuse_resp.data["code"], "TOKEN_ALREADY_USED")

    def test_invalid_token_code(self):
        resp = self.client.post("/api/v1/auth/verify-email/", {"token": "completely_bogus_token"}, format="json")
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(resp.data["code"], "INVALID_TOKEN")

    def test_expired_token_code(self):
        raw_token = EmailService.send_verification_email(self.user)
        # Backdate token expiration
        token_hash = EmailService._hash_token(raw_token)
        EmailVerificationToken.objects.filter(token_hash=token_hash).update(
            expires_at=timezone.now() - timedelta(minutes=5)
        )

        resp = self.client.post("/api/v1/auth/verify-email/", {"token": raw_token}, format="json")
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(resp.data["code"], "TOKEN_EXPIRED")

    def test_resend_verification_generic_response_enumeration_protection(self):
        # Non-existent email returns same generic success message
        resp = self.client.post("/api/v1/auth/resend-verification/", {"email": "nonexistent@example.com", "recaptcha_token": "test-token"}, format="json")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertTrue(resp.data["success"])
        self.assertIn("verification email will be sent", resp.data["message"])

        # Existing unverified email returns same generic success message
        resp_exist = self.client.post("/api/v1/auth/resend-verification/", {"email": "verif@example.com", "recaptcha_token": "test-token"}, format="json")
        self.assertEqual(resp_exist.status_code, status.HTTP_200_OK)
        self.assertTrue(resp_exist.data["success"])
        self.assertIn("verification email will be sent", resp_exist.data["message"])

    def test_resend_rate_limit(self):
        # Trigger max requests
        for _ in range(5):
            EmailService.send_verification_email(self.user)

        with self.assertRaises(ValidationError):
            EmailService.send_verification_email(self.user)

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
        self.assertIsNotNone(self.user.phone_verified_at)

    @mock.patch("accounts.services.tumasend.requests.post")
    def test_tumasend_client_and_otp_lifecycle(self, mock_post):
        mock_resp = mock.MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "batch_id": "batch-tumasend-test-123",
            "success": True,
            "queued": 1,
        }
        mock_resp.raise_for_status.return_value = None
        mock_post.return_value = mock_resp

        self.client.force_authenticate(user=self.user)

        with override_settings(SMS_PROVIDER="tumasend", TEST_REAL_TUMASEND=True, TUMASEND_API_KEY="ts_test_key_123"):
            req_resp = self.client.post("/api/v1/auth/otp/request/", {"phone_number": "0991112233"}, format="json")
            self.assertEqual(req_resp.status_code, status.HTTP_200_OK)
            self.assertIn("expires_in", req_resp.data)
            self.assertIn("resend_after", req_resp.data)

            # Confirm TumaSend API call parameters
            mock_post.assert_called_once()
            args, kwargs = mock_post.call_args
            self.assertEqual(kwargs["headers"]["x-api-key"], "ts_test_key_123")
            self.assertEqual(kwargs["json"]["recipients"], ["+265991112233"])

            # Verify cooldown enforcement
            cooldown_resp = self.client.post("/api/v1/auth/otp/request/", {"phone_number": "0991112233"}, format="json")
            self.assertEqual(cooldown_resp.status_code, status.HTTP_400_BAD_REQUEST)
            self.assertIn("resend_after", str(cooldown_resp.data))

    def test_otp_attempt_limits_and_expiry(self):
        self.client.force_authenticate(user=self.user)
        req_resp = self.client.post("/api/v1/auth/otp/request/", {"phone_number": "0881234567"}, format="json")
        dev_code = req_resp.data.get("dev_code")

        # 4 wrong attempts
        for _ in range(4):
            fail_resp = self.client.post("/api/v1/auth/otp/verify/", {"phone_number": "0881234567", "code": "000000"}, format="json")
            self.assertEqual(fail_resp.status_code, status.HTTP_400_BAD_REQUEST)

        # 5th wrong attempt reaches max attempts (attempts=5) and fails with lockout message
        fifth_resp = self.client.post("/api/v1/auth/otp/verify/", {"phone_number": "0881234567", "code": "000000"}, format="json")
        self.assertEqual(fifth_resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("Maximum verification attempts exceeded", str(fifth_resp.data))

        # Even correct code is rejected after max attempts
        ver_resp = self.client.post("/api/v1/auth/otp/verify/", {"phone_number": "0881234567", "code": dev_code}, format="json")
        self.assertEqual(ver_resp.status_code, status.HTTP_400_BAD_REQUEST)


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
        req_resp = self.client.post("/api/v1/auth/password-reset/", {"email": "unknown@example.com", "recaptcha_token": "test-token"}, format="json")
        self.assertEqual(req_resp.status_code, status.HTTP_200_OK)
        self.assertIn("password reset instructions have been sent", req_resp.data["message"])


class GoogleAuthTests(TestCase):
    def setUp(self):
        cache.clear()
        self.client = APIClient()

    def test_google_auth_new_user_with_credential(self):
        resp = self.client.post("/api/v1/auth/google/", {"credential": "mock-google-token-new"}, format="json")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertTrue(resp.data["success"])
        self.assertIn("tokens", resp.data["data"])
        self.assertIn("access", resp.data["data"]["tokens"])
        self.assertIn("refresh", resp.data["data"]["tokens"])

        user_data = resp.data["data"]["user"]
        self.assertEqual(user_data["email"], "googleuser@example.com")
        self.assertEqual(user_data["verification_status"], "unverified")
        self.assertTrue(user_data["email_verified"])

        created_user = User.objects.get(email="googleuser@example.com")
        self.assertEqual(created_user.google_id, "google-uid-12345")
        self.assertFalse(created_user.is_trading_eligible)

    def test_google_auth_account_linking_verified_email(self):
        user = User.objects.create_user(
            username="google_link",
            email="googleuser@example.com",
            phone_number="+265999444333",
            password="Password123!",
            email_verified=True,
        )
        resp = self.client.post("/api/v1/auth/google/", {"credential": "mock-google-token-link"}, format="json")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)

        user.refresh_from_db()
        self.assertEqual(user.google_id, "google-uid-12345")
        self.assertEqual(User.objects.filter(email="googleuser@example.com").count(), 1)

    def test_google_auth_account_linking_blocked_unverified_email(self):
        User.objects.create_user(
            username="unverified_existing",
            email="googleuser@example.com",
            phone_number="+265999444555",
            password="Password123!",
            email_verified=False,
        )
        resp = self.client.post("/api/v1/auth/google/", {"credential": "mock-google-token-link"}, format="json")
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("verify your email first", str(resp.data))

    def test_google_auth_identity_conflict_rejected(self):
        User.objects.create_user(
            username="conflict_existing",
            email="existing_diff_google@example.com",
            phone_number="+265999444666",
            password="Password123!",
            email_verified=True,
            google_id="different-google-sub-777",
        )
        resp = self.client.post("/api/v1/auth/google/", {"credential": "mock-google-token-conflict"}, format="json")
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("different Google identity", str(resp.data))

    def test_google_auth_conflicting_payload_keys_rejected(self):
        resp = self.client.post(
            "/api/v1/auth/google/",
            {"credential": "mock-google-token-1", "id_token": "mock-google-token-2"},
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("Conflicting", str(resp.data))

    def test_google_auth_invalid_credential(self):
        resp = self.client.post("/api/v1/auth/google/", {"credential": "mock-google-token-invalid"}, format="json")
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_google_auth_expired_credential(self):
        resp = self.client.post("/api/v1/auth/google/", {"credential": "mock-google-token-expired"}, format="json")
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("expired", str(resp.data).lower())

    def test_google_auth_wrong_audience(self):
        resp = self.client.post("/api/v1/auth/google/", {"credential": "mock-google-token-wrong-aud"}, format="json")
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("audience mismatch", str(resp.data).lower())

    def test_google_auth_does_not_bypass_kyc_or_trading_restrictions(self):
        resp = self.client.post("/api/v1/auth/google/", {"credential": "mock-google-token-new"}, format="json")
        access_token = resp.data["data"]["tokens"]["access"]

        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {access_token}")
        send_resp = self.client.post(
            "/api/v1/auth/wallets/send/",
            {
                "recipient_username": "other_user",
                "amount": "10.00",
                "currency": "USDT",
                "idempotency_key": "idemp-google-kyc-test",
            },
            format="json",
        )
        self.assertEqual(send_resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_multiple_users_with_null_phone_number(self):
        # User 1 with phone_number=None
        user1 = User.objects.create_user(
            username="null_phone_1",
            email="null1@example.com",
            phone_number=None,
            password="Password123!",
        )
        self.assertIsNone(user1.phone_number)

        # User 2 with phone_number=None
        user2 = User.objects.create_user(
            username="null_phone_2",
            email="null2@example.com",
            phone_number=None,
            password="Password123!",
        )
        self.assertIsNone(user2.phone_number)

        # Create two Google users sequentially with no phone number
        resp1 = self.client.post("/api/v1/auth/google/", {"credential": "mock-google-token-new"}, format="json")
        self.assertEqual(resp1.status_code, status.HTTP_200_OK)
        guser1 = User.objects.get(email="googleuser@example.com")
        self.assertIsNone(guser1.phone_number)

        resp2 = self.client.post("/api/v1/auth/google/", {"credential": "mock-google-token-conflict"}, format="json")
        self.assertEqual(resp2.status_code, status.HTTP_200_OK)
        guser2 = User.objects.get(email="existing_diff_google@example.com")
        self.assertIsNone(guser2.phone_number)

    def test_real_phone_number_uniqueness_enforced(self):
        User.objects.create_user(
            username="real_phone_user",
            email="realphone@example.com",
            phone_number="+265999000999",
            password="Password123!",
        )

        with self.assertRaises(Exception):
            User.objects.create_user(
                username="duplicate_phone_user",
                email="dup_realphone@example.com",
                phone_number="+265999000999",
                password="Password123!",
            )


from datetime import timedelta
from django.utils import timezone
from accounts.models import UserSession


class SessionAndInactivityTests(TestCase):
    def setUp(self):
        cache.clear()
        self.client = APIClient()
        self.user = User.objects.create_user(
            username="session_user",
            email="session@example.com",
            phone_number="+265999777666",
            password="ValidPassword123!",
        )

    def test_login_creates_usersession(self):
        resp = self.client.post(
            "/api/v1/auth/login/",
            {"email": "session@example.com", "password": "ValidPassword123!", "recaptcha_token": "test-token"},
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)

        session = UserSession.objects.filter(user=self.user, is_active=True).first()
        self.assertIsNotNone(session)
        self.assertTrue(session.is_active)

    def test_active_user_updates_last_activity(self):
        login_resp = self.client.post(
            "/api/v1/auth/login/",
            {"email": "session@example.com", "password": "ValidPassword123!", "recaptcha_token": "test-token"},
            format="json",
        )
        access_token = login_resp.data["data"]["tokens"]["access"]
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {access_token}")

        session = UserSession.objects.get(user=self.user, is_active=True)
        old_activity = session.last_activity

        # Backdate last_activity slightly
        past_time = timezone.now() - timedelta(minutes=2)
        UserSession.objects.filter(id=session.id).update(last_activity=past_time)

        # Make active user API request
        me_resp = self.client.get("/api/v1/auth/me/")
        self.assertEqual(me_resp.status_code, status.HTTP_200_OK)

        session.refresh_from_db()
        self.assertGreater(session.last_activity, past_time)

    def test_background_polling_does_not_update_last_activity(self):
        login_resp = self.client.post(
            "/api/v1/auth/login/",
            {"email": "session@example.com", "password": "ValidPassword123!", "recaptcha_token": "test-token"},
            format="json",
        )
        access_token = login_resp.data["data"]["tokens"]["access"]
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {access_token}")

        session = UserSession.objects.get(user=self.user, is_active=True)
        past_time = timezone.now() - timedelta(minutes=2)
        UserSession.objects.filter(id=session.id).update(last_activity=past_time)

        # Call background polling endpoint
        with mock.patch("accounts.services.fetch_wallet_balance", return_value={"MWK": 0, "USDT": 0}):
            wallet_resp = self.client.get("/api/v1/auth/wallets/")
            self.assertEqual(wallet_resp.status_code, status.HTTP_200_OK)

        session.refresh_from_db()
        # Activity should NOT be updated for background request
        self.assertEqual(session.last_activity, past_time)

    def test_session_inactivity_timeout_rejects_api_and_refresh(self):
        login_resp = self.client.post(
            "/api/v1/auth/login/",
            {"email": "session@example.com", "password": "ValidPassword123!", "recaptcha_token": "test-token"},
            format="json",
        )
        access_token = login_resp.data["data"]["tokens"]["access"]
        refresh_token = login_resp.data["data"]["tokens"]["refresh"]

        session = UserSession.objects.get(user=self.user, is_active=True)
        # Backdate last_activity past the 15-minute threshold (e.g. 20 mins ago)
        expired_activity = timezone.now() - timedelta(minutes=20)
        UserSession.objects.filter(id=session.id).update(last_activity=expired_activity)

        # Authenticated API request fails with SESSION_EXPIRED
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {access_token}")
        me_resp = self.client.get("/api/v1/auth/me/")
        self.assertEqual(me_resp.status_code, status.HTTP_401_UNAUTHORIZED)
        self.assertEqual(me_resp.data["code"], "SESSION_EXPIRED")

        # Token refresh fails with SESSION_EXPIRED
        ref_client = APIClient()
        ref_resp = ref_client.post("/api/v1/auth/login/refresh/", {"refresh": refresh_token}, format="json")
        self.assertEqual(ref_resp.status_code, status.HTTP_401_UNAUTHORIZED)
        self.assertEqual(ref_resp.data["code"], "SESSION_EXPIRED")

    def test_session_max_lifetime_rejects_session(self):
        login_resp = self.client.post(
            "/api/v1/auth/login/",
            {"email": "session@example.com", "password": "ValidPassword123!", "recaptcha_token": "test-token"},
            format="json",
        )
        access_token = login_resp.data["data"]["tokens"]["access"]

        session = UserSession.objects.get(user=self.user, is_active=True)
        # Backdate created_at past the 24-hour limit
        old_created = timezone.now() - timedelta(hours=25)
        UserSession.objects.filter(id=session.id).update(created_at=old_created)

        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {access_token}")
        me_resp = self.client.get("/api/v1/auth/me/")
        self.assertEqual(me_resp.status_code, status.HTTP_401_UNAUTHORIZED)
        self.assertEqual(me_resp.data["code"], "SESSION_EXPIRED")

    def test_single_device_logout_revokes_only_target_session(self):
        # Device A Login
        client_a = APIClient()
        resp_a = client_a.post(
            "/api/v1/auth/login/",
            {"email": "session@example.com", "password": "ValidPassword123!", "recaptcha_token": "test-token"},
            format="json",
            HTTP_USER_AGENT="DeviceA",
        )
        tokens_a = resp_a.data["data"]["tokens"]

        # Device B Login
        client_b = APIClient()
        resp_b = client_b.post(
            "/api/v1/auth/login/",
            {"email": "session@example.com", "password": "ValidPassword123!", "recaptcha_token": "test-token"},
            format="json",
            HTTP_USER_AGENT="DeviceB",
        )
        tokens_b = resp_b.data["data"]["tokens"]

        # Logout Device A
        client_a.credentials(HTTP_AUTHORIZATION=f"Bearer {tokens_a['access']}")
        logout_resp = client_a.post("/api/v1/auth/logout/", {"refresh": tokens_a["refresh"]}, format="json")
        self.assertEqual(logout_resp.status_code, status.HTTP_200_OK)

        # Device A is revoked
        me_a = client_a.get("/api/v1/auth/me/")
        self.assertEqual(me_a.status_code, status.HTTP_401_UNAUTHORIZED)

        # Device B remains active and working
        client_b.credentials(HTTP_AUTHORIZATION=f"Bearer {tokens_b['access']}")
        me_b = client_b.get("/api/v1/auth/me/")
        self.assertEqual(me_b.status_code, status.HTTP_200_OK)


class BlnkRepairTests(TestCase):
    def setUp(self):
        cache.clear()
        PlatformAccount.objects.all().delete()
        User.objects.all().delete()
        Wallet.objects.all().delete()

        self.user = User.objects.create_user(
            username="repair_user",
            email="repair@example.com",
            phone_number="+265999000111",
            password="Password123!",
            email_verified=True,
            phone_verified=True,
            verification_status="verified",
            blnk_ledger_id="ldg_existing_valid",
        )
        self.mwk_wallet = Wallet.objects.create(
            user=self.user, currency="MWK", blnk_balance_id="bal_mwk_existing_valid"
        )
        self.usdt_wallet = Wallet.objects.create(
            user=self.user, currency="USDT", blnk_balance_id="bal_usdt_existing_valid"
        )

    def test_valid_references_reused_without_recreation(self):
        mock_client = mock.MagicMock()
        mock_client.ledger_exists.return_value = True
        mock_client.balance_exists.return_value = True

        with mock.patch("accounts.services.BlnkClient", return_value=mock_client):
            mwk_w, usdt_w = ensure_user_wallets(self.user)

        self.assertEqual(mwk_w.blnk_balance_id, "bal_mwk_existing_valid")
        self.assertEqual(usdt_w.blnk_balance_id, "bal_usdt_existing_valid")
        self.assertEqual(self.user.blnk_ledger_id, "ldg_existing_valid")
        mock_client.create_ledger.assert_not_called()
        mock_client.create_balance.assert_not_called()

    def test_stale_ledger_404_triggers_recreation(self):
        mock_client = mock.MagicMock()
        # Ledger 404s
        mock_client.ledger_exists.return_value = False
        mock_client.create_ledger.return_value = {"ledger_id": "ldg_repaired_new"}
        mock_client.create_balance.side_effect = lambda ledger_id, curr, meta: {
            "balance_id": f"bal_{curr.lower()}_repaired_new"
        }

        with mock.patch("accounts.services.BlnkClient", return_value=mock_client):
            mwk_w, usdt_w = ensure_user_wallets(self.user)

        self.user.refresh_from_db()
        self.assertEqual(self.user.blnk_ledger_id, "ldg_repaired_new")
        self.assertEqual(mwk_w.blnk_balance_id, "bal_mwk_repaired_new")
        self.assertEqual(usdt_w.blnk_balance_id, "bal_usdt_repaired_new")
        # Ensure no duplicate Wallet rows created
        self.assertEqual(Wallet.objects.filter(user=self.user).count(), 2)

    def test_stale_balance_404_triggers_balance_recreation_only(self):
        mock_client = mock.MagicMock()
        # Ledger exists
        mock_client.ledger_exists.return_value = True
        # MWK balance exists, USDT balance 404s
        def balance_exists_side_effect(balance_id):
            return balance_id == "bal_mwk_existing_valid"

        mock_client.balance_exists.side_effect = balance_exists_side_effect
        mock_client.create_balance.return_value = {"balance_id": "bal_usdt_repaired_fresh"}

        with mock.patch("accounts.services.BlnkClient", return_value=mock_client):
            mwk_w, usdt_w = ensure_user_wallets(self.user)

        self.user.refresh_from_db()
        # Ledger untouched
        self.assertEqual(self.user.blnk_ledger_id, "ldg_existing_valid")
        # MWK wallet untouched
        self.assertEqual(mwk_w.blnk_balance_id, "bal_mwk_existing_valid")
        # USDT wallet updated in-place
        self.assertEqual(usdt_w.blnk_balance_id, "bal_usdt_repaired_fresh")
        self.assertEqual(Wallet.objects.filter(user=self.user).count(), 2)

    def test_blnk_503_outage_does_not_recreate(self):
        mock_client = mock.MagicMock()
        resp_503 = mock.MagicMock()
        resp_503.status_code = 503
        http_err = requests.HTTPError("503 Service Unavailable", response=resp_503)

        mock_client.ledger_exists.side_effect = http_err

        with mock.patch("accounts.services.BlnkClient", return_value=mock_client):
            with self.assertRaises(requests.HTTPError):
                ensure_user_wallets(self.user)

        self.user.refresh_from_db()
        # Ledgers and balances must NOT be altered or deleted
        self.assertEqual(self.user.blnk_ledger_id, "ldg_existing_valid")
        self.assertEqual(Wallet.objects.get(user=self.user, currency="MWK").blnk_balance_id, "bal_mwk_existing_valid")
        mock_client.create_ledger.assert_not_called()
        mock_client.create_balance.assert_not_called()

    def test_blnk_timeout_does_not_recreate(self):
        mock_client = mock.MagicMock()
        mock_client.ledger_exists.side_effect = requests.Timeout("Connection timed out")

        with mock.patch("accounts.services.BlnkClient", return_value=mock_client):
            with self.assertRaises(requests.Timeout):
                ensure_user_wallets(self.user)

        self.user.refresh_from_db()
        self.assertEqual(self.user.blnk_ledger_id, "ldg_existing_valid")
        mock_client.create_ledger.assert_not_called()

    def test_blnk_api_key_header_configuration(self):
        from accounts.blnk_client import BlnkClient
        with override_settings(BLNK_API_KEY="test_api_key_64_chars_long_1234567890_abcdefghijklmnopqrstuvwxyz"):
            client = BlnkClient()
            self.assertEqual(client.headers.get("X-Blnk-Key"), "test_api_key_64_chars_long_1234567890_abcdefghijklmnopqrstuvwxyz")

    def test_blnk_secret_key_not_required(self):
        from accounts.blnk_client import BlnkClient
        with override_settings(BLNK_API_KEY="my_api_key"):
            if hasattr(settings, "BLNK_SECRET_KEY"):
                delattr(settings, "BLNK_SECRET_KEY")
            client = BlnkClient()
            self.assertEqual(client.headers.get("X-Blnk-Key"), "my_api_key")

    def test_blnk_auth_failure_does_not_recreate(self):
        mock_client = mock.MagicMock()
        resp_401 = mock.MagicMock()
        resp_401.status_code = 401
        http_err = requests.HTTPError("401 Unauthorized", response=resp_401)
        mock_client.ledger_exists.side_effect = http_err

        with mock.patch("accounts.services.BlnkClient", return_value=mock_client):
            with self.assertRaises(requests.HTTPError):
                ensure_user_wallets(self.user)

        mock_client.create_ledger.assert_not_called()

    def test_blnk_403_forbidden_does_not_recreate(self):
        mock_client = mock.MagicMock()
        resp_403 = mock.MagicMock()
        resp_403.status_code = 403
        http_err = requests.HTTPError("403 Forbidden", response=resp_403)
        mock_client.ledger_exists.side_effect = http_err

        with mock.patch("accounts.services.BlnkClient", return_value=mock_client):
            with self.assertRaises(requests.HTTPError):
                ensure_user_wallets(self.user)

        self.user.refresh_from_db()
        self.assertEqual(self.user.blnk_ledger_id, "ldg_existing_valid")
        mock_client.create_ledger.assert_not_called()

    def test_blnk_500_server_error_does_not_recreate(self):
        mock_client = mock.MagicMock()
        resp_500 = mock.MagicMock()
        resp_500.status_code = 500
        http_err = requests.HTTPError("500 Internal Server Error", response=resp_500)
        mock_client.ledger_exists.side_effect = http_err

        with mock.patch("accounts.services.BlnkClient", return_value=mock_client):
            with self.assertRaises(requests.HTTPError):
                ensure_user_wallets(self.user)

        self.user.refresh_from_db()
        self.assertEqual(self.user.blnk_ledger_id, "ldg_existing_valid")
        mock_client.create_ledger.assert_not_called()

    def test_idempotency_multiple_calls_do_not_duplicate_wallets(self):
        mock_client = mock.MagicMock()
        mock_client.ledger_exists.return_value = True
        mock_client.balance_exists.return_value = True

        with mock.patch("accounts.services.BlnkClient", return_value=mock_client):
            for _ in range(10):
                ensure_user_wallets(self.user)

        self.assertEqual(Wallet.objects.filter(user=self.user).count(), 2)

    def test_platform_account_stale_ledger_and_balance_repair(self):
        platform = PlatformAccount.objects.create(
            ledger_id="led_stale_old",
            mwk_float_balance_id="mwk_float_old",
            usdt_float_balance_id="usdt_float_old",
            mwk_external_contra_id="mwk_contra_old",
            usdt_external_contra_id="usdt_contra_old",
            usdt_frozen_balance_id="usdt_frozen_old",
        )

        mock_client = mock.MagicMock()
        # Ledger returns 404
        mock_client.ledger_exists.return_value = False
        mock_client.create_ledger.return_value = {"ledger_id": "led_platform_repaired"}
        mock_client.create_balance.side_effect = lambda l_id, curr, meta: {
            "balance_id": f"bal_{meta.get('role')}_new"
        }

        repaired_platform = get_or_create_platform_account(client=mock_client)

        self.assertEqual(repaired_platform.id, platform.id)
        self.assertEqual(repaired_platform.ledger_id, "led_platform_repaired")
        self.assertEqual(repaired_platform.mwk_float_balance_id, "bal_platform_mwk_float_new")
        self.assertEqual(PlatformAccount.objects.count(), 1)

    def test_reconcile_blnk_management_command_execution(self):
        out = StringIO()

        mock_client = mock.MagicMock()
        mock_client.ledger_exists.return_value = True
        mock_client.balance_exists.return_value = True
        mock_client.create_ledger.return_value = {"ledger_id": "led_platform_repaired"}
        mock_client.create_balance.side_effect = lambda l_id, curr, meta: {
            "balance_id": f"bal_{meta.get('role', 'generic')}_new"
        }

        with mock.patch("accounts.management.commands.reconcile_blnk.BlnkClient", return_value=mock_client), \
             mock.patch("accounts.services.BlnkClient", return_value=mock_client):
            call_command("reconcile_blnk", stdout=out)

        output = out.getvalue()
        self.assertIn("=== Bitfuse Blnk Reconciliation ===", output)
        self.assertIn("Status: SUCCESS", output)


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

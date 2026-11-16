from unittest import mock
import requests
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.test import TestCase, override_settings
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient
from rest_framework import status

from accounts.recaptcha import verify_recaptcha, GOOGLE_SITEVERIFY_URL
from orders.models import Order
from withdrawals.models import Withdrawal

User = get_user_model()


@override_settings(RECAPTCHA_ENABLED=True)
class ReCaptchaServiceUnitTests(TestCase):
    """Unit tests for central verify_recaptcha service function."""

    @mock.patch("requests.post")
    def test_successful_verification(self, mock_post):
        mock_response = mock.MagicMock()
        mock_response.json.return_value = {
            "success": True,
            "score": 0.9,
            "action": "login",
            "hostname": "bitfuse.mw",
        }
        mock_response.raise_for_status.return_value = None
        mock_post.return_value = mock_response

        res = verify_recaptcha(token="valid-token", expected_action="login")
        self.assertTrue(res)
        mock_post.assert_called_once()

    def test_missing_token_raises_validation_error(self):
        with self.assertRaises(Exception) as ctx:
            verify_recaptcha(token="", expected_action="login")
        self.assertIn("detail", ctx.exception.detail)
        self.assertEqual(str(ctx.exception.detail["detail"]), "reCAPTCHA verification is required.")

    @mock.patch("requests.post")
    def test_google_siteverify_success_false(self, mock_post):
        mock_response = mock.MagicMock()
        mock_response.json.return_value = {
            "success": False,
            "error-codes": ["invalid-input-response"],
        }
        mock_response.raise_for_status.return_value = None
        mock_post.return_value = mock_response

        with self.assertRaises(Exception) as ctx:
            verify_recaptcha(token="bad-token", expected_action="login")
        self.assertEqual(str(ctx.exception.detail["detail"]), "We couldn't verify this request. Please try again.")

    @mock.patch("requests.post")
    def test_action_mismatch(self, mock_post):
        mock_response = mock.MagicMock()
        mock_response.json.return_value = {
            "success": True,
            "score": 0.9,
            "action": "register",
            "hostname": "bitfuse.mw",
        }
        mock_response.raise_for_status.return_value = None
        mock_post.return_value = mock_response

        with self.assertRaises(Exception) as ctx:
            verify_recaptcha(token="token", expected_action="login")
        self.assertEqual(str(ctx.exception.detail["detail"]), "We couldn't verify this request. Please try again.")

    @mock.patch("requests.post")
    def test_hostname_mismatch(self, mock_post):
        mock_response = mock.MagicMock()
        mock_response.json.return_value = {
            "success": True,
            "score": 0.9,
            "action": "login",
            "hostname": "malicious-site.com",
        }
        mock_response.raise_for_status.return_value = None
        mock_post.return_value = mock_response

        with self.assertRaises(Exception) as ctx:
            verify_recaptcha(token="token", expected_action="login")
        self.assertEqual(str(ctx.exception.detail["detail"]), "We couldn't verify this request. Please try again.")

    @mock.patch("requests.post")
    def test_low_score_general(self, mock_post):
        mock_response = mock.MagicMock()
        mock_response.json.return_value = {
            "success": True,
            "score": 0.3,
            "action": "login",
            "hostname": "bitfuse.mw",
        }
        mock_response.raise_for_status.return_value = None
        mock_post.return_value = mock_response

        with self.assertRaises(Exception) as ctx:
            verify_recaptcha(token="token", expected_action="login")
        self.assertEqual(str(ctx.exception.detail["detail"]), "We couldn't verify this request. Please try again.")

    @mock.patch("requests.post")
    def test_low_score_financial_threshold(self, mock_post):
        # General threshold 0.5 passes (0.6 > 0.5), but financial threshold 0.7 fails (0.6 < 0.7)
        mock_response = mock.MagicMock()
        mock_response.json.return_value = {
            "success": True,
            "score": 0.6,
            "action": "buy",
            "hostname": "bitfuse.mw",
        }
        mock_response.raise_for_status.return_value = None
        mock_post.return_value = mock_response

        with self.assertRaises(Exception) as ctx:
            verify_recaptcha(token="token", expected_action="buy", is_financial=True)
        self.assertEqual(str(ctx.exception.detail["detail"]), "We couldn't verify this request. Please try again.")

    @mock.patch("requests.post")
    def test_google_service_timeout_fails_closed(self, mock_post):
        mock_post.side_effect = requests.Timeout("Google connection timed out")

        with self.assertRaises(Exception) as ctx:
            verify_recaptcha(token="token", expected_action="login")
        self.assertEqual(str(ctx.exception.detail["detail"]), "We couldn't verify this request. Please try again.")

    @override_settings(DEBUG=False, TESTING=False, RECAPTCHA_ENABLED=False)
    def test_disabled_recaptcha_bypasses_verification(self):
        res = verify_recaptcha(token="", expected_action="login")
        self.assertTrue(res)


@override_settings(RECAPTCHA_ENABLED=True)
class ProtectedEndpointsIntegrationTests(TestCase):
    """Integration tests verifying all 8 protected endpoints require and validate reCAPTCHA."""

    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(
            username="testrecaptchauser",
            email="recaptcha@example.com",
            password="SecurePassword123!",
            email_verified=True,
            verification_status="verified",
        )

    def _mock_google_success(self, action="login", score=0.9, hostname="bitfuse.mw"):
        mock_resp = mock.MagicMock()
        mock_resp.json.return_value = {
            "success": True,
            "score": score,
            "action": action,
            "hostname": hostname,
        }
        mock_resp.raise_for_status.return_value = None
        return mock_resp

    @mock.patch("requests.post")
    def test_registration_endpoint_recaptcha(self, mock_post):
        url = "/api/v1/auth/register/"
        payload = {
            "first_name": "Test",
            "last_name": "User",
            "email": "newuser@example.com",
            "phone_number": "+265991112233",
            "password": "Password123!",
            "password_confirmation": "Password123!",
        }

        # 1. Missing token -> 400
        resp = self.client.post(url, payload, format="json")
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(resp.json()["detail"], "reCAPTCHA verification is required.")

        # 2. Invalid token -> 400
        mock_post.return_value = self._mock_google_success(action="wrong_action")
        payload["recaptcha_token"] = "invalid-token"
        resp = self.client.post(url, payload, format="json")
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(resp.json()["detail"], "We couldn't verify this request. Please try again.")

        # 3. Valid token -> 201
        mock_post.return_value = self._mock_google_success(action="register")
        resp = self.client.post(url, payload, format="json")
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)

    @mock.patch("requests.post")
    def test_login_endpoint_recaptcha(self, mock_post):
        url = "/api/v1/auth/login/"
        payload = {"email": "recaptcha@example.com", "password": "SecurePassword123!"}

        # Missing token -> 400
        resp = self.client.post(url, payload, format="json")
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(resp.json()["detail"], "reCAPTCHA verification is required.")

        # Valid token -> 200
        mock_post.return_value = self._mock_google_success(action="login")
        payload["recaptcha_token"] = "valid-login-token"
        resp = self.client.post(url, payload, format="json")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)

    @mock.patch("requests.post")
    def test_password_reset_endpoint_recaptcha(self, mock_post):
        url = "/api/v1/auth/password-reset/"
        payload = {"email": "recaptcha@example.com"}

        # Missing token -> 400
        resp = self.client.post(url, payload, format="json")
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

        # Valid token -> 200
        mock_post.return_value = self._mock_google_success(action="password_reset")
        payload["recaptcha_token"] = "reset-token"
        resp = self.client.post(url, payload, format="json")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)

    @mock.patch("requests.post")
    def test_resend_verification_endpoint_recaptcha(self, mock_post):
        url = "/api/v1/auth/resend-verification/"
        payload = {"email": "recaptcha@example.com"}

        # Missing token -> 400
        resp = self.client.post(url, payload, format="json")
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

        # Valid token -> 200
        mock_post.return_value = self._mock_google_success(action="resend_verification")
        payload["recaptcha_token"] = "resend-token"
        resp = self.client.post(url, payload, format="json")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)

    @mock.patch("requests.post")
    def test_kyc_submission_endpoint_recaptcha(self, mock_post):
        self.client.force_authenticate(user=self.user)
        url = "/api/v1/kyc/submit/"

        # Missing token in multipart data -> 400
        resp = self.client.post(url, {}, format="multipart")
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(resp.json()["detail"], "reCAPTCHA verification is required.")

        # Action mismatch -> 400
        mock_post.return_value = self._mock_google_success(action="wrong_action")
        resp = self.client.post(url, {"recaptcha_token": "kyc-token"}, format="multipart")
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    @mock.patch("requests.post")
    def test_financial_endpoints_fail_closed_and_no_side_effects(self, mock_post):
        self.client.force_authenticate(user=self.user)

        # 1. Buy order failure -> NO order created
        mock_post.return_value = self._mock_google_success(action="buy", score=0.4)  # Score too low for financial (0.4 < 0.7)
        buy_resp = self.client.post(
            "/api/v1/orders/buy/",
            {"amount_mwk": 10000, "payment_method": "AIRTEL_MONEY", "phone": "0991112233", "recaptcha_token": "buy-token"},
            format="json",
        )
        self.assertEqual(buy_resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(Order.objects.filter(user=self.user, order_type="BUY").count(), 0)

        # 2. Sell order failure -> NO order created
        mock_post.return_value = self._mock_google_success(action="buy")  # Action mismatch ('buy' vs expected 'sell')
        sell_resp = self.client.post(
            "/api/v1/orders/sell/",
            {"amount_usdt": 10, "payment_method": "AIRTEL_MONEY", "phone": "0991112233", "recaptcha_token": "sell-token"},
            format="json",
        )
        self.assertEqual(sell_resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(Order.objects.filter(user=self.user, order_type="SELL").count(), 0)

        # 3. Withdrawal failure -> NO withdrawal created
        mock_post.side_effect = requests.Timeout("Network timeout")
        withdraw_resp = self.client.post(
            "/api/v1/withdrawals/",
            {
                "asset": "USDT",
                "network": "TRON",
                "amount": 10,
                "destination_address": "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t",
                "recaptcha_token": "withdraw-token",
            },
            format="json",
        )
        self.assertEqual(withdraw_resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(Withdrawal.objects.filter(user=self.user).count(), 0)

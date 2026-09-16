from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import generics, permissions, status, serializers
from rest_framework.exceptions import ValidationError
from Bitfuse.throttling import ConfigurableScopedRateThrottle
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.tokens import RefreshToken, TokenError

from .models import Notification, Transaction, Wallet
from .serializers import (
    NotificationSerializer,
    OrderHistorySerializer,
    RegisterSerializer,
    TransactionSerializer,
    TransferCreateSerializer,
    TransferSerializer,
    UserSerializer,
)
from .auth_services import (
    CaptchaService,
    EmailService,
    GoogleAuthService,
    OTPService,
    normalize_phone_number,
)
from .services import perform_p2p_transfer
from orders.models import Order

User = get_user_model()


class RegisterView(generics.CreateAPIView):
    queryset = User.objects.all()
    serializer_class = RegisterSerializer
    permission_classes = [permissions.AllowAny]
    throttle_classes = [ConfigurableScopedRateThrottle]
    throttle_scope = "auth"

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        captcha_token = serializer.validated_data.get("captcha_token", "")
        CaptchaService.verify_captcha(captcha_token)

        user = serializer.save()

        # Send verification email asynchronously / inline
        try:
            EmailService.send_verification_email(user)
        except Exception as exc:
            import logging
            logging.getLogger(__name__).warning("Failed to send verification email on register: %s", exc)

        refresh = RefreshToken.for_user(user)

        return Response(
            {
                "success": True,
                "message": "Account created successfully. Please check your email to verify your account.",
                "data": {
                    "user": UserSerializer(user).data,
                    "tokens": {
                        "refresh": str(refresh),
                        "access": str(refresh.access_token),
                    },
                },
            },
            status=status.HTTP_201_CREATED,
        )


class LoginView(APIView):
    permission_classes = [permissions.AllowAny]
    throttle_classes = [ConfigurableScopedRateThrottle]
    throttle_scope = "auth"

    def post(self, request):
        email_or_username = request.data.get("email") or request.data.get("username")
        password = request.data.get("password")

        if not email_or_username or not password:
            raise ValidationError(
                {
                    "email": ["Email or username is required."] if not email_or_username else [],
                    "password": ["Password is required."] if not password else [],
                }
            )

        # Look up user by email or username
        email_clean = str(email_or_username).strip().lower()
        user = (
            User.objects.filter(email__iexact=email_clean).first()
            or User.objects.filter(username__iexact=email_clean).first()
        )

        if not user or not user.check_password(password):
            return Response(
                {"success": False, "message": "Invalid email or password."},
                status=status.HTTP_401_UNAUTHORIZED,
            )

        if not user.is_active:
            return Response(
                {"success": False, "message": "Account is disabled. Please contact support."},
                status=status.HTTP_401_UNAUTHORIZED,
            )

        refresh = RefreshToken.for_user(user)

        return Response(
            {
                "success": True,
                "message": "Login successful.",
                "data": {
                    "user": UserSerializer(user).data,
                    "tokens": {
                        "refresh": str(refresh),
                        "access": str(refresh.access_token),
                    },
                },
            },
            status=status.HTTP_200_OK,
        )


class LogoutView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        refresh_token = request.data.get("refresh")
        if not refresh_token:
            raise ValidationError({"refresh": ["Refresh token is required."]})

        try:
            token = RefreshToken(refresh_token)
            token.blacklist()
        except TokenError as exc:
            raise ValidationError({"refresh": [str(exc)]})

        return Response(
            {"success": True, "message": "Successfully logged out."},
            status=status.HTTP_200_OK,
        )


class VerifyEmailView(APIView):
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        token = request.data.get("token")
        if not token:
            raise ValidationError({"token": ["Verification token is required."]})

        user = EmailService.verify_email_token(token)

        return Response(
            {
                "success": True,
                "message": "Email address verified successfully.",
                "data": {"user": UserSerializer(user).data},
            },
            status=status.HTTP_200_OK,
        )


class ResendEmailVerificationView(APIView):
    permission_classes = [permissions.AllowAny]
    throttle_classes = [ConfigurableScopedRateThrottle]
    throttle_scope = "auth"

    def post(self, request):
        email = request.data.get("email")
        user = None

        if request.user and request.user.is_authenticated:
            user = request.user
        elif email:
            user = User.objects.filter(email__iexact=email.strip()).first()

        if user:
            if user.email_verified:
                return Response(
                    {"success": True, "message": "Your email address is already verified."},
                    status=status.HTTP_200_OK,
                )
            EmailService.send_verification_email(user)

        # Standard generic response to prevent email enumeration
        return Response(
            {
                "success": True,
                "message": "If an account exists for that email, a verification message has been sent.",
            },
            status=status.HTTP_200_OK,
        )


class RequestOTPView(APIView):
    permission_classes = [permissions.AllowAny]
    throttle_classes = [ConfigurableScopedRateThrottle]
    throttle_scope = "auth"

    def post(self, request):
        phone_number = request.data.get("phone_number")
        if not phone_number:
            raise ValidationError({"phone_number": ["Phone number is required."]})

        user = request.user if request.user.is_authenticated else None
        code = OTPService.generate_otp(phone_number=phone_number, user=user)

        resp_data = {
            "success": True,
            "message": "Verification code sent via SMS.",
        }
        # In testing / dev, surface test code in debug context
        from django.conf import settings
        if getattr(settings, "TESTING", False) or settings.DEBUG:
            resp_data["dev_code"] = code

        return Response(resp_data, status=status.HTTP_200_OK)


class VerifyOTPView(APIView):
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        phone_number = request.data.get("phone_number")
        code = request.data.get("code")

        if not phone_number or not code:
            raise ValidationError(
                {
                    "phone_number": ["Phone number is required."] if not phone_number else [],
                    "code": ["Verification code is required."] if not code else [],
                }
            )

        user = request.user if request.user.is_authenticated else None
        OTPService.verify_otp(phone_number=phone_number, code=code, user=user)

        return Response(
            {"success": True, "message": "Phone number verified successfully."},
            status=status.HTTP_200_OK,
        )


class PasswordResetRequestView(APIView):
    permission_classes = [permissions.AllowAny]
    throttle_classes = [ConfigurableScopedRateThrottle]
    throttle_scope = "auth"

    def post(self, request):
        email = request.data.get("email")
        captcha_token = request.data.get("captcha_token", "")

        if not email:
            raise ValidationError({"email": ["Email address is required."]})

        CaptchaService.verify_captcha(captcha_token)
        EmailService.send_password_reset_email(email)

        return Response(
            {
                "success": True,
                "message": "If an account exists for that email address, password reset instructions have been sent.",
            },
            status=status.HTTP_200_OK,
        )


class PasswordResetConfirmView(APIView):
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        token = request.data.get("token")
        new_password = request.data.get("new_password")
        password_confirmation = request.data.get("password_confirmation")

        if not token or not new_password or not password_confirmation:
            raise ValidationError(
                {
                    "token": ["Reset token is required."] if not token else [],
                    "new_password": ["New password is required."] if not new_password else [],
                    "password_confirmation": ["Password confirmation is required."] if not password_confirmation else [],
                }
            )

        if new_password != password_confirmation:
            raise ValidationError({"password_confirmation": ["Passwords do not match."]})

        try:
            validate_password(new_password)
        except DjangoValidationError as exc:
            raise ValidationError({"new_password": list(exc.messages)})

        user = EmailService.confirm_password_reset(token, new_password)

        return Response(
            {
                "success": True,
                "message": "Password reset successfully. You may now log in with your new password.",
                "data": {"user": UserSerializer(user).data},
            },
            status=status.HTTP_200_OK,
        )


class GoogleAuthView(APIView):
    permission_classes = [permissions.AllowAny]
    throttle_classes = [ConfigurableScopedRateThrottle]
    throttle_scope = "auth"

    def post(self, request):
        id_token_str = request.data.get("id_token")
        if not id_token_str:
            raise ValidationError({"id_token": ["Google ID token is required."]})

        google_payload = GoogleAuthService.verify_google_id_token(id_token_str)
        google_sub = google_payload.get("sub")
        google_email = google_payload.get("email", "").lower().strip()
        google_email_verified = google_payload.get("email_verified", False)
        given_name = google_payload.get("given_name", "Google")
        family_name = google_payload.get("family_name", "User")

        if not google_email:
            raise ValidationError({"id_token": ["Google token does not contain a valid email."]})

        # 1. Search existing user by google_id
        user = User.objects.filter(google_id=google_sub).first()

        if not user:
            # 2. Search existing user by email
            existing_user = User.objects.filter(email__iexact=google_email).first()
            if existing_user:
                if existing_user.email_verified and google_email_verified:
                    existing_user.google_id = google_sub
                    existing_user.save(update_fields=["google_id"])
                    user = existing_user
                else:
                    raise ValidationError(
                        {
                            "email": [
                                "An account with this email exists. Please verify your email first before linking Google Sign-In."
                            ]
                        }
                    )
            else:
                # 3. Create new user
                import uuid, re
                base_username = re.sub(r"[^\w]", "_", google_email.split("@")[0]).lower()[:20] or "google_user"
                username = base_username
                while User.objects.filter(username=username).exists():
                    username = f"{base_username}_{uuid.uuid4().hex[:6]}"

                user = User.objects.create_user(
                    username=username,
                    email=google_email,
                    first_name=given_name,
                    last_name=family_name,
                    google_id=google_sub,
                    email_verified=google_email_verified,
                    phone_verified=False,
                    verification_status="unverified",
                )

        refresh = RefreshToken.for_user(user)

        return Response(
            {
                "success": True,
                "message": "Google authentication successful.",
                "data": {
                    "user": UserSerializer(user).data,
                    "tokens": {
                        "refresh": str(refresh),
                        "access": str(refresh.access_token),
                    },
                },
            },
            status=status.HTTP_200_OK,
        )


class MeView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        return Response(
            {
                "success": True,
                "data": UserSerializer(request.user).data,
            }
        )


class WalletBalanceView(APIView):
    """GET /api/v1/auth/wallets/ - returns real MWK and USDT balances."""
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        from accounts.services import fetch_wallet_balance

        try:
            balances = fetch_wallet_balance(request.user)
            response_data = {
                "mwk": float(balances["MWK"]),
                "usdt": float(balances["USDT"]),
            }
            return Response(response_data, status=status.HTTP_200_OK)
        except Exception as exc:
            return Response(
                {
                    "success": False,
                    "code": "BALANCE_SERVICE_UNAVAILABLE",
                    "message": "Wallet balance is temporarily unavailable. Please try again shortly.",
                    "error": str(exc),
                },
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )


class AdminReconciliationView(APIView):
    permission_classes = [permissions.IsAdminUser]

    def get(self, request):
        from decimal import Decimal
        from accounts.models import PlatformAccount, User, Wallet
        from accounts.services import fetch_wallet_balance
        from orders.models import Order
        from withdrawals.models import Withdrawal

        users = User.objects.all()
        discrepancies = []
        users_checked = 0

        for u in users:
            users_checked += 1
            try:
                bals = fetch_wallet_balance(u)
                usdt_bal = bals.get("USDT", Decimal("0"))
                mwk_bal = bals.get("MWK", Decimal("0"))
                if usdt_bal < Decimal("0"):
                    discrepancies.append({
                        "user_id": str(u.id),
                        "username": u.username,
                        "issue": "Negative USDT balance detected",
                        "balance": str(usdt_bal),
                    })
                if mwk_bal < Decimal("0"):
                    discrepancies.append({
                        "user_id": str(u.id),
                        "username": u.username,
                        "issue": "Negative MWK balance detected",
                        "balance": str(mwk_bal),
                    })
            except Exception as exc:
                discrepancies.append({
                    "user_id": str(u.id),
                    "username": u.username,
                    "issue": "Failed to fetch Blnk balance",
                    "error": str(exc),
                })

        active_sells = Order.objects.filter(order_type="sell", status=Order.AWAITING_DEPOSIT)
        total_escrow_usdt = sum((o.usdt_amount for o in active_sells), Decimal("0"))

        pending_withdrawals = Withdrawal.objects.filter(status="PENDING")
        total_pending_withdrawal_usdt = sum((w.amount for w in pending_withdrawals), Decimal("0"))

        platform = PlatformAccount.objects.first()

        report = {
            "status": "ok" if len(discrepancies) == 0 else "discrepancies_found",
            "users_checked": users_checked,
            "discrepancies_count": len(discrepancies),
            "discrepancies": discrepancies,
            "escrow_summary": {
                "platform_frozen_balance_id": platform.usdt_frozen_balance_id if platform else "",
                "active_sell_orders_escrow_usdt": str(total_escrow_usdt),
            },
            "withdrawals_summary": {
                "pending_withdrawals_count": pending_withdrawals.count(),
                "pending_withdrawals_usdt": str(total_pending_withdrawal_usdt),
            },
        }
        return Response(report, status=status.HTTP_200_OK)


from .permissions import IsTradingEligible


class WalletSendView(APIView):
    permission_classes = [permissions.IsAuthenticated, IsTradingEligible]
    throttle_classes = [ConfigurableScopedRateThrottle]
    throttle_scope = "financial"

    def post(self, request):
        serializer = TransferCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        data = serializer.validated_data
        try:
            transfer = perform_p2p_transfer(
                sender=request.user,
                recipient_username=data["recipient_username"],
                amount=data["amount"],
                currency=data["currency"],
                idempotency_key=data["idempotency_key"],
            )
            return Response({"success": True, "data": TransferSerializer(transfer).data}, status=status.HTTP_200_OK)
        except ValueError as exc:
            return Response({"success": False, "message": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        except Exception as exc:
            return Response(
                {"success": False, "message": "Transfer could not be processed.", "error": str(exc)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class NotificationListView(generics.ListAPIView):
    serializer_class = NotificationSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        return Notification.objects.filter(user=self.request.user)


class NotificationReadView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, notification_id):
        updated = Notification.objects.filter(id=notification_id, user=request.user).update(read=True)
        if not updated:
            return Response({"success": False, "message": "Notification not found."}, status=status.HTTP_404_NOT_FOUND)
        return Response({"success": True, "read": True})


class TransactionListView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        user = request.user
        transactions = Transaction.objects.filter(user=user)
        orders = Order.objects.filter(user=user)

        tx_data = TransactionSerializer(transactions, many=True).data
        order_data = OrderHistorySerializer(orders, many=True).data

        merged = list(tx_data) + list(order_data)
        merged.sort(key=lambda item: item.get("created_at") or "", reverse=True)

        return Response({"success": True, "data": merged})

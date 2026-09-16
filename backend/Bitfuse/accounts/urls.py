from django.urls import path
from rest_framework_simplejwt.views import TokenRefreshView

from .views import (
    GoogleAuthView,
    LoginView,
    LogoutView,
    MeView,
    PasswordResetConfirmView,
    PasswordResetRequestView,
    RegisterView,
    RequestOTPView,
    ResendEmailVerificationView,
    VerifyEmailView,
    VerifyOTPView,
    WalletBalanceView,
    WalletSendView,
)

urlpatterns = [
    path("register/", RegisterView.as_view(), name="register"),
    path("login/", LoginView.as_view(), name="login"),
    path("logout/", LogoutView.as_view(), name="logout"),
    path("login/refresh/", TokenRefreshView.as_view(), name="login-refresh"),
    path("verify-email/", VerifyEmailView.as_view(), name="verify-email"),
    path("resend-email-verification/", ResendEmailVerificationView.as_view(), name="resend-email-verification"),
    path("otp/request/", RequestOTPView.as_view(), name="otp-request"),
    path("otp/verify/", VerifyOTPView.as_view(), name="otp-verify"),
    path("password-reset/", PasswordResetRequestView.as_view(), name="password-reset"),
    path("password-reset/confirm/", PasswordResetConfirmView.as_view(), name="password-reset-confirm"),
    path("google/", GoogleAuthView.as_view(), name="google-auth"),
    path("me/", MeView.as_view(), name="me"),
    path("wallets/", WalletBalanceView.as_view(), name="wallets"),
    path("wallets/send/", WalletSendView.as_view(), name="wallets-send"),
]

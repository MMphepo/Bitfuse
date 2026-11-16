from datetime import timedelta
from django.conf import settings
from django.utils import timezone
from rest_framework.exceptions import AuthenticationFailed
from rest_framework_simplejwt.authentication import JWTAuthentication

from .models import UserSession


def is_background_request(request):
    if not request:
        return False
    if request.META.get("HTTP_X_BACKGROUND_ACTIVITY") == "true":
        return True

    path = getattr(request, "path", "") or ""
    background_paths = [
        "/api/v1/auth/wallets/",
        "/api/v1/kyc/status/",
        "/api/v1/notifications/",
    ]
    for bg_path in background_paths:
        if path.startswith(bg_path):
            return True
    return False


class CustomJWTAuthentication(JWTAuthentication):
    """Subclasses SimpleJWT authentication to enforce UserSession inactivity and max lifetime."""

    def authenticate(self, request):
        header = self.get_header(request)
        if header is None:
            return None

        raw_token = self.get_raw_token(header)
        if raw_token is None:
            return None

        validated_token = self.get_validated_token(raw_token)
        user = self.get_user(validated_token)

        session_key = validated_token.get("session_key")
        if session_key:
            session = UserSession.objects.filter(session_key=session_key, is_active=True).first()
            if not session:
                raise AuthenticationFailed("Your session has been revoked or expired.", code="SESSION_REVOKED")

            now = timezone.now()
            max_lifetime = timedelta(hours=getattr(settings, "SESSION_MAX_LIFETIME_HOURS", 24))
            if now - session.created_at > max_lifetime:
                session.is_active = False
                session.revoked_at = now
                session.save(update_fields=["is_active", "revoked_at"])
                raise AuthenticationFailed("Your session has reached its maximum lifetime limit.", code="SESSION_EXPIRED")

            inactivity_timeout = timedelta(minutes=getattr(settings, "SESSION_INACTIVITY_TIMEOUT_MINUTES", 15))
            if now - session.last_activity > inactivity_timeout:
                session.is_active = False
                session.revoked_at = now
                session.save(update_fields=["is_active", "revoked_at"])
                raise AuthenticationFailed("Your session has expired due to inactivity.", code="SESSION_EXPIRED")

            if not is_background_request(request):
                session.last_activity = now
                session.save(update_fields=["last_activity"])

        return user, validated_token

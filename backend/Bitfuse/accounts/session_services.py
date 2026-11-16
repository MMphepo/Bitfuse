from datetime import timedelta
from django.conf import settings
from django.db import transaction
from django.utils import timezone
from rest_framework import status
from rest_framework.exceptions import AuthenticationFailed, ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.tokens import RefreshToken, TokenError

from .authentication import is_background_request
from .models import UserSession


def extract_client_ip(request):
    if not request:
        return None
    x_forwarded_for = request.META.get("HTTP_X_FORWARDED_FOR")
    if x_forwarded_for:
        ip = x_forwarded_for.split(",")[0].strip()
    else:
        ip = request.META.get("REMOTE_ADDR")
    return ip


def extract_user_agent(request):
    if not request:
        return ""
    ua = request.META.get("HTTP_USER_AGENT", "")
    return ua[:500] if ua else ""


def create_user_session(user, request=None):
    """Creates a new UserSession and returns (session, refresh_token)."""
    refresh = RefreshToken.for_user(user)

    ip_addr = extract_client_ip(request)
    user_agent = extract_user_agent(request)

    session = UserSession.objects.create(
        user=user,
        current_jti=refresh["jti"],
        ip_address=ip_addr,
        user_agent=user_agent,
    )

    refresh["session_key"] = str(session.session_key)
    refresh.access_token["session_key"] = str(session.session_key)

    return session, refresh


class CustomTokenRefreshView(APIView):
    """Custom TokenRefreshView that validates session status, inactivity, and rotates tokens safely."""
    permission_classes = []

    def post(self, request, *args, **kwargs):
        refresh_str = request.data.get("refresh")
        if not refresh_str:
            raise ValidationError({"refresh": ["Refresh token is required."]})

        try:
            token = RefreshToken(refresh_str)
        except TokenError:
            raise AuthenticationFailed("Token is invalid or expired.", code="TOKEN_INVALID")

        session_key = token.get("session_key")
        jti = token.get("jti")

        now = timezone.now()

        with transaction.atomic():
            session = None
            if session_key:
                session = UserSession.objects.select_for_update().filter(session_key=session_key).first()
            elif jti:
                session = UserSession.objects.select_for_update().filter(current_jti=jti).first()

            if not session or not session.is_active:
                raise AuthenticationFailed("Your session has been revoked or expired.", code="SESSION_EXPIRED")

            max_lifetime = timedelta(hours=getattr(settings, "SESSION_MAX_LIFETIME_HOURS", 24))
            if now - session.created_at > max_lifetime:
                session.is_active = False
                session.revoked_at = now
                session.save(update_fields=["is_active", "revoked_at"])
                try:
                    token.blacklist()
                except Exception:
                    pass
                raise AuthenticationFailed("Your session has reached its maximum lifetime limit.", code="SESSION_EXPIRED")

            inactivity_timeout = timedelta(minutes=getattr(settings, "SESSION_INACTIVITY_TIMEOUT_MINUTES", 15))
            if now - session.last_activity > inactivity_timeout:
                session.is_active = False
                session.revoked_at = now
                session.save(update_fields=["is_active", "revoked_at"])
                try:
                    token.blacklist()
                except Exception:
                    pass
                raise AuthenticationFailed("Your session has expired due to inactivity.", code="SESSION_EXPIRED")

            if session.current_jti != jti:
                raise AuthenticationFailed("Refresh token is invalid or has already been used.", code="TOKEN_INVALID")

            # Generate new rotated token pair for the user and associate with session
            new_refresh = RefreshToken.for_user(session.user)
            new_refresh["session_key"] = str(session.session_key)
            new_access = new_refresh.access_token
            new_access["session_key"] = str(session.session_key)

            session.current_jti = new_refresh["jti"]
            if not is_background_request(request):
                session.last_activity = now
            session.save(update_fields=["current_jti", "last_activity"])

            try:
                token.blacklist()
            except Exception:
                pass

            return Response(
                {
                    "success": True,
                    "data": {
                        "access": str(new_access),
                        "refresh": str(new_refresh),
                    },
                },
                status=status.HTTP_200_OK,
            )

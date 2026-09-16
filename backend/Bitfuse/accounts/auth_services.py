import hashlib
import hmac
import logging
import re
import secrets
from datetime import timedelta
from decimal import Decimal

import requests
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.mail import send_mail
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from .models import EmailVerificationToken, PasswordResetToken, PhoneOTP

logger = logging.getLogger(__name__)
User = get_user_model()


# ==============================================================================
# 1. PHONE NUMBER NORMALIZATION & VALIDATION
# ==============================================================================

def normalize_phone_number(phone_str: str) -> str:
    """Normalizes phone numbers to standard E.164 format.

    Bitfuse operates in Malawi (+265):
    - '0991234567' -> '+265991234567'
    - '0881234567' -> '+265881234567'
    - '265991234567' -> '+265991234567'
    - '+265991234567' -> '+265991234567'
    International E.164 format (e.g., '+12125550199') is also preserved.
    """
    if not phone_str:
        raise ValidationError({"phone_number": ["Phone number is required."]})

    # Remove whitespace, dashes, parens
    cleaned = re.sub(r"[\s\-\(\)]", "", phone_str.strip())

    if not cleaned:
        raise ValidationError({"phone_number": ["Enter a valid phone number."]})

    # Malawian local formats starting with 0 (e.g. 09... or 08...)
    if re.match(r"^0[189]\d{8}$", cleaned):
        cleaned = "+265" + cleaned[1:]
    elif re.match(r"^265[189]\d{8}$", cleaned):
        cleaned = "+" + cleaned
    elif not cleaned.startswith("+"):
        cleaned = "+" + cleaned

    # E.164 validation regex: + followed by 7-15 digits
    if not re.match(r"^\+[1-9]\d{6,14}$", cleaned):
        raise ValidationError({"phone_number": ["Enter a valid phone number in format +265888123456 or +12125550199."]})

    return cleaned


# ==============================================================================
# 2. SMS SERVICE & OTP SERVICE
# ==============================================================================

class SMSService:
    @staticmethod
    def send_sms(phone_number: str, message: str) -> bool:
        provider = getattr(settings, "SMS_PROVIDER", "console").lower()

        if provider == "console" or settings.DEBUG or getattr(settings, "TESTING", False):
            logger.info("[SMS CONSOLE] To: %s | Message: %s", phone_number, message)
            print(f"[SMS CONSOLE] To: {phone_number} | Message: {message}")
            return True

        if provider == "africas_talking":
            try:
                # Africa's Talking integration placeholder / API call
                api_key = settings.SMS_API_KEY
                username = settings.SMS_API_SECRET or "sandbox"
                url = "https://api.africastalking.com/version1/messaging"
                headers = {
                    "Accept": "application/json",
                    "Content-Type": "application/x-www-form-urlencoded",
                    "apiKey": api_key,
                }
                data = {
                    "username": username,
                    "to": phone_number,
                    "message": message,
                    "from": getattr(settings, "SMS_SENDER_ID", "Bitfuse"),
                }
                resp = requests.post(url, headers=headers, data=data, timeout=10)
                resp.raise_for_status()
                return True
            except Exception as exc:
                logger.error("Africa's Talking SMS failed: %s", exc)
                return False

        if provider == "twilio":
            try:
                account_sid = settings.SMS_API_KEY
                auth_token = settings.SMS_API_SECRET
                url = f"https://api.twilio.com/2010-04-01/Accounts/{account_sid}/Messages.json"
                resp = requests.post(
                    url,
                    auth=(account_sid, auth_token),
                    data={
                        "To": phone_number,
                        "From": getattr(settings, "SMS_SENDER_ID", "Bitfuse"),
                        "Body": message,
                    },
                    timeout=10,
                )
                resp.raise_for_status()
                return True
            except Exception as exc:
                logger.error("Twilio SMS failed: %s", exc)
                return False

        logger.warning("Unknown SMS_PROVIDER '%s'. Message printed to log.", provider)
        logger.info("[SMS FALLBACK] To: %s | Message: %s", phone_number, message)
        return True


class OTPService:
    @staticmethod
    def _hash_otp(code: str) -> str:
        return hashlib.sha256(code.encode("utf-8")).hexdigest()

    @classmethod
    def generate_otp(cls, phone_number: str, user=None, purpose="phone_verification") -> str:
        normalized_phone = normalize_phone_number(phone_number)

        # Rate limit check: max 5 requests per hour per phone
        one_hour_ago = timezone.now() - timedelta(hours=1)
        recent_count = PhoneOTP.objects.filter(
            phone_number=normalized_phone,
            purpose=purpose,
            created_at__gte=one_hour_ago,
        ).count()
        if recent_count >= 5:
            raise ValidationError({"non_field_errors": ["Too many OTP requests. Please wait before requesting another code."]})

        # Invalidate previous unused OTPs for this phone/purpose
        PhoneOTP.objects.filter(
            phone_number=normalized_phone,
            purpose=purpose,
            used=False,
        ).update(used=True)

        # Generate cryptographically secure 6-digit OTP
        code = f"{secrets.SystemRandom().randint(100000, 999999)}"
        otp_hash = cls._hash_otp(code)
        expires_at = timezone.now() + timedelta(minutes=10)

        PhoneOTP.objects.create(
            user=user,
            phone_number=normalized_phone,
            otp_hash=otp_hash,
            expires_at=expires_at,
            purpose=purpose,
        )

        message = f"Your Bitfuse verification code is {code}. Valid for 10 minutes. Do not share this code."
        SMSService.send_sms(normalized_phone, message)
        return code

    @classmethod
    def verify_otp(cls, phone_number: str, code: str, user=None, purpose="phone_verification") -> bool:
        normalized_phone = normalize_phone_number(phone_number)
        now = timezone.now()

        otp_record = PhoneOTP.objects.filter(
            phone_number=normalized_phone,
            purpose=purpose,
            used=False,
            expires_at__gt=now,
        ).order_by("-created_at").first()

        if not otp_record:
            raise ValidationError({"code": ["Invalid or expired verification code."]})

        if otp_record.attempts >= 5:
            otp_record.used = True
            otp_record.save(update_fields=["used"])
            raise ValidationError({"code": ["Maximum verification attempts exceeded. Please request a new code."]})

        otp_record.attempts += 1
        otp_record.save(update_fields=["attempts"])

        incoming_hash = cls._hash_otp(code.strip())
        if not hmac.compare_digest(otp_record.otp_hash, incoming_hash):
            raise ValidationError({"code": ["Invalid verification code."]})

        # Success
        otp_record.used = True
        otp_record.save(update_fields=["used"])

        if user:
            user.phone_verified = True
            user.save(update_fields=["phone_verified"])
        else:
            matching_users = User.objects.filter(phone_number=normalized_phone)
            matching_users.update(phone_verified=True)

        return True


# ==============================================================================
# 3. EMAIL VERIFICATION & PASSWORD RESET SERVICES
# ==============================================================================

class EmailService:
    @staticmethod
    def _hash_token(token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    @classmethod
    def send_verification_email(cls, user) -> str:
        # Rate limit: max 5 verification emails per hour
        one_hour_ago = timezone.now() - timedelta(hours=1)
        recent_count = EmailVerificationToken.objects.filter(
            user=user,
            created_at__gte=one_hour_ago,
        ).count()
        if recent_count >= 5:
            raise ValidationError({"email": ["Too many verification emails requested. Please wait before retrying."]})

        # Invalidate previous unused tokens for user
        EmailVerificationToken.objects.filter(user=user, used=False).update(used=True)

        raw_token = secrets.token_urlsafe(32)
        token_hash = cls._hash_token(raw_token)
        expires_at = timezone.now() + timedelta(hours=24)

        EmailVerificationToken.objects.create(
            user=user,
            token_hash=token_hash,
            expires_at=expires_at,
        )

        subject = "Verify your Bitfuse Email Address"
        message = (
            f"Hello {user.first_name or user.username},\n\n"
            f"Thank you for signing up with Bitfuse.\n"
            f"Your email verification token is:\n\n{raw_token}\n\n"
            f"This token is valid for 24 hours.\n"
            f"If you did not create a Bitfuse account, please ignore this email."
        )

        send_mail(
            subject=subject,
            message=message,
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[user.email],
            fail_silently=False,
        )
        return raw_token

    @classmethod
    def verify_email_token(cls, raw_token: str) -> User:
        token_hash = cls._hash_token(raw_token.strip())
        now = timezone.now()

        email_token = EmailVerificationToken.objects.filter(
            token_hash=token_hash,
            used=False,
            expires_at__gt=now,
        ).first()

        if not email_token:
            raise ValidationError({"token": ["Invalid or expired verification token."]})

        email_token.used = True
        email_token.save(update_fields=["used"])

        user = email_token.user
        user.email_verified = True
        user.save(update_fields=["email_verified"])
        return user

    @classmethod
    def send_password_reset_email(cls, email: str) -> str:
        try:
            user = User.objects.get(email__iexact=email.strip())
        except User.DoesNotExist:
            return "reset_sent"

        # Invalidate previous tokens
        PasswordResetToken.objects.filter(user=user, used=False).update(used=True)

        raw_token = secrets.token_urlsafe(32)
        token_hash = cls._hash_token(raw_token)
        expires_at = timezone.now() + timedelta(hours=1)

        PasswordResetToken.objects.create(
            user=user,
            token_hash=token_hash,
            expires_at=expires_at,
        )

        subject = "Reset your Bitfuse Password"
        message = (
            f"Hello {user.first_name or user.username},\n\n"
            f"We received a request to reset your password.\n"
            f"Your password reset token is:\n\n{raw_token}\n\n"
            f"This token is valid for 1 hour.\n"
            f"If you did not request a password reset, please ignore this email."
        )

        send_mail(
            subject=subject,
            message=message,
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[user.email],
            fail_silently=False,
        )
        return raw_token

    @classmethod
    def confirm_password_reset(cls, raw_token: str, new_password: str) -> User:
        token_hash = cls._hash_token(raw_token.strip())
        now = timezone.now()

        reset_token = PasswordResetToken.objects.filter(
            token_hash=token_hash,
            used=False,
            expires_at__gt=now,
        ).first()

        if not reset_token:
            raise ValidationError({"token": ["Invalid or expired password reset token."]})

        reset_token.used = True
        reset_token.save(update_fields=["used"])

        user = reset_token.user
        user.set_password(new_password)
        user.save()
        return user


# ==============================================================================
# 4. CAPTCHA SERVICE
# ==============================================================================

class CaptchaService:
    @staticmethod
    def verify_captcha(captcha_token: str, remote_ip: str = None) -> bool:
        if not getattr(settings, "CAPTCHA_ENABLED", False):
            return True

        if getattr(settings, "TESTING", False):
            if captcha_token == "invalid-captcha":
                raise ValidationError({"captcha": ["Invalid CAPTCHA token."]})
            return True

        if not captcha_token:
            raise ValidationError({"captcha": ["CAPTCHA verification required."]})

        secret_key = getattr(settings, "CAPTCHA_SECRET_KEY", "")
        provider = getattr(settings, "CAPTCHA_PROVIDER", "turnstile").lower()

        if provider == "turnstile":
            url = "https://challenges.cloudflare.com/turnstile/v0/siteverify"
        else:
            url = "https://www.google.com/recaptcha/api/siteverify"

        try:
            data = {"secret": secret_key, "response": captcha_token}
            if remote_ip:
                data["remoteip"] = remote_ip

            resp = requests.post(url, data=data, timeout=5)
            res_json = resp.json()
            if not res_json.get("success"):
                raise ValidationError({"captcha": ["CAPTCHA verification failed. Please try again."]})
            return True
        except ValidationError:
            raise
        except Exception as exc:
            logger.error("CAPTCHA verification request error: %s", exc)
            raise ValidationError({"captcha": ["CAPTCHA verification unavailable."]})


# ==============================================================================
# 5. GOOGLE OAUTH SERVICE
# ==============================================================================

class GoogleAuthService:
    @staticmethod
    def verify_google_id_token(id_token_str: str) -> dict:
        if not id_token_str:
            raise ValidationError({"id_token": ["Google ID token is required."]})

        if getattr(settings, "TESTING", False) and id_token_str.startswith("mock-google-token"):
            if id_token_str == "mock-google-token-invalid":
                raise ValidationError({"id_token": ["Invalid Google ID token."]})
            return {
                "sub": "google-uid-12345",
                "email": "googleuser@example.com",
                "given_name": "Google",
                "family_name": "User",
                "email_verified": True,
            }

        try:
            from google.auth.transport import requests as google_requests
            from google.oauth2 import id_token as google_id_token

            client_id = getattr(settings, "GOOGLE_CLIENT_ID", "")
            id_info = google_id_token.verify_oauth2_token(
                id_token_str, google_requests.Request(), client_id if client_id else None
            )

            if id_info.get("iss") not in ["accounts.google.com", "https://accounts.google.com"]:
                raise ValidationError({"id_token": ["Invalid Google ID token issuer."]})

            return id_info
        except ValidationError:
            raise
        except Exception as exc:
            logger.error("Google ID token verification failed: %s", exc)
            raise ValidationError({"id_token": ["Google authentication failed. Invalid token."]})

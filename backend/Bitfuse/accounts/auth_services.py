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
    @classmethod
    def send_sms(cls, phone_number: str, message: str) -> tuple[bool, str]:
        """Dispatches an SMS using configured SMS provider.

        Returns:
            tuple[bool, str]: (success_flag, provider_batch_id)
        """
        provider = getattr(settings, "SMS_PROVIDER", "tumasend").lower()
        normalized_phone = normalize_phone_number(phone_number)

        if provider == "console" or (getattr(settings, "TESTING", False) and not getattr(settings, "TEST_REAL_TUMASEND", False)):
            import uuid
            batch_id = f"dev-batch-{uuid.uuid4()}"
            masked_phone = f"{normalized_phone[:6]}***{normalized_phone[-3:]}"
            logger.info("[SMS CONSOLE] To: %s | Batch ID: %s | Message: %s", masked_phone, batch_id, message)
            print(f"[SMS CONSOLE] To: {masked_phone} | Batch ID: {batch_id} | Message: {message}")
            return True, batch_id

        if provider == "tumasend":
            try:
                from .tumasend import TumaSendClient
                client = TumaSendClient()
                res = client.send_sms(recipients=[normalized_phone], message=message)
                batch_id = res.get("batch_id", "")
                return True, batch_id
            except Exception as exc:
                logger.error("TumaSend SMS delivery failed: %s", exc)
                return False, ""

        if provider == "africas_talking":
            try:
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
                    "to": normalized_phone,
                    "message": message,
                    "from": getattr(settings, "SMS_SENDER_ID", "Bitfuse"),
                }
                resp = requests.post(url, headers=headers, data=data, timeout=10)
                resp.raise_for_status()
                return True, "africastalking-ok"
            except Exception as exc:
                logger.error("Africa's Talking SMS failed: %s", exc)
                return False, ""

        if provider == "twilio":
            try:
                account_sid = settings.SMS_API_KEY
                auth_token = settings.SMS_API_SECRET
                url = f"https://api.twilio.com/2010-04-01/Accounts/{account_sid}/Messages.json"
                resp = requests.post(
                    url,
                    auth=(account_sid, auth_token),
                    data={
                        "To": normalized_phone,
                        "From": getattr(settings, "SMS_SENDER_ID", "Bitfuse"),
                        "Body": message,
                    },
                    timeout=10,
                )
                resp.raise_for_status()
                return True, "twilio-ok"
            except Exception as exc:
                logger.error("Twilio SMS failed: %s", exc)
                return False, ""

        logger.warning("Unknown SMS_PROVIDER '%s'. Message printed to log.", provider)
        return False, ""


class OTPService:
    @staticmethod
    def _hash_otp(code: str) -> str:
        return hashlib.sha256(code.encode("utf-8")).hexdigest()

    @classmethod
    def generate_otp(cls, phone_number: str, user=None, purpose="phone_verification") -> dict:
        normalized_phone = normalize_phone_number(phone_number)
        now = timezone.now()

        # Enforce rate limits
        cooldown_seconds = getattr(settings, "PHONE_VERIFICATION_RESEND_COOLDOWN_SECONDS", 60)
        max_hourly_sends = getattr(settings, "PHONE_VERIFICATION_MAX_SENDS_PER_HOUR", 5)
        max_daily_sends = getattr(settings, "PHONE_VERIFICATION_MAX_SENDS_PER_DAY", 10)

        one_hour_ago = now - timedelta(hours=1)
        one_day_ago = now - timedelta(days=1)

        # 1. Check 60-second cooldown
        latest_otp = PhoneOTP.objects.filter(
            phone_number=normalized_phone,
            purpose=purpose,
        ).order_by("-last_sent_at").first()

        if latest_otp and latest_otp.last_sent_at:
            seconds_since_last = (now - latest_otp.last_sent_at).total_seconds()
            if seconds_since_last < cooldown_seconds:
                remaining_cooldown = int(cooldown_seconds - seconds_since_last)
                raise ValidationError({
                    "non_field_errors": ["Please wait before requesting another code."],
                    "resend_after": remaining_cooldown
                })

        # 2. Check per-phone hourly limit
        phone_hourly_count = PhoneOTP.objects.filter(
            phone_number=normalized_phone,
            purpose=purpose,
            created_at__gte=one_hour_ago,
        ).count()
        if phone_hourly_count >= max_hourly_sends:
            raise ValidationError({"detail": "Too many verification requests for this phone number. Please try again later."})

        # 3. Check per-user limits (hourly and daily)
        if user:
            user_hourly_count = PhoneOTP.objects.filter(
                user=user,
                purpose=purpose,
                created_at__gte=one_hour_ago,
            ).count()
            if user_hourly_count >= max_hourly_sends:
                raise ValidationError({"detail": "Too many verification requests. Please wait an hour before trying again."})

            user_daily_count = PhoneOTP.objects.filter(
                user=user,
                purpose=purpose,
                created_at__gte=one_day_ago,
            ).count()
            if user_daily_count >= max_daily_sends:
                raise ValidationError({"detail": "Maximum daily verification limit reached. Please try again tomorrow."})

        # Mark previous pending OTPs for this phone/purpose as superseded
        PhoneOTP.objects.filter(
            phone_number=normalized_phone,
            purpose=purpose,
            status="pending",
        ).update(status="superseded", used=True)

        # Generate cryptographically secure 6-digit OTP
        code = f"{secrets.SystemRandom().randint(100000, 999999)}"
        otp_hash = cls._hash_otp(code)
        expiry_seconds = getattr(settings, "PHONE_VERIFICATION_OTP_EXPIRY_SECONDS", 300)
        expires_at = now + timedelta(seconds=expiry_seconds)

        # Send SMS via SMSService (TumaSendClient)
        message = f"Your BitFuse verification code is {code}. It expires in {int(expiry_seconds // 60)} minutes."
        success, batch_id = SMSService.send_sms(normalized_phone, message)

        if not success:
            logger.error("[OTP_SEND_FAILED] Failed to dispatch SMS to %s via provider.", normalized_phone)
            raise ValidationError({"detail": "Failed to send verification SMS. Please try again shortly."})

        otp_record = PhoneOTP.objects.create(
            user=user,
            phone_number=normalized_phone,
            otp_hash=otp_hash,
            expires_at=expires_at,
            purpose=purpose,
            status="pending",
            provider_batch_id=batch_id,
            last_sent_at=now,
        )

        return {
            "code": code,
            "expires_in": expiry_seconds,
            "resend_after": cooldown_seconds,
            "batch_id": batch_id,
        }

    @classmethod
    def verify_otp(cls, phone_number: str = None, code: str = None, user=None, purpose="phone_verification") -> bool:
        if not code or not str(code).strip():
            raise ValidationError({"code": ["Verification code is required."]})

        clean_code = str(code).strip()
        if not clean_code.isdigit() or len(clean_code) != 6:
            raise ValidationError({"code": ["Verification code must be a 6-digit number."]})

        now = timezone.now()

        # Build query matching user or phone
        query = PhoneOTP.objects.filter(
            purpose=purpose,
            used=False,
        )
        if user:
            query = query.filter(user=user)
        elif phone_number:
            normalized_phone = normalize_phone_number(phone_number)
            query = query.filter(phone_number=normalized_phone)
        else:
            raise ValidationError({"detail": "User or phone number is required for OTP verification."})

        otp_record = query.order_by("-created_at").first()

        if not otp_record or otp_record.status in ("expired", "failed", "superseded"):
            raise ValidationError({"code": ["Invalid or expired verification code."]})

        # Expiration check
        if now > otp_record.expires_at:
            otp_record.status = "expired"
            otp_record.used = True
            otp_record.save(update_fields=["status", "used"])
            raise ValidationError({"code": ["Invalid or expired verification code."]})

        # Attempt limit check
        max_attempts = getattr(settings, "PHONE_VERIFICATION_MAX_ATTEMPTS", 5)
        if otp_record.attempts >= max_attempts:
            otp_record.status = "failed"
            otp_record.used = True
            otp_record.save(update_fields=["status", "used"])
            raise ValidationError({"detail": "Maximum verification attempts exceeded. Please request a new code."})

        # Check hash comparison
        incoming_hash = cls._hash_otp(clean_code)
        if not hmac.compare_digest(otp_record.otp_hash, incoming_hash):
            otp_record.attempts += 1
            if otp_record.attempts >= max_attempts:
                otp_record.status = "failed"
                otp_record.used = True
                otp_record.save(update_fields=["attempts", "status", "used"])
                raise ValidationError({"detail": "Maximum verification attempts exceeded. Please request a new code."})
            else:
                otp_record.save(update_fields=["attempts"])
                raise ValidationError({"code": ["Invalid verification code."]})

        # OTP verification successful - execute user and OTP status update inside atomic transaction
        from django.db import transaction

        with transaction.atomic():
            otp_record.status = "verified"
            otp_record.used = True
            otp_record.verified_at = now
            otp_record.save(update_fields=["status", "used", "verified_at"])

            target_user = user or otp_record.user
            if target_user:
                # Lock row if user instance is present in database
                User.objects.filter(id=target_user.id).update(
                    phone_number=otp_record.phone_number,
                    phone_verified=True,
                    phone_verified_at=now,
                )
                target_user.refresh_from_db()
                logger.info(
                    "[OTP_USER_PERSISTED] User %s (id=%s) phone_verified set to %s at %s",
                    target_user.username,
                    target_user.id,
                    target_user.phone_verified,
                    target_user.phone_verified_at,
                )
            else:
                matching_users = User.objects.filter(phone_number=otp_record.phone_number)
                updated_count = matching_users.update(phone_verified=True, phone_verified_at=now)
                logger.info("[OTP_USER_BULK_PERSISTED] Updated %s user records for phone %s", updated_count, otp_record.phone_number)

        logger.info(f"[OTP_VERIFIED] Phone number {otp_record.phone_number} successfully verified.")
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
        if user.email_verified:
            return ""

        # Rate limit: max requests per hour
        max_requests_per_hour = getattr(settings, "EMAIL_VERIFICATION_MAX_REQUESTS_PER_HOUR", 5)
        one_hour_ago = timezone.now() - timedelta(hours=1)
        recent_count = EmailVerificationToken.objects.filter(
            user=user,
            created_at__gte=one_hour_ago,
        ).count()
        if recent_count >= max_requests_per_hour:
            raise ValidationError({"email": ["Too many verification emails requested. Please wait before retrying."]})

        # Invalidate previous unused tokens for user
        EmailVerificationToken.objects.filter(user=user, used=False).update(used=True)

        raw_token = secrets.token_urlsafe(32)
        token_hash = cls._hash_token(raw_token)
        expiry_minutes = getattr(settings, "EMAIL_VERIFICATION_TOKEN_EXPIRY_MINUTES", 30)
        expires_at = timezone.now() + timedelta(minutes=expiry_minutes)

        EmailVerificationToken.objects.create(
            user=user,
            token_hash=token_hash,
            expires_at=expires_at,
        )

        frontend_url = getattr(settings, "FRONTEND_URL", "https://bitfuse.mw").rstrip("/")
        verification_url = f"{frontend_url}/verify-email?token={raw_token}"

        subject = "Verify your Bitfuse email address"
        plain_message = (
            f"Bitfuse\n\n"
            f"Verify your email address\n\n"
            f"Welcome to Bitfuse.\n\n"
            f"Please verify your email address to complete your account setup.\n\n"
            f"Verification Link: {verification_url}\n\n"
            f"This verification link expires in {expiry_minutes} minutes.\n\n"
            f"If you did not create a Bitfuse account, you can safely ignore this email.\n\n"
            f"Bitfuse"
        )

        html_message = (
            f"<!DOCTYPE html><html><body>"
            f"<div style='font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto; padding: 20px; color: #333;'>"
            f"<h2 style='color: #0052FF;'>Bitfuse</h2>"
            f"<h3>Verify your email address</h3>"
            f"<p>Welcome to Bitfuse.</p>"
            f"<p>Please verify your email address to complete your account setup.</p>"
            f"<div style='margin: 30px 0;'>"
            f"<a href='{verification_url}' style='background-color: #0052FF; color: #ffffff; padding: 12px 24px; text-decoration: none; border-radius: 6px; font-weight: bold; display: inline-block;'>Verify my email</a>"
            f"</div>"
            f"<p style='font-size: 14px; color: #666;'>This verification link expires in {expiry_minutes} minutes.</p>"
            f"<p style='font-size: 14px; color: #666;'>Or copy and paste this URL into your browser: <br/><a href='{verification_url}'>{verification_url}</a></p>"
            f"<p style='font-size: 14px; color: #888;'>If you did not create a Bitfuse account, you can safely ignore this email.</p>"
            f"</div>"
            f"</body></html>"
        )

        try:
            sent_count = send_mail(
                subject=subject,
                message=plain_message,
                html_message=html_message,
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=[user.email],
                fail_silently=False,
            )
            logger.info(f"[EMAIL_SENT] Verification email sent to {user.email} (sent_count={sent_count}, backend={settings.EMAIL_BACKEND})")
        except Exception as exc:
            logger.error(f"[EMAIL_DELIVERY_FAILURE] Verification email to user {user.id} ({user.email}) failed: {exc}")

        return raw_token

    @classmethod
    def verify_email_token(cls, raw_token: str) -> User:
        if not raw_token or not str(raw_token).strip():
            raise ValidationError({"code": "INVALID_TOKEN", "message": "This verification link is invalid."})

        token_hash = cls._hash_token(raw_token.strip())
        now = timezone.now()

        from django.db import transaction
        with transaction.atomic():
            email_token = EmailVerificationToken.objects.select_for_update().filter(
                token_hash=token_hash
            ).first()

            if not email_token:
                raise ValidationError({"code": "INVALID_TOKEN", "message": "This verification link is invalid."})

            if email_token.used:
                raise ValidationError({"code": "TOKEN_ALREADY_USED", "message": "This verification link has already been used."})

            if email_token.expires_at <= now:
                raise ValidationError({"code": "TOKEN_EXPIRED", "message": "This verification link has expired."})

            email_token.used = True
            email_token.save(update_fields=["used"])

            user = email_token.user
            user.email_verified = True
            user.save(update_fields=["email_verified"])

            logger.info(f"[EMAIL_VERIFIED] User {user.id} email address verified successfully.")
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
    def verify_captcha(captcha_token: str, remote_ip: str = None, expected_action: str = None, is_financial: bool = False) -> bool:
        from .recaptcha import verify_recaptcha
        return verify_recaptcha(
            token=captcha_token,
            expected_action=expected_action,
            is_financial=is_financial,
            request_ip=remote_ip,
        )


# ==============================================================================
# 5. GOOGLE OAUTH SERVICE
# ==============================================================================

class GoogleAuthService:
    @staticmethod
    def verify_google_id_token(id_token_str: str) -> dict:
        if not id_token_str or not str(id_token_str).strip():
            raise ValidationError({"credential": ["Google credential is required."]})

        id_token_str = str(id_token_str).strip()

        if getattr(settings, "TESTING", False) and id_token_str.startswith("mock-google-token"):
            if id_token_str == "mock-google-token-invalid":
                raise ValidationError({"credential": ["Invalid Google credential."]})
            if id_token_str == "mock-google-token-expired":
                raise ValidationError({"credential": ["Google authentication has expired. Please try again."]})
            if id_token_str == "mock-google-token-wrong-aud":
                raise ValidationError({"credential": ["Google authentication token audience mismatch."]})
            if id_token_str == "mock-google-token-unverified-email":
                return {
                    "sub": "google-uid-unverified-email",
                    "email": "unverified_google@example.com",
                    "given_name": "Google",
                    "family_name": "User",
                    "email_verified": False,
                }
            if id_token_str == "mock-google-token-conflict":
                return {
                    "sub": "google-uid-conflict-999",
                    "email": "existing_diff_google@example.com",
                    "given_name": "Google",
                    "family_name": "User",
                    "email_verified": True,
                }
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
                raise ValidationError({"credential": ["Invalid Google ID token issuer."]})

            return id_info
        except ValidationError:
            raise
        except ValueError as exc:
            err_msg = str(exc)
            logger.warning("Google ID token verification failed (ValueError): %s", err_msg)
            if "expired" in err_msg.lower():
                raise ValidationError({"credential": ["Google authentication has expired. Please try again."]})
            if "audience" in err_msg.lower() or "recipient" in err_msg.lower():
                raise ValidationError({"credential": ["Google authentication token audience mismatch."]})
            raise ValidationError({"credential": ["Google authentication could not be verified."]})
        except Exception as exc:
            logger.error("Google ID token verification failed: %s", exc)
            raise ValidationError({"credential": ["Google sign-in could not be completed."]})

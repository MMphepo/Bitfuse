import logging
from datetime import datetime
from django.conf import settings
from django.core.mail import send_mail
from django.db import IntegrityError, transaction
from django.template.loader import render_to_string
from django.utils import timezone

from accounts.models import EmailNotification

logger = logging.getLogger(__name__)


def _dispatch_email(
    user,
    event_type: str,
    reference_type: str,
    reference_id: str,
    recipient_email: str,
    subject: str,
    html_template: str,
    txt_template: str,
    context: dict,
) -> bool:
    """Centralized, idempotent email dispatcher using EmailNotification model.

    Deduplicates email dispatches by (event_type, reference_type, reference_id, recipient).
    If an EmailNotification row exists and status == "sent", dispatches are skipped safely.
    Failure in sending email logs error and marks status="failed" without raising exceptions or rolling back DB transactions.
    """
    if not recipient_email or not str(recipient_email).strip():
        logger.warning(f"[EMAIL_DISPATCH_SKIP] Empty recipient email for event {event_type}")
        return False

    recipient = str(recipient_email).strip().lower()
    ref_type = str(reference_type or "").strip()
    ref_id = str(reference_id or "").strip()

    # Idempotency check: look for existing record
    existing = EmailNotification.objects.filter(
        event_type=event_type,
        reference_type=ref_type,
        reference_id=ref_id,
        recipient=recipient,
    ).first()

    if existing and existing.status == "sent":
        logger.info(
            f"[EMAIL_IDEMPOTENT_SKIP] Notification already sent for event={event_type}, "
            f"ref={ref_type}:{ref_id}, recipient={recipient}"
        )
        return True

    notification = existing
    if not notification:
        try:
            notification = EmailNotification.objects.create(
                user=user,
                event_type=event_type,
                reference_type=ref_type,
                reference_id=ref_id,
                recipient=recipient,
                subject=subject,
                status="pending",
                attempt_count=0,
            )
        except IntegrityError:
            # Concurrent creation race condition
            notification = EmailNotification.objects.filter(
                event_type=event_type,
                reference_type=ref_type,
                reference_id=ref_id,
                recipient=recipient,
            ).first()
            if notification and notification.status == "sent":
                return True

    if not notification:
        logger.error(f"[EMAIL_CREATE_FAILED] Could not create or find EmailNotification row for {event_type}")
        return False

    # Render email content
    frontend_url = getattr(settings, "FRONTEND_URL", "https://bitfuse.mw").rstrip("/")
    context_data = {
        "frontend_url": frontend_url,
        "recipient_name": (user.get_full_name() or user.username) if user else recipient,
        **context,
    }

    try:
        html_content = render_to_string(html_template, context_data)
        txt_content = render_to_string(txt_template, context_data)
    except Exception as render_exc:
        logger.error(f"[EMAIL_RENDER_ERROR] Template render failed for {html_template}: {render_exc}")
        notification.last_error = f"Template render error: {render_exc}"
        notification.status = "failed"
        notification.save(update_fields=["last_error", "status"])
        return False

    notification.attempt_count += 1

    try:
        sent_count = send_mail(
            subject=subject,
            message=txt_content,
            html_message=html_content,
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[recipient],
            fail_silently=False,
        )
        notification.status = "sent"
        notification.sent_at = timezone.now()
        notification.last_error = ""
        notification.save(update_fields=["status", "sent_at", "attempt_count", "last_error"])
        logger.info(
            f"[EMAIL_DELIVERED] Sent email event={event_type} to {recipient} "
            f"(sent_count={sent_count}, backend={getattr(settings, 'EMAIL_BACKEND', '')})"
        )
        return True
    except Exception as exc:
        logger.error(f"[EMAIL_DELIVERY_FAILURE] Event={event_type} to {recipient} failed: {exc}")
        notification.status = "failed"
        notification.last_error = str(exc)
        notification.save(update_fields=["status", "attempt_count", "last_error"])
        return False


class EmailNotificationService:
    @staticmethod
    def send_kyc_submitted_email(user) -> bool:
        return _dispatch_email(
            user=user,
            event_type="kyc_submitted",
            reference_type="user",
            reference_id=str(user.id),
            recipient_email=user.email,
            subject="Your Bitfuse KYC verification was submitted",
            html_template="emails/kyc/submitted.html",
            txt_template="emails/kyc/submitted.txt",
            context={},
        )

    @staticmethod
    def send_kyc_approved_email(user) -> bool:
        return _dispatch_email(
            user=user,
            event_type="kyc_approved",
            reference_type="user",
            reference_id=str(user.id),
            recipient_email=user.email,
            subject="Your Bitfuse account has been verified",
            html_template="emails/kyc/approved.html",
            txt_template="emails/kyc/approved.txt",
            context={},
        )

    @staticmethod
    def send_kyc_rejected_email(user, reason: str = "") -> bool:
        return _dispatch_email(
            user=user,
            event_type="kyc_rejected",
            reference_type="user",
            reference_id=str(user.id),
            recipient_email=user.email,
            subject="Update on your Bitfuse KYC submission",
            html_template="emails/kyc/rejected.html",
            txt_template="emails/kyc/rejected.txt",
            context={"reason": reason},
        )

    @staticmethod
    def send_kyc_resubmission_required_email(user, reason: str = "") -> bool:
        return _dispatch_email(
            user=user,
            event_type="kyc_resubmission_required",
            reference_type="user",
            reference_id=str(user.id),
            recipient_email=user.email,
            subject="Action Required: Bitfuse KYC Resubmission",
            html_template="emails/kyc/resubmission_required.html",
            txt_template="emails/kyc/resubmission_required.txt",
            context={"reason": reason},
        )

    @staticmethod
    def send_buy_order_created_email(order) -> bool:
        return _dispatch_email(
            user=order.user,
            event_type="buy_order_created",
            reference_type="order",
            reference_id=order.reference_number,
            recipient_email=order.user.email,
            subject=f"Buy Order Created - {order.reference_number}",
            html_template="emails/buy/order_created.html",
            txt_template="emails/buy/order_created.txt",
            context={
                "reference_number": order.reference_number,
                "usdt_amount": str(order.usdt_amount),
                "mwk_amount": str(order.mwk_amount),
                "total_payable_mwk": str(order.total_payable_mwk),
                "rate": str(order.rate),
                "payment_method": order.payment_method,
                "payment_reference": order.payment_reference,
            },
        )

    @staticmethod
    def send_payment_confirmed_email(order) -> bool:
        return _dispatch_email(
            user=order.user,
            event_type="buy_payment_confirmed",
            reference_type="order",
            reference_id=order.reference_number,
            recipient_email=order.user.email,
            subject=f"Payment Verified - Order {order.reference_number}",
            html_template="emails/buy/payment_confirmed.html",
            txt_template="emails/buy/payment_confirmed.txt",
            context={
                "reference_number": order.reference_number,
                "usdt_amount": str(order.usdt_amount),
                "mwk_amount": str(order.received_mwk_amount or order.total_payable_mwk),
            },
        )

    @staticmethod
    def send_usdt_credited_email(order) -> bool:
        return _dispatch_email(
            user=order.user,
            event_type="buy_usdt_credited",
            reference_type="order",
            reference_id=order.reference_number,
            recipient_email=order.user.email,
            subject=f"USDT Credited - Order {order.reference_number}",
            html_template="emails/buy/usdt_credited.html",
            txt_template="emails/buy/usdt_credited.txt",
            context={
                "reference_number": order.reference_number,
                "usdt_amount": str(order.usdt_amount),
                "timestamp": timezone.now().strftime("%Y-%m-%d %H:%M:%S UTC"),
            },
        )

    @staticmethod
    def send_buy_order_rejected_email(order, reason: str = "") -> bool:
        return _dispatch_email(
            user=order.user,
            event_type="buy_order_rejected",
            reference_type="order",
            reference_id=order.reference_number,
            recipient_email=order.user.email,
            subject=f"Buy Order Update - {order.reference_number}",
            html_template="emails/buy/rejected.html",
            txt_template="emails/buy/rejected.txt",
            context={
                "reference_number": order.reference_number,
                "reason": reason or order.rejection_reason,
            },
        )

    @staticmethod
    def send_buy_order_expired_email(order) -> bool:
        return _dispatch_email(
            user=order.user,
            event_type="buy_order_expired",
            reference_type="order",
            reference_id=order.reference_number,
            recipient_email=order.user.email,
            subject=f"Buy Order Expired - {order.reference_number}",
            html_template="emails/buy/expired.html",
            txt_template="emails/buy/expired.txt",
            context={
                "reference_number": order.reference_number,
            },
        )

    @staticmethod
    def send_sell_order_created_email(order) -> bool:
        return _dispatch_email(
            user=order.user,
            event_type="sell_order_created",
            reference_type="order",
            reference_id=order.reference_number,
            recipient_email=order.user.email,
            subject=f"Sell Order Created - {order.reference_number}",
            html_template="emails/sell/order_created.html",
            txt_template="emails/sell/order_created.txt",
            context={
                "reference_number": order.reference_number,
                "usdt_amount": str(order.usdt_amount),
                "mwk_amount": str(order.mwk_amount),
                "rate": str(order.rate),
                "phone": order.phone,
            },
        )

    @staticmethod
    def send_sell_order_completed_email(order) -> bool:
        return _dispatch_email(
            user=order.user,
            event_type="sell_order_completed",
            reference_type="order",
            reference_id=order.reference_number,
            recipient_email=order.user.email,
            subject=f"Sell Order Completed - {order.reference_number}",
            html_template="emails/sell/completed.html",
            txt_template="emails/sell/completed.txt",
            context={
                "reference_number": order.reference_number,
                "usdt_amount": str(order.usdt_amount),
                "mwk_amount": str(order.mwk_amount),
                "phone": order.phone,
                "timestamp": timezone.now().strftime("%Y-%m-%d %H:%M:%S UTC"),
            },
        )

    @staticmethod
    def send_sell_order_rejected_email(order, reason: str = "") -> bool:
        return _dispatch_email(
            user=order.user,
            event_type="sell_order_rejected",
            reference_type="order",
            reference_id=order.reference_number,
            recipient_email=order.user.email,
            subject=f"Sell Order Update - {order.reference_number}",
            html_template="emails/sell/rejected.html",
            txt_template="emails/sell/rejected.txt",
            context={
                "reference_number": order.reference_number,
                "reason": reason or order.rejection_reason,
            },
        )

    @staticmethod
    def send_withdrawal_requested_email(withdrawal) -> bool:
        return _dispatch_email(
            user=withdrawal.user,
            event_type="withdrawal_requested",
            reference_type="withdrawal",
            reference_id=str(withdrawal.id),
            recipient_email=withdrawal.user.email,
            subject=f"Withdrawal Requested - {withdrawal.amount} {withdrawal.asset}",
            html_template="emails/withdrawal/requested.html",
            txt_template="emails/withdrawal/requested.txt",
            context={
                "withdrawal_id": str(withdrawal.id),
                "amount": str(withdrawal.amount),
                "fee": str(withdrawal.fee),
                "net_amount": str(withdrawal.net_amount),
                "asset": withdrawal.asset,
                "network": withdrawal.network,
                "destination_address": withdrawal.destination_address,
            },
        )

    @staticmethod
    def send_withdrawal_completed_email(withdrawal) -> bool:
        return _dispatch_email(
            user=withdrawal.user,
            event_type="withdrawal_completed",
            reference_type="withdrawal",
            reference_id=str(withdrawal.id),
            recipient_email=withdrawal.user.email,
            subject=f"Withdrawal Confirmed - {withdrawal.net_amount} {withdrawal.asset}",
            html_template="emails/withdrawal/completed.html",
            txt_template="emails/withdrawal/completed.txt",
            context={
                "withdrawal_id": str(withdrawal.id),
                "net_amount": str(withdrawal.net_amount),
                "asset": withdrawal.asset,
                "network": withdrawal.network,
                "destination_address": withdrawal.destination_address,
                "tx_hash": withdrawal.transaction_hash,
            },
        )

    @staticmethod
    def send_withdrawal_failed_email(withdrawal, reason: str = "") -> bool:
        return _dispatch_email(
            user=withdrawal.user,
            event_type="withdrawal_failed",
            reference_type="withdrawal",
            reference_id=str(withdrawal.id),
            recipient_email=withdrawal.user.email,
            subject=f"Withdrawal Failed - {withdrawal.amount} {withdrawal.asset}",
            html_template="emails/withdrawal/failed.html",
            txt_template="emails/withdrawal/failed.txt",
            context={
                "withdrawal_id": str(withdrawal.id),
                "amount": str(withdrawal.amount),
                "asset": withdrawal.asset,
                "network": withdrawal.network,
                "reason": reason or withdrawal.failure_reason,
            },
        )

    @staticmethod
    def send_transfer_sent_email(transfer) -> bool:
        return _dispatch_email(
            user=transfer.sender,
            event_type="p2p_transfer_sent",
            reference_type="transfer",
            reference_id=transfer.reference,
            recipient_email=transfer.sender.email,
            subject=f"P2P Transfer Sent - {transfer.reference}",
            html_template="emails/p2p/transfer_sent.html",
            txt_template="emails/p2p/transfer_sent.txt",
            context={
                "reference": transfer.reference,
                "amount": str(transfer.amount),
                "currency": transfer.currency,
                "recipient_username": transfer.recipient.username,
            },
        )

    @staticmethod
    def send_transfer_received_email(transfer) -> bool:
        return _dispatch_email(
            user=transfer.recipient,
            event_type="p2p_transfer_received",
            reference_type="transfer",
            reference_id=transfer.reference,
            recipient_email=transfer.recipient.email,
            subject=f"P2P Transfer Received - {transfer.reference}",
            html_template="emails/p2p/transfer_received.html",
            txt_template="emails/p2p/transfer_received.txt",
            context={
                "reference": transfer.reference,
                "amount": str(transfer.amount),
                "currency": transfer.currency,
                "sender_username": transfer.sender.username,
            },
        )

    @staticmethod
    def send_password_changed_email(user) -> bool:
        now_str = timezone.now().strftime("%Y-%m-%d %H:%M:%S UTC")
        return _dispatch_email(
            user=user,
            event_type="password_changed",
            reference_type="user_password",
            reference_id=now_str[:13],  # hourly reference window
            recipient_email=user.email,
            subject="Your Bitfuse password was changed",
            html_template="emails/auth/password_changed.html",
            txt_template="emails/auth/password_changed.txt",
            context={
                "timestamp": now_str,
            },
        )

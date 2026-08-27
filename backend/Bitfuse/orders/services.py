"""Order services — pricing, USDT locking, and Blnk settlement flows.

Architecture principle: the backend owns the business logic; Blnk owns the
financial truth. Every money movement goes through Blnk, and the Django
database records the business outcome.
"""

import random
import string
import time
from datetime import timedelta
from decimal import Decimal

from django.db import transaction as db_transaction
from django.utils import timezone
from django.conf import settings

from accounts.blnk_client import BlnkClient
from accounts.models import Notification, PlatformAccount, Rate, Transaction, Wallet
from accounts.services import ensure_frozen_balance, ensure_user_wallets

from .models import Order, OrderAuditLog, OrderSettlement
from .payment_methods import method_details, normalise_transaction_id, transaction_id_error

BUY_RATE_DEFAULT = Decimal("4220.00")  # MWK charged per 1 USDT bought
SELL_RATE_DEFAULT = Decimal("4050.00")  # MWK paid per 1 USDT sold
FEE_DEFAULT = Decimal("1.00")  # percent


class OrderError(Exception):
    """Business-rule violation in the order/payment workflow."""


def generate_reference():
    """Generate a unique reference number like BF-A3XK9M2P."""
    while True:
        reference = "BF-" + "".join(random.choices(string.ascii_uppercase + string.digits, k=8))
        if not Order.objects.filter(reference_number=reference).exists():
            return reference


def payment_reference_for(reference_number: str) -> str:
    """The compact form of the order reference the user types into the narration field."""
    return reference_number.replace("-", "")


def payment_window_minutes() -> int:
    return int(getattr(settings, "PAYMENT_WINDOW_MINUTES", 15))


def payment_expiry() -> timezone.datetime:
    """When a freshly created buy order's locked rate stops being honoured."""
    return timezone.now() + timedelta(minutes=payment_window_minutes())


def log_order_event(order, action, actor=None, from_status="", to_status="", note=""):
    return OrderAuditLog.objects.create(
        order=order,
        actor=actor,
        action=action,
        from_status=from_status,
        to_status=to_status,
        note=note,
    )


def notify(user, level, title, body, reference=""):
    return Notification.objects.create(
        user=user, level=level, title=title, body=body, reference=reference
    )


def payment_instructions(order):
    """Everything the buyer needs on the 'complete your payment' screen."""
    details = method_details(order.payment_method) or {}
    return {
        "payment_method": order.payment_method,
        "label": details.get("label", order.payment_method),
        "business_code": details.get("business_code", ""),
        "account_name": details.get("account_name", ""),
        "transaction_id_example": details.get("transaction_id_example", ""),
        "steps": details.get("instructions", []),
        "amount_to_pay": str(order.total_payable_mwk),
        "reference": order.payment_reference or payment_reference_for(order.reference_number),
        "expires_at": order.expires_at,
        "seconds_remaining": order.seconds_until_expiry,
    }


def _to_decimal(value) -> Decimal:
    """Robustly convert int/float/str/Decimal/None to Decimal.

    Prevents "can't multiply sequence by non-int" type errors when a client
    sends an amount as a string or when DRF passes through a raw value.
    """
    if value is None:
        return Decimal("0")
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def _current_rate() -> Rate:
    """Return the live Rate row with safe defaults (buy 4220 / sell 4050, fee 1%)."""
    return Rate.current()


def price_buy_order(usdt_amount):
    """Calculate what a buy order costs given the USDT amount.

    Returns (mwk_amount, fee_amount, rate, fee_percent), where mwk_amount is the
    value of the USDT at the locked rate and fee_amount is charged on top of it.
    The buyer pays mwk_amount + fee_amount (Order.total_payable_mwk).
    """
    usdt_amount = _to_decimal(usdt_amount)

    rate = _current_rate()
    buy_rate = _to_decimal(rate.buy_rate)
    buy_fee_percent = _to_decimal(rate.buy_fee_percent)
    mwk_amount = usdt_amount * buy_rate
    fee_amount = (mwk_amount * buy_fee_percent / Decimal(100)).quantize(Decimal("0.01"))
    return (
        mwk_amount.quantize(Decimal("0.01")),
        fee_amount,
        buy_rate,
        buy_fee_percent,
    )


def price_sell_order(usdt_amount):
    """Calculate MWK net payout for a sell order given the USDT amount.

    Returns (mwk_net_payout, fee_amount, rate, fee_percent).
    """
    usdt_amount = _to_decimal(usdt_amount)

    rate = _current_rate()
    sell_rate = _to_decimal(rate.sell_rate)
    sell_fee_percent = _to_decimal(rate.sell_fee_percent)
    mwk_gross = usdt_amount * sell_rate
    fee_amount = (mwk_gross * sell_fee_percent / Decimal(100)).quantize(Decimal("0.01"))
    net_payout = mwk_gross - fee_amount
    return (
        net_payout.quantize(Decimal("0.01")),
        fee_amount,
        sell_rate,
        sell_fee_percent,
    )


def lock_sell_order(order):
    """Lock the seller's USDT into the platform's frozen escrow balance.

    Called at order creation: user USDT wallet → platform frozen balance.
    This protects the seller and guarantees the funds are available on completion.
    """
    client = BlnkClient()
    platform = ensure_frozen_balance()
    _, usdt_wallet = ensure_user_wallets(order.user)

    precision_usdt = settings.CURRENCY_PRECISION["USDT"]

    txn = client.create_transaction(
        amount=int(order.usdt_amount * precision_usdt),
        currency="USDT",
        precision=precision_usdt,
        reference=f"{order.reference_number}-lock",
        source=usdt_wallet.blnk_balance_id,
        destination=platform.usdt_frozen_balance_id,
        description=f"USDT locked for sell order {order.reference_number}",
    )
    order.blnk_transaction_refs.append(txn["transaction_id"])
    order.save(update_fields=["blnk_transaction_refs"])


def method_label(payment_method: str) -> str:
    """Human-readable rail name used in transaction history ("airtel_money" → "Airtel Money")."""
    details = method_details(payment_method)
    return details["label"] if details else "Airtel Money"


def _write_history(order, method: str, phone: str, fee_amount: Decimal):
    """Write an immutable application-database history record for an order."""
    Transaction.objects.create(
        user=order.user,
        type="Buy" if order.order_type == "buy" else "Sell",
        amount_usdt=order.usdt_amount,
        amount_mwk=order.total_payable_mwk if order.order_type == "buy" else order.mwk_amount,
        rate=order.rate,
        fee=fee_amount,
        status="Completed",
        method=method,
        phone=phone,
        reference=order.reference_number,
    )


def expire_order_if_due(order):
    """Move a buy order to EXPIRED once its locked rate window has elapsed."""
    if not order.is_expired:
        return order

    previous = order.status
    order.status = Order.EXPIRED
    order.save(update_fields=["status"])
    log_order_event(
        order, "expired", from_status=previous, to_status=order.status,
        note="Payment window elapsed before a transaction ID was submitted.",
    )
    notify(
        order.user, "error", "Buy order expired",
        f"Order {order.reference_number} expired before payment was confirmed. "
        "If you already paid, contact Bitfuse support with your transaction ID.",
        order.reference_number,
    )
    return order


def expire_stale_orders():
    """Expire every buy order whose payment window has passed. Returns the count."""
    stale = Order.objects.filter(status=Order.AWAITING_PAYMENT, expires_at__lt=timezone.now())
    count = 0
    for order in stale:
        expire_order_if_due(order)
        count += 1
    return count


def submit_payment(order, user, transaction_id):
    """Record the mobile money transaction ID the buyer says they paid with.

    This never credits anything — it only moves the order into the admin's
    verification queue. A transaction ID is a claim, not proof of payment.
    """
    if order.user_id != user.id:
        raise OrderError("This order does not belong to you.")
    if order.order_type != "buy":
        raise OrderError("Only buy orders are paid by mobile money.")

    expire_order_if_due(order)

    if order.status == Order.EXPIRED:
        raise OrderError(
            "This order has expired. Create a new order — if you already paid, contact support."
        )
    if order.status not in Order.PAYABLE_STATUSES:
        raise OrderError("This order is no longer awaiting payment.")

    error = transaction_id_error(transaction_id)
    if error:
        raise OrderError(error)

    normalised = normalise_transaction_id(transaction_id)
    if Order.objects.filter(payment_transaction_id=normalised).exclude(pk=order.pk).exists():
        raise OrderError(
            "This transaction has already been submitted for another order. "
            "If you believe this is an error, contact Bitfuse support."
        )

    previous = order.status
    order.payment_transaction_id = normalised
    order.payment_submitted_at = timezone.now()
    order.status = Order.PAYMENT_SUBMITTED
    order.save(update_fields=["payment_transaction_id", "payment_submitted_at", "status"])

    log_order_event(
        order, "payment_submitted", actor=user, from_status=previous, to_status=order.status,
        note=f"Transaction ID {normalised}",
    )
    notify(
        order.user, "pending", "Payment submitted",
        f"We've received your payment details for order {order.reference_number}. "
        "Your payment is being verified.",
        order.reference_number,
    )
    return order


def start_review(order, admin):
    """Claim an order for review so two admins don't verify the same payment."""
    if order.status != Order.PAYMENT_SUBMITTED:
        return order

    previous = order.status
    order.status = Order.UNDER_REVIEW
    order.reviewed_by = admin
    order.save(update_fields=["status", "reviewed_by"])
    log_order_event(order, "review_started", actor=admin, from_status=previous, to_status=order.status)
    return order


def flag_payment_mismatch(order, admin, received_amount, note=""):
    """The payment exists but does not match the order — park it for a human decision."""
    if order.status not in Order.REVIEWABLE_STATUSES:
        raise OrderError("This order is not awaiting payment verification.")

    received = _to_decimal(received_amount)
    previous = order.status
    order.received_mwk_amount = received
    order.status = Order.PAYMENT_MISMATCH
    order.reviewed_by = admin
    order.reviewed_at = timezone.now()
    order.save(update_fields=["received_mwk_amount", "status", "reviewed_by", "reviewed_at"])

    difference = received - order.total_payable_mwk
    log_order_event(
        order, "payment_mismatch", actor=admin, from_status=previous, to_status=order.status,
        note=note or f"Expected {order.total_payable_mwk} MWK, received {received} MWK.",
    )
    notify(
        order.user, "error", "Payment amount does not match",
        f"Order {order.reference_number} expected MWK {order.total_payable_mwk} but we found "
        f"MWK {received} (difference MWK {difference}). Our team will contact you.",
        order.reference_number,
    )
    return order


def reject_payment(order, admin, reason):
    """Reject an unverifiable payment claim."""
    if order.status not in Order.REVIEWABLE_STATUSES:
        raise OrderError("This order is not awaiting payment verification.")
    if not reason:
        raise OrderError("A rejection reason is required.")

    previous = order.status
    order.status = Order.REJECTED
    order.rejection_reason = reason
    order.reviewed_by = admin
    order.reviewed_at = timezone.now()
    order.save(update_fields=["status", "rejection_reason", "reviewed_by", "reviewed_at"])

    log_order_event(
        order, "payment_rejected", actor=admin, from_status=previous, to_status=order.status,
        note=reason,
    )
    notify(
        order.user, "error", "Payment could not be verified",
        f"We couldn't verify your payment for order {order.reference_number}. Reason: {reason}",
        order.reference_number,
    )
    return order


def verify_payment(order, admin, received_amount=None, note=""):
    """Confirm the mobile money payment arrived, then settle the order.

    The admin only confirms that the payment is real: the USDT amount comes from
    the order itself, so approving cannot change what the buyer receives.
    """
    if order.status == Order.COMPLETED:
        return order
    if order.status not in Order.REVIEWABLE_STATUSES:
        raise OrderError("This order is not awaiting payment verification.")

    if received_amount is None:
        received = order.total_payable_mwk
    else:
        received = _to_decimal(received_amount)
        if received != order.total_payable_mwk:
            return flag_payment_mismatch(order, admin, received, note)

    previous = order.status
    order.status = Order.PAYMENT_VERIFIED
    order.received_mwk_amount = received
    order.reviewed_by = admin
    order.reviewed_at = timezone.now()
    order.save(update_fields=["status", "received_mwk_amount", "reviewed_by", "reviewed_at"])
    log_order_event(
        order, "payment_verified", actor=admin, from_status=previous, to_status=order.status,
        note=note,
    )

    return complete_buy_order(order, admin=admin)


def complete_buy_order(order, admin=None):
    """Settle a verified buy order on the ledger. Safe to call more than once.

    Leg 1: Record the Kwacha (amount + fee) that arrived via mobile money into the
           platform's MWK float (from external contra → platform MWK float).
    Leg 2: Release USDT from the platform float to the buyer's USDT wallet.

    Idempotency: the settlement row is created first inside the transaction, so a
    duplicated approval finds the existing row and returns without touching Blnk.
    """
    platform = PlatformAccount.objects.first()
    if not platform:
        raise RuntimeError("PlatformAccount not found. Run: python manage.py init_platform_account")

    with db_transaction.atomic():
        locked = Order.objects.select_for_update().get(pk=order.pk)
        if OrderSettlement.objects.filter(order=locked).exists():
            return locked

        settlement = OrderSettlement.objects.create(
            order=locked,
            usdt_credited=locked.usdt_amount,
            mwk_received=locked.received_mwk_amount or locked.total_payable_mwk,
            settled_by=admin,
        )

        locked.status = Order.SETTLING
        locked.save(update_fields=["status"])

        client = BlnkClient()
        _, usdt_wallet = ensure_user_wallets(locked.user)
        precision_mwk = settings.CURRENCY_PRECISION["MWK"]
        precision_usdt = settings.CURRENCY_PRECISION["USDT"]

        try:
            # Leg 1: cash-in from mobile money (amount + fee)
            txn1 = client.create_transaction(
                amount=int(locked.total_payable_mwk * precision_mwk),
                currency="MWK",
                precision=precision_mwk,
                reference=f"{locked.reference_number}-cash-in",
                source=platform.mwk_external_contra_id,
                destination=platform.mwk_float_balance_id,
                description=f"Mobile money payment received for order {locked.reference_number}",
            )

            # Leg 2: release USDT to buyer
            txn2 = client.create_transaction(
                amount=int(locked.usdt_amount * precision_usdt),
                currency="USDT",
                precision=precision_usdt,
                reference=f"{locked.reference_number}-usdt-out",
                source=platform.usdt_float_balance_id,
                destination=usdt_wallet.blnk_balance_id,
                description=f"USDT released for order {locked.reference_number}",
            )

            # Verify Blnk transaction lifecycle status
            txn1_id = txn1.get("transaction_id")
            txn2_id = txn2.get("transaction_id")
            status1 = txn1.get("status", "QUEUED")
            status2 = txn2.get("status", "QUEUED")

            # Poll/verify until terminal state if queued
            if status2 in ["QUEUED", "INFLIGHT"]:
                for _ in range(5):
                    time.sleep(0.3)
                    poll_data = client.get_transaction(txn2_id)
                    status2 = poll_data.get("status", status2)
                    if status2 not in ["QUEUED", "INFLIGHT"]:
                        break

            # If transaction failed or rejected on Blnk, raise OrderError so order is not marked completed
            if status2 in ["REJECTED", "FAILED"]:
                settlement.delete()
                locked.status = Order.PAYMENT_VERIFIED
                locked.save(update_fields=["status"])
                raise OrderError(f"Blnk transaction {txn2_id} failed with status: {status2}")

        except Exception as exc:
            # If Blnk transaction creation fails, rollback DB transaction so order is NOT marked as completed
            if OrderSettlement.objects.filter(pk=settlement.pk).exists():
                settlement.delete()
            locked.status = Order.PAYMENT_VERIFIED
            locked.save(update_fields=["status"])
            raise OrderError(f"Failed to credit USDT in Blnk ledger: {str(exc)}")

        refs = [txn1_id, txn2_id]
        settlement.blnk_transaction_refs = refs
        settlement.save(update_fields=["blnk_transaction_refs"])

        locked.status = Order.COMPLETED
        locked.completed_at = timezone.now()
        locked.blnk_transaction_refs = refs
        locked.save(update_fields=["status", "completed_at", "blnk_transaction_refs"])

        _write_history(locked, method_label(locked.payment_method), locked.phone or "", locked.fee_amount)
        log_order_event(
            locked, "settled", actor=admin, from_status=Order.SETTLING, to_status=locked.status,
            note=f"Blnk transactions: {', '.join(refs)}",
        )

    notify(
        locked.user, "success", "Payment confirmed",
        f"Your payment for order {locked.reference_number} has been verified. "
        f"{locked.usdt_amount} USDT has been credited to your Bitfuse account.",
        locked.reference_number,
    )
    order.refresh_from_db()
    return locked


def complete_sell_order(order):
    """Call once an admin has confirmed the payout was sent to the seller.

    The seller's USDT is already locked in the platform frozen escrow (lock_sell_order).

    Leg 1: Move USDT from frozen escrow → platform USDT float.
    Leg 2: Record the MWK net payout (platform MWK float → external contra).
    """
    client = BlnkClient()
    platform = PlatformAccount.objects.first()
    if not platform:
        raise RuntimeError("PlatformAccount not found. Run: python manage.py init_platform_account")

    ensure_frozen_balance()

    precision_mwk = settings.CURRENCY_PRECISION["MWK"]
    precision_usdt = settings.CURRENCY_PRECISION["USDT"]

    # Leg 1: frozen escrow → platform USDT float
    txn1 = client.create_transaction(
        amount=int(order.usdt_amount * precision_usdt),
        currency="USDT",
        precision=precision_usdt,
        reference=f"{order.reference_number}-escrow-out",
        source=platform.usdt_frozen_balance_id,
        destination=platform.usdt_float_balance_id,
        description=f"Frozen USDT released for order {order.reference_number}",
    )

    # Leg 2: MWK net payout (platform MWK float → external contra)
    txn2 = client.create_transaction(
        amount=int(order.mwk_amount * precision_mwk),
        currency="MWK",
        precision=precision_mwk,
        reference=f"{order.reference_number}-cash-out",
        source=platform.mwk_float_balance_id,
        destination=platform.mwk_external_contra_id,
        description=f"Mobile money payout for order {order.reference_number}",
    )

    order.status = "completed"
    order.completed_at = timezone.now()
    order.blnk_transaction_refs = [txn1["transaction_id"], txn2["transaction_id"]]
    order.save()

    _write_history(order, method_label(order.payment_method), order.phone or "", order.fee_amount)

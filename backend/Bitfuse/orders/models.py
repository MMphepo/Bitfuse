import uuid
from django.db import models
from django.conf import settings
from django.utils import timezone


class Order(models.Model):
    ORDER_TYPE = [("buy", "Buy"), ("sell", "Sell")]

    AWAITING_PAYMENT = "awaiting_payment"
    PAYMENT_SUBMITTED = "payment_submitted"
    UNDER_REVIEW = "under_review"
    PAYMENT_MISMATCH = "payment_mismatch"
    PAYMENT_VERIFIED = "payment_verified"
    SETTLING = "settling"
    COMPLETED = "completed"
    REJECTED = "rejected"
    EXPIRED = "expired"
    AWAITING_DEPOSIT = "awaiting_deposit"
    CANCELLED = "cancelled"

    STATUS = [
        (AWAITING_PAYMENT, "Awaiting Payment"),  # buy: waiting for user's mobile money payment
        (PAYMENT_SUBMITTED, "Payment Submitted"),  # buy: user supplied a mobile money transaction ID
        (UNDER_REVIEW, "Under Review"),  # buy: an admin is checking the mobile money record
        (PAYMENT_MISMATCH, "Payment Mismatch"),  # buy: payment found but does not match the order
        (PAYMENT_VERIFIED, "Payment Verified"),  # buy: payment confirmed, settlement pending
        (SETTLING, "Settling"),  # buy: ledger settlement in progress
        (COMPLETED, "Completed"),
        (REJECTED, "Rejected"),
        (EXPIRED, "Expired"),
        (AWAITING_DEPOSIT, "Awaiting Deposit"),  # sell: waiting for user's on-chain USDT deposit
        (CANCELLED, "Cancelled"),
    ]

    # Statuses a buy order can still receive (or re-receive) a payment submission in.
    PAYABLE_STATUSES = {AWAITING_PAYMENT, PAYMENT_MISMATCH}
    # Statuses an admin can act on.
    REVIEWABLE_STATUSES = {PAYMENT_SUBMITTED, UNDER_REVIEW, PAYMENT_MISMATCH}
    # Statuses that can no longer change.
    TERMINAL_STATUSES = {COMPLETED, REJECTED, EXPIRED, CANCELLED}

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    reference_number = models.CharField(max_length=20, unique=True)
    payment_reference = models.CharField(
        max_length=20, blank=True, default="",
        help_text="Short reference the user quotes in the mobile money narration.",
    )
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="orders")
    order_type = models.CharField(max_length=4, choices=ORDER_TYPE)

    mwk_amount = models.DecimalField(max_digits=14, decimal_places=2)
    usdt_amount = models.DecimalField(max_digits=14, decimal_places=6)
    rate = models.DecimalField(max_digits=10, decimal_places=2)
    fee_percent = models.DecimalField(max_digits=5, decimal_places=2)
    fee_amount = models.DecimalField(max_digits=14, decimal_places=2, default=0)

    payment_method = models.CharField(max_length=30, blank=True, default="")
    phone = models.CharField(max_length=30, blank=True, default="")

    status = models.CharField(max_length=20, choices=STATUS, default=AWAITING_PAYMENT)
    blnk_transaction_refs = models.JSONField(default=list, blank=True)

    # Mobile money payment tracking (buy orders)
    expires_at = models.DateTimeField(
        null=True, blank=True, help_text="When the locked rate / payment window expires."
    )
    payment_transaction_id = models.CharField(
        max_length=64, blank=True, default="",
        help_text="Mobile money transaction ID supplied by the user.",
    )
    payment_submitted_at = models.DateTimeField(null=True, blank=True)
    received_mwk_amount = models.DecimalField(
        max_digits=14, decimal_places=2, null=True, blank=True,
        help_text="Amount the admin actually found on the mobile money statement.",
    )
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="reviewed_orders",
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    rejection_reason = models.TextField(blank=True, default="")

    created_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["payment_transaction_id"],
                condition=~models.Q(payment_transaction_id=""),
                name="unique_payment_transaction_id",
            ),
            models.UniqueConstraint(
                fields=["payment_reference"],
                condition=~models.Q(payment_reference=""),
                name="unique_payment_reference",
            ),
        ]

    def __str__(self):
        return f"{self.reference_number} — {self.order_type} — {self.status}"

    @property
    def total_payable_mwk(self):
        """What the buyer must send via mobile money (order amount + platform fee)."""
        return self.mwk_amount + self.fee_amount

    @property
    def is_expired(self):
        return (
            self.expires_at is not None
            and self.status == self.AWAITING_PAYMENT
            and timezone.now() >= self.expires_at
        )

    @property
    def seconds_until_expiry(self):
        if self.expires_at is None:
            return None
        return max(0, int((self.expires_at - timezone.now()).total_seconds()))


class OrderSettlement(models.Model):
    """One settlement per order — the database-level guarantee against double crediting.

    The OneToOne constraint means a second settlement attempt for the same order
    cannot create a second ledger movement, no matter how many times an admin
    clicks approve.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    order = models.OneToOneField(Order, on_delete=models.CASCADE, related_name="settlement")
    usdt_credited = models.DecimalField(max_digits=14, decimal_places=6)
    mwk_received = models.DecimalField(max_digits=14, decimal_places=2)
    blnk_transaction_refs = models.JSONField(default=list, blank=True)
    settled_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="settlements",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Settlement for {self.order.reference_number}"


class OrderAuditLog(models.Model):
    """Append-only trail of every status change and admin decision on an order."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="audit_logs")
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="order_audit_logs",
    )
    action = models.CharField(max_length=40)
    from_status = models.CharField(max_length=20, blank=True, default="")
    to_status = models.CharField(max_length=20, blank=True, default="")
    note = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]

    def __str__(self):
        return f"{self.order.reference_number}: {self.action}"

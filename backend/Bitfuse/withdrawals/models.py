import uuid
from decimal import Decimal
from django.db import models
from django.conf import settings


class WithdrawalConfig(models.Model):
    """Database-driven withdrawal settings. Singleton pattern."""
    withdrawal_fee = models.DecimalField(
        max_digits=10, decimal_places=2, default=Decimal("0.50"),
        help_text="Flat fee in USDT charged for each TRON withdrawal."
    )
    min_usdt_withdrawal = models.DecimalField(
        max_digits=10, decimal_places=2, default=Decimal("10.00"),
        help_text="Minimum allowed USDT withdrawal amount."
    )
    max_usdt_withdrawal = models.DecimalField(
        max_digits=10, decimal_places=2, default=Decimal("5000.00"),
        help_text="Maximum allowed USDT withdrawal amount."
    )
    withdrawals_frozen = models.BooleanField(
        default=False,
        help_text="Global switch to immediately freeze all external withdrawals."
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Withdrawal Configuration"
        verbose_name_plural = "Withdrawal Configuration"

    @classmethod
    def get_current(cls):
        """Fetch the current configuration, creating a default if none exists."""
        config_obj = cls.objects.first()
        if not config_obj:
            config_obj = cls.objects.create(
                withdrawal_fee=Decimal("0.50"),
                min_usdt_withdrawal=Decimal("10.00"),
                max_usdt_withdrawal=Decimal("5000.00"),
                withdrawals_frozen=False
            )
        return config_obj

    def __str__(self):
        status = "FROZEN" if self.withdrawals_frozen else "ACTIVE"
        return f"Withdrawal Config ({status}, Fee: {self.withdrawal_fee} USDT)"


class Withdrawal(models.Model):
    STATUS_CHOICES = [
        ("PENDING", "Pending"),
        ("PROCESSING", "Processing"),
        ("BROADCAST", "Broadcast"),
        ("CONFIRMED", "Confirmed"),
        ("FAILED", "Failed"),
        ("CANCELLED", "Cancelled"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="withdrawals"
    )
    asset = models.CharField(max_length=10, default="USDT")
    network = models.CharField(max_length=20, default="TRON")
    amount = models.DecimalField(max_digits=18, decimal_places=6)
    fee = models.DecimalField(max_digits=18, decimal_places=6)
    net_amount = models.DecimalField(max_digits=18, decimal_places=6)
    destination_address = models.CharField(max_length=128)
    status = models.CharField(
        max_length=20, choices=STATUS_CHOICES, default="PENDING", db_index=True
    )
    transaction_hash = models.CharField(
        max_length=256, blank=True, null=True, unique=True, db_index=True
    )
    failure_reason = models.TextField(blank=True, null=True)
    blnk_transaction_refs = models.JSONField(default=list, blank=True)

    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)
    broadcast_at = models.DateTimeField(blank=True, null=True)
    confirmed_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["user", "status"]),
        ]

    def __str__(self):
        return f"Withdrawal {self.id} — {self.amount} USDT to {self.destination_address[:8]}... ({self.status})"

import uuid
from decimal import Decimal

from django.contrib.auth.models import AbstractUser
from django.db import models


class User(AbstractUser):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    phone_number = models.CharField(max_length=20, unique=True, default="")
    national_id_number = models.CharField(max_length=50, blank=True, null=True)
    location = models.CharField(max_length=100, blank=True)
    profile_picture = models.URLField(blank=True, null=True)

    email_verified = models.BooleanField(default=False)
    phone_verified = models.BooleanField(default=False)

    VERIFICATION_CHOICES = [
        ("unverified", "Unverified"),
        ("pending", "Pending KYC Review"),
        ("verified", "Verified"),
        ("rejected", "Rejected"),
    ]
    verification_status = models.CharField(
        max_length=20, choices=VERIFICATION_CHOICES, default="unverified"
    )

    blnk_wallet_balance_id = models.CharField(max_length=100, blank=True, null=True)
    blnk_ledger_id = models.CharField(max_length=100, blank=True, null=True)

    def __str__(self):
        return self.username


class Wallet(models.Model):
    CURRENCY_CHOICES = [("MWK", "MWK"), ("USDT", "USDT")]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="wallets")
    currency = models.CharField(max_length=10, choices=CURRENCY_CHOICES)
    blnk_balance_id = models.CharField(max_length=100)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("user", "currency")

    def __str__(self):
        return f"{self.user.username} — {self.currency}"


class Transaction(models.Model):
    TX_TYPE_CHOICES = [
        ("Buy", "Buy"),
        ("Sell", "Sell"),
    ]
    TX_STATUS_CHOICES = [
        ("Completed", "Completed"),
        ("Pending", "Pending"),
        ("Cancelled", "Cancelled"),
        ("Disputed", "Disputed"),
    ]
    METHOD_CHOICES = [
        ("Airtel Money", "Airtel Money"),
        ("TNM Mpamba", "TNM Mpamba"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="transactions")
    type = models.CharField(max_length=10, choices=TX_TYPE_CHOICES)
    amount_usdt = models.DecimalField(max_digits=18, decimal_places=6)
    amount_mwk = models.DecimalField(max_digits=18, decimal_places=2)
    rate = models.DecimalField(max_digits=10, decimal_places=2)
    fee = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    status = models.CharField(max_length=10, choices=TX_STATUS_CHOICES, default="Pending")
    method = models.CharField(max_length=20, choices=METHOD_CHOICES)
    phone = models.CharField(max_length=20)
    reference = models.CharField(max_length=50, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.type} {self.amount_usdt} USDT — {self.reference}"


class Notification(models.Model):
    """In-app notification shown to a user as their order moves through the flow."""

    LEVEL_CHOICES = [
        ("info", "Info"),
        ("pending", "Pending"),
        ("success", "Success"),
        ("error", "Error"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="notifications")
    level = models.CharField(max_length=10, choices=LEVEL_CHOICES, default="info")
    title = models.CharField(max_length=120)
    body = models.TextField(blank=True, default="")
    reference = models.CharField(max_length=50, blank=True, default="")
    read = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.user.username}: {self.title}"


class PlatformAccount(models.Model):
    """Singleton-style row holding the platform's own Blnk balance IDs."""
    ledger_id = models.CharField(max_length=100)
    mwk_float_balance_id = models.CharField(max_length=100)
    usdt_float_balance_id = models.CharField(max_length=100)
    mwk_external_contra_id = models.CharField(max_length=100)
    usdt_external_contra_id = models.CharField(max_length=100)  # represents USDT in/out via the blockchain
    usdt_frozen_balance_id = models.CharField(max_length=100, blank=True, default="")  # escrow for locked sell USDT

    class Meta:
        verbose_name = "Platform Account"
        verbose_name_plural = "Platform Account"

    def __str__(self):
        return f"Platform Account (ledger: {self.ledger_id})"


class Rate(models.Model):
    buy_rate = models.DecimalField(max_digits=10, decimal_places=2, help_text="MWK charged per 1 USDT bought")
    sell_rate = models.DecimalField(max_digits=10, decimal_places=2, help_text="MWK paid per 1 USDT sold")
    buy_fee_percent = models.DecimalField(max_digits=5, decimal_places=2, default=1.0)
    sell_fee_percent = models.DecimalField(max_digits=5, decimal_places=2, default=1.0)
    updated_at = models.DateTimeField(auto_now=True)

    @classmethod
    def current(cls):
        """Return the latest rate, or a safe default (buy 4220 / sell 4050, fee 1%) if none exist."""
        try:
            return cls.objects.latest("updated_at")
        except cls.DoesNotExist:
            return cls(
                buy_rate=Decimal("4220.00"),
                sell_rate=Decimal("4050.00"),
                buy_fee_percent=Decimal("1.00"),
                sell_fee_percent=Decimal("1.00"),
            )

    def __str__(self):
        return f"Buy: {self.buy_rate} / Sell: {self.sell_rate}"

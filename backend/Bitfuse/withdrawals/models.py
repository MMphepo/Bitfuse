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


class WithdrawalNetworkConfig(models.Model):
    """Network-specific configuration for cryptocurrency withdrawals/deposits."""
    network = models.CharField(max_length=20, db_index=True)  # e.g., "TRON", "BSC"
    asset = models.CharField(max_length=10, default="USDT", db_index=True)
    withdrawal_fee = models.DecimalField(
        max_digits=18, decimal_places=6, default=Decimal("0.50")
    )
    min_withdrawal = models.DecimalField(
        max_digits=18, decimal_places=6, default=Decimal("10.00")
    )
    max_withdrawal = models.DecimalField(
        max_digits=18, decimal_places=6, default=Decimal("5000.00")
    )
    withdrawals_enabled = models.BooleanField(default=True)
    withdrawals_frozen = models.BooleanField(default=False)
    confirmations_required = models.PositiveIntegerField(default=12)
    contract_address = models.CharField(max_length=128, blank=True, null=True)
    decimals = models.PositiveIntegerField(default=18)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ("network", "asset")
        verbose_name = "Withdrawal Network Configuration"
        verbose_name_plural = "Withdrawal Network Configurations"

    @classmethod
    def get_for_network(cls, network: str, asset: str = "USDT"):
        canonical_net = network.strip().upper()
        if canonical_net in ["BEP20", "BNB"]:
            canonical_net = "BSC"
        elif canonical_net == "TRC20":
            canonical_net = "TRON"

        config_obj, created = cls.objects.get_or_create(
            network=canonical_net,
            asset=asset.strip().upper(),
            defaults={
                "withdrawal_fee": Decimal("0.50") if canonical_net == "TRON" else Decimal("1.00"),
                "min_withdrawal": Decimal("10.00"),
                "max_withdrawal": Decimal("5000.00"),
                "withdrawals_enabled": True,
                "withdrawals_frozen": False,
                "confirmations_required": 12 if canonical_net == "BSC" else 1,
                "decimals": 18 if canonical_net == "BSC" else 6,
            }
        )
        return config_obj

    def __str__(self):
        status = "FROZEN" if self.withdrawals_frozen or not self.withdrawals_enabled else "ACTIVE"
        return f"{self.asset} / {self.network} Config ({status}, Fee: {self.withdrawal_fee})"


class WithdrawalAddress(models.Model):
    """User saved withdrawal addresses bound explicitly to a specific network."""
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="withdrawal_addresses"
    )
    address = models.CharField(max_length=128)
    network = models.CharField(max_length=20)  # "TRON" or "BSC"
    label = models.CharField(max_length=100, blank=True)
    verified = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    last_used_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        unique_together = ("user", "address", "network")
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.user} - {self.network}: {self.address[:10]}..."


class BscNonceTracker(models.Model):
    """Centralized database lock-backed transaction nonce manager for BSC hot wallet."""
    wallet_address = models.CharField(max_length=128, unique=True, db_index=True)
    next_nonce = models.BigIntegerField(default=0)
    last_reconciled_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"NonceTracker for {self.wallet_address[:10]}... next_nonce={self.next_nonce}"


class DepositRecord(models.Model):
    """Auditable record for incoming on-chain deposits."""
    STATUS_CHOICES = [
        ("PENDING", "Pending"),
        ("CONFIRMED", "Confirmed"),
        ("CREDITED", "Credited"),
        ("FAILED", "Failed"),
    ]

    event_id = models.CharField(max_length=256, unique=True, db_index=True) # chain_id:tx_hash:log_index
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="deposits"
    )
    network = models.CharField(max_length=20, default="BSC")
    asset = models.CharField(max_length=10, default="USDT")
    tx_hash = models.CharField(max_length=256, db_index=True)
    log_index = models.IntegerField(default=0)
    from_address = models.CharField(max_length=128)
    to_address = models.CharField(max_length=128)
    amount = models.DecimalField(max_digits=18, decimal_places=6)
    block_number = models.BigIntegerField()
    confirmations = models.IntegerField(default=0)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="PENDING", db_index=True)
    blnk_transaction_id = models.CharField(max_length=128, blank=True, null=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"Deposit {self.event_id} - {self.amount} {self.asset} on {self.network} ({self.status})"


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

import uuid
from django.db import models
from django.conf import settings


class Order(models.Model):
    ORDER_TYPE = [("buy", "Buy"), ("sell", "Sell")]
    STATUS = [
        ("awaiting_payment", "Awaiting Payment"),  # buy: waiting for user's mobile money payment
        ("awaiting_deposit", "Awaiting Deposit"),  # sell: waiting for user's on-chain USDT deposit
        ("completed", "Completed"),
        ("cancelled", "Cancelled"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    reference_number = models.CharField(max_length=20, unique=True)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="orders")
    order_type = models.CharField(max_length=4, choices=ORDER_TYPE)

    mwk_amount = models.DecimalField(max_digits=14, decimal_places=2)
    usdt_amount = models.DecimalField(max_digits=14, decimal_places=6)
    rate = models.DecimalField(max_digits=10, decimal_places=2)
    fee_percent = models.DecimalField(max_digits=5, decimal_places=2)
    fee_amount = models.DecimalField(max_digits=14, decimal_places=2, default=0)

    payment_method = models.CharField(max_length=30, blank=True, default="")
    phone = models.CharField(max_length=30, blank=True, default="")

    status = models.CharField(max_length=20, choices=STATUS, default="awaiting_payment")
    blnk_transaction_refs = models.JSONField(default=list, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"{self.reference_number} — {self.order_type} — {self.status}"

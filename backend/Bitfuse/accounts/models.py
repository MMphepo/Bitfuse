import uuid

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

    def __str__(self):
        return self.username

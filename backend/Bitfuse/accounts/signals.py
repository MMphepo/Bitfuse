from django.contrib.auth import get_user_model
from django.db.models.signals import post_save
from django.dispatch import receiver

from kyc.models import KYCSubmission

User = get_user_model()


@receiver(post_save, sender=KYCSubmission)
def sync_kyc_status_to_user(sender, instance, created, **kwargs):
    """
    When a KYCSubmission is saved with status 'approved' or 'rejected',
    update the associated User's verification_status field.
    """
    user = instance.user
    if instance.status == "approved":
        if user.verification_status != "verified":
            user.verification_status = "verified"
            user.save(update_fields=["verification_status"])
            print(f"[signal] User {user.username} KYC approved → verification_status=verified")
    elif instance.status == "rejected":
        if user.verification_status != "rejected":
            user.verification_status = "rejected"
            user.save(update_fields=["verification_status"])
            print(f"[signal] User {user.username} KYC rejected → verification_status=rejected")
    elif instance.status == "pending":
        if user.verification_status != "pending":
            user.verification_status = "pending"
            user.save(update_fields=["verification_status"])
            print(f"[signal] User {user.username} KYC submitted → verification_status=pending")


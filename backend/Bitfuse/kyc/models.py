import uuid
from django.db import models
from django.conf import settings


class KYCSubmission(models.Model):
    STATUS_CHOICES = [
        ("pending", "Pending Review"),
        ("approved", "Approved"),
        ("rejected", "Rejected"),
        ("resubmission_required", "Resubmission Required"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="kyc_submission"
    )
    id_front = models.FileField(upload_to="kyc/id_front/")
    id_back = models.FileField(upload_to="kyc/id_back/")
    selfie = models.FileField(upload_to="kyc/selfie/")

    status = models.CharField(max_length=30, choices=STATUS_CHOICES, default="pending")
    rejection_reason = models.CharField(max_length=255, blank=True)

    submitted_at = models.DateTimeField(auto_now_add=True)
    reviewed_at = models.DateTimeField(null=True, blank=True)
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name="kyc_reviews"
    )

    class Meta:
        ordering = ["-submitted_at"]

    def __str__(self):
        return f"KYC for {self.user.username} ({self.status})"


class KYCReviewAction(models.Model):
    ACTION_CHOICES = [
        ("approved", "Approved"),
        ("rejected", "Rejected"),
        ("requested_resubmission", "Requested Resubmission"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    submission = models.ForeignKey(
        KYCSubmission, on_delete=models.CASCADE, related_name="audit_logs"
    )
    admin = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="kyc_review_actions"
    )
    action = models.CharField(max_length=30, choices=ACTION_CHOICES)
    previous_status = models.CharField(max_length=30)
    new_status = models.CharField(max_length=30)
    reason = models.CharField(max_length=255, blank=True)
    note = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        admin_identifier = self.admin.username if self.admin else "System"
        return f"[{self.action}] KYC {self.submission_id} by {admin_identifier} at {self.created_at}"

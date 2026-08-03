from django.contrib import admin, messages
from django.utils import timezone

from .models import KYCSubmission


@admin.action(description="Approve selected KYC submissions")
def approve_kyc(modeladmin, request, queryset):
    for submission in queryset.filter(status="pending"):
        submission.status = "approved"
        submission.reviewed_at = timezone.now()
        submission.reviewed_by = request.user
        submission.save(update_fields=["status", "reviewed_at", "reviewed_by"])
        messages.success(request, f"KYC for {submission.user.username} approved.")


@admin.action(description="Reject selected KYC submissions")
def reject_kyc(modeladmin, request, queryset):
    for submission in queryset.filter(status="pending"):
        submission.status = "rejected"
        submission.reviewed_at = timezone.now()
        submission.reviewed_by = request.user
        submission.rejection_reason = "Rejected by admin review."
        submission.save(update_fields=["status", "reviewed_at", "reviewed_by", "rejection_reason"])
        messages.warning(request, f"KYC for {submission.user.username} rejected.")


class KYCSubmissionAdmin(admin.ModelAdmin):
    list_display = ["user", "status", "submitted_at", "reviewed_at", "reviewed_by"]
    list_filter = ["status"]
    search_fields = ["user__username", "user__email"]
    actions = [approve_kyc, reject_kyc]
    readonly_fields = ["submitted_at", "reviewed_at", "reviewed_by"]

    def save_model(self, request, obj, form, change):
        if obj.status in ("approved", "rejected") and not obj.reviewed_by:
            obj.reviewed_by = request.user
            obj.reviewed_at = timezone.now()
        super().save_model(request, obj, form, change)


admin.site.register(KYCSubmission, KYCSubmissionAdmin)


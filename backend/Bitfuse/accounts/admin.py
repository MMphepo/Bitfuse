from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from .models import EmailNotification, Notification, PlatformAccount, Rate, User


admin.site.register(User, UserAdmin)
admin.site.register(PlatformAccount)
admin.site.register(Rate)


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = ["user", "level", "title", "reference", "read", "created_at"]
    list_filter = ["level", "read"]
    search_fields = ["user__username", "reference", "title"]


@admin.register(EmailNotification)
class EmailNotificationAdmin(admin.ModelAdmin):
    list_display = ["event_type", "recipient", "status", "attempt_count", "sent_at", "created_at"]
    list_filter = ["event_type", "status"]
    search_fields = ["recipient", "reference_id", "subject"]
    readonly_fields = [
        "id", "user", "event_type", "reference_type", "reference_id",
        "recipient", "subject", "status", "attempt_count", "sent_at",
        "created_at", "last_error",
    ]

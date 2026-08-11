from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from .models import Notification, PlatformAccount, Rate, User


admin.site.register(User, UserAdmin)
admin.site.register(PlatformAccount)
admin.site.register(Rate)


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = ["user", "level", "title", "reference", "read", "created_at"]
    list_filter = ["level", "read"]
    search_fields = ["user__username", "reference", "title"]

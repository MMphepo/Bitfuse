from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from .models import PlatformAccount, Rate, User


admin.site.register(User, UserAdmin)
admin.site.register(PlatformAccount)
admin.site.register(Rate)

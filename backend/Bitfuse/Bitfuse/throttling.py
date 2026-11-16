from django.conf import settings
from rest_framework.throttling import ScopedRateThrottle


class ConfigurableScopedRateThrottle(ScopedRateThrottle):
    def allow_request(self, request, view):
        if getattr(settings, "TESTING", False):
            return True
        return super().allow_request(request, view)

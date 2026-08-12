from rest_framework.permissions import BasePermission

PAYMENT_VERIFIER_GROUP = "Payment Verifiers"


class IsPaymentVerifier(BasePermission):
    """Staff in the "Payment Verifiers" group (or superusers) may settle payments."""

    message = "You are not authorised to verify Bitfuse payments."

    def has_permission(self, request, view):
        user = request.user
        if not user or not user.is_authenticated or not user.is_staff:
            return False
        return user.is_superuser or user.groups.filter(name=PAYMENT_VERIFIER_GROUP).exists()

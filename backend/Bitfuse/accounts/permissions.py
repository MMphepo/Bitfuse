from rest_framework.permissions import BasePermission


class IsKycVerified(BasePermission):
    """
    Allows access only to users whose KYC verification status is 'verified'.
    """

    message = "KYC verification is required to access this resource."

    def has_permission(self, request, view):
        user = request.user
        if not user or not user.is_authenticated:
            return False
        return user.verification_status == "verified"


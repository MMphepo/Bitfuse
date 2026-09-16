from rest_framework.permissions import BasePermission
from rest_framework.exceptions import PermissionDenied


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


class IsTradingEligible(BasePermission):
    """
    Backend gate for financial operations (Buy, Sell, P2P Send, Withdrawal).
    Requires: Active account + Verified Email + Verified Phone + Verified KYC.
    """

    def has_permission(self, request, view):
        user = request.user
        if not user or not user.is_authenticated:
            return False

        if not user.is_active:
            raise PermissionDenied("Account is inactive.")

        if not user.email_verified:
            raise PermissionDenied("Please verify your email address to enable trading.")

        if not user.phone_verified:
            raise PermissionDenied("Please complete phone verification to enable trading.")

        if user.verification_status != "verified":
            if user.verification_status == "pending":
                raise PermissionDenied("Your KYC verification is currently pending review.")
            elif user.verification_status == "rejected":
                raise PermissionDenied("Your KYC verification was rejected. Please resubmit identity verification.")
            else:
                raise PermissionDenied("Complete KYC identity verification to enable trading.")

        return True

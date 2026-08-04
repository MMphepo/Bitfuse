from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import generics, permissions, status
from rest_framework.parsers import MultiPartParser, FormParser
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import KYCSubmission
from .serializers import (
    KYCReviewSerializer,
    KYCSubmissionSerializer,
    KYCUploadSerializer,
)


class KYCSubmitView(generics.CreateAPIView):
    """
    POST /api/v1/kyc/submit/
    Accepts multipart form: id_front, id_back, selfie.
    Creates a KYCSubmission for the authenticated user.
    """
    serializer_class = KYCUploadSerializer
    parser_classes = [MultiPartParser, FormParser]
    permission_classes = [permissions.IsAuthenticated]

    def create(self, request, *args, **kwargs):
        # Debug
        print("=" * 60)
        print("[KYC DEBUG] Request user:", request.user,
              "(authenticated:", request.user.is_authenticated, ")")
        print("[KYC DEBUG] Content-Type:",
              request.META.get("CONTENT_TYPE", "(none)"))
        print("[KYC DEBUG] request.data keys:", list(request.data.keys()))
        print("[KYC DEBUG] request.FILES keys:", list(request.FILES.keys()))
        for key in ["id_front", "id_back", "selfie"]:
            f = request.FILES.get(key)
            if f:
                print(
                    f"[KYC DEBUG]   {key}: name={f.name}, size={f.size}, content_type={f.content_type}")
            else:
                print(
                    f"[KYC DEBUG]   {key}: NOT PRESENT in request.FILES")
            d = request.data.get(key)
            if d and key not in request.FILES:
                print(
                    f"[KYC DEBUG]   {key} found in request.data (type={type(d).__name__}) but NOT in FILES")
        print("-" * 60)

        # Prevent duplicate submissions
        existing = KYCSubmission.objects.filter(user=request.user).first()
        if existing and existing.status == "pending":
            print("[KYC DEBUG] Duplicate pending submission detected, returning 400")
            return Response(
                {"detail": "You already have a KYC submission under review."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        serializer = self.get_serializer(data=request.data)
        try:
            serializer.is_valid(raise_exception=True)
        except Exception:
            print("[KYC DEBUG] Serializer errors:", serializer.errors)
            print("=" * 60)
            raise

        # If there's a previous rejected submission, update it instead
        if existing:
            for attr in ["id_front", "id_back", "selfie"]:
                setattr(existing, attr, serializer.validated_data[attr])
            existing.status = "pending"
            existing.rejection_reason = ""
            existing.reviewed_at = None
            existing.reviewed_by = None
            existing.save()
            return Response(
                KYCSubmissionSerializer(existing).data,
                status=status.HTTP_200_OK,
            )

        # Create new submission
        submission = serializer.save(
            user=request.user,
            status="pending",
        )
        return Response(
            KYCSubmissionSerializer(submission).data,
            status=status.HTTP_201_CREATED,
        )


class KYCStatusView(APIView):
    """
    GET /api/v1/kyc/status/
    Returns the KYC submission status for the authenticated user.
    """
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        submission = KYCSubmission.objects.filter(user=request.user).first()
        if not submission:
            return Response(
                {"status": "unverified", "submission": None},
                status=status.HTTP_200_OK,
            )
        return Response(
            {
                "status": submission.status,
                "submission": KYCSubmissionSerializer(submission).data,
            },
            status=status.HTTP_200_OK,
        )


class KYCAdminReviewView(APIView):
    """
    POST /api/v1/kyc/admin/<submission_id>/review/
    Admin-only. Approves or rejects a pending KYC submission.

    Body: {"status": "approved"} or {"status": "rejected", "rejection_reason": "..."}
    Syncs the linked user's verification_status to 'verified'/'rejected'.
    """
    permission_classes = [permissions.IsAdminUser]

    def post(self, request, submission_id):
        submission = get_object_or_404(KYCSubmission, id=submission_id)

        if submission.status != "pending":
            return Response(
                {"detail": "Only pending KYC submissions can be reviewed."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        serializer = KYCReviewSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        new_status = data["status"]
        submission.status = new_status
        submission.reviewed_by = request.user
        submission.reviewed_at = timezone.now()

        if new_status == "approved":
            submission.rejection_reason = ""
            submission.user.verification_status = "verified"
        else:
            submission.rejection_reason = data.get("rejection_reason", "")
            submission.user.verification_status = "rejected"

        submission.user.save(update_fields=["verification_status"])
        submission.save(update_fields=[
            "status", "rejection_reason", "reviewed_at", "reviewed_by",
        ])

        return Response(
            KYCSubmissionSerializer(submission).data,
            status=status.HTTP_200_OK,
        )

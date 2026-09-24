from django.db import models, transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import generics, permissions, status
from rest_framework.pagination import PageNumberPagination
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import KYCReviewAction, KYCSubmission
from .serializers import (
    KYCAdminDetailSerializer,
    KYCAdminListSerializer,
    KYCApproveRequestSerializer,
    KYCRejectRequestSerializer,
    KYCResubmitRequestSerializer,
    KYCReviewSerializer,
    KYCSubmissionSerializer,
    KYCUploadSerializer,
)


class StandardResultsSetPagination(PageNumberPagination):
    page_size = 20
    page_size_query_param = "page_size"
    max_page_size = 100


class KYCAdminStatsView(APIView):
    """
    GET /api/v1/kyc/admin/stats/
    Returns summary counters for admin dashboard header:
    - pending
    - approved_today
    - rejected_today
    - total_approved
    - total_rejected
    - resubmission_required
    - total_submissions
    """
    permission_classes = [permissions.IsAdminUser]

    def get(self, request):
        today_start = timezone.now().replace(hour=0, minute=0, second=0, microsecond=0)

        pending_count = KYCSubmission.objects.filter(status="pending").count()
        approved_today = KYCSubmission.objects.filter(status="approved", reviewed_at__gte=today_start).count()
        rejected_today = KYCSubmission.objects.filter(status="rejected", reviewed_at__gte=today_start).count()
        total_approved = KYCSubmission.objects.filter(status="approved").count()
        total_rejected = KYCSubmission.objects.filter(status="rejected").count()
        resubmission_required = KYCSubmission.objects.filter(status="resubmission_required").count()
        total_submissions = KYCSubmission.objects.count()

        return Response(
            {
                "pending": pending_count,
                "approved_today": approved_today,
                "rejected_today": rejected_today,
                "total_approved": total_approved,
                "total_rejected": total_rejected,
                "resubmission_required": resubmission_required,
                "total": total_submissions,
            },
            status=status.HTTP_200_OK,
        )


class KYCAdminQueueView(generics.ListAPIView):
    """
    GET /api/v1/kyc/admin/queue/
    GET /api/v1/kyc/admin/list/
    Supports:
    - pagination
    - status filtering (?status=pending|approved|rejected|resubmission_required|needs_review|all)
    - search (?search=name|email|phone|national_id)
    - date filtering (?date_from=YYYY-MM-DD, ?date_to=YYYY-MM-DD)
    """
    permission_classes = [permissions.IsAdminUser]
    serializer_class = KYCAdminListSerializer
    pagination_class = StandardResultsSetPagination

    def get_queryset(self):
        qs = KYCSubmission.objects.select_related("user").order_by("-submitted_at")

        status_param = self.request.query_params.get("status", "pending").strip().lower()
        if status_param and status_param != "all":
            if status_param == "needs_review":
                qs = qs.filter(status__in=["pending", "resubmission_required"])
            else:
                qs = qs.filter(status=status_param)

        search = self.request.query_params.get("search", "").strip()
        if search:
            qs = qs.filter(
                Q(user__first_name__icontains=search)
                | Q(user__last_name__icontains=search)
                | Q(user__username__icontains=search)
                | Q(user__email__icontains=search)
                | Q(user__phone_number__icontains=search)
                | Q(user__national_id_number__icontains=search)
            )

        date_from = self.request.query_params.get("date_from", "").strip()
        if date_from:
            qs = qs.filter(submitted_at__date__gte=date_from)

        date_to = self.request.query_params.get("date_to", "").strip()
        if date_to:
            qs = qs.filter(submitted_at__date__lte=date_to)

        return qs


class KYCAdminDetailView(APIView):
    """
    GET /api/v1/kyc/admin/<submission_id>/
    Admin-only endpoint for retrieving full details of one KYC submission.
    """
    permission_classes = [permissions.IsAdminUser]

    def get(self, request, submission_id):
        submission = get_object_or_404(
            KYCSubmission.objects.select_related("user", "reviewed_by").prefetch_related("audit_logs__admin"),
            id=submission_id,
        )
        serializer = KYCAdminDetailSerializer(submission)
        return Response(serializer.data, status=status.HTTP_200_OK)


class KYCAdminApproveView(APIView):
    """
    POST /api/v1/kyc/admin/<submission_id>/approve/
    Admin-only. Approves a pending/resubmission_required KYC submission.
    """
    permission_classes = [permissions.IsAdminUser]

    def post(self, request, submission_id):
        serializer = KYCApproveRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        note = serializer.validated_data.get("note", "").strip()

        with transaction.atomic():
            submission = KYCSubmission.objects.select_for_update().filter(id=submission_id).first()
            if not submission:
                return Response({"detail": "KYC submission not found."}, status=status.HTTP_404_NOT_FOUND)

            if submission.status == "approved":
                return Response(
                    {"detail": "KYC submission is already approved.", "submission": KYCAdminDetailSerializer(submission).data},
                    status=status.HTTP_200_OK,
                )

            previous_status = submission.status
            submission.status = "approved"
            submission.rejection_reason = ""
            submission.reviewed_by = request.user
            submission.reviewed_at = timezone.now()

            submission.user.verification_status = "verified"
            submission.user.save(update_fields=["verification_status"])
            submission.save(update_fields=["status", "rejection_reason", "reviewed_at", "reviewed_by"])

            KYCReviewAction.objects.create(
                submission=submission,
                admin=request.user,
                action="approved",
                previous_status=previous_status,
                new_status="approved",
                reason="",
                note=note,
            )

        return Response(
            {
                "message": f"KYC for {submission.user.username} approved successfully.",
                "submission": KYCAdminDetailSerializer(submission).data,
            },
            status=status.HTTP_200_OK,
        )


class KYCAdminRejectView(APIView):
    """
    POST /api/v1/kyc/admin/<submission_id>/reject/
    Admin-only. Rejects a KYC submission. Requires rejection reason.
    """
    permission_classes = [permissions.IsAdminUser]

    def post(self, request, submission_id):
        serializer = KYCRejectRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        reason = serializer.validated_data["final_reason"]
        note = serializer.validated_data.get("note", "").strip()

        with transaction.atomic():
            submission = KYCSubmission.objects.select_for_update().filter(id=submission_id).first()
            if not submission:
                return Response({"detail": "KYC submission not found."}, status=status.HTTP_404_NOT_FOUND)

            previous_status = submission.status
            submission.status = "rejected"
            submission.rejection_reason = reason
            submission.reviewed_by = request.user
            submission.reviewed_at = timezone.now()

            submission.user.verification_status = "rejected"
            submission.user.save(update_fields=["verification_status"])
            submission.save(update_fields=["status", "rejection_reason", "reviewed_at", "reviewed_by"])

            KYCReviewAction.objects.create(
                submission=submission,
                admin=request.user,
                action="rejected",
                previous_status=previous_status,
                new_status="rejected",
                reason=reason,
                note=note,
            )

        return Response(
            {
                "message": f"KYC for {submission.user.username} rejected.",
                "submission": KYCAdminDetailSerializer(submission).data,
            },
            status=status.HTTP_200_OK,
        )


class KYCAdminRequestResubmissionView(APIView):
    """
    POST /api/v1/kyc/admin/<submission_id>/request-resubmission/
    Admin-only. Requests resubmission for a KYC submission.
    """
    permission_classes = [permissions.IsAdminUser]

    def post(self, request, submission_id):
        serializer = KYCResubmitRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        reason = serializer.validated_data["final_reason"]
        note = serializer.validated_data.get("note", "").strip()

        with transaction.atomic():
            submission = KYCSubmission.objects.select_for_update().filter(id=submission_id).first()
            if not submission:
                return Response({"detail": "KYC submission not found."}, status=status.HTTP_404_NOT_FOUND)

            previous_status = submission.status
            submission.status = "resubmission_required"
            submission.rejection_reason = reason
            submission.reviewed_by = request.user
            submission.reviewed_at = timezone.now()

            submission.user.verification_status = "unverified"
            submission.user.save(update_fields=["verification_status"])
            submission.save(update_fields=["status", "rejection_reason", "reviewed_at", "reviewed_by"])

            KYCReviewAction.objects.create(
                submission=submission,
                admin=request.user,
                action="requested_resubmission",
                previous_status=previous_status,
                new_status="resubmission_required",
                reason=reason,
                note=note,
            )

        return Response(
            {
                "message": f"Resubmission requested for {submission.user.username}.",
                "submission": KYCAdminDetailSerializer(submission).data,
            },
            status=status.HTTP_200_OK,
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

            if request.user.verification_status != "pending":
                request.user.verification_status = "pending"
                request.user.save(update_fields=["verification_status"])

            return Response(
                KYCSubmissionSerializer(existing).data,
                status=status.HTTP_200_OK,
            )

        # Create new submission
        submission = serializer.save(
            user=request.user,
            status="pending",
        )
        if request.user.verification_status != "pending":
            request.user.verification_status = "pending"
            request.user.save(update_fields=["verification_status"])

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


class KYCAdminDocumentDownloadView(APIView):
    """
    GET /api/v1/kyc/admin/documents/<submission_id>/<file_type>/
    Admin-only endpoint to securely retrieve/download uploaded KYC documents (id_front, id_back, selfie).
    """
    permission_classes = [permissions.IsAdminUser]

    def get(self, request, submission_id, file_type):
        from django.http import FileResponse, Http404

        if file_type not in ["id_front", "id_back", "selfie"]:
            return Response({"detail": "Invalid file type requested."}, status=status.HTTP_400_BAD_REQUEST)

        submission = get_object_or_404(KYCSubmission, id=submission_id)
        file_field = getattr(submission, file_type, None)

        if not file_field or not file_field.name:
            raise Http404("Document not found.")

        try:
            return FileResponse(file_field.open("rb"), content_type="image/jpeg")
        except Exception as exc:
            return Response({"detail": f"Could not read document: {str(exc)}"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

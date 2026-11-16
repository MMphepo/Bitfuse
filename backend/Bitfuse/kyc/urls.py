from django.urls import path
from .views import (
    KYCAdminApproveView,
    KYCAdminDetailView,
    KYCAdminDocumentDownloadView,
    KYCAdminQueueView,
    KYCAdminRejectView,
    KYCAdminRequestResubmissionView,
    KYCAdminReviewView,
    KYCAdminStatsView,
    KYCStatusView,
    KYCSubmitView,
)

urlpatterns = [
    path("submit/", KYCSubmitView.as_view(), name="kyc-submit"),
    path("status/", KYCStatusView.as_view(), name="kyc-status"),
    path("admin/stats/", KYCAdminStatsView.as_view(), name="kyc-admin-stats"),
    path("admin/queue/", KYCAdminQueueView.as_view(), name="kyc-admin-queue"),
    path("admin/list/", KYCAdminQueueView.as_view(), name="kyc-admin-list"),
    path("admin/<uuid:submission_id>/", KYCAdminDetailView.as_view(), name="kyc-admin-detail"),
    path("admin/<uuid:submission_id>/approve/", KYCAdminApproveView.as_view(), name="kyc-admin-approve"),
    path("admin/<uuid:submission_id>/reject/", KYCAdminRejectView.as_view(), name="kyc-admin-reject"),
    path(
        "admin/<uuid:submission_id>/request-resubmission/",
        KYCAdminRequestResubmissionView.as_view(),
        name="kyc-admin-request-resubmission",
    ),
    path("admin/<uuid:submission_id>/review/", KYCAdminReviewView.as_view(), name="kyc-admin-review"),
    path(
        "admin/documents/<uuid:submission_id>/<str:file_type>/",
        KYCAdminDocumentDownloadView.as_view(),
        name="kyc-admin-document",
    ),
]

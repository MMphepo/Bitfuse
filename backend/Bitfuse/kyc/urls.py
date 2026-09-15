from django.urls import path
from .views import KYCAdminDocumentDownloadView, KYCAdminReviewView, KYCStatusView, KYCSubmitView

urlpatterns = [
    path("submit/", KYCSubmitView.as_view(), name="kyc-submit"),
    path("status/", KYCStatusView.as_view(), name="kyc-status"),
    path("admin/<uuid:submission_id>/review/", KYCAdminReviewView.as_view(), name="kyc-admin-review"),
    path(
        "admin/documents/<uuid:submission_id>/<str:file_type>/",
        KYCAdminDocumentDownloadView.as_view(),
        name="kyc-admin-document",
    ),
]

from django.urls import path
from .views import KYCSubmitView, KYCStatusView

urlpatterns = [
    path("submit/", KYCSubmitView.as_view(), name="kyc-submit"),
    path("status/", KYCStatusView.as_view(), name="kyc-status"),
]

from django.urls import path
from .views import (
    BuyInformationView,
    CreateBuyOrderView,
    CreateSellOrderView,
    OrderConfirmView,
    OrderDetailView,
    OrderListView,
    PaymentReviewView,
    PaymentVerificationQueueView,
    RejectPaymentView,
    SubmitPaymentView,
    VerifyPaymentView,
)

urlpatterns = [
    path("buy-information/", BuyInformationView.as_view(), name="order-buy-information"),
    path("buy/", CreateBuyOrderView.as_view(), name="order-buy"),
    path("sell/", CreateSellOrderView.as_view(), name="order-sell"),
    path("", OrderListView.as_view(), name="order-list"),
    path("verification-queue/", PaymentVerificationQueueView.as_view(), name="payment-verification-queue"),
    path("<uuid:order_id>/", OrderDetailView.as_view(), name="order-detail"),
    path("<uuid:order_id>/payment/", SubmitPaymentView.as_view(), name="order-submit-payment"),
    path("<uuid:order_id>/review/", PaymentReviewView.as_view(), name="order-payment-review"),
    path("<uuid:order_id>/verify-payment/", VerifyPaymentView.as_view(), name="order-verify-payment"),
    path("<uuid:order_id>/reject-payment/", RejectPaymentView.as_view(), name="order-reject-payment"),
    path("<uuid:order_id>/confirm/", OrderConfirmView.as_view(), name="order-confirm"),
]

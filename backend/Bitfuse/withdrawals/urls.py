from django.urls import path
from .views import (
    WithdrawalQuoteView,
    WithdrawalListCreateView,
    WithdrawalDetailView,
)

urlpatterns = [
    path("", WithdrawalListCreateView.as_view(), name="withdrawal-list-create"),
    path("quote/", WithdrawalQuoteView.as_view(), name="withdrawal-quote"),
    path("<uuid:id>/", WithdrawalDetailView.as_view(), name="withdrawal-detail"),
]

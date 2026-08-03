from django.urls import path
from .views import (
    BuyInformationView,
    CreateBuyOrderView,
    CreateSellOrderView,
    OrderListView,
    OrderConfirmView,
)

urlpatterns = [
    path("buy-information/", BuyInformationView.as_view(), name="order-buy-information"),
    path("buy/", CreateBuyOrderView.as_view(), name="order-buy"),
    path("sell/", CreateSellOrderView.as_view(), name="order-sell"),
    path("", OrderListView.as_view(), name="order-list"),
    path("<uuid:order_id>/confirm/", OrderConfirmView.as_view(), name="order-confirm"),
]


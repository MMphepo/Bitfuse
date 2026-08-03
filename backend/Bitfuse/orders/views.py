from rest_framework import generics, permissions, status
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.models import Rate
from .models import Order
from .serializers import CreateBuyOrderSerializer, CreateSellOrderSerializer, OrderSerializer
from .services import complete_buy_order, complete_sell_order


def _require_verified_kyc(user):
    """KYC gate: only fully verified users can transact."""
    if getattr(user, "verification_status", "unverified") != "verified":
        raise PermissionDenied("Complete identity verification before trading on Bitfuse.")


class CreateBuyOrderView(generics.CreateAPIView):
    serializer_class = CreateBuyOrderSerializer
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, *args, **kwargs):
        _require_verified_kyc(request.user)
        return super().post(request, *args, **kwargs)


class CreateSellOrderView(generics.CreateAPIView):
    serializer_class = CreateSellOrderSerializer
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, *args, **kwargs):
        _require_verified_kyc(request.user)
        return super().post(request, *args, **kwargs)


class BuyInformationView(APIView):
    """GET /api/v1/orders/buy-information/
    Returns current buy/sell rates, fee, payment methods, and limits.
    """
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        rate = Rate.current()
        return Response({
            "buy_rate": str(rate.buy_rate),
            "sell_rate": str(rate.sell_rate),
            "buy_fee_percent": str(rate.buy_fee_percent),
            "sell_fee_percent": str(rate.sell_fee_percent),
            "available_payment_methods": ["airtel_money", "tnm_mpamba"],
            "minimum": "10",
            "maximum": "5000",
        })


class OrderListView(generics.ListAPIView):
    serializer_class = OrderSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        return Order.objects.filter(user=self.request.user).order_by("-created_at")


class OrderConfirmView(APIView):
    """Admin-only: confirm payment/deposit received and complete the order via Blnk."""
    permission_classes = [permissions.IsAdminUser]

    def post(self, request, order_id):
        order = Order.objects.get(id=order_id)

        if order.status == "awaiting_payment":
            complete_buy_order(order)
        elif order.status == "awaiting_deposit":
            complete_sell_order(order)
        else:
            return Response({"detail": "Order is not awaiting confirmation."}, status=status.HTTP_400_BAD_REQUEST)

        return Response(OrderSerializer(order).data)


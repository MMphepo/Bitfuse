from rest_framework import generics, permissions, status
from rest_framework.views import APIView
from rest_framework.response import Response
from .models import Order
from .serializers import CreateBuyOrderSerializer, CreateSellOrderSerializer, OrderSerializer
from .services import complete_buy_order, complete_sell_order


class CreateBuyOrderView(generics.CreateAPIView):
    serializer_class = CreateBuyOrderSerializer
    permission_classes = [permissions.IsAuthenticated]


class CreateSellOrderView(generics.CreateAPIView):
    serializer_class = CreateSellOrderSerializer
    permission_classes = [permissions.IsAuthenticated]


class OrderListView(generics.ListAPIView):
    serializer_class = OrderSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        return Order.objects.filter(user=self.request.user).order_by("-created_at")


class OrderConfirmView(APIView):
    """Admin-only: confirm payment/deposit received and complete the order."""
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

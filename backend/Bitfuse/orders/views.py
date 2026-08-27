from rest_framework import generics, permissions, status
from rest_framework.exceptions import PermissionDenied
from rest_framework.generics import get_object_or_404
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.models import Rate
from .models import Order
from .payment_methods import payment_methods
from .permissions import IsPaymentVerifier
from .serializers import (
    AdminOrderReviewSerializer,
    CreateBuyOrderSerializer,
    CreateSellOrderSerializer,
    OrderSerializer,
    RejectPaymentSerializer,
    SubmitPaymentSerializer,
    VerifyPaymentSerializer,
)
from .services import (
    OrderError,
    complete_buy_order,
    complete_sell_order,
    expire_order_if_due,
    reject_payment,
    start_review,
    submit_payment,
    verify_payment,
)


def _require_verified_kyc(user):
    """KYC gate: only fully verified users can transact."""
    if getattr(user, "verification_status", "unverified") != "verified":
        raise PermissionDenied("Complete identity verification before trading on Bitfuse.")


def _error(exc):
    return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)


class CreateBuyOrderView(generics.CreateAPIView):
    serializer_class = CreateBuyOrderSerializer
    permission_classes = [permissions.IsAuthenticated]

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        order = serializer.save()
        return Response(OrderSerializer(order).data, status=status.HTTP_201_CREATED)

    def post(self, request, *args, **kwargs):
        _require_verified_kyc(request.user)
        return super().post(request, *args, **kwargs)


class CreateSellOrderView(generics.CreateAPIView):
    serializer_class = CreateSellOrderSerializer
    permission_classes = [permissions.IsAuthenticated]

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        order = serializer.save()
        return Response(OrderSerializer(order).data, status=status.HTTP_201_CREATED)

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
        methods = payment_methods()
        return Response({
            "buy_rate": str(rate.buy_rate),
            "sell_rate": str(rate.sell_rate),
            "buy_fee_percent": str(rate.buy_fee_percent),
            "sell_fee_percent": str(rate.sell_fee_percent),
            "available_payment_methods": list(methods),
            "payment_methods": list(methods.values()),
            "minimum": "10",
            "maximum": "5000",
        })


class OrderListView(generics.ListAPIView):
    serializer_class = OrderSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        return Order.objects.filter(user=self.request.user).order_by("-created_at")


class OrderDetailView(APIView):
    """The buyer's payment screen: amounts, merchant details, and time remaining."""

    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, order_id):
        order = get_object_or_404(Order, id=order_id, user=request.user)
        expire_order_if_due(order)
        if order.status == Order.SETTLING and order.order_type == "buy":
            try:
                complete_buy_order(order)
            except Exception:
                pass
        return Response(OrderSerializer(order).data)


class SubmitPaymentView(APIView):
    """POST /api/v1/orders/{id}/payment/ — the buyer reports their mobile money transaction ID."""

    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, order_id):
        order = get_object_or_404(Order, id=order_id, user=request.user)
        serializer = SubmitPaymentSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            order = submit_payment(order, request.user, serializer.validated_data["transaction_id"])
        except OrderError as exc:
            return _error(exc)

        return Response(OrderSerializer(order).data)


class PaymentVerificationQueueView(generics.ListAPIView):
    """Payment Verification Center: every buy order waiting on an admin decision."""

    serializer_class = AdminOrderReviewSerializer
    permission_classes = [IsPaymentVerifier]

    def get_queryset(self):
        return (
            Order.objects.filter(order_type="buy", status__in=Order.REVIEWABLE_STATUSES)
            .select_related("user")
            .order_by("payment_submitted_at")
        )


class PaymentReviewView(APIView):
    """GET the full review sheet for one order; POST claims it for review."""

    permission_classes = [IsPaymentVerifier]

    def get(self, request, order_id):
        order = get_object_or_404(Order, id=order_id)
        return Response(AdminOrderReviewSerializer(order).data)

    def post(self, request, order_id):
        order = get_object_or_404(Order, id=order_id)
        order = start_review(order, request.user)
        return Response(AdminOrderReviewSerializer(order).data)


class VerifyPaymentView(APIView):
    """Approve a mobile money payment and settle the order through Blnk."""

    permission_classes = [IsPaymentVerifier]

    def post(self, request, order_id):
        order = get_object_or_404(Order, id=order_id)
        serializer = VerifyPaymentSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            order = verify_payment(
                order,
                request.user,
                received_amount=serializer.validated_data.get("received_amount"),
                note=serializer.validated_data.get("note", ""),
            )
        except OrderError as exc:
            return _error(exc)

        return Response(AdminOrderReviewSerializer(order).data)


class RejectPaymentView(APIView):
    permission_classes = [IsPaymentVerifier]

    def post(self, request, order_id):
        order = get_object_or_404(Order, id=order_id)
        serializer = RejectPaymentSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            order = reject_payment(order, request.user, serializer.validated_data["reason"])
        except OrderError as exc:
            return _error(exc)

        return Response(AdminOrderReviewSerializer(order).data)


class OrderConfirmView(APIView):
    """Admin-only: confirm a sell payout was sent and complete the order via Blnk.

    Buy orders are settled through the payment verification endpoints, which
    require the transaction ID and an explicit confirmation.
    """
    permission_classes = [IsPaymentVerifier]

    def post(self, request, order_id):
        order = get_object_or_404(Order, id=order_id)

        if order.order_type == "buy":
            return Response(
                {"detail": "Verify the mobile money payment via /verify-payment/ instead."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if order.status != Order.AWAITING_DEPOSIT:
            return Response({"detail": "Order is not awaiting confirmation."}, status=status.HTTP_400_BAD_REQUEST)

        complete_sell_order(order)
        return Response(OrderSerializer(order).data)

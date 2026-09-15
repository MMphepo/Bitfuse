from django.contrib.auth import get_user_model
from rest_framework import generics, permissions, serializers
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.views import TokenObtainPairView
from rest_framework_simplejwt.tokens import RefreshToken

from .models import Notification, Transaction, Wallet
from .serializers import (
    NotificationSerializer,
    OrderHistorySerializer,
    RegisterSerializer,
    TransactionSerializer,
    TransferCreateSerializer,
    TransferSerializer,
    UserSerializer,
)
from .services import perform_p2p_transfer
from orders.models import Order

User = get_user_model()


class LoginView(TokenObtainPairView):
    """POST /api/v1/auth/login/
    Debug-instrumented simple-jwt token obtain view.
    """
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "auth"

    def post(self, request, *args, **kwargs):
        print("[login] request.data:", request.data)
        serializer = self.get_serializer(data=request.data)
        print("[login] serializer initialized")
        is_valid = serializer.is_valid()
        print("[login] serializer.is_valid():", is_valid)
        print("[login] serializer.errors:", serializer.errors)

        if not is_valid:
            print("[login] returning validation errors")
            return Response(serializer.errors, status=400)

        print("[login] credentials valid, producing tokens...")
        response = super().post(request, *args, **kwargs)
        print("[login] response status:", response.status_code)
        print("[login] response data keys:", list(response.data.keys()) if hasattr(response, "data") else "n/a")
        return response


class RegisterView(generics.CreateAPIView):
    queryset = User.objects.all()
    serializer_class = RegisterSerializer
    permission_classes = [permissions.AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "auth"

    def create(self, request, *args, **kwargs):
        print("[register] request.data:", request.data)
        serializer = self.get_serializer(data=request.data)
        print("[register] serializer initialized")
        is_valid = serializer.is_valid()
        print("[register] serializer.is_valid():", is_valid)
        print("[register] serializer.errors:", serializer.errors)

        if not is_valid:
            print("[register] returning validation errors")
            return Response(serializer.errors, status=400)

        user = serializer.save()
        print("[register] user created:", user.id, user.username)
        refresh = RefreshToken.for_user(user)
        response_data = serializer.data
        response_data["tokens"] = {
            "refresh": str(refresh),
            "access": str(refresh.access_token),
        }
        print("[register] response data:", response_data)
        return Response(response_data, status=201)


class MeView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        return Response(UserSerializer(request.user).data)


class WalletBalanceSerializer(serializers.ModelSerializer):
    class Meta:
        model = Wallet
        fields = ["currency", "blnk_balance_id", "created_at"]


class WalletBalanceView(APIView):
    """
    GET /api/v1/auth/wallets/
    Returns real numeric MWK and USDT wallet balances for the authenticated user.
    """
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        from accounts.services import fetch_wallet_balance
        from rest_framework import status

        try:
            balances = fetch_wallet_balance(request.user)
            response_data = {
                "mwk": float(balances["MWK"]),
                "usdt": float(balances["USDT"]),
            }
            return Response(response_data, status=status.HTTP_200_OK)
        except Exception as exc:
            return Response(
                {"detail": "Ledger service temporarily unavailable. Please try again shortly.", "error": str(exc)},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )


class AdminReconciliationView(APIView):
    """
    GET /api/v1/admin/reconcile/
    Admin-only endpoint to detect discrepancies between application state and Blnk ledger balances.
    Read-only audit tool: flags discrepancies without mutating financial state.
    """
    permission_classes = [permissions.IsAdminUser]

    def get(self, request):
        from decimal import Decimal
        from accounts.models import PlatformAccount, User, Wallet
        from accounts.services import fetch_wallet_balance
        from orders.models import Order
        from withdrawals.models import Withdrawal

        users = User.objects.all()
        discrepancies = []
        users_checked = 0

        for u in users:
            users_checked += 1
            try:
                bals = fetch_wallet_balance(u)
                usdt_bal = bals.get("USDT", Decimal("0"))
                mwk_bal = bals.get("MWK", Decimal("0"))
                if usdt_bal < Decimal("0"):
                    discrepancies.append({
                        "user_id": str(u.id),
                        "username": u.username,
                        "issue": "Negative USDT balance detected",
                        "balance": str(usdt_bal),
                    })
                if mwk_bal < Decimal("0"):
                    discrepancies.append({
                        "user_id": str(u.id),
                        "username": u.username,
                        "issue": "Negative MWK balance detected",
                        "balance": str(mwk_bal),
                    })
            except Exception as exc:
                discrepancies.append({
                    "user_id": str(u.id),
                    "username": u.username,
                    "issue": "Failed to fetch Blnk balance",
                    "error": str(exc),
                })

        # Calculate active escrow USDT in pending sell orders
        active_sells = Order.objects.filter(order_type="sell", status=Order.AWAITING_DEPOSIT)
        total_escrow_usdt = sum((o.usdt_amount for o in active_sells), Decimal("0"))

        # Pending withdrawals count and total amount
        pending_withdrawals = Withdrawal.objects.filter(status="PENDING")
        total_pending_withdrawal_usdt = sum((w.amount for w in pending_withdrawals), Decimal("0"))

        platform = PlatformAccount.objects.first()

        report = {
            "status": "ok" if len(discrepancies) == 0 else "discrepancies_found",
            "users_checked": users_checked,
            "discrepancies_count": len(discrepancies),
            "discrepancies": discrepancies,
            "escrow_summary": {
                "platform_frozen_balance_id": platform.usdt_frozen_balance_id if platform else "",
                "active_sell_orders_escrow_usdt": str(total_escrow_usdt),
            },
            "withdrawals_summary": {
                "pending_withdrawals_count": pending_withdrawals.count(),
                "pending_withdrawals_usdt": str(total_pending_withdrawal_usdt),
            },
        }
        return Response(report, status=status.HTTP_200_OK)


class WalletSendView(APIView):
    """
    POST /api/v1/auth/wallets/send/
    Performs internal peer-to-peer USDT or MWK transfer to another Bitfuse user.
    """
    permission_classes = [permissions.IsAuthenticated]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "financial"

    def post(self, request):
        from rest_framework import status

        serializer = TransferCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        data = serializer.validated_data
        try:
            transfer = perform_p2p_transfer(
                sender=request.user,
                recipient_username=data["recipient_username"],
                amount=data["amount"],
                currency=data["currency"],
                idempotency_key=data["idempotency_key"],
            )
            return Response(TransferSerializer(transfer).data, status=status.HTTP_200_OK)
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        except Exception as exc:
            return Response(
                {"detail": "Transfer could not be processed.", "error": str(exc)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class NotificationListView(generics.ListAPIView):
    """GET /api/v1/notifications/ — order lifecycle updates for the current user."""

    serializer_class = NotificationSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        return Notification.objects.filter(user=self.request.user)


class NotificationReadView(APIView):
    """POST /api/v1/notifications/{id}/read/ — mark one notification as read."""

    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, notification_id):
        updated = Notification.objects.filter(id=notification_id, user=request.user).update(read=True)
        if not updated:
            return Response({"detail": "Notification not found."}, status=404)
        return Response({"read": True})


class TransactionListView(APIView):
    """
    GET /api/v1/transactions/
    Returns the transaction history for the authenticated user.

    Combines the user's immutable Transaction records with their live Order
    records (every buy/sell the user has placed), so the history page reflects
    the actual activity of the logged-in user.
    """
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        user = request.user
        print("[transactions] GET /api/v1/transactions/ for user:", user.id, user.username)

        transactions = Transaction.objects.filter(user=user)
        orders = Order.objects.filter(user=user)

        print("[transactions] Transaction count:", transactions.count())
        print("[transactions] Order count:", orders.count())

        tx_data = TransactionSerializer(transactions, many=True).data
        order_data = OrderHistorySerializer(orders, many=True).data

        merged = list(tx_data) + list(order_data)
        # Sort newest first by created_at (ISO timestamps sort lexicographically).
        merged.sort(key=lambda item: item.get("created_at") or "", reverse=True)

        print("[transactions] merged history count:", len(merged))
        print("[transactions] merged data:", merged)
        return Response(merged)

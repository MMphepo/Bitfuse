from django.contrib.auth import get_user_model
from rest_framework import generics, permissions, serializers
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.views import TokenObtainPairView
from rest_framework_simplejwt.tokens import RefreshToken

from .models import Transaction, Wallet
from .serializers import RegisterSerializer, TransactionSerializer, UserSerializer

User = get_user_model()


class LoginView(TokenObtainPairView):
    """POST /api/v1/auth/login/
    Debug-instrumented simple-jwt token obtain view.
    """

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
        print("[wallets] GET /api/v1/auth/wallets/ called")
        print("[wallets] authenticated user:", request.user.id, request.user.username)
        print("[wallets] user.blnk_ledger_id:", getattr(request.user, "blnk_ledger_id", None))

        from accounts.services import fetch_wallet_balance

        print("[wallets] calling fetch_wallet_balance...")
        balances = fetch_wallet_balance(request.user)
        print("[wallets] raw balances:", balances)
        print("[wallets] MWK:", balances.get("MWK"), "| USDT:", balances.get("USDT"))

        response_data = {
            "mwk": float(balances["MWK"]),
            "usdt": float(balances["USDT"]),
        }
        print("[wallets] response data:", response_data)
        return Response(response_data)


class TransactionListView(generics.ListAPIView):
    """
    GET /api/v1/transactions/
    Returns the transaction history for the authenticated user.
    """
    serializer_class = TransactionSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        return Transaction.objects.filter(user=self.request.user)

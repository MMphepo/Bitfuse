from django.shortcuts import get_object_or_404
from rest_framework import generics, permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import Withdrawal
from .serializers import (
    CreateWithdrawalSerializer,
    WithdrawalQuoteSerializer,
    WithdrawalSerializer,
)
from .services.withdrawal_service import (
    get_withdrawal_quote,
    initiate_withdrawal,
    check_kyc_status,
    WithdrawalError,
)


class WithdrawalQuoteView(APIView):
    """POST /api/v1/withdrawals/quote/

    Returns withdrawal fee, minimum/maximum limits and net amount calculations
    based on the input amount.
    """
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, *args, **kwargs):
        check_kyc_status(request.user)
        serializer = WithdrawalQuoteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        amount = serializer.validated_data["amount"]
        asset = serializer.validated_data.get("asset", "USDT")
        network = serializer.validated_data.get("network", "TRON")
        try:
            quote = get_withdrawal_quote(request.user, amount, asset=asset, network=network)
            return Response(quote, status=status.HTTP_200_OK)
        except WithdrawalError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)


class WithdrawalListCreateView(APIView):
    """POST /api/v1/withdrawals/ - Initiate a new USDT withdrawal on TRON.
    GET /api/v1/withdrawals/ - List current user's withdrawals.
    """
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, *args, **kwargs):
        # List user's withdrawals
        queryset = Withdrawal.objects.filter(user=request.user)
        # Use existing pagination style if desired, or return list
        serializer = WithdrawalSerializer(queryset, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)

    def post(self, request, *args, **kwargs):
        check_kyc_status(request.user)
        serializer = CreateWithdrawalSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        asset = serializer.validated_data.get("asset", "USDT")
        network = serializer.validated_data.get("network", "TRON")
        amount = serializer.validated_data["amount"]
        destination_address = serializer.validated_data["destination_address"]

        try:
            withdrawal = initiate_withdrawal(
                user=request.user,
                asset=asset,
                network=network,
                amount=amount,
                destination_address=destination_address
            )

            # If the withdrawal failed immediately before broadcast (e.g. build/sign failed),
            # return appropriate status.
            if withdrawal.status == "FAILED":
                return Response(
                    {
                        "detail": f"Withdrawal failed: {withdrawal.failure_reason}",
                        "withdrawal": WithdrawalSerializer(withdrawal).data
                    },
                    status=status.HTTP_400_BAD_REQUEST
                )

            return Response(
                WithdrawalSerializer(withdrawal).data,
                status=status.HTTP_201_CREATED
            )
        except WithdrawalError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)


class WithdrawalDetailView(APIView):
    """GET /api/v1/withdrawals/{id}/

    Get withdrawal status, transaction hash, and timestamps.
    Users can only access their own withdrawals.
    """
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, id, *args, **kwargs):
        withdrawal = get_object_or_404(Withdrawal, id=id, user=request.user)
        return Response(WithdrawalSerializer(withdrawal).data, status=status.HTTP_200_OK)

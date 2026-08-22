from rest_framework import serializers
from decimal import Decimal
from .models import Withdrawal, WithdrawalConfig


class WithdrawalConfigSerializer(serializers.ModelSerializer):
    class Meta:
        model = WithdrawalConfig
        fields = [
            "withdrawal_fee",
            "min_usdt_withdrawal",
            "max_usdt_withdrawal",
            "withdrawals_frozen",
        ]


class WithdrawalQuoteSerializer(serializers.Serializer):
    asset = serializers.CharField(default="USDT")
    network = serializers.CharField(default="TRON")
    amount = serializers.DecimalField(max_digits=18, decimal_places=6)
    fee = serializers.DecimalField(max_digits=18, decimal_places=6, read_only=True)
    net_amount = serializers.DecimalField(max_digits=18, decimal_places=6, read_only=True)

    def validate_network(self, value):
        canonical = value.strip().upper()
        if canonical in ["BEP20", "BNB"]:
            return "BSC"
        elif canonical == "TRC20":
            return "TRON"
        if canonical not in ["TRON", "BSC"]:
            raise serializers.ValidationError(f"Unsupported network '{value}'. Must be TRON or BSC.")
        return canonical

    def validate_amount(self, value):
        if value <= Decimal("0"):
            raise serializers.ValidationError("Amount must be greater than zero.")
        return value


class CreateWithdrawalSerializer(serializers.Serializer):
    asset = serializers.CharField(default="USDT")
    network = serializers.CharField(default="TRON")
    amount = serializers.DecimalField(max_digits=18, decimal_places=6)
    destination_address = serializers.CharField(max_length=128)

    def validate_network(self, value):
        canonical = value.strip().upper()
        if canonical in ["BEP20", "BNB"]:
            return "BSC"
        elif canonical == "TRC20":
            return "TRON"
        if canonical not in ["TRON", "BSC"]:
            raise serializers.ValidationError(f"Unsupported network '{value}'. Must be TRON or BSC.")
        return canonical


class WithdrawalSerializer(serializers.ModelSerializer):
    class Meta:
        model = Withdrawal
        fields = [
            "id",
            "asset",
            "network",
            "amount",
            "fee",
            "net_amount",
            "destination_address",
            "status",
            "transaction_hash",
            "failure_reason",
            "created_at",
            "updated_at",
            "broadcast_at",
            "confirmed_at",
        ]
        read_only_fields = [
            "id",
            "status",
            "transaction_hash",
            "failure_reason",
            "created_at",
            "updated_at",
            "broadcast_at",
            "confirmed_at",
        ]

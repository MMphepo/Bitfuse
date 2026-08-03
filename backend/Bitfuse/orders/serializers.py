from decimal import Decimal

from rest_framework import serializers

from accounts.services import ensure_user_wallets, fetch_wallet_balance
from .models import Order
from .services import (
    generate_reference,
    lock_sell_order,
    price_buy_order,
    price_sell_order,
)

MIN_USDT = Decimal("10")
MAX_USDT = Decimal("5000")
PAYMENT_METHODS = ["airtel_money", "tnm_mpamba"]


class CreateBuyOrderSerializer(serializers.Serializer):
    amount_usdt = serializers.DecimalField(
        max_digits=14, decimal_places=6, min_value=MIN_USDT
    )
    payment_method = serializers.CharField(default="airtel_money")
    phone = serializers.CharField(required=False, allow_blank=True, default="")

    def validate_amount_usdt(self, value):
        if value > MAX_USDT:
            raise serializers.ValidationError(f"Maximum purchase is {MAX_USDT} USDT.")
        return value

    def create(self, validated_data):
        user = self.context["request"].user
        usdt_amount = validated_data["amount_usdt"]
        mwk_total, fee_amount, rate, fee_percent = price_buy_order(usdt_amount)

        return Order.objects.create(
            reference_number=generate_reference(),
            user=user,
            order_type="buy",
            mwk_amount=mwk_total,
            usdt_amount=usdt_amount,
            rate=rate,
            fee_percent=fee_percent,
            fee_amount=fee_amount,
            payment_method=validated_data["payment_method"],
            phone=validated_data.get("phone", ""),
            status="awaiting_payment",
        )


class CreateSellOrderSerializer(serializers.Serializer):
    amount_usdt = serializers.DecimalField(
        max_digits=14, decimal_places=6, min_value=MIN_USDT
    )
    payment_method = serializers.CharField(default="airtel_money")
    phone = serializers.CharField(required=False, allow_blank=True, default="")

    def validate_amount_usdt(self, value):
        if value > MAX_USDT:
            raise serializers.ValidationError(f"Maximum sale is {MAX_USDT} USDT.")
        return value

    def validate(self, attrs):
        user = self.context["request"].user
        _, usdt_wallet = ensure_user_wallets(user)
        balances = fetch_wallet_balance(user)
        available = balances["USDT"]
        if attrs["amount_usdt"] > available:
            raise serializers.ValidationError(
                {"amount_usdt": f"Insufficient USDT balance. Available: {available}."}
            )
        return attrs

    def create(self, validated_data):
        user = self.context["request"].user
        usdt_amount = validated_data["amount_usdt"]
        mwk_net, fee_amount, rate, fee_percent = price_sell_order(usdt_amount)

        order = Order.objects.create(
            reference_number=generate_reference(),
            user=user,
            order_type="sell",
            mwk_amount=mwk_net,
            usdt_amount=usdt_amount,
            rate=rate,
            fee_percent=fee_percent,
            fee_amount=fee_amount,
            payment_method=validated_data["payment_method"],
            phone=validated_data.get("phone", ""),
            status="awaiting_deposit",
        )

        # Freeze the seller's USDT immediately (Blnk escrow movement).
        lock_sell_order(order)
        return order


class OrderSerializer(serializers.ModelSerializer):
    class Meta:
        model = Order
        fields = [
            "id", "reference_number", "order_type", "mwk_amount", "usdt_amount",
            "rate", "fee_percent", "fee_amount", "payment_method", "phone",
            "status", "created_at", "completed_at",
        ]


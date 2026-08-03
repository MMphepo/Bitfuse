from decimal import Decimal
from rest_framework import serializers
from .models import Order
from .services import generate_reference, price_buy_order, price_sell_order


class CreateBuyOrderSerializer(serializers.Serializer):
    mwk_amount = serializers.DecimalField(max_digits=14, decimal_places=2, min_value=Decimal("500"))

    def create(self, validated_data):
        user = self.context["request"].user
        mwk_amount = validated_data["mwk_amount"]
        usdt_amount, rate, fee_percent = price_buy_order(mwk_amount)

        return Order.objects.create(
            reference_number=generate_reference(),
            user=user,
            order_type="buy",
            mwk_amount=mwk_amount,
            usdt_amount=usdt_amount,
            rate=rate,
            fee_percent=fee_percent,
            status="awaiting_payment",
        )


class CreateSellOrderSerializer(serializers.Serializer):
    usdt_amount = serializers.DecimalField(max_digits=14, decimal_places=6, min_value=Decimal("1"))

    def create(self, validated_data):
        user = self.context["request"].user
        usdt_amount = validated_data["usdt_amount"]
        mwk_amount, rate, fee_percent = price_sell_order(usdt_amount)

        return Order.objects.create(
            reference_number=generate_reference(),
            user=user,
            order_type="sell",
            mwk_amount=mwk_amount,
            usdt_amount=usdt_amount,
            rate=rate,
            fee_percent=fee_percent,
            status="awaiting_deposit",
        )


class OrderSerializer(serializers.ModelSerializer):
    class Meta:
        model = Order
        fields = [
            "id", "reference_number", "order_type", "mwk_amount", "usdt_amount",
            "rate", "fee_percent", "status", "created_at", "completed_at",
        ]

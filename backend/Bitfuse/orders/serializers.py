from decimal import Decimal

from rest_framework import serializers

from accounts.services import ensure_user_wallets, fetch_wallet_balance
from .models import Order, OrderAuditLog
from .payment_methods import is_supported, payment_methods
from .services import (
    generate_reference,
    lock_sell_order,
    log_order_event,
    notify,
    payment_expiry,
    payment_instructions,
    payment_reference_for,
    price_buy_order,
    price_sell_order,
)

MIN_USDT = Decimal("10")
MAX_USDT = Decimal("5000")
PAYMENT_METHODS = list(payment_methods())


class PaymentMethodField(serializers.CharField):
    def to_internal_value(self, data):
        value = super().to_internal_value(data)
        if not is_supported(value):
            raise serializers.ValidationError(
                f"Unsupported payment method. Choose one of: {', '.join(PAYMENT_METHODS)}."
            )
        return value


class CreateBuyOrderSerializer(serializers.Serializer):
    amount_usdt = serializers.DecimalField(
        max_digits=14, decimal_places=6, min_value=MIN_USDT
    )
    payment_method = PaymentMethodField(default="airtel_money")
    phone = serializers.CharField(required=False, allow_blank=True, default="")

    def validate_amount_usdt(self, value):
        if value > MAX_USDT:
            raise serializers.ValidationError(f"Maximum purchase is {MAX_USDT} USDT.")
        return value

    def create(self, validated_data):
        user = self.context["request"].user
        usdt_amount = Decimal(validated_data["amount_usdt"])
        mwk_amount, fee_amount, rate, fee_percent = price_buy_order(usdt_amount)

        reference = generate_reference()
        order = Order.objects.create(
            reference_number=reference,
            payment_reference=payment_reference_for(reference),
            user=user,
            order_type="buy",
            mwk_amount=mwk_amount,
            usdt_amount=usdt_amount,
            rate=rate,
            fee_percent=fee_percent,
            fee_amount=fee_amount,
            payment_method=validated_data["payment_method"],
            phone=validated_data.get("phone", ""),
            status=Order.AWAITING_PAYMENT,
            expires_at=payment_expiry(),
        )
        log_order_event(
            order, "created", actor=user, to_status=order.status,
            note=f"Rate locked at {rate} MWK/USDT until {order.expires_at:%Y-%m-%d %H:%M:%S} UTC.",
        )
        notify(
            user, "pending", "Buy order created",
            f"Your order for {usdt_amount} USDT has been created. Please pay "
            f"MWK {order.total_payable_mwk} using reference {order.payment_reference}.",
            order.reference_number,
        )
        return order


class CreateSellOrderSerializer(serializers.Serializer):
    amount_usdt = serializers.DecimalField(
        max_digits=14, decimal_places=6, min_value=MIN_USDT
    )
    payment_method = PaymentMethodField(default="airtel_money")
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
        usdt_amount = Decimal(validated_data["amount_usdt"])
        mwk_net, fee_amount, rate, fee_percent = price_sell_order(usdt_amount)

        reference = generate_reference()
        order = Order.objects.create(
            reference_number=reference,
            payment_reference=payment_reference_for(reference),
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
    total_payable_mwk = serializers.DecimalField(max_digits=14, decimal_places=2, read_only=True)
    seconds_until_expiry = serializers.IntegerField(read_only=True)
    payment_instructions = serializers.SerializerMethodField()

    class Meta:
        model = Order
        fields = [
            "id", "reference_number", "payment_reference", "order_type", "mwk_amount",
            "total_payable_mwk", "usdt_amount", "rate", "fee_percent", "fee_amount",
            "payment_method", "phone", "status", "payment_transaction_id",
            "payment_submitted_at", "rejection_reason", "expires_at",
            "seconds_until_expiry", "payment_instructions", "created_at", "completed_at",
        ]

    def get_payment_instructions(self, order):
        if order.order_type != "buy":
            return None
        return payment_instructions(order)


class SubmitPaymentSerializer(serializers.Serializer):
    transaction_id = serializers.CharField(max_length=64)


class VerifyPaymentSerializer(serializers.Serializer):
    """Admin approval. `confirm` is the deliberate second confirmation step."""

    confirm = serializers.BooleanField()
    received_amount = serializers.DecimalField(
        max_digits=14, decimal_places=2, required=False, allow_null=True
    )
    note = serializers.CharField(required=False, allow_blank=True, default="")

    def validate_confirm(self, value):
        if not value:
            raise serializers.ValidationError("Approval must be explicitly confirmed.")
        return value


class RejectPaymentSerializer(serializers.Serializer):
    reason = serializers.CharField(max_length=500)


class OrderAuditLogSerializer(serializers.ModelSerializer):
    actor = serializers.CharField(source="actor.username", default="", read_only=True)

    class Meta:
        model = OrderAuditLog
        fields = ["id", "actor", "action", "from_status", "to_status", "note", "created_at"]


class AdminOrderReviewSerializer(serializers.ModelSerializer):
    """The payment verification view an admin sees before approving a payment."""

    customer = serializers.SerializerMethodField()
    total_payable_mwk = serializers.DecimalField(max_digits=14, decimal_places=2, read_only=True)
    audit_logs = OrderAuditLogSerializer(many=True, read_only=True)

    class Meta:
        model = Order
        fields = [
            "id", "reference_number", "payment_reference", "customer", "order_type",
            "usdt_amount", "rate", "fee_percent", "fee_amount", "mwk_amount",
            "total_payable_mwk", "received_mwk_amount", "payment_method",
            "payment_transaction_id", "payment_submitted_at", "status",
            "rejection_reason", "expires_at", "created_at", "completed_at", "audit_logs",
        ]

    def get_customer(self, order):
        user = order.user
        return {
            "id": str(user.id),
            "username": user.username,
            "full_name": user.get_full_name(),
            "phone_number": user.phone_number,
            "kyc_status": user.verification_status,
        }

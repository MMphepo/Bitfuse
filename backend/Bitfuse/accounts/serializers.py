from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from rest_framework import serializers

from .models import Notification, Transaction
from orders.models import Order

User = get_user_model()


class RegisterSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True, validators=[validate_password])

    class Meta:
        model = User
        fields = ["id", "username", "email", "phone_number", "password", "location"]

    def create(self, validated_data):
        print("[register serializer] validated_data keys:", sorted(validated_data.keys()))
        print("[register serializer] username:", validated_data.get("username"))
        print("[register serializer] email:", validated_data.get("email"))
        print("[register serializer] phone_number:", validated_data.get("phone_number"))
        print("[register serializer] location:", validated_data.get("location"))
        print("[register serializer] password present:", "password" in validated_data)
        user = User.objects.create_user(
            username=validated_data["username"],
            email=validated_data["email"],
            phone_number=validated_data["phone_number"],
            location=validated_data.get("location", ""),
            password=validated_data["password"],
        )
        print("[register serializer] user created:", user.id)
        return user


class UserSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = [
            "id",
            "username",
            "email",
            "phone_number",
            "location",
            "verification_status",
            "email_verified",
            "phone_verified",
        ]


class TransactionSerializer(serializers.ModelSerializer):
    class Meta:
        model = Transaction
        fields = [
            "id",
            "type",
            "amount_usdt",
            "amount_mwk",
            "rate",
            "fee",
            "status",
            "method",
            "phone",
            "reference",
            "created_at",
        ]


class OrderHistorySerializer(serializers.ModelSerializer):
    """Serialize an Order into the same shape as TransactionSerializer.

    Order statuses are mapped to the frontend TxStatus enum:
      - completed                        -> Completed
      - cancelled / rejected / expired   -> Cancelled
      - payment_mismatch                 -> Disputed
      - everything else                  -> Pending
    """

    type = serializers.SerializerMethodField()
    status = serializers.SerializerMethodField()
    method = serializers.SerializerMethodField()
    amount_usdt = serializers.DecimalField(
        source="usdt_amount", max_digits=18, decimal_places=6
    )
    amount_mwk = serializers.SerializerMethodField()
    fee = serializers.DecimalField(
        source="fee_amount", max_digits=18, decimal_places=2
    )
    reference = serializers.CharField(source="reference_number")
    created_at = serializers.DateTimeField()

    class Meta:
        model = Order
        fields = [
            "id",
            "type",
            "amount_usdt",
            "amount_mwk",
            "rate",
            "fee",
            "status",
            "method",
            "phone",
            "reference",
            "created_at",
        ]

    def get_type(self, obj):
        return "Buy" if obj.order_type == "buy" else "Sell"

    def get_amount_mwk(self, obj):
        return obj.total_payable_mwk if obj.order_type == "buy" else obj.mwk_amount

    def get_status(self, obj):
        if obj.status == Order.COMPLETED:
            return "Completed"
        if obj.status in {Order.CANCELLED, Order.REJECTED, Order.EXPIRED}:
            return "Cancelled"
        if obj.status == Order.PAYMENT_MISMATCH:
            return "Disputed"
        return "Pending"

    def get_method(self, obj):
        method = (obj.payment_method or "").replace("_", " ").title()
        return method or "Airtel Money"


class NotificationSerializer(serializers.ModelSerializer):
    class Meta:
        model = Notification
        fields = ["id", "level", "title", "body", "reference", "read", "created_at"]

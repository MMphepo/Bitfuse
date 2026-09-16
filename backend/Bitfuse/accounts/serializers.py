import uuid
import re
from decimal import Decimal
from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from rest_framework import serializers

from .models import Notification, Transaction, Transfer, User
from .auth_services import normalize_phone_number
from orders.models import Order

User = get_user_model()


class RegisterSerializer(serializers.ModelSerializer):
    first_name = serializers.CharField(required=True, max_length=150)
    last_name = serializers.CharField(required=True, max_length=150)
    email = serializers.EmailField(required=True)
    phone_number = serializers.CharField(required=True, max_length=30)
    password = serializers.CharField(write_only=True, required=True)
    password_confirmation = serializers.CharField(write_only=True, required=True)
    captcha_token = serializers.CharField(write_only=True, required=False, allow_blank=True)

    class Meta:
        model = User
        fields = [
            "id",
            "first_name",
            "last_name",
            "email",
            "phone_number",
            "password",
            "password_confirmation",
            "captcha_token",
        ]

    def validate_first_name(self, value):
        val = value.strip()
        if not val:
            raise serializers.ValidationError("First name is required.")
        if len(val) < 2:
            raise serializers.ValidationError("First name must be at least 2 characters long.")
        return val

    def validate_last_name(self, value):
        val = value.strip()
        if not val:
            raise serializers.ValidationError("Last name is required.")
        if len(val) < 2:
            raise serializers.ValidationError("Last name must be at least 2 characters long.")
        return val

    def validate_email(self, value):
        normalized_email = value.strip().lower()
        if User.objects.filter(email__iexact=normalized_email).exists():
            raise serializers.ValidationError("An account with this email address already exists.")
        return normalized_email

    def validate_phone_number(self, value):
        normalized_phone = normalize_phone_number(value)
        if User.objects.filter(phone_number=normalized_phone).exists():
            raise serializers.ValidationError("An account with this phone number already exists.")
        return normalized_phone

    def validate_password(self, value):
        validate_password(value)
        return value

    def validate(self, data):
        pwd = data.get("password")
        pwd_confirm = data.get("password_confirmation")
        if pwd and pwd_confirm and pwd != pwd_confirm:
            raise serializers.ValidationError({"password_confirmation": ["Passwords do not match."]})
        return data

    def create(self, validated_data):
        email = validated_data["email"]
        first_name = validated_data["first_name"]
        last_name = validated_data["last_name"]
        phone_number = validated_data["phone_number"]
        password = validated_data["password"]

        # Generate unique internal username derived from email
        base_username = re.sub(r"[^\w]", "_", email.split("@")[0]).lower()[:20]
        if not base_username:
            base_username = "user"

        username = base_username
        counter = 1
        while User.objects.filter(username=username).exists():
            suffix = uuid.uuid4().hex[:6]
            username = f"{base_username}_{suffix}"
            counter += 1

        user = User.objects.create_user(
            username=username,
            email=email,
            phone_number=phone_number,
            first_name=first_name,
            last_name=last_name,
            password=password,
            email_verified=False,
            phone_verified=False,
            verification_status="unverified",
        )
        return user


class UserSerializer(serializers.ModelSerializer):
    is_trading_eligible = serializers.BooleanField(read_only=True)

    class Meta:
        model = User
        fields = [
            "id",
            "username",
            "first_name",
            "last_name",
            "email",
            "phone_number",
            "location",
            "verification_status",
            "email_verified",
            "phone_verified",
            "is_trading_eligible",
        ]


class TransferSerializer(serializers.ModelSerializer):
    sender_username = serializers.CharField(source="sender.username", read_only=True)
    recipient_username = serializers.CharField(source="recipient.username", read_only=True)

    class Meta:
        model = Transfer
        fields = [
            "id",
            "reference",
            "sender_username",
            "recipient_username",
            "amount",
            "currency",
            "status",
            "idempotency_key",
            "blnk_tx_id",
            "created_at",
        ]


class TransferCreateSerializer(serializers.Serializer):
    recipient_username = serializers.CharField(required=True)
    amount = serializers.DecimalField(max_digits=18, decimal_places=6, required=True)
    currency = serializers.ChoiceField(choices=["USDT", "MWK"], default="USDT")
    idempotency_key = serializers.CharField(max_length=100, required=True)

    def validate_amount(self, value):
        if value <= Decimal("0"):
            raise serializers.ValidationError("Amount must be strictly positive.")
        return value


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

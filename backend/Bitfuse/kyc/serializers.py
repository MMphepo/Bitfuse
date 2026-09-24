from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework import serializers

from .models import KYCReviewAction, KYCSubmission

User = get_user_model()


class KYCSubmissionSerializer(serializers.ModelSerializer):
    class Meta:
        model = KYCSubmission
        fields = [
            "id",
            "status",
            "rejection_reason",
            "submitted_at",
            "reviewed_at",
        ]
        read_only_fields = [
            "id",
            "status",
            "rejection_reason",
            "submitted_at",
            "reviewed_at",
        ]


class KYCAuditLogSerializer(serializers.ModelSerializer):
    admin_email = serializers.SerializerMethodField()
    admin_username = serializers.SerializerMethodField()

    class Meta:
        model = KYCReviewAction
        fields = [
            "id",
            "admin_email",
            "admin_username",
            "action",
            "previous_status",
            "new_status",
            "reason",
            "note",
            "created_at",
        ]

    def get_admin_email(self, obj):
        return obj.admin.email if obj.admin else None

    def get_admin_username(self, obj):
        return obj.admin.username if obj.admin else "System"


class KYCAdminUserSerializer(serializers.ModelSerializer):
    full_name = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = [
            "id",
            "username",
            "full_name",
            "first_name",
            "last_name",
            "email",
            "phone_number",
            "national_id_number",
            "location",
            "verification_status",
            "date_joined",
        ]

    def get_full_name(self, obj):
        name = f"{obj.first_name} {obj.last_name}".strip()
        return name if name else obj.username


class KYCAdminListSerializer(serializers.ModelSerializer):
    user = KYCAdminUserSerializer(read_only=True)
    has_id_front = serializers.SerializerMethodField()
    has_id_back = serializers.SerializerMethodField()
    has_selfie = serializers.SerializerMethodField()
    time_waiting_seconds = serializers.SerializerMethodField()

    class Meta:
        model = KYCSubmission
        fields = [
            "id",
            "user",
            "status",
            "rejection_reason",
            "submitted_at",
            "reviewed_at",
            "has_id_front",
            "has_id_back",
            "has_selfie",
            "time_waiting_seconds",
        ]

    def get_has_id_front(self, obj):
        return bool(obj.id_front and obj.id_front.name)

    def get_has_id_back(self, obj):
        return bool(obj.id_back and obj.id_back.name)

    def get_has_selfie(self, obj):
        return bool(obj.selfie and obj.selfie.name)

    def get_time_waiting_seconds(self, obj):
        if obj.status == "pending" and obj.submitted_at:
            delta = timezone.now() - obj.submitted_at
            return int(delta.total_seconds())
        return 0


class KYCAdminDetailSerializer(serializers.ModelSerializer):
    user = KYCAdminUserSerializer(read_only=True)
    reviewed_by_email = serializers.SerializerMethodField()
    id_front_url = serializers.SerializerMethodField()
    id_back_url = serializers.SerializerMethodField()
    selfie_url = serializers.SerializerMethodField()
    audit_logs = KYCAuditLogSerializer(many=True, read_only=True)

    class Meta:
        model = KYCSubmission
        fields = [
            "id",
            "user",
            "status",
            "rejection_reason",
            "submitted_at",
            "reviewed_at",
            "reviewed_by_email",
            "id_front_url",
            "id_back_url",
            "selfie_url",
            "audit_logs",
        ]

    def get_reviewed_by_email(self, obj):
        return obj.reviewed_by.email if obj.reviewed_by else None

    def get_id_front_url(self, obj):
        if obj.id_front and obj.id_front.name:
            return f"/api/v1/kyc/admin/documents/{obj.id}/id_front/"
        return None

    def get_id_back_url(self, obj):
        if obj.id_back and obj.id_back.name:
            return f"/api/v1/kyc/admin/documents/{obj.id}/id_back/"
        return None

    def get_selfie_url(self, obj):
        if obj.selfie and obj.selfie.name:
            return f"/api/v1/kyc/admin/documents/{obj.id}/selfie/"
        return None


class KYCRejectRequestSerializer(serializers.Serializer):
    reason = serializers.CharField(required=False, allow_blank=True, max_length=255)
    rejection_reason = serializers.CharField(required=False, allow_blank=True, max_length=255)
    note = serializers.CharField(required=False, allow_blank=True)

    def validate(self, attrs):
        reason = attrs.get("reason", "") or attrs.get("rejection_reason", "")
        if not reason.strip():
            raise serializers.ValidationError(
                {"reason": "A rejection reason is required when rejecting a KYC submission."}
            )
        attrs["final_reason"] = reason.strip()
        return attrs


class KYCResubmitRequestSerializer(serializers.Serializer):
    reason = serializers.CharField(required=False, allow_blank=True, max_length=255)
    rejection_reason = serializers.CharField(required=False, allow_blank=True, max_length=255)
    note = serializers.CharField(required=False, allow_blank=True)

    def validate(self, attrs):
        reason = attrs.get("reason", "") or attrs.get("rejection_reason", "")
        if not reason.strip():
            raise serializers.ValidationError(
                {"reason": "A reason is required when requesting KYC resubmission."}
            )
        attrs["final_reason"] = reason.strip()
        return attrs


class KYCApproveRequestSerializer(serializers.Serializer):
    note = serializers.CharField(required=False, allow_blank=True)


class KYCReviewSerializer(serializers.Serializer):
    """Serializer for admin review of a KYC submission (approve/reject)."""

    status = serializers.ChoiceField(choices=["approved", "rejected", "resubmission_required"])
    rejection_reason = serializers.CharField(
        required=False, allow_blank=True, max_length=255
    )
    note = serializers.CharField(required=False, allow_blank=True)

    def validate(self, attrs):
        status_ = attrs.get("status")
        reason = attrs.get("rejection_reason", "")
        if status_ in ["rejected", "resubmission_required"] and not reason.strip():
            raise serializers.ValidationError(
                {"rejection_reason": "A reason is required when rejecting or requesting resubmission."}
            )
        return attrs


class KYCUploadSerializer(serializers.ModelSerializer):
    class Meta:
        model = KYCSubmission
        fields = ["id_front", "id_back", "selfie"]

    def validate(self, attrs):
        for field_name in ["id_front", "id_back", "selfie"]:
            if field_name not in attrs or not attrs[field_name]:
                raise serializers.ValidationError(
                    {field_name: "This file is required."}
                )
        return attrs

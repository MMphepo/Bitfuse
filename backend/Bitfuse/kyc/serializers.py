from rest_framework import serializers
from .models import KYCSubmission


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


class KYCReviewSerializer(serializers.Serializer):
    """Serializer for admin review of a KYC submission (approve/reject)."""

    status = serializers.ChoiceField(choices=["approved", "rejected"])
    rejection_reason = serializers.CharField(
        required=False, allow_blank=True, max_length=255
    )

    def validate(self, attrs):
        status_ = attrs.get("status")
        reason = attrs.get("rejection_reason", "")
        if status_ == "rejected" and not reason.strip():
            raise serializers.ValidationError(
                {"rejection_reason": "A rejection reason is required when rejecting a KYC submission."}
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

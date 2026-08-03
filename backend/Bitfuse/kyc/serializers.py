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

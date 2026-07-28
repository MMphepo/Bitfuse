from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from rest_framework import serializers

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

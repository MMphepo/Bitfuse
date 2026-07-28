from django.contrib.auth import get_user_model
from rest_framework import generics, permissions
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.tokens import RefreshToken

from .serializers import RegisterSerializer, UserSerializer

User = get_user_model()


class RegisterView(generics.CreateAPIView):
    queryset = User.objects.all()
    serializer_class = RegisterSerializer
    permission_classes = [permissions.AllowAny]

    def create(self, request, *args, **kwargs):
        print("[register] request.data:", request.data)
        serializer = self.get_serializer(data=request.data)
        print("[register] serializer initialized")
        is_valid = serializer.is_valid()
        print("[register] serializer.is_valid():", is_valid)
        print("[register] serializer.errors:", serializer.errors)

        if not is_valid:
            print("[register] returning validation errors")
            return Response(serializer.errors, status=400)

        user = serializer.save()
        print("[register] user created:", user.id, user.username)
        refresh = RefreshToken.for_user(user)
        response_data = serializer.data
        response_data["tokens"] = {
            "refresh": str(refresh),
            "access": str(refresh.access_token),
        }
        print("[register] response data:", response_data)
        return Response(response_data, status=201)


class MeView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        return Response(UserSerializer(request.user).data)

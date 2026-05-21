"""Authentication service layer."""
from django.contrib.auth import authenticate, get_user_model, login
from django.contrib.auth.models import User

UserModel = get_user_model()


class AuthenticationService:
    @staticmethod
    def register_user(username: str, email: str, password: str) -> User:
        return UserModel.objects.create_user(
            username=username,
            email=email,
            password=password,
        )

    @staticmethod
    def authenticate_user(request, username: str, password: str):
        user = authenticate(request, username=username, password=password)
        if user:
            login(request, user)
        return user

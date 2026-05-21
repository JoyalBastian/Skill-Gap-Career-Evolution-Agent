from django.contrib import messages
from django.contrib.auth import logout
from django.contrib.auth.views import LoginView
from django.shortcuts import redirect, render
from django.urls import reverse_lazy
from django.views import View

from core.sqlite import db_retry
from services.authentication_service import AuthenticationService

from .forms import LoginForm, RegisterForm


class RegisterView(View):
    template_name = "authentication/register.html"

    def get(self, request):
        if request.user.is_authenticated:
            return redirect("users:dashboard")
        return render(request, self.template_name, {"form": RegisterForm()})

    def post(self, request):
        form = RegisterForm(request.POST)
        if form.is_valid():
            AuthenticationService.register_user(
                username=form.cleaned_data["username"],
                email=form.cleaned_data["email"],
                password=form.cleaned_data["password1"],
            )
            messages.success(request, "Account created. Please log in.")
            return redirect("authentication:login")
        return render(request, self.template_name, {"form": form})


class CustomLoginView(LoginView):
    template_name = "authentication/login.html"
    form_class = LoginForm
    redirect_authenticated_user = True


class LogoutView(View):
    @db_retry()
    def get(self, request):
        logout(request)
        messages.info(request, "You have been logged out.")
        return redirect("authentication:login")

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.shortcuts import get_object_or_404, redirect, render
from django.views import View
from django.views.generic import TemplateView, UpdateView

from apps.careers.models import CareerPrediction
from apps.careers.models import SkillGapReport
from services.career_prediction_service import CareerPredictionService

from .forms import ProfileForm
from .models import Profile


class DashboardView(LoginRequiredMixin, TemplateView):
    template_name = "users/dashboard.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        user = self.request.user
        ctx["predictions"] = CareerPrediction.objects.filter(user=user).order_by("rank")[:3]
        ctx["gap_report"] = SkillGapReport.objects.filter(user=user).first()
        ctx["profile"] = Profile.objects.filter(user=user).first()
        ctx["sessions_count"] = user.questionnaire_sessions.filter(status="completed").count()
        return ctx


class ProfileUpdateView(LoginRequiredMixin, UpdateView):
    model = Profile
    form_class = ProfileForm
    template_name = "users/profile.html"
    success_url = "/profile/"

    def get_object(self):
        return Profile.objects.get(user=self.request.user)

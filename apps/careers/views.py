from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.shortcuts import get_object_or_404, redirect, render
from django.views import View
from django.views.generic import ListView

from ai_engine.llm_client import GeminiUnavailable, user_message_for
from apps.users.mixins import JourneyGatedViewMixin
from services.career_prediction_service import CareerPredictionService
from services.skill_gap_service import SkillGapService

from .models import CareerDomain, CareerPrediction


class CareerListView(JourneyGatedViewMixin, LoginRequiredMixin, ListView):
    page_url_name = "careers:list"
    model = CareerDomain
    template_name = "careers/list.html"
    context_object_name = "careers"

    def get_queryset(self):
        predicted_ids = CareerPrediction.objects.filter(
            user=self.request.user
        ).values_list("career_id", flat=True)
        return CareerDomain.objects.filter(id__in=predicted_ids).distinct()


class CareerPredictionsView(JourneyGatedViewMixin, LoginRequiredMixin, View):
    page_url_name = "careers:predictions"
    template_name = "careers/predictions.html"

    def get(self, request):
        predictions = CareerPrediction.objects.filter(user=request.user).order_by("rank")
        return render(request, self.template_name, {"predictions": predictions})

    def post(self, request):
        try:
            CareerPredictionService().run_prediction(request.user.id)
            messages.success(request, "Career predictions updated.")
        except GeminiUnavailable as e:
            messages.error(request, user_message_for(e))
        return redirect("careers:predictions")


class CareerDetailView(JourneyGatedViewMixin, LoginRequiredMixin, View):
    page_url_name = "careers:detail"
    template_name = "careers/detail.html"

    def get(self, request, slug):
        career = get_object_or_404(CareerDomain, slug=slug)
        prediction = CareerPrediction.objects.filter(
            user=request.user, career=career
        ).first()
        gap_svc = SkillGapService()
        gap_report = gap_svc.get_latest_report(request.user.id)
        return render(request, self.template_name, {
            "career": career,
            "prediction": prediction,
            "gap_report": gap_report if gap_report and gap_report.career_id == career.id else None,
        })

    def post(self, request, slug):
        career = get_object_or_404(CareerDomain, slug=slug)
        try:
            SkillGapService().analyze_gaps(request.user.id, career.id)
            messages.success(request, f"Skill gap analysis completed for {career.name}.")
        except GeminiUnavailable as e:
            messages.error(request, user_message_for(e))
        return redirect("careers:detail", slug=slug)

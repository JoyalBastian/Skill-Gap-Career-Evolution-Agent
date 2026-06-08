from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.shortcuts import get_object_or_404, redirect, render
from django.views import View
from django.views.generic import ListView

from ai_engine.llm_client import GeminiUnavailable, user_message_for
from apps.careers.models import CareerPrediction
from apps.users.mixins import JourneyGatedViewMixin
from services.recommendation_service import RecommendationService
from services.roadmap_service import RoadmapService
from services.skill_gap_service import SkillGapService

from .models import Skill, UserSkill


class SkillListView(JourneyGatedViewMixin, LoginRequiredMixin, ListView):
    page_url_name = "skills:list"
    model = UserSkill
    template_name = "skills/list.html"
    context_object_name = "user_skills"

    def get_queryset(self):
        return UserSkill.objects.filter(user=self.request.user).select_related("skill")


class SkillGapView(JourneyGatedViewMixin, LoginRequiredMixin, View):
    page_url_name = "skills:gap"
    template_name = "skills/gap.html"

    def get(self, request):
        prediction = CareerPrediction.objects.filter(user=request.user).order_by("rank").first()
        gap_report = None
        if prediction:
            gap_report = SkillGapService().get_latest_report(request.user.id)
        return render(request, self.template_name, {
            "gap_report": gap_report,
            "prediction": prediction,
            "user_skills": UserSkill.objects.filter(user=request.user).select_related("skill"),
        })

    def post(self, request):
        prediction = CareerPrediction.objects.filter(user=request.user).order_by("rank").first()
        if prediction:
            try:
                SkillGapService().analyze_gaps(request.user.id, prediction.career_id)
                messages.success(request, "Skill gap analysis updated.")
            except GeminiUnavailable as e:
                messages.error(request, user_message_for(e))
        return redirect("skills:gap")


class SkillGapSkillView(JourneyGatedViewMixin, LoginRequiredMixin, View):
    page_url_name = "skills:gap_skill"
    template_name = "skills/gap_skill.html"

    def get(self, request, slug):
        skill = get_object_or_404(Skill, slug=slug)
        gap_report = SkillGapService().get_latest_report(request.user.id)

        gap_info = None
        if gap_report:
            for item in gap_report.missing_skills or []:
                if item.get("slug") == slug or (
                    (item.get("skill_name") or "").lower() == skill.name.lower()
                ):
                    gap_info = item
                    break

        recommendations = RecommendationService().get_recommendations_for_skill(
            request.user.id,
            skill.name,
            skill.slug,
        )
        roadmap_steps, active_roadmap = RoadmapService().get_steps_for_skill(
            request.user.id,
            skill.slug,
        )

        return render(request, self.template_name, {
            "skill": skill,
            "gap_info": gap_info,
            "gap_report": gap_report,
            "recommendations": recommendations,
            "roadmap_steps": roadmap_steps,
            "active_roadmap": active_roadmap,
        })

import logging

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.shortcuts import get_object_or_404, redirect, render
from django.views import View
from django.views.generic import ListView

from ai_engine.llm_client import GeminiUnavailable, LLMUnavailable, flash_ai_error
from apps.careers.models import CareerPrediction
from apps.recommendations.models import Recommendation
from apps.users.mixins import JourneyGatedViewMixin
from services.progress_service import ProgressService
from services.recommendation_service import RecommendationService
from services.roadmap_service import RoadmapService
from services.skill_gap_service import SkillGapService, find_gap_info_for_skill
from services.skill_level_utils import enrich_gap_context

from .models import Skill, UserSkill

logger = logging.getLogger(__name__)


class SkillListView(JourneyGatedViewMixin, LoginRequiredMixin, ListView):
    page_url_name = "skills:list"
    model = UserSkill
    template_name = "skills/list.html"
    context_object_name = "user_skills"

    def get_queryset(self):
        return UserSkill.objects.filter(user=self.request.user).select_related("skill")

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        gap_report = SkillGapService().get_latest_report(self.request.user.id)
        ctx["gap_slugs"] = {
            (s.get("slug") or "").strip()
            for s in (gap_report.prioritized_skills or [] if gap_report else [])
            if s.get("slug")
        }
        return ctx


class SkillGapView(JourneyGatedViewMixin, LoginRequiredMixin, View):
    page_url_name = "skills:gap"
    template_name = "skills/gap.html"

    def get(self, request):
        prediction = CareerPrediction.objects.filter(user=request.user).order_by("rank").first()
        gap_report = None
        courses_by_slug = {}
        gap_skills = []
        progress_svc = ProgressService()
        if prediction:
            gap_report = SkillGapService().get_latest_report(request.user.id)
            if gap_report and gap_report.prioritized_skills:
                missing_by_slug = {
                    (m.get("slug") or "").strip(): m
                    for m in (gap_report.missing_skills or [])
                    if m.get("slug")
                }
                slugs = [
                    (s.get("slug") or "").strip()
                    for s in gap_report.prioritized_skills
                    if s.get("slug")
                ]
                courses_by_slug = RecommendationService().get_courses_preview_by_slug(
                    request.user.id,
                    slugs,
                )
                skill_ids_by_slug = {
                    row["slug"]: row["id"]
                    for row in Skill.objects.filter(slug__in=slugs).values("slug", "id")
                }
                for s in gap_report.prioritized_skills:
                    slug = (s.get("slug") or "").strip()
                    merged = enrich_gap_context({**missing_by_slug.get(slug, {}), **s})
                    skill_id = skill_ids_by_slug.get(slug)
                    gap_skills.append({
                        **merged,
                        "skill": merged.get("skill") or merged.get("skill_name"),
                        "course_preview": courses_by_slug.get(slug, []),
                        "is_acquired": (
                            progress_svc.is_skill_acquired(request.user.id, skill_id)
                            if skill_id
                            else False
                        ),
                    })
        return render(request, self.template_name, {
            "gap_report": gap_report,
            "prediction": prediction,
            "user_skills": UserSkill.objects.filter(user=request.user).select_related("skill"),
            "courses_by_slug": courses_by_slug,
            "gap_skills": gap_skills,
        })

    def post(self, request):
        prediction = CareerPrediction.objects.filter(user=request.user).order_by("rank").first()
        if prediction:
            try:
                SkillGapService().analyze_gaps(request.user.id, prediction.career_id)
                try:
                    RecommendationService().generate_gap_targeted_courses(request.user.id)
                except LLMUnavailable as e:
                    logger.warning("Gap courses regen failed: %s", e)
                messages.success(request, "Skill gap analysis and courses updated.")
            except (GeminiUnavailable, LLMUnavailable) as e:
                flash_ai_error(request, e)
        return redirect("skills:gap")


class SkillGapSkillView(JourneyGatedViewMixin, LoginRequiredMixin, View):
    page_url_name = "skills:gap_skill"
    template_name = "skills/gap_skill.html"

    def get(self, request, slug):
        skill = get_object_or_404(Skill, slug=slug)
        gap_report = SkillGapService().get_latest_report(request.user.id)
        gap_info = find_gap_info_for_skill(gap_report, skill)

        recommendations = RecommendationService().get_recommendations_for_skill(
            request.user.id,
            skill.name,
            skill.slug,
        )
        primary_recommendations = [r for r in recommendations if r.is_primary_for_gap]
        next_recommendations = [r for r in recommendations if not r.is_primary_for_gap]
        if not primary_recommendations and recommendations:
            start_lvl = (gap_info or {}).get("recommended_start_level", "beginner")
            primary_recommendations = [
                r for r in recommendations
                if r.resource and r.resource.level == start_lvl
            ]
            next_recommendations = [
                r for r in recommendations
                if r not in primary_recommendations
            ]
        filter_level = (gap_info or {}).get("recommended_start_level", "")
        roadmap_steps, active_roadmap = RoadmapService().get_steps_for_skill(
            request.user.id,
            skill.slug,
        )

        return render(request, self.template_name, {
            "skill": skill,
            "gap_info": gap_info,
            "gap_report": gap_report,
            "is_acquired": ProgressService().is_skill_acquired(request.user.id, skill.id),
            "recommendations": recommendations,
            "primary_recommendations": primary_recommendations,
            "next_recommendations": next_recommendations,
            "filter_level": filter_level,
            "level_labels": {
                "beginner": "Beginner",
                "intermediate": "Intermediate",
                "advanced": "Expert",
            },
            "roadmap_steps": roadmap_steps,
            "active_roadmap": active_roadmap,
        })

    def post(self, request, slug):
        skill = get_object_or_404(Skill, slug=slug)
        gap_report = SkillGapService().get_latest_report(request.user.id)
        gap_info = find_gap_info_for_skill(gap_report, skill)
        if not gap_info:
            messages.warning(request, "This skill is not in your current gap report.")
            return redirect("skills:gap_skill", slug=slug)
        try:
            prediction = CareerPrediction.objects.filter(user=request.user).order_by("rank").first()
            career_name = prediction.career.name if prediction else ""
            Recommendation.objects.filter(
                user_id=request.user.id,
                target_skill=skill,
            ).delete()
            RecommendationService().generate_courses_for_skill(
                request.user.id,
                skill,
                gap_info,
                gap_report=gap_report,
                career_name=career_name,
            )
            messages.success(request, f"Generated courses for {skill.name}.")
        except (GeminiUnavailable, LLMUnavailable) as e:
            flash_ai_error(request, e)
        return redirect("skills:gap_skill", slug=slug)


class MarkSkillAcquiredView(JourneyGatedViewMixin, LoginRequiredMixin, View):
    page_url_name = "skills:gap_skill"

    def post(self, request, slug):
        skill = get_object_or_404(Skill, slug=slug)
        progress_svc = ProgressService()

        if progress_svc.is_skill_acquired(request.user.id, skill.id):
            messages.info(request, f"{skill.name} is already marked as acquired.")
            return redirect("skills:gap_skill", slug=slug)

        gap_report = SkillGapService().get_latest_report(request.user.id)
        gap_info = find_gap_info_for_skill(gap_report, skill)
        if not gap_info:
            messages.warning(request, "This skill is not in your current gap report.")
            return redirect("skills:gap_skill", slug=slug)

        target = gap_info.get("required_proficiency", 5)
        progress_svc.mark_skill_acquired(
            request.user.id,
            skill.id,
            target_proficiency=target,
        )
        messages.success(
            request,
            f"Marked {skill.name} as acquired. "
            "Re-analyze skill gaps on the main gap page to refresh your gap list.",
        )
        return redirect("skills:gap_skill", slug=slug)

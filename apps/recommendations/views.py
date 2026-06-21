from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.views import View

from apps.questionnaire.models import QuestionnaireSession
from apps.users.mixins import JourneyGatedViewMixin
from services.recommendation_service import RecommendationService

from .models import Recommendation


class RecommendationListView(JourneyGatedViewMixin, LoginRequiredMixin, View):
    page_url_name = "recommendations:list"
    template_name = "recommendations/list.html"

    def get(self, request):
        svc = RecommendationService()
        category = request.GET.get("category", "")
        level = request.GET.get("level", "")
        step_id = request.GET.get("step", "").strip()
        skill_slug = request.GET.get("skill", "").strip()
        roadmap_step = None
        filter_skill = None
        gap_groups = None
        recs = None

        if step_id:
            from apps.roadmap.models import RoadmapStep

            roadmap_step = (
                RoadmapStep.objects.filter(
                    id=step_id,
                    roadmap__user=request.user,
                )
                .select_related("roadmap")
                .prefetch_related("skills")
                .first()
            )
            if roadmap_step:
                recs = svc.get_recommendations_for_roadmap_step(
                    request.user.id, roadmap_step
                )
        elif skill_slug:
            from apps.skills.models import Skill

            filter_skill = Skill.objects.filter(slug=skill_slug).first()
            if filter_skill:
                recs = svc.get_recommendations_for_skill(
                    request.user.id,
                    filter_skill.name,
                    filter_skill.slug,
                )

        if recs is None:
            recs = svc.get_user_recommendations(
                request.user.id,
                category=category or None,
                level=level or None,
            )
            if not step_id and not skill_slug and not category and not level:
                gap_groups = svc.get_grouped_by_gap_skill(request.user.id)
                grouped_count = sum(len(g.get("recommendations") or []) for g in (gap_groups or []))
                if grouped_count > 0:
                    recs = None
                else:
                    gap_groups = None

        generating = svc.is_generating(request.user.id)
        from services.questionnaire_service import QuestionnaireService

        pipeline_running = QuestionnaireService().is_pipeline_running(request.user.id)
        if pipeline_running:
            generating = True

        has_interview = QuestionnaireSession.objects.filter(
            user=request.user,
            status="completed",
        ).exists()
        has_gap_targeted = svc.has_gap_targeted_recommendations(request.user.id)

        if not has_gap_targeted and not generating and has_interview and not pipeline_running:
            state = svc.start_generate_async(request.user.id)
            generating = state in ("started", "running")

        # Hide stale legacy recommendations while gap-targeted courses generate.
        if (
            generating
            and not has_gap_targeted
            and not pipeline_running
            and not step_id
            and not skill_slug
            and not category
            and not level
        ):
            gap_groups = None
            recs = None

        categories = Recommendation.objects.filter(user=request.user).values_list(
            "category", flat=True
        ).distinct()
        from services.recommendation_service import LEVEL_FILTER_OPTIONS

        all_recommendations = svc.get_user_recommendations(request.user.id)

        return render(request, self.template_name, {
            "recommendations": recs,
            "all_recommendations": all_recommendations,
            "gap_groups": gap_groups,
            "categories": categories,
            "active_category": category,
            "active_level": level,
            "level_filters": LEVEL_FILTER_OPTIONS,
            "generating": generating,
            "has_interview": has_interview,
            "pipeline_running": pipeline_running,
            "has_gap_targeted": has_gap_targeted,
            "roadmap_step": roadmap_step,
            "filter_skill": filter_skill,
            "has_active_filters": bool(category or level),
        })

    def post(self, request):
        svc = RecommendationService()
        if svc.is_generating(request.user.id):
            messages.info(
                request,
                "Courses are already being generated. Please wait a moment.",
            )
            return redirect("recommendations:list")

        from apps.careers.models import SkillGapReport

        if not SkillGapReport.objects.filter(user=request.user).exists():
            messages.error(
                request,
                "Complete skill gap analysis first, then try again.",
            )
            return redirect("recommendations:list")

        state = svc.start_generate_async(request.user.id, force=True)
        if state == "running":
            messages.info(request, "Courses are already being generated.")
        else:
            messages.info(
                request,
                "Generating courses for your skill gaps — this page will update shortly.",
            )
        return redirect("recommendations:list")


class RecommendationStatusView(LoginRequiredMixin, View):
    """Poll whether background recommendation generation has finished."""

    def get(self, request):
        svc = RecommendationService()
        total = Recommendation.objects.filter(user=request.user).count()
        gap_count = Recommendation.objects.filter(
            user=request.user,
            target_skill__isnull=False,
        ).count()
        generating = svc.is_generating(request.user.id)
        return JsonResponse({
            "count": total,
            "gap_count": gap_count,
            "generating": generating,
            "ready": gap_count > 0 and not generating,
        })

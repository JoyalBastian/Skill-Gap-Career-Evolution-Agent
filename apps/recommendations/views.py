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
        recs = svc.get_user_recommendations(
            request.user.id,
            category=category or None,
            level=level or None,
        )
        generating = svc.is_generating(request.user.id)
        has_interview = QuestionnaireSession.objects.filter(
            user=request.user,
            status="completed",
        ).exists()

        if not recs.exists() and not generating and has_interview:
            state = svc.start_generate_async(request.user.id)
            generating = state in ("started", "running")

        categories = Recommendation.objects.filter(user=request.user).values_list(
            "category", flat=True
        ).distinct()
        from services.recommendation_service import LEVEL_FILTER_OPTIONS

        return render(request, self.template_name, {
            "recommendations": recs,
            "categories": categories,
            "active_category": category,
            "active_level": level,
            "level_filters": LEVEL_FILTER_OPTIONS,
            "generating": generating,
            "has_interview": has_interview,
        })

    def post(self, request):
        svc = RecommendationService()
        state = svc.start_generate_async(request.user.id)
        if state == "done":
            messages.info(request, "Recommendations are already up to date.")
        elif state == "running":
            messages.info(
                request,
                "Recommendations are already being generated. Please wait a moment.",
            )
        else:
            messages.info(
                request,
                "Generating recommendations in the background. This page will refresh automatically.",
            )
        return redirect("recommendations:list")


class RecommendationStatusView(LoginRequiredMixin, View):
    """Poll whether background recommendation generation has finished."""

    def get(self, request):
        svc = RecommendationService()
        count = Recommendation.objects.filter(user=request.user).count()
        return JsonResponse({
            "count": count,
            "generating": svc.is_generating(request.user.id),
            "ready": count > 0,
        })

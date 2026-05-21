from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.shortcuts import redirect, render
from django.views import View

from ai_engine.llm_client import GeminiUnavailable, user_message_for
from apps.users.mixins import JourneyGatedViewMixin
from services.recommendation_service import RecommendationService

from .models import Recommendation


class RecommendationListView(JourneyGatedViewMixin, LoginRequiredMixin, View):
    page_url_name = "recommendations:list"
    template_name = "recommendations/list.html"

    def get(self, request):
        category = request.GET.get("category", "")
        recs = RecommendationService().get_user_recommendations(
            request.user.id, category or None
        )
        categories = Recommendation.objects.filter(user=request.user).values_list(
            "category", flat=True
        ).distinct()
        return render(request, self.template_name, {
            "recommendations": recs,
            "categories": categories,
            "active_category": category,
        })

    def post(self, request):
        try:
            RecommendationService().generate_recommendations(request.user.id)
            messages.success(request, "Recommendations refreshed.")
        except GeminiUnavailable as e:
            messages.error(request, user_message_for(e))
        return redirect("recommendations:list")

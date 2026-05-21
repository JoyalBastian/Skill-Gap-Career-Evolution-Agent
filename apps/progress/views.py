from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import JsonResponse
from django.shortcuts import render
from django.views import View
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated

from apps.users.mixins import JourneyGatedViewMixin
from services.progress_service import ProgressService

from .models import ProgressEntry


class ProgressDashboardView(JourneyGatedViewMixin, LoginRequiredMixin, View):
    page_url_name = "progress:dashboard"
    template_name = "progress/dashboard.html"

    def get(self, request):
        progress = ProgressService().get_overall_progress(request.user.id)
        entries = ProgressEntry.objects.filter(user=request.user).order_by("-updated_at")[:20]
        progress_metrics = [
            {"label": "Roadmap", "value": progress.get("roadmap_percent", 0)},
            {"label": "Skills", "value": progress.get("skills_percent", 0)},
            {"label": "Learning", "value": progress.get("learning_percent", 0)},
        ]
        return render(request, self.template_name, {
            "progress": progress,
            "progress_metrics": progress_metrics,
            "entries": entries,
        })


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def progress_api(request):
    data = ProgressService().get_overall_progress(request.user.id)
    return JsonResponse(data)

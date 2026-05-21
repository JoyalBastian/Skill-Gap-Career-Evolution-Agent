from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.shortcuts import get_object_or_404, redirect, render
from django.views import View

from ai_engine.llm_client import GeminiUnavailable, user_message_for
from apps.users.mixins import JourneyGatedViewMixin
from services.trending_jobs_service import TrendingJobsService

from .models import JobMatch, TrendingJob


class TrendingJobsView(JourneyGatedViewMixin, LoginRequiredMixin, View):
    page_url_name = "jobs:trending"
    template_name = "jobs/trending.html"

    def get(self, request):
        jobs = list(TrendingJob.objects.all().order_by("-demand_label", "title"))
        matches = list(
            JobMatch.objects.filter(user=request.user)
            .select_related("job")
            .order_by("-fit_score")[:10]
        )
        last_refreshed = jobs[0].refreshed_at if jobs else None
        return render(request, self.template_name, {
            "jobs": jobs,
            "matches": matches,
            "last_refreshed": last_refreshed,
        })

    def post(self, request):
        svc = TrendingJobsService()
        action = request.POST.get("action", "match")
        try:
            if action == "refresh":
                svc.refresh_trending(force=True)
                messages.success(request, "Trending jobs refreshed.")
            svc.match_for_user(request.user.id)
            messages.success(request, "Personalized job matches updated.")
        except GeminiUnavailable as e:
            messages.error(request, user_message_for(e))
        return redirect("jobs:trending")


class TrendingJobDetailView(JourneyGatedViewMixin, LoginRequiredMixin, View):
    page_url_name = "jobs:detail"
    template_name = "jobs/detail.html"

    def get(self, request, slug):
        job = get_object_or_404(TrendingJob, slug=slug)
        match = JobMatch.objects.filter(user=request.user, job=job).first()
        return render(request, self.template_name, {
            "job": job,
            "match": match,
        })

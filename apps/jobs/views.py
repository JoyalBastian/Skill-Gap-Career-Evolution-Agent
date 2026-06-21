from django.conf import settings
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views import View

from ai_engine.llm_client import GeminiUnavailable, LLMUnavailable, user_message_for
from apps.careers.models import CareerPrediction
from apps.users.mixins import JourneyGatedViewMixin
from services.ats_resume_service import ATSResumeService
from services.job_vacancy_service import JobVacancyService
from services.trending_jobs_service import TrendingJobsService

from .models import ATSResume, JobMatch, TrendingJob


class JobOpeningsView(LoginRequiredMixin, View):
    """Live market vacancies from Remotive / Arbeitnow (real apply links)."""
    template_name = "jobs/vacancies.html"

    def get(self, request):
        keyword = (request.GET.get("q") or "").strip()
        location = (request.GET.get("location") or "").strip()
        remote_param = request.GET.get("remote")
        if remote_param is None:
            remote_only = bool(getattr(settings, "JOB_DEFAULT_REMOTE", True))
        else:
            remote_only = remote_param.lower() in ("1", "true", "yes", "on")

        default_keyword = keyword
        if not default_keyword:
            prediction = (
                CareerPrediction.objects.filter(user=request.user)
                .select_related("career")
                .order_by("rank")
                .first()
            )
            if prediction and prediction.career:
                default_keyword = prediction.career.name

        result = JobVacancyService().search_vacancies(
            keyword=default_keyword,
            location=location,
            remote_only=remote_only,
        )

        return render(request, self.template_name, {
            "vacancies": result.vacancies,
            "api_error": result.error,
            "q": keyword,
            "location": location,
            "remote_only": remote_only,
            "search_keyword": default_keyword,
        })


class TrendingJobsView(JourneyGatedViewMixin, LoginRequiredMixin, View):
    page_url_name = "jobs:trending"
    template_name = "jobs/trending.html"

    def get(self, request):
        svc = TrendingJobsService()
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
            "generating": svc.is_running(request.user.id),
        })

    def post(self, request):
        svc = TrendingJobsService()
        action = request.POST.get("action", "match")
        refresh_trends = action == "refresh"
        state = svc.start_async(request.user.id, refresh_trends=refresh_trends)
        if state == "running":
            messages.info(
                request,
                "Your job matches are already being updated. Please wait a moment.",
            )
        elif refresh_trends:
            messages.info(
                request,
                "Refreshing trends and matching roles in the background. This page will update shortly.",
            )
        else:
            messages.info(
                request,
                "Matching roles in the background. This page will update shortly.",
            )
        return redirect("jobs:trending")


class TrendingJobsStatusView(LoginRequiredMixin, View):
    """Poll background trending-jobs task progress."""

    def get(self, request):
        svc = TrendingJobsService()
        return JsonResponse({
            "generating": svc.is_running(request.user.id),
            "match_count": JobMatch.objects.filter(user=request.user).count(),
            "job_count": TrendingJob.objects.count(),
        })


class TrendingJobDetailView(JourneyGatedViewMixin, LoginRequiredMixin, View):
    page_url_name = "jobs:detail"
    template_name = "jobs/detail.html"

    def get(self, request, slug):
        job = get_object_or_404(TrendingJob, slug=slug)
        match = JobMatch.objects.filter(user=request.user, job=job).first()
        ats_resume = ATSResumeService().get_latest_for_job(request.user.id, job.id)
        return render(request, self.template_name, {
            "job": job,
            "match": match,
            "ats_resume": ats_resume,
        })


class ATSResumeGenerateView(JourneyGatedViewMixin, LoginRequiredMixin, View):
    """Generate an ATS-friendly resume for a trending job."""

    page_url_name = "jobs:detail"

    def post(self, request, slug):
        job = get_object_or_404(TrendingJob, slug=slug)
        svc = ATSResumeService()
        try:
            ats = svc.generate_for_trending_job(request.user.id, job.id)
            return redirect("jobs:ats_resume_detail", pk=ats.id)
        except (GeminiUnavailable, LLMUnavailable) as e:
            messages.error(request, user_message_for(e))
            return redirect("jobs:detail", slug=slug)


class ATSResumeVacancyGenerateView(LoginRequiredMixin, View):
    """Generate an ATS-friendly resume for a live vacancy listing."""

    def post(self, request):
        title = (request.POST.get("job_title") or "").strip()
        company = (request.POST.get("company") or "").strip()
        description = (request.POST.get("description") or "").strip()
        tags_raw = (request.POST.get("tags") or "").strip()
        tags = [t.strip() for t in tags_raw.split(",") if t.strip()]

        if not title:
            messages.warning(request, "Job title is required to tailor a resume.")
            return redirect("jobs:openings")

        svc = ATSResumeService()
        try:
            ats = svc.generate_for_vacancy(
                request.user.id,
                job_title=title,
                company=company,
                description=description,
                tags=tags,
            )
            return redirect("jobs:ats_resume_detail", pk=ats.id)
        except (GeminiUnavailable, LLMUnavailable) as e:
            messages.error(request, user_message_for(e))
            return redirect("jobs:openings")


class ATSResumeDetailView(LoginRequiredMixin, View):
    template_name = "jobs/ats_resume.html"

    def get(self, request, pk):
        ats = get_object_or_404(ATSResume, pk=pk, user=request.user)
        return render(request, self.template_name, {"ats_resume": ats})


class ATSResumeDownloadView(LoginRequiredMixin, View):
    def get(self, request, pk):
        ats = get_object_or_404(ATSResume, pk=pk, user=request.user)
        safe_title = "".join(c if c.isalnum() or c in "-_" else "_" for c in ats.job_title)[:50]
        filename = f"ATS_Resume_{safe_title or 'job'}.txt"
        response = HttpResponse(ats.content, content_type="text/plain; charset=utf-8")
        response["Content-Disposition"] = f'attachment; filename="{filename}"'
        return response

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views import View

from services.resume_analysis_service import ResumeAnalysisService

from .forms import ResumeUploadForm
from .models import ResumeUpload


class ResumeUploadView(LoginRequiredMixin, View):
    template_name = "users/resume_upload.html"

    def _context(self, request, form=None, processing_resume_id=None):
        svc = ResumeAnalysisService()
        if processing_resume_id is None:
            processing_resume_id = svc.get_processing_resume_id(request.user.id)
        return {
            "form": form or ResumeUploadForm(),
            "resumes": ResumeUpload.objects.filter(user=request.user).order_by("-uploaded_at"),
            "extracts": [
                "Skills & Technologies", "Work Experience", "Education & Degrees",
                "Job Titles", "Certifications", "Career Domain",
                "Experience Level", "Professional Summary",
            ],
            "processing_resume_id": processing_resume_id,
        }

    def get(self, request):
        processing_id = request.GET.get("processing")
        try:
            processing_resume_id = int(processing_id) if processing_id else None
        except (TypeError, ValueError):
            processing_resume_id = None
        return render(request, self.template_name, self._context(request, processing_resume_id=processing_resume_id))

    def post(self, request):
        form = ResumeUploadForm(request.POST, request.FILES)
        if form.is_valid():
            resume = form.save(commit=False)
            resume.user = request.user
            resume.status = "processing"
            resume.save()

            svc = ResumeAnalysisService()
            state = svc.start_process_async(resume.id)
            if state == "running":
                messages.info(request, "Resume analysis is already in progress.")
            else:
                messages.info(
                    request,
                    "Resume uploaded. Analysis is running — this page will update when ready.",
                )
            return redirect(f"{request.path}?processing={resume.id}")

        return render(request, self.template_name, self._context(request, form=form))


class ResumeStatusView(LoginRequiredMixin, View):
    """Poll resume analysis progress."""

    def get(self, request, pk):
        svc = ResumeAnalysisService()
        status = svc.get_status(pk, request.user.id)
        if status.get("status") == "missing":
            return JsonResponse(status, status=404)
        return JsonResponse(status)


class ResumeResultsView(LoginRequiredMixin, View):
    template_name = "users/resume_results.html"

    def get(self, request, pk):
        resume = get_object_or_404(ResumeUpload, pk=pk, user=request.user)
        analysis = getattr(resume, "analysis", None)
        return render(request, self.template_name, {
            "resume": resume,
            "analysis": analysis,
        })

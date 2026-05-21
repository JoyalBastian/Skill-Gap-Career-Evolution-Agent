from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.shortcuts import get_object_or_404, redirect, render
from django.views import View

from ai_engine.llm_client import GeminiUnavailable, user_message_for
from services.resume_analysis_service import ResumeAnalysisService

from .forms import ResumeUploadForm
from .models import ResumeUpload


class ResumeUploadView(LoginRequiredMixin, View):
    template_name = "users/resume_upload.html"

    def get(self, request):
        resumes = ResumeUpload.objects.filter(user=request.user).order_by("-uploaded_at")
        return render(request, self.template_name, {
            "form": ResumeUploadForm(),
            "resumes": resumes,
            "extracts": [
                "Skills & Technologies", "Work Experience", "Education & Degrees",
                "Job Titles", "Certifications", "Career Domain",
                "Experience Level", "Professional Summary",
            ],
        })

    def post(self, request):
        form = ResumeUploadForm(request.POST, request.FILES)
        if form.is_valid():
            resume = form.save(commit=False)
            resume.user = request.user
            resume.save()
            try:
                ResumeAnalysisService().process_resume(resume.id)
                messages.success(request, "Resume analyzed successfully.")
                return redirect("users:resume_results", pk=resume.id)
            except GeminiUnavailable as e:
                messages.error(request, user_message_for(e))
            except Exception as e:
                messages.error(request, user_message_for(e))
        return render(request, self.template_name, {
            "form": form,
            "resumes": ResumeUpload.objects.filter(user=request.user),
            "extracts": [
                "Skills & Technologies", "Work Experience", "Education & Degrees",
                "Job Titles", "Certifications", "Career Domain",
                "Experience Level", "Professional Summary",
            ],
        })


class ResumeResultsView(LoginRequiredMixin, View):
    template_name = "users/resume_results.html"

    def get(self, request, pk):
        resume = get_object_or_404(ResumeUpload, pk=pk, user=request.user)
        analysis = getattr(resume, "analysis", None)
        return render(request, self.template_name, {
            "resume": resume,
            "analysis": analysis,
        })

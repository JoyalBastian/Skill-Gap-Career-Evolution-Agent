from django.urls import path

from . import views

app_name = "jobs"

urlpatterns = [
    path("openings/", views.JobOpeningsView.as_view(), name="openings"),
    path("trending/", views.TrendingJobsView.as_view(), name="trending"),
    path("trending/status/", views.TrendingJobsStatusView.as_view(), name="trending_status"),
    path("ats-resume/<int:pk>/download/", views.ATSResumeDownloadView.as_view(), name="ats_resume_download"),
    path("ats-resume/<int:pk>/", views.ATSResumeDetailView.as_view(), name="ats_resume_detail"),
    path("vacancy/ats-resume/", views.ATSResumeVacancyGenerateView.as_view(), name="ats_resume_vacancy"),
    path("<slug:slug>/ats-resume/", views.ATSResumeGenerateView.as_view(), name="ats_resume_generate"),
    path("<slug:slug>/", views.TrendingJobDetailView.as_view(), name="detail"),
]

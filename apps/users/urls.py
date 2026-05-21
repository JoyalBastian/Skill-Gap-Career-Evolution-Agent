from django.urls import path

from . import views, views_resume

app_name = "users"

urlpatterns = [
    path("", views.DashboardView.as_view(), name="dashboard"),
    path("profile/", views.ProfileUpdateView.as_view(), name="profile"),
    path("resume/upload/", views_resume.ResumeUploadView.as_view(), name="resume_upload"),
    path("resume/<int:pk>/results/", views_resume.ResumeResultsView.as_view(), name="resume_results"),
]

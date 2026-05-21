from django.urls import path

from . import views_resume

app_name = "users_resume"

urlpatterns = [
    path("upload/", views_resume.ResumeUploadView.as_view(), name="resume_upload"),
    path("<int:pk>/results/", views_resume.ResumeResultsView.as_view(), name="resume_results"),
]

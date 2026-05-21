from django.urls import path

from . import views

app_name = "progress"

urlpatterns = [
    path("", views.ProgressDashboardView.as_view(), name="dashboard"),
    path("api/", views.progress_api, name="api"),
]

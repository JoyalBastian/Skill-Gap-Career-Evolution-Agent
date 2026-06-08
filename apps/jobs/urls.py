from django.urls import path

from . import views

app_name = "jobs"

urlpatterns = [
    path("openings/", views.JobOpeningsView.as_view(), name="openings"),
    path("trending/", views.TrendingJobsView.as_view(), name="trending"),
    path("trending/status/", views.TrendingJobsStatusView.as_view(), name="trending_status"),
    path("<slug:slug>/", views.TrendingJobDetailView.as_view(), name="detail"),
]

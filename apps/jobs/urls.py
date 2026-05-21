from django.urls import path

from . import views

app_name = "jobs"

urlpatterns = [
    path("trending/", views.TrendingJobsView.as_view(), name="trending"),
    path("<slug:slug>/", views.TrendingJobDetailView.as_view(), name="detail"),
]

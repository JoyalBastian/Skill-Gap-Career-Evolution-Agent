from django.urls import path

from . import views

app_name = "recommendations"

urlpatterns = [
    path("", views.RecommendationListView.as_view(), name="list"),
    path("status/", views.RecommendationStatusView.as_view(), name="status"),
]

from django.urls import path

from . import views

app_name = "recommendations"

urlpatterns = [
    path("", views.RecommendationListView.as_view(), name="list"),
]

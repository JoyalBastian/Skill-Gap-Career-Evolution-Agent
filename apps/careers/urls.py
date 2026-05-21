from django.urls import path

from . import views

app_name = "careers"

urlpatterns = [
    path("", views.CareerListView.as_view(), name="list"),
    path("predictions/", views.CareerPredictionsView.as_view(), name="predictions"),
    path("<slug:slug>/", views.CareerDetailView.as_view(), name="detail"),
]

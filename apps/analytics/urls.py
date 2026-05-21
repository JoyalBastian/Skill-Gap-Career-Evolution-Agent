from django.urls import path

from . import views

app_name = "analytics"

urlpatterns = [
    path("dashboard/", views.AnalyticsDashboardView.as_view(), name="dashboard"),
    path("api/charts/", views.chart_data_api, name="chart_api"),
    path("chat/", views.ChatView.as_view(), name="chat"),
]

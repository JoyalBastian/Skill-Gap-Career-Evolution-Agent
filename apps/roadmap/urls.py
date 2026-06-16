from django.urls import path

from . import views

app_name = "roadmap"

urlpatterns = [
    path("", views.RoadmapListView.as_view(), name="list"),
    path("for-career/<slug:slug>/", views.RoadmapForCareerRedirectView.as_view(), name="for_career"),
    path("for-skill/<slug:slug>/", views.RoadmapForSkillRedirectView.as_view(), name="for_skill"),
    path("<int:pk>/", views.RoadmapDetailView.as_view(), name="detail"),
    path("step/<int:step_id>/complete/", views.MarkStepCompleteView.as_view(), name="step_complete"),
]

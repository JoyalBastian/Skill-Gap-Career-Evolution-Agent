from django.urls import path

from . import views

app_name = "skills"

urlpatterns = [
    path("", views.SkillListView.as_view(), name="list"),
    path("gap/skill/<slug:slug>/acquire/", views.MarkSkillAcquiredView.as_view(), name="gap_skill_acquire"),
    path("gap/skill/<slug:slug>/", views.SkillGapSkillView.as_view(), name="gap_skill"),
    path("gap/", views.SkillGapView.as_view(), name="gap"),
]

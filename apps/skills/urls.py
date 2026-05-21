from django.urls import path

from . import views

app_name = "skills"

urlpatterns = [
    path("", views.SkillListView.as_view(), name="list"),
    path("gap/", views.SkillGapView.as_view(), name="gap"),
]

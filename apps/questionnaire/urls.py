from django.urls import path

from . import views

app_name = "questionnaire"

urlpatterns = [
    path("start/", views.QuestionnaireStartView.as_view(), name="start"),
    path("<int:session_id>/", views.QuestionView.as_view(), name="question"),
    path("<int:session_id>/complete/", views.QuestionnaireCompleteView.as_view(), name="complete"),
    path("<int:session_id>/pipeline/run/", views.PipelineRunView.as_view(), name="pipeline_run"),
    path("<int:session_id>/pipeline/status/", views.PipelineStatusView.as_view(), name="pipeline_status"),
]

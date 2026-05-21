from django.contrib import admin

from .models import AIAnswer, AIQuestion, QuestionnaireSession


@admin.register(QuestionnaireSession)
class QuestionnaireSessionAdmin(admin.ModelAdmin):
    list_display = ["id", "user", "status", "started_at", "completed_at"]
    list_filter = ["status"]


admin.site.register(AIQuestion)
admin.site.register(AIAnswer)

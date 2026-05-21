from django.contrib import admin

from .models import ResumeAnalysisResult, Skill, UserSkill


@admin.register(Skill)
class SkillAdmin(admin.ModelAdmin):
    list_display = ["name", "slug", "category"]
    prepopulated_fields = {"slug": ("name",)}


admin.site.register(UserSkill)
admin.site.register(ResumeAnalysisResult)

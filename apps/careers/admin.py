from django.contrib import admin

from .models import CareerDomain, CareerPrediction, CareerSkillRequirement, SkillGapReport


@admin.register(CareerDomain)
class CareerDomainAdmin(admin.ModelAdmin):
    list_display = ["name", "slug", "is_technical"]
    prepopulated_fields = {"slug": ("name",)}


admin.site.register(CareerSkillRequirement)
admin.site.register(CareerPrediction)
admin.site.register(SkillGapReport)

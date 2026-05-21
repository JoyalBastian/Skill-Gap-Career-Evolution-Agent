from django.contrib import admin

from .models import JobMatch, TrendingJob


@admin.register(TrendingJob)
class TrendingJobAdmin(admin.ModelAdmin):
    list_display = ["title", "slug", "demand_label", "refreshed_at"]
    prepopulated_fields = {"slug": ("title",)}
    search_fields = ["title", "summary"]


@admin.register(JobMatch)
class JobMatchAdmin(admin.ModelAdmin):
    list_display = ["user", "job", "fit_score", "created_at"]
    list_filter = ["job"]

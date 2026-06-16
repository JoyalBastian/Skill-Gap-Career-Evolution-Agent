from django.contrib import admin

from .models import ATSResume, JobMatch, TrendingJob


@admin.register(TrendingJob)
class TrendingJobAdmin(admin.ModelAdmin):
    list_display = ["title", "slug", "demand_label", "refreshed_at"]
    prepopulated_fields = {"slug": ("title",)}
    search_fields = ["title", "summary"]


@admin.register(JobMatch)
class JobMatchAdmin(admin.ModelAdmin):
    list_display = ["user", "job", "fit_score", "created_at"]
    list_filter = ["job"]


@admin.register(ATSResume)
class ATSResumeAdmin(admin.ModelAdmin):
    list_display = ["job_title", "company", "user", "source", "created_at"]
    list_filter = ["source", "created_at"]
    search_fields = ["job_title", "company", "user__username"]

from django.conf import settings
from django.db import models


class TrendingJob(models.Model):
    """A currently in-demand job role surfaced by Gemini.

    Refreshed periodically; the same job rows are matched per-user via JobMatch.
    """

    DEMAND_CHOICES = [
        ("low", "Low"),
        ("medium", "Medium"),
        ("high", "High"),
        ("very_high", "Very High"),
    ]

    title = models.CharField(max_length=200)
    slug = models.SlugField(max_length=80, unique=True)
    summary = models.TextField(blank=True)
    demand_label = models.CharField(max_length=20, choices=DEMAND_CHOICES, default="medium")
    growth_reason = models.TextField(blank=True)
    required_skills = models.JSONField(default=list, blank=True)
    suggested_titles = models.JSONField(default=list, blank=True)
    salary_band_text = models.CharField(max_length=120, blank=True)
    refreshed_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-refreshed_at"]

    def __str__(self):
        return self.title


class JobMatch(models.Model):
    """Per-user fit ranking for trending jobs."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="job_matches",
    )
    job = models.ForeignKey(
        TrendingJob,
        on_delete=models.CASCADE,
        related_name="matches",
    )
    fit_score = models.FloatField(default=0.0)
    fit_reason = models.TextField(blank=True)
    matched_skills = models.JSONField(default=list, blank=True)
    missing_skills = models.JSONField(default=list, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-fit_score"]
        unique_together = [("user", "job")]

    def __str__(self):
        return f"{self.user.username} -> {self.job.title} ({self.fit_score:.0f}%)"


class ATSResume(models.Model):
    """ATS-optimized plain-text resume tailored to a specific job."""

    SOURCE_CHOICES = [
        ("trending", "Trending Job"),
        ("vacancy", "Live Vacancy"),
    ]

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="ats_resumes",
    )
    trending_job = models.ForeignKey(
        TrendingJob,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="ats_resumes",
    )
    source = models.CharField(max_length=20, choices=SOURCE_CHOICES, default="trending")
    job_title = models.CharField(max_length=200)
    company = models.CharField(max_length=150, blank=True)
    job_description = models.TextField(blank=True)
    required_skills = models.JSONField(default=list, blank=True)
    content = models.TextField()
    keywords_included = models.JSONField(default=list, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        target = self.company or self.job_title
        return f"ATS resume for {target} ({self.user_id})"

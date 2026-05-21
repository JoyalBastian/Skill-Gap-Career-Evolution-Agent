from django.conf import settings
from django.db import models


class CareerDomain(models.Model):
    name = models.CharField(max_length=150, unique=True)
    slug = models.SlugField(unique=True)
    description = models.TextField()
    is_technical = models.BooleanField(default=True)

    def __str__(self):
        return self.name


class CareerSkillRequirement(models.Model):
    """Legacy table kept for FK stability; no longer populated by the pipeline."""

    career = models.ForeignKey(
        CareerDomain,
        on_delete=models.CASCADE,
        related_name="skill_requirements",
    )
    skill = models.ForeignKey(
        "skills.Skill",
        on_delete=models.CASCADE,
        related_name="career_requirements",
    )
    importance = models.IntegerField(default=3)  # 1-5
    min_proficiency = models.IntegerField(default=2)

    class Meta:
        unique_together = ["career", "skill"]

    def __str__(self):
        return f"{self.career.name} requires {self.skill.name}"


class CareerPrediction(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="career_predictions",
    )
    career = models.ForeignKey(CareerDomain, on_delete=models.CASCADE)
    confidence_pct = models.FloatField()
    rank = models.IntegerField()
    explanation_text = models.TextField(blank=True)
    model_version = models.CharField(max_length=50, default="gemini-v1")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["rank"]

    def __str__(self):
        return f"{self.user.username} -> {self.career.name} ({self.confidence_pct}%)"


class SkillGapReport(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="skill_gap_reports",
    )
    career = models.ForeignKey(CareerDomain, on_delete=models.CASCADE)
    employability_score = models.FloatField(default=0.0)
    missing_skills = models.JSONField(default=list)
    prioritized_skills = models.JSONField(default=list)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"Gap Report: {self.user.username} - {self.career.name}"

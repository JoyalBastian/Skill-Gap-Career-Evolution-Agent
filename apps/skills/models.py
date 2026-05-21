from django.conf import settings
from django.db import models


class Skill(models.Model):
    CATEGORY_CHOICES = [
        ("technical", "Technical"),
        ("soft", "Soft Skill"),
        ("tool", "Tool"),
        ("domain", "Domain Knowledge"),
    ]

    name = models.CharField(max_length=150, unique=True)
    slug = models.SlugField(unique=True)
    category = models.CharField(max_length=20, choices=CATEGORY_CHOICES, default="technical")
    description = models.TextField(blank=True)
    synonyms = models.JSONField(default=list, blank=True)

    def __str__(self):
        return self.name


class UserSkill(models.Model):
    SOURCE_CHOICES = [
        ("resume", "Resume"),
        ("questionnaire", "Questionnaire"),
        ("manual", "Manual"),
        ("ai", "AI Detection"),
    ]

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="user_skills",
    )
    skill = models.ForeignKey(Skill, on_delete=models.CASCADE, related_name="user_skills")
    proficiency = models.IntegerField(default=1)
    source = models.CharField(max_length=20, choices=SOURCE_CHOICES, default="manual")
    confidence = models.FloatField(default=0.0)

    class Meta:
        unique_together = ["user", "skill"]

    def __str__(self):
        return f"{self.user.username} - {self.skill.name} ({self.proficiency})"


class ResumeAnalysisResult(models.Model):
    resume = models.OneToOneField(
        "users.ResumeUpload",
        on_delete=models.CASCADE,
        related_name="analysis",
    )
    skills_detected = models.JSONField(default=list)
    experience_years = models.FloatField(default=0.0)
    education = models.JSONField(default=list)
    projects = models.JSONField(default=list, blank=True)
    employability_score = models.FloatField(default=0.0)
    summary = models.TextField(blank=True)
    analyzed_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Analysis for Resume {self.resume_id}"

from django.conf import settings
from django.db import models


class Roadmap(models.Model):
    LEVEL_CHOICES = [
        ("beginner", "Beginner"),
        ("intermediate", "Intermediate"),
        ("advanced", "Advanced"),
    ]

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="roadmaps",
    )
    target_career = models.ForeignKey(
        "careers.CareerDomain",
        on_delete=models.CASCADE,
        related_name="roadmaps",
    )
    level = models.CharField(max_length=20, choices=LEVEL_CHOICES, default="beginner")
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    generated_at = models.DateTimeField(auto_now_add=True)
    is_active = models.BooleanField(default=True)

    def __str__(self):
        return f"{self.title} - {self.user.username}"


class RoadmapStep(models.Model):
    roadmap = models.ForeignKey(
        Roadmap,
        on_delete=models.CASCADE,
        related_name="steps",
    )
    order = models.IntegerField()
    title = models.CharField(max_length=255)
    description = models.TextField()
    skills = models.ManyToManyField("skills.Skill", blank=True)
    estimated_weeks = models.IntegerField(default=2)
    prerequisites = models.JSONField(default=list, blank=True)

    class Meta:
        ordering = ["order"]

    def __str__(self):
        return f"Step {self.order}: {self.title}"

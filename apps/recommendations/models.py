from django.conf import settings
from django.db import models


class LearningResource(models.Model):
    TYPE_CHOICES = [
        ("course", "Course"),
        ("certification", "Certification"),
        ("project", "Project"),
        ("technology", "Technology"),
        ("book", "Book"),
    ]

    LEVEL_CHOICES = [
        ("beginner", "Beginner"),
        ("intermediate", "Intermediate"),
        ("advanced", "Advanced"),
    ]

    title = models.CharField(max_length=255)
    resource_type = models.CharField(max_length=20, choices=TYPE_CHOICES)
    url = models.URLField(blank=True)
    description = models.TextField()
    level = models.CharField(max_length=20, choices=LEVEL_CHOICES, default="beginner")
    skills = models.ManyToManyField("skills.Skill", blank=True, related_name="resources")
    careers = models.ManyToManyField(
        "careers.CareerDomain", blank=True, related_name="resources"
    )
    is_ai_generated = models.BooleanField(default=True)

    def __str__(self):
        return self.title


class Recommendation(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="recommendations",
    )
    resource = models.ForeignKey(
        LearningResource,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
    )
    career = models.ForeignKey(
        "careers.CareerDomain",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
    )
    category = models.CharField(max_length=50)
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    url = models.URLField(blank=True)
    score = models.FloatField(default=0.0)
    reason = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-score"]

    def __str__(self):
        return f"{self.title} ({self.score:.2f})"

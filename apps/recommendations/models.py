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

    PROVIDER_CHOICES = [
        ("coursera", "Coursera"),
        ("udemy", "Udemy"),
        ("edx", "edX"),
        ("mit_ocw", "MIT OpenCourseWare"),
        ("google", "Google"),
        ("microsoft", "Microsoft"),
        ("aws", "AWS"),
        ("university", "University"),
        ("other", "Other"),
    ]

    SOURCE_CHOICES = [
        ("curated", "Curated"),
        ("web_search", "Web Search"),
        ("ai", "AI Generated"),
    ]

    title = models.CharField(max_length=255)
    resource_type = models.CharField(max_length=20, choices=TYPE_CHOICES)
    url = models.URLField(blank=True)
    description = models.TextField()
    level = models.CharField(max_length=20, choices=LEVEL_CHOICES, default="beginner")
    provider = models.CharField(
        max_length=20, choices=PROVIDER_CHOICES, blank=True, default=""
    )
    source = models.CharField(
        max_length=20, choices=SOURCE_CHOICES, default="ai", blank=True
    )
    verified_at = models.DateTimeField(null=True, blank=True)
    skills = models.ManyToManyField("skills.Skill", blank=True, related_name="resources")
    careers = models.ManyToManyField(
        "careers.CareerDomain", blank=True, related_name="resources"
    )
    is_ai_generated = models.BooleanField(default=True)

    class Meta:
        indexes = [
            models.Index(fields=["provider", "level"]),
            models.Index(fields=["source", "level"]),
        ]

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
    target_skill = models.ForeignKey(
        "skills.Skill",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="gap_recommendations",
    )
    gap_report = models.ForeignKey(
        "careers.SkillGapReport",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="recommendations",
    )
    category = models.CharField(max_length=50)
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    url = models.URLField(blank=True)
    score = models.FloatField(default=0.0)
    reason = models.TextField(blank=True)
    is_primary_for_gap = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-score"]

    def __str__(self):
        return f"{self.title} ({self.score:.2f})"

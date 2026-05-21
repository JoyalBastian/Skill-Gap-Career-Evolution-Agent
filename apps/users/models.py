from django.conf import settings
from django.db import models


class Profile(models.Model):
    LEVEL_CHOICES = [
        ("beginner", "Beginner"),
        ("intermediate", "Intermediate"),
        ("advanced", "Advanced"),
    ]

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="profile",
    )
    bio = models.TextField(blank=True)
    avatar = models.ImageField(upload_to="avatars/", blank=True, null=True)
    target_career_level = models.CharField(
        max_length=20, choices=LEVEL_CHOICES, default="beginner"
    )
    is_technical_track = models.BooleanField(default=True)
    target_career = models.ForeignKey(
        "careers.CareerDomain",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="targeted_by",
    )
    # Structured data extracted from resume by Gemini, plus persona payload
    resume_context = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Profile: {self.user.username}"

    def has_resume_context(self) -> bool:
        return bool(self.resume_context)


class ResumeUpload(models.Model):
    STATUS_CHOICES = [
        ("pending", "Pending"),
        ("processing", "Processing"),
        ("completed", "Completed"),
        ("failed", "Failed"),
    ]

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="resumes",
    )
    file = models.FileField(upload_to="resumes/")
    parsed_text = models.TextField(blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="pending")
    uploaded_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Resume {self.id} - {self.user.username}"

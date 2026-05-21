from django.conf import settings
from django.db import models


class AIInsight(models.Model):
    INSIGHT_TYPES = [
        ("career_prediction", "Career Prediction"),
        ("personality", "Personality Analysis"),
        ("interest", "Interest Analysis"),
        ("skill_gap", "Skill Gap"),
        ("employability", "Employability"),
        ("recommendation", "Recommendation"),
        ("roadmap", "Roadmap"),
    ]

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="ai_insights",
    )
    insight_type = models.CharField(max_length=30, choices=INSIGHT_TYPES)
    payload = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.insight_type} for {self.user.username}"


class ChatMessage(models.Model):
    ROLE_CHOICES = [
        ("user", "User"),
        ("assistant", "Assistant"),
    ]

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="chat_messages",
    )
    role = models.CharField(max_length=10, choices=ROLE_CHOICES)
    content = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]

    def __str__(self):
        return f"{self.role}: {self.content[:50]}"

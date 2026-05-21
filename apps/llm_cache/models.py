from django.db import models


class LLMCacheEntry(models.Model):
    """Persistent cache for Gemini responses, keyed by SHA-256 of prompt."""

    key_hash = models.CharField(max_length=64, unique=True, db_index=True)
    prompt_preview = models.TextField(blank=True)
    response_json = models.JSONField(default=dict, blank=True)
    response_text = models.TextField(blank=True)
    model_name = models.CharField(max_length=80, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"LLMCacheEntry({self.key_hash[:10]}...)"

from django.contrib import admin

from .models import LLMCacheEntry


@admin.register(LLMCacheEntry)
class LLMCacheEntryAdmin(admin.ModelAdmin):
    list_display = ["key_hash", "model_name", "created_at", "expires_at"]
    search_fields = ["key_hash", "prompt_preview"]
    readonly_fields = ["key_hash", "prompt_preview", "response_json", "response_text", "model_name", "created_at", "expires_at"]
